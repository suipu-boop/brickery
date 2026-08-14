"""§P2 调度内核（clean room）：后台异步 agent + 多 agent 子代理编排。

- Scheduler = 任务队列 + 每个任务一个独立 AgentLoop 实例（复用 loop_factory）。
  派发即返回 task_id，不阻塞前台；worker 池并发执行，默认 2（M4 内存/推理并发兜底）。
- 多 agent = 主 agent 用 SpawnAgent 工具派子任务，用 WaitTask 汇聚结果；子任务
  彼此隔离（各自会话/记忆/工具执行上下文），跑在独立 worker 线程。
- 持久化到 <home>/tasks.jsonl；崩溃恢复：重启时遗留 RUNNING 标记 FAILED，绝不留孤儿。
- 红线：① 任务复用既有 loop（权限/确认/模式闸门对后台任务一视同仁）；② 零新依赖；
  ③ 不触外网（除非任务内容本身走网络 API，遵循既有 §4.3 红线）。
"""
from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, List, Optional


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)


@dataclass
class Task:
    id: str
    prompt: str
    project: str = ""
    parent_id: Optional[str] = None
    status: TaskStatus = TaskStatus.QUEUED
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: str = ""
    finished_at: Optional[str] = None
    subtasks: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "project": self.project,
            "parent_id": self.parent_id,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "subtasks": list(self.subtasks),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        return cls(
            id=d.get("id", ""),
            prompt=d.get("prompt", ""),
            project=d.get("project", ""),
            parent_id=d.get("parent_id"),
            status=TaskStatus(d.get("status", "queued")),
            result=d.get("result"),
            error=d.get("error"),
            created_at=d.get("created_at", ""),
            finished_at=d.get("finished_at"),
            subtasks=list(d.get("subtasks", []) or []),
        )


