"""§X 后端常驻监督器（clean room，纯标准库）。

独立进程，由桌面 App 拉起，形成「App -> supervisor -> ipc（后端）」三层防孤儿链：
- supervisor 是 App 的直接子进程；ipc 是 supervisor 的子进程。
- 任一层父进程意外死亡，子层都能感知并优雅退出，绝不遗留孤儿。

职责（全部纯代码确定性，绝不依赖任何模型）：
- 拉起 `python -m runtime.ipc` 子进程（透传 PYTHONPATH / BRICKERY_HOME）。
- 周期探活：端口能连通且后端能响应 health，即视为存活。
- 后端崩溃 / 启动失败：自动重启，重启间隔按指数退避（封顶）。
- 连续失败超限：停止盲重启，生成纯代码诊断报告（把后端错误日志按已知模式
  分类，翻成人话建议），写 selfheal.report.json / .txt，UI 可读。
- 写 supervisor.status.json：暴露健康状态 / 重启次数 / 最近自愈事件 / 报告路径。
- SIGTERM / SIGINT：优雅停止（先停后端，再退出）。
- 父进程（宿主 App）死亡：立即自杀，防孤儿。

纪律红线：本文件严禁 import 任何模型或重型依赖（llama_cpp / websocket 等），
只用 Python 标准库。模型若参与排障，只能在未来作为「解释层（翻译官）」——
读诊断报告的人话版，不得决策、不得执行。本文件不实现模型部分。
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# 常量（可被测试通过构造参数覆盖，便于加速验证）
# ---------------------------------------------------------------------------
DEFAULT_PORT = 18765
DEFAULT_HOST = "127.0.0.1"
HEALTH_TIMEOUT = 2.0          # 单次探活超时（秒）
POLL_INTERVAL = 3.0           # 监控循环间隔（秒）
BASE_BACKOFF = 1.0            # 退避基数（秒）
MAX_BACKOFF = 30.0            # 退避封顶（秒）
MAX_CONSECUTIVE_FAILURES = 5  # 连续失败上限，超过则停止盲重启
HEALTH_FAIL_THRESHOLD = 2            # 进程已死时，health 连续失败几次就重启
HEALTH_FAIL_THRESHOLD_TRANSIENT = 10  # 进程还活着时，health 连续失败几次才重启（防瞬断/GGUF加载慢）

STATUS_FILE = "supervisor.status.json"
STDERR_LOG = "logs/backend.stderr.log"
REPORT_JSON = "logs/selfheal.report.json"
REPORT_TXT = "logs/selfheal.report.txt"
MAX_STDERR_BYTES = 2 * 1024 * 1024   # 后端日志过大时截断保留尾部
STDERR_RING = 600                    # 内存中保留的最近后端日志行数（供诊断报告）


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def compute_backoff(attempt: int, base: float = BASE_BACKOFF,
                    cap: float = MAX_BACKOFF) -> float:
    """指数退避：base * 2^(attempt-1)，封顶 cap。attempt 视作 >=1。"""
    if attempt < 1:
        attempt = 1
    return min(base * (2 ** (attempt - 1)), cap)


def classify_backend_error(stderr: str) -> dict:
    """纯代码分类后端 stderr 文本 -> {category, suggestion, tail}。

    不依赖任何模型；只为把日志翻成人话建议，供 UI / 诊断报告展示。
    """
    text = stderr or ""
    lines = text.strip().splitlines()
    tail = "\n".join(lines[-15:]) if lines else ""

    lowered = text.lower()
    # 端口占用（最高优先级：最易发生在上次未干净退出后）
    if ("address already in use" in lowered
            or "errno 48" in lowered
            or "error while attempting to bind" in lowered):
        return {
            "category": "port_in_use",
            "suggestion": "后端端口被占用（多半是上次未干净退出，或其它程序占用了该端口）。"
                          "建议：完全退出 App 后重试；若仍不行，检查是否有残留进程占用了该端口。",
            "tail": tail,
        }
    # 依赖缺失
    if "modulenotfounderror" in lowered or "importerror" in lowered:
        mod = ""
        for line in text.splitlines():
            if "No module named" in line:
                mod = line.split("No module named")[-1].strip().strip("'\"")
                break
        return {
            "category": "missing_dependency",
            "suggestion": f"Python 依赖缺失：{mod or '未知模块'}。本地推理依赖 llama-cpp-python 等；"
                          "建议确认运行环境完整（重装 / 恢复打包的 Python 运行时）。",
            "tail": tail,
        }
    # 源码语法错误（开发期）
    if "syntaxerror" in lowered:
        return {
            "category": "syntax_error",
            "suggestion": "源码存在语法错误（开发期问题）。请检查最近改动的后端文件，或回退到上一个可用版本。",
            "tail": tail,
        }
    # 内存不足
    if "memoryerror" in lowered or "cannot allocate" in lowered or "out of memory" in lowered:
        return {
            "category": "out_of_memory",
            "suggestion": "内存不足。本地模型可能过大，或同时运行了其它占内存的程序；"
                          "建议关闭其它应用，或换用更小的模型。",
            "tail": tail,
        }
    # 通用运行时异常
    if "traceback" in lowered or "error" in lowered or "exception" in lowered:
        last_exc = ""
        for line in reversed(text.splitlines()):
            if "Error" in line or "Exception" in line:
                last_exc = line.strip()
                break
        return {
            "category": "runtime_exception",
            "suggestion": f"后端运行时抛出异常：{last_exc or '（见日志）'}。"
                          "这是软件层错误，可按日志定位修复；若反复出现请查看诊断报告。",
            "tail": tail,
        }
    # 无法归类
    return {
        "category": "unknown",
        "suggestion": "后端异常退出但未能自动归类。请查看完整日志（backend.stderr.log）或诊断报告。",
        "tail": tail,
    }


class Supervisor:
    """后端常驻监督器：拉起 / 监控 / 自愈 / 诊断，全纯代码。"""

    def __init__(self, *, port: int = DEFAULT_PORT, host: str = DEFAULT_HOST,
                 home: Optional[Path] = None, python: Optional[str] = None,
                 repo: Optional[Path] = None, poll_interval: float = POLL_INTERVAL,
                 max_failures: int = MAX_CONSECUTIVE_FAILURES,
                 base_backoff: float = BASE_BACKOFF, max_backoff: float = MAX_BACKOFF,
                 health_timeout: float = HEALTH_TIMEOUT,
                 backend_args: Optional[list] = None,
                 health_check: Optional[Callable[[str, int], bool]] = None,
                 stop_event: Optional[threading.Event] = None):
        self.port = port
        self.host = host
        self.home = Path(home) if home else Path(
            os.environ.get("BRICKERY_HOME", Path.home() / ".brickery"))
        self.home.mkdir(parents=True, exist_ok=True)
        self.python = python or sys.executable
        # repo 根：supervisor.py 位于 <repo>/runtime/，故父目录即仓库根
        self.repo = Path(repo) if repo else Path(__file__).resolve().parent.parent
        self.poll_interval = poll_interval
        self.max_failures = max_failures
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff
        self.health_timeout = health_timeout
        # 后端启动命令（默认 python -m runtime.ipc；测试可覆盖为 fake 脚本）
        self.backend_args = list(backend_args) if backend_args else ["-m", "runtime.ipc"]
        self.health_check = health_check or self._default_health_check

        self._proc: Optional[subprocess.Popen] = None
        self._stop = stop_event or threading.Event()
        self._lock = threading.Lock()

        # 状态
        self.restarts = 0
        self.consecutive_failures = 0
        self.consecutive_health_fail = 0
        self.healthy = False
        self.status = "starting"            # starting|running|recovering|needs_attention
        # 是否「拥有」当前后端子进程。单例复用模式下，端口已被上轮 App 残留的
        # 健康后端占用，本监督器不拉起自有后端、仅复用并监控；此时为 False，
        # 退出时不杀外部进程，且其挂掉后自动改由本监督器拉起自有后端。
        self._owns_backend = True
        self.last_event: Optional[dict] = None
        self.report_path: Optional[str] = None
        self.started_at = now_iso()
        self.last_health_check = now_iso()

        # 日志 / 状态文件路径
        self._stderr_path = self.home / STDERR_LOG
        self._stderr_path.parent.mkdir(parents=True, exist_ok=True)
        self._status_path = self.home / STATUS_FILE
        self._report_json = self.home / REPORT_JSON
        self._report_txt = self.home / REPORT_TXT
        self._stderr_ring: deque = deque(maxlen=STDERR_RING)

        self._thread: Optional[threading.Thread] = None
        self._ppid_thread: Optional[threading.Thread] = None
        # 记录「启动时的父进程」pid（宿主 App）；一旦父进程死亡，
        # getppid() 会变成 1（init），此时自杀以防孤儿。注意：必须是 getppid()
        # 而非 getpid()——记自己的 pid 会让「父≠己」永远成立而误自杀。
        self._parent_pid = os.getppid()

    # ----- 后端子进程管理 -----

    def _build_env(self) -> dict:
        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        parts = [str(self.repo)]
        if existing:
            parts.append(existing)
        env["PYTHONPATH"] = os.pathsep.join(parts)
        env["BRICKERY_HOME"] = str(self.home)
        return env

    def _spawn_backend(self) -> None:
        """拉起后端子进程。stdout 合并进 stderr 统一捕获写日志。"""
        cmd = [self.python, *self.backend_args, "--port", str(self.port)]
        env = self._build_env()
        self._proc = subprocess.Popen(
            cmd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1)
        # 起一个泵线程把后端输出持续写入日志 + ring buffer（避免管道阻塞）
        pump = threading.Thread(target=self._pump_stderr, args=(self._proc,),
                                daemon=True)
        pump.start()
        with self._lock:
            self.last_event = {
                "ts": now_iso(),
                "action": "spawn",
                "backend_pid": self._proc.pid,
                "note": "拉起后端",
            }
        self._write_status()

    def _pump_stderr(self, proc: subprocess.Popen) -> None:
        # 后端用 stdout=PIPE, stderr=STDOUT 合并输出，故统一读 proc.stdout
        stream = proc.stdout
        if stream is not None:
            try:
                for line in stream:
                    self._append_stderr(line.rstrip("\n"))
            finally:
                try:
                    stream.close()
                except OSError:
                    pass
        # 子进程已退出（EOF）：记录退出码，触发一次失败判定
        rc = proc.poll()
        self._append_stderr(
            f"[supervisor] 后端进程退出，退出码={rc}")

    def _append_stderr(self, line: str) -> None:
        self._stderr_ring.append(line)
        try:
            with open(self._stderr_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
        # 超限截断保留尾部
        try:
            if self._stderr_path.exists() and self._stderr_path.stat().st_size > MAX_STDERR_BYTES:
                tail = self._stderr_path.read_text(encoding="utf-8").splitlines()[-2000:]
                self._stderr_path.write_text("\n".join(tail) + "\n", encoding="utf-8")
        except OSError:
            pass

    def _terminate_backend(self) -> None:
        if self._proc is None:
            return
        p = self._proc
        self._proc = None
        try:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    p.kill()
        except OSError:
            pass
        finally:
            # 关闭管道，避免文件描述符泄漏（ResourceWarning）
            for s in (p.stdout, p.stderr):
                try:
                    if s is not None:
                        s.close()
                except OSError:
                    pass

    # ----- 探活 -----

    def _default_health_check(self, host: str, port: int) -> bool:
        """连 127.0.0.1:port，发一次 health，能收到合法 JSON 即视为健康。"""
        try:
            with socket.create_connection((host, port), timeout=self.health_timeout) as s:
                s.settimeout(self.health_timeout)
                payload = json.dumps(
                    {"req_id": 1, "method": "health", "params": {}}) + "\n"
                s.sendall(payload.encode("utf-8"))
                buf = b""
                while b"\n" not in buf:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                line = buf.split(b"\n", 1)[0]
                if not line:
                    return False
                obj = json.loads(line.decode("utf-8", "replace"))
                return isinstance(obj, dict) and "ok" in obj
        except (OSError, socket.timeout, json.JSONDecodeError, ValueError):
            return False

    # ----- 失败处理 / 自愈 -----

    def _handle_failure(self, reason: str, exit_code: Optional[int]) -> None:
        self.consecutive_health_fail = 0
        # 瞬断/弱网导致 health 超时但进程还活着：只重启，不占 failure 配额
        # （避免被 connector 网络抖动误判为"后端崩了"耗尽 max_failures 停服）。
        is_transient = reason == "health_unreachable_transient"
        if not is_transient:
            self.consecutive_failures += 1
        event = {
            "ts": now_iso(),
            "action": "restart",
            "reason": reason,
            "exit_code": exit_code,
            "attempt": self.consecutive_failures,
        }
        with self._lock:
            self.last_event = event
        level = ("WARN" if is_transient else "ERROR")
        self._append_stderr(
            f"[supervisor] [{level}] 后端异常（{reason}，退出码={exit_code}），"
            f"第 {self.consecutive_failures}/{self.max_failures} 次自愈重启")

        if self.consecutive_failures > self.max_failures:
            self._enter_needs_attention(reason)
            return

        self.status = "recovering"
        backoff = compute_backoff(self.consecutive_failures,
                                  self.base_backoff, self.max_backoff)
        self._write_status()
        # 等退避（可被 stop 打断）
        self._stop.wait(backoff)
        if self._stop.is_set():
            return
        self._terminate_backend()
        self._spawn_backend()
        self.restarts += 1

    def _enter_needs_attention(self, reason: str) -> None:
        self.status = "needs_attention"
        self.healthy = False
        self.report_path = self._generate_report(reason)
        with self._lock:
            if self.last_event:
                self.last_event["note"] = "已达最大自愈次数，停止盲重启，生成诊断报告"
        self._append_stderr(
            "[supervisor] 连续自愈失败超限，停止自动重启，已生成诊断报告；"
            "请查看诊断页或日志后手动重启 App")
        self._write_status()

    def _generate_report(self, reason: str) -> str:
        """纯代码诊断报告：分类累积的后端日志，翻成人话建议。"""
        try:
            recent = "\n".join(self._stderr_ring)
            if not recent and self._stderr_path.exists():
                lines = self._stderr_path.read_text(
                    encoding="utf-8", errors="replace").splitlines()
                recent = "\n".join(lines[-STDERR_RING:])
            diag = classify_backend_error(recent)
        except OSError:
            diag = classify_backend_error("")

        report = {
            "generated_at": now_iso(),
            "reason": reason,
            "restarts": self.restarts,
            "consecutive_failures": self.consecutive_failures,
            "category": diag["category"],
            "suggestion": diag["suggestion"],
            "recent_log_tail": diag["tail"],
            "stderr_log": str(self._stderr_path),
        }
        try:
            self._report_json.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8")
            txt = (
                "Brickery 后端自愈诊断报告\n"
                f"生成时间：{report['generated_at']}\n"
                f"触发原因：{reason}\n"
                f"自愈尝试次数：{self.restarts}\n"
                f"连续失败次数：{self.consecutive_failures}\n"
                f"主要故障分类：{diag['category']}\n\n"
                f"建议：\n{diag['suggestion']}\n\n"
                "最近错误日志尾部：\n"
                "----------------------------------------\n"
                f"{diag['tail']}\n"
                "----------------------------------------\n"
                f"完整后端日志：{self._stderr_path}\n"
            )
            self._report_txt.write_text(txt, encoding="utf-8")
            return str(self._report_txt)
        except OSError:
            return ""

    # ----- 状态文件（原子写） -----

    def _write_status(self) -> None:
        self.last_health_check = now_iso()
        data = {
            "pid": os.getpid(),
            "backend_pid": self._proc.pid if self._proc and self._proc.poll() is None else None,
            "healthy": self.healthy,
            "status": self.status,
            "started_at": self.started_at,
            "last_health_check": self.last_health_check,
            "restarts": self.restarts,
            "consecutive_failures": self.consecutive_failures,
            "last_event": self.last_event,
            "report_path": self.report_path,
            "note": self._status_note(),
        }
        try:
            tmp = self._status_path.with_suffix(".status.json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self._status_path)
        except OSError:
            pass

    def _status_note(self) -> str:
        if self.status == "running":
            return "后端运行正常"
        if self.status == "recovering":
            return f"后端异常，正在自愈（第 {self.consecutive_failures} 次重启）"
        if self.status == "needs_attention":
            return "后端持续崩溃，已停止自动重启，请查看诊断报告"
        return "后端启动中"

    # ----- 主循环 -----

    def _loop(self) -> None:
        # 单例保护：先探活端口。若已有健康后端在跑（多半是上轮 App 退出残留的
        # 后端子进程仍占着端口），直接复用、不重复拉起——否则新拉的后端会因
        # Errno 48（Address already in use）绑定失败，陷入崩溃自愈死循环。
        if self.health_check(self.host, self.port):
            self.healthy = True
            self.status = "running"
            self._owns_backend = False
            self._write_status()
            self._append_stderr(
                "[supervisor] 端口已被健康后端占用，复用现有实例（不重复拉起）。")
        else:
            self._spawn_backend()
            self._owns_backend = True
        while not self._stop.is_set():
            if self.status == "needs_attention":
                # 已达自愈上限：停止盲重启，仅周期性刷新状态供 UI 读取，
                # 不再无意义累加 consecutive_failures / 频繁写盘。
                self._write_status()
                self._stop.wait(max(self.poll_interval, 5.0))
                continue
            self._stop.wait(self.poll_interval)
            if self._stop.is_set():
                break
            # 探活
            if self.health_check(self.host, self.port):
                self.healthy = True
                self.consecutive_failures = 0
                self.consecutive_health_fail = 0
                if self.status != "running":
                    self.status = "running"
                self._write_status()
                continue
            # 端口探活失败
            if not self._owns_backend:
                # 当前是复用外部（残留）后端；它挂了 → 改由本监督器拉起自有后端
                self._owns_backend = True
                self._append_stderr(
                    "[supervisor] 复用的外部后端已退出，改由本监督器拉起自有后端。")
                self._spawn_backend()
                continue
            # 我们拥有的后端挂了：明确崩溃
            self.healthy = False
            self.consecutive_health_fail += 1
            # 分级：进程还活着=transient（瞬断/弱网/GGUF加载慢），用高阈值等恢复；
            # 进程已死=fatal，用低阈值快速重启。
            transient = self._proc is not None and self._proc.poll() is None
            threshold = HEALTH_FAIL_THRESHOLD_TRANSIENT if transient else HEALTH_FAIL_THRESHOLD
            if self.consecutive_health_fail >= threshold:
                reason = ("health_unreachable_transient" if transient
                          else "health_unreachable")
                self._handle_failure(reason, self._proc.poll() if self._proc else None)
            # 否则继续等下一次 poll（可能是瞬时抖动）

    def _ppid_watch(self) -> None:
        """父进程（宿主 App）死亡则立即自杀，防孤儿。"""
        while not self._stop.is_set():
            time.sleep(1)
            if os.getppid() != self._parent_pid:
                self.stop()
                os._exit(0)

    # ----- 生命周期 -----

    def start(self, block: bool = False) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._ppid_thread = threading.Thread(target=self._ppid_watch, daemon=True)
        self._ppid_thread.start()
        self._write_status()
        if block:
            try:
                while not self._stop.is_set():
                    self._stop.wait(1)
            except KeyboardInterrupt:
                self.stop()

    def stop(self) -> None:
        self._stop.set()
        self._terminate_backend()
        self.status = "stopped"
        self._write_status()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def install_signal_handlers(self) -> None:
        def _handler(signum, frame):
            self.stop()
        try:
            signal.signal(signal.SIGTERM, _handler)
            signal.signal(signal.SIGINT, _handler)
        except (ValueError, OSError):
            pass


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Brickery 后端监督器")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--home", default=None)
    args = ap.parse_args(argv)

    sup = Supervisor(port=args.port, host=args.host, home=args.home)
    sup.install_signal_handlers()
    print(f"[Brickery Supervisor] 启动，监控后端于 {sup.host}:{sup.port}"
          f"（home={sup.home}）", flush=True)
    sup.start(block=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