class TaskStore:
    """任务持久化（整体重写文件；任务量小，足够；崩溃恢复在 _load 内做）。"""

    def __init__(self, home: Path):
        self.path = Path(home) / "tasks.jsonl"
        self._tasks: dict[str, Task] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    t = Task.from_dict(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    continue
                # 崩溃恢复：上一进程遗留的 RUNNING 视为中断失败，绝不留孤儿任务
                if t.status == TaskStatus.RUNNING:
                    t.status = TaskStatus.FAILED
                    t.error = (t.error or "") + "（进程重启，未完成任务标记失败）"
                    t.finished_at = _now()
                self._tasks[t.id] = t
        except OSError:
            pass

    def _persist(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lines = [json.dumps(t.to_dict(), ensure_ascii=False)
                     for t in self._tasks.values()]
            self.path.write_text("\n".join(lines) + ("\n" if lines else ""),
                                 encoding="utf-8")
        except OSError:
            pass

    def put(self, task: Task) -> None:
        self._tasks[task.id] = task
        self._persist()

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def all(self) -> List[Task]:
        return sorted(self._tasks.values(),
                      key=lambda t: t.created_at, reverse=True)

    def list_status(self, status: Optional[str] = None) -> List[Task]:
        items = self.all()
        if status:
            items = [t for t in items if t.status.value == status]
        return items


class Scheduler:
    """任务调度内核：队列 + worker 线程池 + 完成通知 + 取消 + 崩溃恢复。"""

    def __init__(self, loop_factory: Callable[..., "object"], *,
                 home: Path,
                 max_workers: int = 2,
                 notifier: Optional[Callable[["Task"], None]] = None):
        self._loop_factory = loop_factory
        self.store = TaskStore(home)
        self.max_workers = max(1, int(max_workers))
        self._notifier = notifier
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._stop = threading.Event()
        self._workers: List[threading.Thread] = []
        self._stops: dict[str, threading.Event] = {}
        self._stops_lock = threading.Lock()
        self._done_events: dict[str, threading.Event] = {}
        self._done_lock = threading.Lock()
        # 重启后：把持久化里仍 QUEUED 的任务重新入队（RUNNING 已在 _load 标失败）
        for t in self.store.all():
            if t.status == TaskStatus.QUEUED:
                self._queue.put(t.id)

    def start(self) -> None:
        self._stop.clear()
        for _ in range(self.max_workers):
            w = threading.Thread(target=self._worker, daemon=True)
            w.start()
            self._workers.append(w)

    def submit(self, prompt: str, project: str = "",
               parent_id: Optional[str] = None,
               session_id: Optional[str] = None) -> Task:
        tid = session_id or ("task_" + uuid.uuid4().hex[:12])
        task = Task(id=tid, prompt=prompt, project=project,
                    parent_id=parent_id, status=TaskStatus.QUEUED,
                    created_at=_now())
        # 父任务记录子任务（用于子代理树展示）
        if parent_id:
            parent = self.store.get(parent_id)
            if parent is not None and tid not in parent.subtasks:
                parent.subtasks.append(tid)
                self.store.put(parent)
        self.store.put(task)
        self._queue.put(tid)
        return task

    def cancel(self, task_id: str) -> bool:
        task = self.store.get(task_id)
        if task is None:
            return False
        if task.status == TaskStatus.QUEUED:
            task.status = TaskStatus.CANCELLED
            task.finished_at = _now()
            self.store.put(task)
            return True
        if task.status == TaskStatus.RUNNING:
            # 通知在跑的 worker 停止（loop 在检查点抛 InterruptedError 收尾）
            with self._stops_lock:
                ev = self._stops.get(task_id)
            if ev is not None:
                ev.set()
            return True
        return False  # 终态任务不可取消

    def get(self, task_id: str) -> Optional[Task]:
        return self.store.get(task_id)

    def list(self, status: Optional[str] = None) -> List[Task]:
        return self.store.list_status(status)

    def wait(self, task_id: str, timeout: float = 120.0) -> Optional[Task]:
        """阻塞直到任务进入终态或超时；返回最新 Task（超时返回 None）。"""
        with self._done_lock:
            ev = self._done_events.setdefault(task_id, threading.Event())
        if not ev.wait(timeout=timeout):
            return None
        return self.store.get(task_id)

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                tid = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            self._execute(tid)

    def _execute(self, task_id: str) -> None:
        # done 事件必须在所有出口（含提前 return）都置位，否则 wait() 会死等超时。
        with self._done_lock:
            done_ev = self._done_events.setdefault(task_id, threading.Event())
        try:
            task = self.store.get(task_id)
            if task is None or task.status != TaskStatus.QUEUED:
                return  # 已被取消/不存在：事件仍由 finally 置位，wait 不会卡死
            stop = threading.Event()
            with self._stops_lock:
                self._stops[task_id] = stop
            task.status = TaskStatus.RUNNING
            self.store.put(task)
            try:
                loop = self._loop_factory(project=task.project, session=task.id,
                                          should_stop=stop.is_set)
                reply = loop.run(task.prompt, project=task.project)
                task.status = TaskStatus.DONE
                task.result = reply
            except InterruptedError:
                task.status = TaskStatus.CANCELLED
            except Exception as e:  # noqa: BLE001 - 单任务失败隔离，不拖垮 worker
                task.status = TaskStatus.FAILED
                task.error = f"{type(e).__name__}: {e}"
            finally:
                task.finished_at = _now()
                self.store.put(task)
                with self._stops_lock:
                    self._stops.pop(task_id, None)
                if self._notifier is not None:
                    try:
                        self._notifier(task)
                    except Exception:  # noqa: BLE001
                        pass
        finally:
            done_ev.set()

    def stop(self) -> None:
        self._stop.set()
        with self._stops_lock:
            for ev in self._stops.values():
                ev.set()
        for w in self._workers:
            if w.is_alive():
                w.join(timeout=5)
        self._workers = []
