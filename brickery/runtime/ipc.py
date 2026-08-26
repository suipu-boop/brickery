"""Shadeling 本地 IPC 服务（clean room，阶段四后端）。

Swift 桌面 App 以子进程托管本服务；服务仅监听 127.0.0.1，
对外暴露 JSON 协议，驱动已建的 runtime（主循环/引擎路由/守护进程）
与 memory 子系统。零外部依赖（仅标准库）；推理引擎严格遵循 §4.3：
本地 GGUF 为默认，未配置时绝不触网（复用 NoEngineConfigured 路径）。

红线：
- 不 import 任何旧 agent 框架；本模块为全新自研。
- 仅监听本机回环地址，不构成"外部推理服务"。
- api 后端仅在用户显式填写 api_url 时才出站，且只连该端点。
"""
from __future__ import annotations

import datetime
import json
import os
import platform
import sys
import re
import shutil
import socket
import subprocess
import threading
import time
import traceback
import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from . import paths
from .config import Config, EngineConfig, load_config
from .engine_router import (EngineRouter, NoEngineConfigured, EngineResult,
                             ToolCall, PromptUsage)
from .engine_providers import LocalGGUFEngine, ApiEngine
from .loop import AgentLoop
from .daemon import Daemon
from .sessions import SessionStore
from .skills import Skill, SkillRegistry
from .skill_library import (SkillLibrary, LibraryEntry, SkillPackageError,
                            validate_skill_package,
                            split_version as _split_version_safe)
from .tools import Tool, ToolRegistry, Mode, ScopePolicy, RiskLevel
from .sandbox import default_sandbox
from .builtin_tools import build_p0_registry
from .confirm import ConfirmBroker, IpcConfirmationGateway
from .mcp import MCPManager, load_mcp_servers
from .scheduler import Scheduler
from .rules import load_rules
from . import model_catalog
from brickery.memory import MemorySystem
from brickery.memory.db import consolidation_conn
from brickery.memory.surfacing import ShadowEngine
from brickery.memory.export_utils import to_markdown, to_json

logger = logging.getLogger("brickery.ipc")


class _DisabledMemory:
    """记忆系统关闭（memory_enabled=false）时的桩。

    所有方法返回 None，不抛异常；使 ipc 内 40+ 处 self.memory.xxx 调用点
    与 AgentLoop/Daemon 传参天然安全，无需逐点加防护。
    """

    def __getattr__(self, name: str):
        def _noop(*args, **kwargs):
            return None
        return _noop


# --------------------------------------------------------------------------
# 内置技能（随安装包分发，不走技能市场）
# 定位：打包态 runtime 在 <root>/brickery-runtime/brickery/runtime/；开发态在 <repo>/brickery/runtime/。
def _builtin_skills_dir() -> Optional[Path]:
    env = os.environ.get("BRICKERY_BUILTIN_SKILLS")
    if env:
        p = Path(env)
        if p.is_dir():
            return p
    here = Path(__file__).resolve()
    cand = here.parents[1] / "builtin_skills"    # 打包态：brickery-runtime/brickery/builtin_skills
    if cand.is_dir():
        return cand
    cand2 = here.parents[2] / "builtin_skills"    # 开发态：<repo>/builtin_skills
    if cand2.is_dir():
        return cand2
    return None


def load_builtin_skills(registry: SkillRegistry, home: Path) -> None:
    """载入随包内置技能并安装其辅助脚本到固定路径。

    内置技能 source 强制标 "builtin"，不写入用户 skills.json（由 SkillRegistry.save 过滤）。
    用户可在运行时禁用内置技能（disable 状态暂不在重启间持久化，属第一版简化）。
    """
    bdir = _builtin_skills_dir()
    if not bdir or not bdir.is_dir():
        return
    for sub in sorted(bdir.iterdir()):
        if not sub.is_dir():
            continue
        sj = sub / "skill.json"
        if sj.is_file():
            try:
                # 兼容单对象 skill.json（与市场技能包格式一致）与数组两种写法
                raw = json.loads(sj.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    raw = [raw]
                tmp = home / "cache" / "builtin_skills_tmp"
                tmp.mkdir(parents=True, exist_ok=True)
                tf = tmp / f"{sub.name}.json"
                tf.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
                registry.load(tf)   # 同名技能后续被用户 skills.json 覆盖
            except Exception:       # noqa: BLE001
                pass
    # 安装各内置技能的辅助产物（脚本/二进制）到 ~/.brickery/bin/<技能名>/
    # 约定：技能目录下除 skill.json / *.swift 源码外的文件（render_diagram.py、axctl 二进制等）
    # 装入 bin/<技能名>/，供技能 content 经 Bash 调用。产物保留可执行位。
    try:
        for sub in sorted(bdir.iterdir()):
            if not sub.is_dir():
                continue
            dest = home / "bin" / sub.name
            dest.mkdir(parents=True, exist_ok=True)
            for item in sorted(sub.iterdir()):
                if item.name == "skill.json" or item.name.endswith(".swift"):
                    continue
                if item.is_file():
                    shutil.copyfile(item, dest / item.name)
                    mode = item.stat().st_mode | 0o111
                    (dest / item.name).chmod(mode)
    except OSError:
        pass


# --------------------------------------------------------------------------
# 推理引擎封装（延迟导入重依赖，未安装/未配置时给出清晰报错，绝不静默外连）
# --------------------------------------------------------------------------



# --------------------------------------------------------------------------
# IPC 服务
# --------------------------------------------------------------------------

DEFAULT_PORT = 18765


def _now_iso_str() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class IpcServer:
    """本地 JSON IPC 服务（仅 127.0.0.1）。"""

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT, *,
                 home: Optional[Path] = None,
                 models_root: Optional[Path] = None,
                 local_engine: Optional[Any] = None,
                 api_engine: Optional[Any] = None,
                 build_real_engines: bool = True):
        self.host = host
        self.port = port
        self.config: Config = load_config(home=home, models_root=models_root)
        if self.config.memory_enabled:
            self.memory = MemorySystem(engine=self._make_smol_engine())
        else:
            self.memory = _DisabledMemory()
            logger.info("记忆系统已关闭（memory_enabled=false），记忆相关能力不可用")
        self._local_override = local_engine
        self._api_override = api_engine
        self._build_real = build_real_engines
        self._engine_lock = threading.Lock()
        # 前台/任务本地引擎单例（马维斯 P0-1）：多会话/多后台任务共享一份 GGUF，
        # 避免每会话/每任务各自加载 2.6G 权重导致内存峰值叠加。
        self._local_engine: Optional[Any] = None
        self._local_engine_key: Optional[str] = None
        # 会话落盘（与记忆库分库：会话是运行时产物，删会话不伤记忆）
        self.sessions = SessionStore(self.config.home / "sessions.db")
        # 工具 / 技能注册表：先注入 clean-room 自研的 P0 真实工具（带 handler），
        # 再叠加用户清单 tools.json 的声明性覆盖（enabled/disabled/risk 等）。
        # 红线：handler 绝不从文件恢复（load 仅保留进程内已有 handler），配置解析
        # 不得执行任意代码。两者必须传入 AgentLoop，否则筛选恒空（等于没通电）。
        self.tools = build_p0_registry(default_sandbox())
        self.tools.load(self.config.home / "tools.json")
        # 阶段 MCP：仅启动时拉起一次白名单本地 stdio 服务器，工具并入注册表。
        # 远程 HTTP/SSE 默认关（MCPManager 内部跳过）。失败安全降级（记录错误，不崩）。
        self._mcp = MCPManager(load_mcp_servers(self.config.home / "mcp_servers.json"))
        try:
            self._mcp.start()
            self.tools.register_many(self._mcp.tools())
        except Exception:  # noqa: BLE001
            pass
        # §P2 持久规则（Hooks 轻量版）：启动时加载，注入到每次主循环 prompt。
        self.rules = load_rules(self.config.home)
        # 飞书任务完成通知：轻量推送器（与连接器双向桥解耦）。未装配时静默跳过。
        # 惰性导入避免 ipc↔feishu 顶层循环依赖（feishu 导入 ipc.DEFAULT_PORT）。
        try:
            from .connectors.feishu import FeishuNotifier
            self.feishu_notifier = FeishuNotifier(
                self.config.home / "config" / "feishu.json")
        except Exception:  # noqa: BLE001
            self.feishu_notifier = None
        # §P2 调度内核：后台异步 + 多 agent 子代理。复用 _make_task_loop 造独立子 loop。
        self.scheduler = Scheduler(
            self._make_task_loop,
            home=self.config.home,
            max_workers=max(1, int(self.config.scheduler_max_workers or 2)),
            notifier=self._on_task_done)
        self._register_spawn_tools()
        self.scheduler.start()
        self.skills = SkillRegistry()
        # 内置技能（随安装包分发，先于用户清单载入；同名技能以用户 skills.json 为准）
        load_builtin_skills(self.skills, self.config.home)
        self.skills.load(self.config.home / "skills.json")
        # 技能携带工具的桥接状态（§4.4）：记录上一轮同步进工具注册表的技能工具名，
        # 供下次同步时先清掉，避免技能卸载后工具残留（§4.5.4）。
        self._skill_tool_names: set = set()
        self._sync_skill_tools()
        # §3.4 确认弹窗中枢：MEDIUM/HIGH 风险工具经它阻塞等 Swift 真弹窗裁决。
        self._confirm_broker = ConfirmBroker()
        # 确认网关（带模式 + 会话级「记住决定」），全服务共享一个实例
        # （loop 每次聊天重建，但网关随服务存活，故「记住决定」跨多轮聊天仍有效）。
        self._confirm_gateway = IpcConfirmationGateway(
            self._confirm_broker, mode=Mode.from_str(self.config.mode))
        self._local_override = local_engine
        self._api_override = api_engine
        self._build_real = build_real_engines
        self._stop_event = threading.Event()
        # 流式 IPC：每连接线程独立持有 (conn, req_id)，供 _h_chat 逐行写回 delta。
        self._conn_local = threading.local()
        self._lock = threading.Lock()
        # 空闲整理：记录最近一次前台活动时间，守护线程据此判断能否开工
        self._last_activity = time.monotonic()
        # 归纳引擎单例：绝不每轮 new——同一份 GGUF 被加载两次会吃掉双倍内存
        self._engine_lock = threading.Lock()
        self._nightly_engine: Optional[Any] = None
        self._nightly_engine_key: Optional[str] = None
        # 前台/任务本地引擎单例（马维斯 P0-1）：多会话/多后台任务共享一份 GGUF，
        # 避免每会话/每任务各自加载 2.6G 权重导致内存峰值叠加。与 _nightly_engine
        # 分开缓存（职责不同：一个是聊天主引擎，一个是归纳引擎）。
        self._local_engine: Optional[Any] = None
        self._local_engine_key: Optional[str] = None
        # 影子引擎懒加载单例（与归纳/浮现共用同一份 GGUF 权重，蓝图 A 档「一份权重两用」）
        self._shadow_engine: Optional[Any] = None
        self._daemon: Optional[Daemon] = None
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        # 积木激活：启动时扫描 home/bricks 按形态激活注册进内核（P4 动态激活层）
        self._brick_states: Dict[str, dict] = {}
        self._activate_bricks()
        # 未配置引擎提示（不阻塞启动；聊天请求会抛 NoEngineConfigured）
        eng = self.config.engine
        if not (eng.api_url and eng.api_key) and not eng.local_model:
            logger.info("引擎未配置：请打开安装引导 http://127.0.0.1:18766 完成配置")

    def _activate_bricks(self) -> None:
        """启动时扫描 home/bricks 下各积木，按形态激活注册进内核。

        积木数非 0 才激活；单个积木失败不拖死启动（故障域隔离）。
        """
        from ..brick_runtime import build_brick
        bricks_root = self.config.home / "bricks"
        if not bricks_root.is_dir():
            return
        for brick_dir in sorted(bricks_root.iterdir()):
            manifest = brick_dir / "brick.json"
            if not manifest.is_file():
                continue
            try:
                raw = json.loads(manifest.read_text(encoding="utf-8"))
                brick = build_brick(
                    raw,
                    skills_registry=self.skills,
                    tool_registry=self.tools,
                    home=self.config.home,
                )
                result = brick.activate()
                self._brick_states[brick.name] = {
                    "ok": result.ok,
                    "error": result.error,
                    "type": type(brick).__name__,
                }
            except Exception as e:  # noqa: BLE001 —— 故障域隔离，不拖死启动
                self._brick_states[brick_dir.name] = {
                    "ok": False, "error": str(e), "type": "unknown"}

    # ----- 引擎构建（延迟，遵循零外连红线）-----
    def _cached_local_engine(self, model: Optional[str]):
        """按模型路径缓存本地引擎单例（马维斯 P0-1）。

        多会话/多后台任务/多预设共用同一份 GGUF，避免每处各自加载 2.6G 权重
        导致内存峰值叠加。key 按模型路径；换模型/解析不到时自动重建或置空。
        """
        key = str(model or "")
        with self._engine_lock:
            if self._local_engine is not None and self._local_engine_key == key:
                return self._local_engine
            eng = LocalGGUFEngine(model)
            if eng._resolve_model() is None:
                self._local_engine = None
                self._local_engine_key = None
                return None
            self._local_engine = eng
            self._local_engine_key = key
            return eng

    def _make_smol_engine(self):
        """本地小模型增强引擎（memory-smol）：仅当本地 GGUF 可用时返回实例。

        供 MemorySystem 的 summarize / semantic_recall 走真实本地模型；不可用
        （无 llama_cpp / 无权重）返回 None，MemorySystem 保持 engine=None 走
        KeywordExtractor / 关键词打分降级，行为与现状完全一致。
        is_available() 只做轻量探测（可导入 + 存在 GGUF），不加载权重。
        """
        try:
            from .engine_providers import LocalGGUFEngine
            eng = LocalGGUFEngine()
            return eng if eng.is_available() else None
        except Exception:  # noqa: BLE001 —— 任何异常都安全降级
            return None

    def _make_local_engine(self):
        """前台/任务本地引擎单例（马维斯 P0-1 修复）。

        LocalGGUFEngine 持有 llama_cpp Llama 实例（约 2.6G GGUF），重复构造会
        把同一份权重反复加载进内存，多会话/多后台任务并发时内存峰值叠加，极端
        OOM 被杀进程又转回功能出错。这里按模型路径缓存单例（仿 _nightly_engine），
        配置换模型时按 key 失配自动重建，旧实例交由 GC 释放。
        注意：单例共享后，decode 并发由 LocalGGUFEngine._decode_lock 串行化保证。
        """
        if self._local_override is not None:
            return self._local_override
        if not self._build_real:
            return None
        return self._cached_local_engine(self.config.engine.local_model or None)

    def _make_api_engine(self):
        if self._api_override is not None:
            return self._api_override
        if not self._build_real:
            return None
        eng = self.config.engine
        if eng.backend == "api" and eng.api_url:
            return ApiEngine(eng.api_url, eng.api_key, eng.api_model)
        return None

    def _make_nightly_engine(self):
        """空闲记忆整理用的归纳引擎（§7，2026-08-09 改：默认走 API）。

        归纳引擎后端决策树：
        - induction_backend=\"api\"（默认）：走与主对话同 API 后端，月均 ¥0.1–1.2；
          API 不可用（未配置 / 断网 / 鉴权失败）→ 自动降级为本地 fallback。
        - induction_backend=\"local\"：强制走本地 GGUF（不出本机，不上传记忆内容）。
        - induction_backend=\"auto\"：先试 API，失败降级本地。
        - use_local_model=False（旧配置兼容）：强制退回纯骨架，不拿任何引擎。

        红线（内存）：**本地引擎按模型路径缓存单例，绝不每轮新建**。
        LocalGGUFEngine 持有 llama_cpp Llama 实例，重复构造会把同一份 GGUF 权重
        重复加载进内存（7B-Q4 每份约 4.5GB，16GB 机器上两份濒临耗尽）。
        配置换模型时按 key 失配自动重建，旧实例交由 GC 释放。
        API 引擎（ApiEngine）轻量无状态，不需缓存。
        """
        ny = self.config.nightly
        if not ny.enabled:
            return None
        # 旧配置兼容：use_local_model=False → 强制退回纯骨架
        if not getattr(ny, 'use_local_model', True):
            return None
        backend = getattr(ny, 'induction_backend', 'api') or 'api'

        # 尝试 API 归纳
        if backend in ('api', 'auto'):
            api_eng = self._make_api_engine()
            if api_eng is not None:
                return api_eng

        # API 不可用或 backend=local → 走本地 GGUF fallback
        if backend in ('api', 'auto', 'local'):
            model = ny.local_model or self.config.engine.local_model or None
            key = str(model or "")
            with self._engine_lock:
                if self._nightly_engine is not None and self._nightly_engine_key == key:
                    return self._nightly_engine
                eng = LocalGGUFEngine(model)
                if eng._resolve_model() is None:
                    self._nightly_engine = None
                    self._nightly_engine_key = None
                    return None
                self._nightly_engine = eng
                self._nightly_engine_key = key
                return eng

        return None

    # ----- 生命周期 -----
    def _free_port(self, host: str, port: int) -> None:
        """释放被占用的本地端口：杀掉占用该端口的现存监听进程。

        根因修复（0.3.24）：App 非干净退出（崩溃/强退）时后端子进程可能沦为
        孤儿继续监听 18765，且内存中是空/陈旧配置。下次启动的新后端因端口被占
        绑定失败 → 自愈也绑不上 → App 被迫连接陈旧空配置进程，表现为
        『退出重启后 API 丢失』。此处在绑定前强制清掉占用者，确保本次是从磁盘
        加载配置的全新后端（load_config 已在启动时读 ~/.brickery/config.json）。
        """
        try:
            out = subprocess.run(
                ["lsof", "-tiTCP:%d" % port, "-sTCP:LISTEN"],
                capture_output=True, text=True, timeout=5,
            )
            for tok in out.stdout.split():
                tok = tok.strip()
                if not tok:
                    continue
                try:
                    pid = int(tok)
                except ValueError:
                    continue
                # 安全收敛（马维斯 P1-1）：杀前校验进程命令行确属本服务，避免
                # 误杀恰好占用该端口的其它非 Shadeling 进程。
                is_ours = False
                try:
                    c = subprocess.run(
                        ["ps", "-p", str(pid), "-o", "command="],
                        capture_output=True, text=True, timeout=3)
                    cmd = (c.stdout or "").strip().lower()
                    is_ours = ("brickery" in cmd or "ipc.py" in cmd
                               or "runtime" in cmd)
                except Exception:  # noqa: BLE001
                    pass
                if is_ours:
                    try:
                        os.kill(pid, 9)  # SIGKILL，强制释放端口
                    except (OSError, ValueError):
                        pass
        except Exception:  # noqa: BLE001
            pass

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # 先清掉端口占用者（陈旧孤儿后端），再绑定（带重试，等端口释放）。
        self._free_port(self.host, self.port)
        bound = False
        for attempt in range(6):
            try:
                self._sock.bind((self.host, self.port))
                bound = True
                break
            except OSError:
                if attempt == 5:
                    break
                time.sleep(0.3)
        if not bound:
            sys.stderr.write(
                f"[Brickery IPC] 无法绑定 {self.host}:{self.port}：端口释放失败，"
                f"端口被其它程序占用。请释放后重试。\n")
            sys.stderr.flush()
            raise OSError(f"bind {self.host}:{self.port} failed")
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        # 停调度内核（触发在跑任务收尾，join worker，避免孤儿）
        try:
            self.scheduler.stop()
        except Exception:  # noqa: BLE001
            pass
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._daemon is not None:
            try:
                self._daemon.stop()
            except Exception:
                pass
        # 关闭已接入的 MCP 服务器子进程（避免孤儿进程）
        try:
            self._mcp.stop()
        except Exception:
            pass

    def _accept_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                break
            t = threading.Thread(target=self._handle_conn, args=(conn,),
                                daemon=True)
            t.start()

    def _handle_conn(self, conn: socket.socket) -> None:
        buf = b""
        self._conn_local.conn = conn
        with conn:
            while not self._stop_event.is_set():
                try:
                    data = conn.recv(65536)
                except OSError:
                    break
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        req = json.loads(line.decode("utf-8"))
                    except json.JSONDecodeError:
                        self._send(conn, {"req_id": None, "ok": False,
                                          "error": "非法 JSON"})
                        continue
                    resp = self._dispatch(req)
                    # 流式 handler 已自行逐行写回 delta/done，此处不再重复发送
                    if resp and isinstance(resp.get("data"), dict) \
                            and resp["data"].get("__streamed__"):
                        continue
                    self._send(conn, resp)

    @staticmethod
    def _send(conn: socket.socket, obj: dict) -> None:
        try:
            conn.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            pass

    # ----- 分发 -----
    def _dispatch(self, req: dict) -> dict:
        req_id = req.get("req_id")
        method = req.get("method", "")
        params = req.get("params", {}) or {}
        # 流式 handler 需要 req_id 组装 delta/done 帧
        self._conn_local.req_id = req_id
        try:
            handler = getattr(self, f"_h_{method}", None)
            if handler is None:
                return {"req_id": req_id, "ok": False,
                        "error": f"未知方法：{method}"}
            data = handler(params)
            return {"req_id": req_id, "ok": True, "data": data}
        except Exception as e:  # noqa: BLE001
            return {"req_id": req_id, "ok": False,
                    "error": f"{type(e).__name__}: {e}"}

    # ----- 各方法实现 -----
    def _h_health(self, params):
        return {"status": "ok", "home": str(self.config.home)}

    def _context_window(self) -> int:
        """内感受「上下文利用率」分母用的**真实窗口**。

        本地 GGUF 受 n_ctx 限制（默认 4096）；API（DeepSeek / OpenAI 兼容）典型 128K。
        不得用 config.max_context_tokens（8192 的 n_ctx 治理预算）当分母，否则虚高（坑⑦）。
        """
        if self.config.engine.backend == "local":
            return 4096
        return 128_000

    def _engines_for_profile(self, profile_id: str):
        """按预设 id 构造一组 (cfg, local_engine, api_engine)。

        返回 None 表示找不到该预设（调用方应回退全局引擎）。
        预设为 api 后端 → 用其 api_url/api_key/api_model 建 ApiEngine；
        预设为 local 后端 → 用其 local_model 建 LocalGGUFEngine。
        2026-08-25 拍板：EngineRouter 只走预设显式选择的后端，**无自动降级**；
        传入的另一个引擎仅当用户显式切换 backend 时才被使用。
        """
        profile = None
        for p in (self.config.profiles or []):
            if isinstance(p, dict) and p.get("id") == profile_id:
                profile = p
                break
        if profile is None:
            return None
        cfg = EngineConfig(
            backend=profile.get("backend", "api"),
            local_model=profile.get("local_model", ""),
            api_url=profile.get("api_url", ""),
            api_key=profile.get("api_key", ""),
            api_model=profile.get("api_model", ""),
            api_name=profile.get("api_name", ""),
        )
        api_engine = None
        if cfg.backend == "api" and cfg.api_url:
            api_engine = ApiEngine(cfg.api_url, cfg.api_key, cfg.api_model)
        # 本地引擎：优先用预设指定的本地模型（走缓存单例，避免多预设重复加载 2.6G）；
        # 解析不到则回退全局本地引擎
        local_engine = self._cached_local_engine(cfg.local_model or None)
        if local_engine is None:
            local_engine = self._make_local_engine()
        return (cfg, local_engine, api_engine)

    def _new_loop(self, session_id: Optional[str] = None,
                  profile_id: str = "") -> AgentLoop:
        """构造主循环。**必须**带上注册表，否则技能注入与工具筛选恒空。

        profile_id 非空且能在 config.profiles 中找到时，本会话使用**该预设专属**
        的引擎配置（每会话独立绑定模型，互不干扰）；否则回退全局 active 引擎。
        """
        built = self._engines_for_profile(profile_id) if profile_id else None
        if built is None:
            cfg = self.config.engine
            local_engine = self._make_local_engine()
            api_engine = self._make_api_engine()
        else:
            cfg, local_engine, api_engine = built
        router = EngineRouter(cfg,
                              local_engine=local_engine,
                              api_engine=api_engine)
        return AgentLoop(
            self.memory, router,
            tools=self.tools if self.config.tools_enabled else ToolRegistry(),
            skills=self.skills if self.config.skills_enabled else SkillRegistry(),
            shadow_engine=self._make_shadow_engine(),
            session_id=session_id,
            should_stop=lambda: self._stop_event.is_set(),
            mode=Mode.from_str(self.config.mode),
            confirmation=self._confirm_gateway,
            rules=self.rules,
            context_window=self._context_window())

    def _make_task_loop(self, project: str = "", session: Optional[str] = None,
                        should_stop=None) -> AgentLoop:
        """调度内核用的 loop 工厂：每个后台任务一个独立 loop 实例（彼此隔离）。

        与 _new_loop 同构，但 should_stop 用 per-task 停止事件（而非全局停止），
        使任务可被单独取消而不影响前台。
        """
        router = EngineRouter(self.config.engine,
                              local_engine=self._make_local_engine(),
                              api_engine=self._make_api_engine())
        return AgentLoop(
            self.memory, router,
            tools=self.tools if self.config.tools_enabled else ToolRegistry(),
            skills=self.skills if self.config.skills_enabled else SkillRegistry(),
            shadow_engine=self._make_shadow_engine(),
            session_id=session,
            should_stop=should_stop or (lambda: self._stop_event.is_set()),
            mode=Mode.from_str(self.config.mode),
            confirmation=self._confirm_gateway,
            rules=self.rules,
            context_window=self._context_window())

    def _register_spawn_tools(self) -> None:
        """注册多 agent 编排原语（绑定到本服务实例的 scheduler）。

        - SpawnAgent：派发一个后台子任务，立即返回 task_id（不阻塞前台）。
        - WaitTask：阻塞等待某后台任务完成并取回结果（超时默认 120s）。
        二者构成「fire-and-forget + gather」的多 agent 骨架，复用同一调度内核。
        """
        def _spawn(prompt, **kw):
            t = self.scheduler.submit(
                prompt, project=kw.get("project", ""),
                parent_id=kw.get("parent_id"))
            return (f"已派发子任务（task_id={t.id}，状态={t.status.value}）。"
                    f"用 WaitTask 取回结果。")

        def _wait(task_id, **kw):
            try:
                timeout = float(kw.get("timeout", 120))
            except (TypeError, ValueError):
                timeout = 120.0
            t = self.scheduler.wait(task_id, timeout=timeout)
            if t is None:
                return f"等待任务 {task_id} 超时（{timeout}s）。"
            if t.status.value == "done":
                return t.result or ""
            if t.status.value == "cancelled":
                return f"任务 {task_id} 已被取消，无结果。"
            return f"任务 {task_id} 失败：{t.error or '未知错误'}"

        spawn = Tool(
            name="SpawnAgent", risk=RiskLevel.MEDIUM,
            description="派发后台子任务异步执行，返回 task_id",
            keywords=["子代理", "多agent", "并行", "委派", "spawn"],
            handler=_spawn,
            parameters={"type": "object", "properties": {
                "prompt": {"type": "string", "description": "子任务指令"},
                "project": {"type": "string", "description": "项目命名空间（可选）"},
            }, "required": ["prompt"]})
        wait = Tool(
            name="WaitTask", risk=RiskLevel.LOW,
            description="等待后台任务完成并取回结果",
            keywords=["取结果", "汇聚", "等待任务", "wait"],
            handler=_wait,
            parameters={"type": "object", "properties": {
                "task_id": {"type": "string", "description": "要等待的任务 id"},
                "timeout": {"type": "number", "description": "超时秒数，默认 120"},
            }, "required": ["task_id"]})
        self.tools.register(spawn)
        self.tools.register(wait)

    def _on_task_done(self, task) -> None:
        """任务完成通知钩子（§P2 通知）。后台任务跑完推飞书。

        解耦设计：Scheduler 仅回调，本方法把通知交给 FeishuNotifier。
        飞书未装配（enabled=false / 缺凭证 / 未绑定接收者）时静默跳过。
        在独立守护线程里发，避免网络延迟阻塞调度 worker。
        """
        notifier = getattr(self, "feishu_notifier", None)
        if notifier is None:
            return
        threading.Thread(target=self._notify_feishu, args=(task,),
                         daemon=True).start()

    def _notify_feishu(self, task) -> None:
        notifier = getattr(self, "feishu_notifier", None)
        if notifier is None:
            return
        try:
            status_value = getattr(task.status, "value", str(task.status))
            label_map = {"done": "完成", "failed": "失败",
                         "cancelled": "⏹ 已取消"}
            status_label = label_map.get(status_value, str(status_value))
            title = f"后台任务{status_label}"
            prompt = (getattr(task, "prompt", "") or "").strip().replace("\n", " ")
            if len(prompt) > 80:
                prompt = prompt[:80] + "…"
            lines = [f"指令：{prompt}", f"状态：{status_label}"]
            if status_value == "done":
                result = (getattr(task, "result", "") or "").strip().replace("\n", " ")
                if len(result) > 200:
                    result = result[:200] + "…"
                lines.append(f"结果：{result}")
            elif status_value in ("failed", "cancelled"):
                err = (getattr(task, "error", "") or "无详情").strip().replace("\n", " ")
                if len(err) > 200:
                    err = err[:200] + "…"
                lines.append(f"原因：{err}")
            notifier.notify(title, "\n".join(lines))
        except Exception:  # noqa: BLE001
            # 通知失败绝不影响任务本身的状态/存储；静默吞掉。
            pass

    def _make_shadow_engine(self):
        """构造影子引擎（O5/O6 异步归纳用）。

        - 独立 Llama 实例，避免与主聊天引擎并发 decode 撞车（llama_cpp 单实例非线程安全）。
        - 用 chat 模板 + 关思考，确保 Qwen3 稳定吐出结构化 JSON。
        - 任何失败（缺权重 / 缺依赖）均安全降级为 None，不阻断主流程。
        """
        try:
            from . import paths
            root = paths.resolve_models_root()
            hits = sorted((root / "gguf").glob("*.gguf")) if (root / "gguf").exists() else []
            if not hits:
                hits = sorted(root.glob("*.gguf"))
            model_path = str(hits[0]) if hits else None
            if not model_path:
                return None

            def loader(mp: str):
                from llama_cpp import Llama
                llm = Llama(model_path=mp, n_ctx=2048, n_gpu_layers=-1, verbose=False)

                def complete(prompt: str, max_tokens: int = 700, **kw):
                    out = llm.create_chat_completion(
                        messages=[
                            {"role": "system", "content":
                             "nothink\n你负责把对话压缩成结构化记忆。只输出一个 JSON 对象，"
                             "不要任何解释，不要输出思考过程。"},
                            {"role": "user", "content": prompt},
                        ],
                        max_tokens=max_tokens, temperature=0.3)
                    return out["choices"][0]["message"]["content"]

                return complete

            return ShadowEngine.get(model_path, loader)
        except Exception:
            return None

    def _get_shadow(self):
        """返回影子引擎单例（缓存），无 GGUF 时为 None → 浮现/推送回落规则。"""
        if self._shadow_engine is None:
            self._shadow_engine = self._make_shadow_engine()
        return self._shadow_engine

    def _h_chat(self, params):
        message = params.get("message", "")
        project = params.get("project", "")
        self._stop_event.clear()
        self.mark_activity()  # 前台开工：空闲整理让路

        # 会话落盘：无 session_id 则新建，用户消息先入库（推理失败也不丢）
        raw_profile_id = params.get("profile_id", "") or ""
        sid = self.sessions.ensure(params.get("session_id"), project=project,
                                   profile_id=raw_profile_id)
        self.sessions.append(sid, "user", message)
        # 取最近 21 条后去掉末尾那条（即刚写入的本轮输入），避免在 prompt 里重复出现
        history = self.sessions.history(sid, limit=21)[:-1]
        # 每会话独立绑定：前端显式指定 > 会话已存储值；空则回退全局 active
        effective_profile_id = raw_profile_id or self.sessions.profile_id_of(sid)

        # 新会话开场上下文（消灭「失忆感」；config 可关，默认开）
        # 判定：未带 session_id = 用户开新对话。仅在 config 开启时主动浮现近期上下文。
        open_ctx = ""
        if not params.get("session_id") and getattr(self.config, "open_session_context", True):
            try:
                open_ctx = self.memory.open_session_context()
            except Exception:
                open_ctx = ""

        loop = self._new_loop(sid, profile_id=effective_profile_id)
        stream = bool(params.get("stream"))
        if stream:
            return self._h_chat_stream(loop, message, project, history,
                                       open_ctx, sid)
        try:
            reply = loop.run(message, project=project, history=history,
                             open_context_text=open_ctx or None)
        except InterruptedError:
            return {"reply": "（已取消）", "session_id": sid,
                    "interrupted": True, "used_tools": [], "used_skills": []}

        used_tools, used_skills = loop.last_tools, loop.last_skills
        self.sessions.append(sid, "assistant", reply,
                             used_tools=used_tools, used_skills=used_skills)
        self.mark_activity()  # 本轮结束才开始计空闲

        # 立项意图检测：仅提示，绝不擅自建抽屉（由用户点击卡片才创建）
        recommendation = None
        try:
            rec = self.memory.detect_recommendation(message)
            if isinstance(rec, dict) and rec.get("recommended"):
                recommendation = rec
        except Exception:  # noqa: BLE001 - 检测失败不影响主流程
            recommendation = None

        return {"reply": reply, "session_id": sid,
                "used_tools": used_tools, "used_skills": used_skills,
                "recommendation": recommendation}

    def _h_chat_stream(self, loop, message, project, history, open_ctx, sid):
        """流式对话：on_token 逐行写回 {"type":"delta"}，结束写 {"type":"done"}。

        返回 {"__streamed__": True} 标记，_handle_conn 据此跳过重复发送。
        """
        conn = getattr(self._conn_local, "conn", None)
        req_id = getattr(self._conn_local, "req_id", None)

        def on_token(delta: str) -> None:
            if conn is not None:
                self._send(conn, {"type": "delta", "req_id": req_id,
                                  "delta": delta})

        def on_event(text: str) -> None:
            if conn is not None:
                self._send(conn, {"type": "event", "req_id": req_id,
                                  "text": text})

        try:
            logger.info("CHAT-DIAG stream pre loop.run")
            reply = loop.run(message, project=project, history=history,
                             open_context_text=open_ctx or None,
                             on_token=on_token, on_event=on_event)
            logger.info("CHAT-DIAG stream post loop.run len=%d", len(reply or ""))
        except InterruptedError:
            reply = "（已取消）"
            interrupted = True
        else:
            interrupted = False

        used_tools, used_skills = loop.last_tools, loop.last_skills
        self.sessions.append(sid, "assistant", reply,
                             used_tools=used_tools, used_skills=used_skills)
        self.mark_activity()  # 本轮结束才开始计空闲

        recommendation = None
        try:
            rec = self.memory.detect_recommendation(message)
            if isinstance(rec, dict) and rec.get("recommended"):
                recommendation = rec
        except Exception:  # noqa: BLE001 - 检测失败不影响主流程
            recommendation = None

        data = {"reply": reply, "session_id": sid,
                "used_tools": used_tools, "used_skills": used_skills,
                "recommendation": recommendation}
        if interrupted:
            data["interrupted"] = True
        if conn is not None:
            self._send(conn, {"type": "done", "req_id": req_id, "data": data})
        return {"__streamed__": True}

    # ----- 会话管理（§3.1，落盘；不复刻前代「重启即丢」的已知缺陷）-----

    def _h_session_list(self, params):
        return {"items": self.sessions.list()}

    def _h_session_new(self, params):
        # 返回**完整形状**（含 messages），不返回半截对象
        return {"session": self.sessions.create(
            title=params.get("title", ""),
            project=params.get("project", ""),
            profile_id=params.get("profile_id", "") or "")}

    def _h_session_set_profile(self, params):
        """绑定/解绑某会话的模型预设（每会话独立，不影响全局 active）。"""
        sid = params.get("session_id", "")
        pid = params.get("profile_id", "") or ""
        if not sid:
            return {"ok": False, "error": "缺少 session_id"}
        self.sessions.set_profile_id(sid, pid)
        return {"ok": True, "session_id": sid, "profile_id": pid}

    def _h_session_get(self, params):
        return {"session": self.sessions.get(params.get("session_id", ""))}

    def _h_session_rename(self, params):
        return {"session": self.sessions.rename(
            params["session_id"], params.get("title", ""))}

    def _h_session_delete(self, params):
        return {"deleted": self.sessions.delete(params.get("session_id", ""))}

    def _h_chat_cancel(self, params):
        self._stop_event.set()
        return {"cancelled": True}

    def _h_recall(self, params):
        return {"items": self.memory.recall(
            params.get("query", ""),
            project=params.get("project"),
            limit=params.get("limit", 10))}

    def _h_portrait(self, params):
        return {"items": self.memory.get_portrait(params.get("attribute"))}

    def _h_portrait_update(self, params):
        return {"item": self.memory.update_portrait(
            params["attribute"], params["value"],
            evidence=params.get("evidence"),
            confidence=params.get("confidence", 0.5))}

    def _h_core_set(self, params):
        """写入固定核手动槽（首次引导的「认识我们」步骤用）。

        params: {"items": [{"attribute": "...", "value": "..."}, ...]}
        空 value 视为删除该条。写入 source='user' 手动槽，每轮注入 prompt。
        """
        from brickery.memory.fixed_core import set_core
        items = params.get("items") or []
        count = 0
        for it in items:
            attr = str(it.get("attribute", "")).strip()
            if not attr:
                continue
            val = it.get("value")
            set_core(attr, "" if val is None else str(val).strip())
            count += 1
        return {"ok": True, "written": count}

    def _h_core_get(self, params):
        """读固定核手动槽（可指定 attribute，或全量）。"""
        from brickery.memory.fixed_core import get_core
        attr = params.get("attribute")
        if attr:
            return {"core": {attr: get_core(attr)}}
        return {"core": get_core() or {}}

    def _h_agent_get(self, params):
        """只读返回 agent 定义（home/agent.json）。

        安全：agent.json 仅含 agent 身份与积木装配清单，无密钥
        （api_key 在 config.json，config_get 已掩码）。不开放写：
        agent.json 兼任"已初始化"标记，写入会破坏初始化语义。
        """
        home = self.config.home
        p = home / "agent.json"
        if not p.exists():
            return {"agent": None, "error": "agent.json 不存在（未初始化）"}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            return {"agent": None, "error": f"agent.json 解析失败: {e}"}
        return {"agent": data}

    def _h_core_smart_get(self, params):
        """读固定核智能槽全量（含置信度/命中数，读取时实时衰减）。"""
        from brickery.memory.fixed_core import get_smart_slots
        return {"items": get_smart_slots()}

    def _h_core_smart_delete(self, params):
        """删除单条固定核智能槽（用户纠错入口）。"""
        from brickery.memory.fixed_core import delete_smart_slot
        label = params.get("label", "").strip()
        if not label:
            raise ValueError("label 必填")
        return {"ok": bool(delete_smart_slot(label)), "label": label}

    def _h_core_candidates(self, params):
        """列出固定核候选（pending_candidates 中 status='pending'）。"""
        from brickery.memory.db import memory_conn
        with memory_conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS pending_candidates("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "label TEXT NOT NULL, value TEXT NOT NULL,"
                "confidence REAL DEFAULT 0.5,"
                "created_at TEXT NOT NULL,"
                "status TEXT DEFAULT 'pending')"
            )
            rows = c.execute(
                "SELECT id, label, value, confidence, created_at FROM pending_candidates "
                "WHERE status='pending' ORDER BY id ASC"
            ).fetchall()
        return {"items": [
            {"id": r["id"], "label": r["label"], "value": r["value"],
             "confidence": r["confidence"], "created_at": r["created_at"]}
            for r in rows]}

    def _h_core_candidate_resolve(self, params):
        """确认候选 → 写入固定核智能槽，候选标记 resolved。"""
        from brickery.memory.db import memory_conn
        from brickery.memory.fixed_core import set_smart_slot
        cid = int(params.get("id", 0))
        with memory_conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS pending_candidates("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "label TEXT NOT NULL, value TEXT NOT NULL,"
                "confidence REAL DEFAULT 0.5,"
                "created_at TEXT NOT NULL,"
                "status TEXT DEFAULT 'pending')"
            )
            row = c.execute(
                "SELECT label, value, confidence FROM pending_candidates WHERE id=? AND status='pending'",
                (cid,),
            ).fetchone()
            if not row:
                return {"ok": False, "error": "候选不存在或已处理"}
            ok = set_smart_slot(row["label"], row["value"],
                                confidence=row["confidence"] or 0.9)
            if ok:
                c.execute("UPDATE pending_candidates SET status='resolved' WHERE id=?", (cid,))
        return {"ok": ok, "id": cid}

    def _h_core_candidate_dismiss(self, params):
        """否决候选 → 标记 dismissed，不入智能槽。"""
        from brickery.memory.db import memory_conn
        cid = int(params.get("id", 0))
        with memory_conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS pending_candidates("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "label TEXT NOT NULL, value TEXT NOT NULL,"
                "confidence REAL DEFAULT 0.5,"
                "created_at TEXT NOT NULL,"
                "status TEXT DEFAULT 'pending')"
            )
            c.execute("UPDATE pending_candidates SET status='dismissed' WHERE id=?", (cid,))
        return {"ok": True, "id": cid}

    # ---- 自进化（evolve）候选确认 ----
    def _h_evolve_candidates(self, params):
        """列出待确认的自进化候选（label 前缀 evolve:）。"""
        from .evolve import list_candidates
        return {"items": list_candidates(self.config.home)}

    def _h_evolve_confirm(self, params):
        """确认候选 → 写入 home/bricks 激活，pending 标记 resolved。"""
        from .evolve import confirm_candidate
        ok, msg = confirm_candidate(self.config.home, int(params.get("id", 0)))
        return {"ok": ok, "error": None if ok else msg, "id": params.get("id", 0)}

    def _h_evolve_reject(self, params):
        """拒绝候选 → 标记 rejected，不激活。"""
        from .evolve import reject_candidate
        ok, msg = reject_candidate(self.config.home, int(params.get("id", 0)))
        return {"ok": ok, "error": None if ok else msg, "id": params.get("id", 0)}

    def _h_evolve_refine_stats(self, params):
        """批次 2：只读返回已激活 evolve 积木的使用 / 置信度 / 状态统计。"""
        from .evolve import refine_stats
        return {"items": refine_stats(self.config.home)}

    def _h_suggestions(self, params):
        return {"items": self.memory.suggest(
            params.get("context", ""),
            project=params.get("project"),
            limit=params.get("limit", 5),
            shadow=self._get_shadow())}

    def _h_memory_search(self, params):
        return {"items": self.memory.search_files(
            params.get("query", ""), limit=params.get("limit", 10))}

    def _h_memory_export(self, params):
        """O9 记忆导出：返回可落盘的文本内容，UI 经系统保存面板写文件。"""
        fmt = (params.get("format") or "markdown").lower()
        include_core = bool(params.get("include_core", False))
        bundle = self.memory.export_all(include_core=include_core)
        if fmt == "json":
            content = to_json(bundle)
            ext = "json"
        else:
            content = to_markdown(bundle)
            ext = "md"
        from datetime import datetime
        stamp = datetime.now().strftime("%Y%m%d")
        return {"content": content,
                "filename": f"brickery-memory-{stamp}.{ext}",
                "format": fmt}

    # 建议反馈的合法取值：与记忆层 record_feedback 的契约保持一致。
    # UI 只提供「采纳 / 忽略」两个按钮，不额外发明第三态。
    FEEDBACK_VALUES = ("accept", "ignore")

    def _h_suggestion_feedback(self, params):
        """记录建议反馈（采纳 / 忽略），影响后续分级权重。"""
        feedback = params.get("feedback", "")
        if feedback not in self.FEEDBACK_VALUES:
            raise ValueError(
                "feedback 必须为 " + " 或 ".join(repr(v) for v in self.FEEDBACK_VALUES))
        self.memory.record_feedback(params["item_ref"], feedback)
        return {"ok": True}

    # ----- 技能 / 工具面板（§3.3 注册表通电）-----

    @staticmethod
    def _skill_dict(s: Skill) -> dict:
        return {"name": s.name, "trigger": list(s.trigger),
                "disabled": s.disabled, "has_content": bool(s.content),
                # marketplace / 分级注入扩展字段（UI 展示用）
                "summary": s.summary, "version": s.version,
                "author": s.author, "category": s.category,
                "source": s.source, "installed_at": s.installed_at}

    @staticmethod
    def _tool_dict(t: Tool) -> dict:
        return {"name": t.name, "description": t.description,
                "keywords": list(t.keywords), "disabled": t.disabled,
                "always_available": t.always_available,
                "risk": t.risk.value if hasattr(t.risk, "value") else str(t.risk),
                # 诚实标注：无 handler 即无可执行实现，UI 据此禁用触发按钮
                "executable": t.handler is not None}

    def _h_skill_list(self, params):
        return {"items": [self._skill_dict(s) for s in self.skills.all()]}

    def _h_tool_list(self, params):
        return {"items": [self._tool_dict(t) for t in self.tools.all()]}

    def _h_skill_toggle(self, params):
        s = self.skills.set_disabled(params["name"], params.get("disabled", False))
        if s is None:
            raise KeyError(f"技能不存在：{params['name']}")
        self.skills.save(self.config.home / "skills.json")   # 状态持久化
        return {"item": self._skill_dict(s)}

    def _h_tool_toggle(self, params):
        t = self.tools.set_disabled(params["name"], params.get("disabled", False))
        if t is None:
            raise KeyError(f"工具不存在：{params['name']}")
        self.tools.save(self.config.home / "tools.json")
        return {"item": self._tool_dict(t)}

    def _h_skill_trigger(self, params):
        """手动触发技能：把技能内容作为一轮输入跑主循环，结果回写会话。"""
        name = params["name"]
        sk = self.skills.get(name)
        if sk is None:
            raise KeyError(f"技能不存在：{name}")
        prompt = sk.content or f"请执行技能：{name}"
        sid = self.sessions.ensure(params.get("session_id"))
        self._stop_event.clear()
        self.sessions.append(sid, "user", f"（手动触发技能：{name}）")
        loop = self._new_loop(sid)
        try:
            reply = loop.run(prompt, project=params.get("project", ""))
        except InterruptedError:
            return {"reply": "（已取消）", "session_id": sid, "interrupted": True}
        self.sessions.append(sid, "assistant", reply,
                             used_skills=[name], used_tools=loop.last_tools)
        return {"reply": reply, "session_id": sid, "used_skills": [name],
                "used_tools": loop.last_tools}

    # ----- 在线技能市场（§3.x：浏览/安装/卸载/升级）-----
    # 网络全在 Python 侧（与 WebFetch/API 一致），优雅失败不崩溃。

    # 公网技能市场默认源（仅 index.json + Release 二进制，不含任何技能文件，
    # 体积为零，符合「空白安装包」原则）。市场组件固定从 GitHub 拉取，
    # 无自定义源、无本地离线源兜底；离线安装走「积木包导入」通道。
    DEFAULT_PUBLIC_SKILL_REPO_URL = (
        "https://gh-proxy.com/https://raw.githubusercontent.com/suipu-boop/shadeling-bricks/main/skills/index.json"
    )

    def _resolve_skill_repo_url(self) -> str:
        """返回积木市场源地址：纯 GitHub 公网镜像源（spec: skill-repo-github-only）。

        已拍板 GitHub Only：无自定义源、无本地离线源兜底；离线安装走「积木包导入」通道。
        """
        return self.DEFAULT_PUBLIC_SKILL_REPO_URL

    def _skill_library(self) -> SkillLibrary:
        return SkillLibrary(self._resolve_skill_repo_url(), self.config.home)

    # ------------------------------------------------------------------
    # Vault 模块（本地资产中枢）：UI 命令栏 + AI 检索工具共用存储
    # ------------------------------------------------------------------
    def _vault(self) -> "VaultStore":
        if not hasattr(self, "_vault_store"):
            from .vault_store import VaultStore
            self._vault_store = VaultStore(str(self.config.home / "vault")
                                          if getattr(self.config, "home", None) else None)
        return self._vault_store

    def _h_vault_list(self, params):
        try:
            items = self._vault().list(type=params.get("type"), q=params.get("q"),
                                       top_k=int(params.get("top_k", 200)))
            return {"ok": True, "items": items}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}", "items": []}

    def _h_vault_add(self, params):
        try:
            item = self._vault().add(params)
            return {"ok": True, "item": item}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def _h_vault_delete(self, params):
        aid = params.get("id")
        if not aid:
            return {"ok": False, "error": "缺少 id"}
        try:
            ok = self._vault().delete(aid)
            return {"ok": ok, "error": "" if ok else "未找到该资产"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def _h_vault_detail(self, params):
        aid = params.get("id")
        if not aid:
            return {"ok": False, "error": "缺少 id"}
        try:
            item = self._vault().get(aid, include_sensitive=bool(params.get("unlock", False)))
            if not item:
                return {"ok": False, "error": "未找到该资产"}
            return {"ok": True, "item": item}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def _h_vault_sync_skills(self, params):
        try:
            skills = [self._skill_dict(s) for s in self.skills.all()]
            n = self._vault().sync_skills(skills)
            return {"ok": True, "synced": n}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def _h_vault_scan(self, params):
        """B 层受控扫描：列出指定目录下疑似数字资产的候选文件（不收纳）。"""
        d = params.get("dir") or ""
        if not d:
            return {"ok": False, "error": "缺少目录路径"}
        try:
            items = self._vault().scan_dir(
                d, recursive=bool(params.get("recursive", True)),
                max_files=int(params.get("max_files", 800)))
            return {"ok": True, "items": items}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def _h_vault_enhance(self, params):
        """§14 B 路：对单个资产生成本地模型增强（总结/标签/要点/风险），写回附加字段。"""
        aid = params.get("id")
        if not aid:
            return {"ok": False, "error": "缺少 id"}
        mode = str(params.get("mode", "summary"))
        text = (params.get("source_text") or "").strip()
        if not text:
            return {"ok": False, "error": "该资产没有可用于分析的内容"}
        eng, kind = self._vault_engine()
        if eng is None:
            return {"ok": False,
                    "error": "当前无可用模型（离线或未配置本地/API 引擎）"}
        prompt = self._enhance_prompt(mode, text)
        try:
            out = eng.complete(prompt, max_tokens=700, temperature=0.3)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"模型调用失败：{type(e).__name__}: {e}"}
        parsed = self._parse_enhance(out)
        ok = self._vault().update_enhancement(
            aid, summary=parsed.get("summary", ""),
            ai_tags=parsed.get("ai_tags", []),
            key_points=parsed.get("key_points", []))
        return {"ok": ok, "result": parsed, "engine": kind}

    def _h_vault_ocr(self, params):
        """OCR 文本入库：把图片识别出的文本写入 vault。

        两种用法：
        - 已有资产：传 id + text，写回该资产 fields.excerpt（不覆盖原文）。
        - 新图片：传 file_path + text，先 add 一个 image 资产再写 excerpt。
        OCR 识别本身由前端（macOS Vision）或外部工具完成，本 handler 只做入库。
        """
        text = (params.get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "缺少 OCR 文本（text）"}
        aid = params.get("id")
        try:
            if aid:
                ok = self._vault().update_fields(aid, {"excerpt": text, "ocr": True})
                if not ok:
                    return {"ok": False, "error": "未找到该资产"}
                return {"ok": True, "id": aid, "updated": True}
            fp = params.get("file_path")
            if not fp:
                return {"ok": False, "error": "缺少 id 或 file_path"}
            item = self._vault().add({
                "type": "image",
                "title": params.get("title") or "",
                "file_path": fp,
                "fields": {"excerpt": text, "ocr": True},
            })
            return {"ok": True, "item": item}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def _h_vault_snapshot(self, params):
        """网页快照入库：抓取 url 摘要存为 webpage 资产（add 内部自动 fetch_webpage）。"""
        url = (params.get("url") or "").strip()
        if not url:
            return {"ok": False, "error": "缺少 url"}
        try:
            item = self._vault().add({
                "type": "webpage",
                "title": params.get("title") or "",
                "fields": {"url": url},
            })
            return {"ok": True, "item": item}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def _vault_engine(self):
        """Vault 隐私分档：文本增强默认本地优先（不出网）；无本地模型才走 API。"""
        if not hasattr(self, "_vault_eng"):
            self._vault_eng = self._make_local_engine()
        if getattr(self, "_vault_eng", None) is not None:
            return self._vault_eng, "local"
        api = self._make_api_engine()
        if api is not None:
            return api, "api"
        return None, None

    @staticmethod
    def _enhance_prompt(mode: str, text: str) -> str:
        head = ("你是 Shadeling 本地 Vault 的整理助手。下面是一段用户资产内容。"
                "请只输出一个 JSON 对象，不要任何额外文字或 Markdown 代码块。"
                "字段：summary(字符串摘要)、ai_tags(字符串数组标签)、"
                "key_points(字符串数组要点)。\n")
        if mode == "tags":
            instr = "请生成 3-6 个中文分类标签，写入 ai_tags。"
        elif mode == "keypoints":
            instr = "请提取 3-8 个关键要点，写入 key_points。"
        elif mode == "risk":
            instr = ("请识别内容中的关键日期、到期/续期提醒与需要注意的风险点，"
                     "写入 key_points；summary 给一句风险提示。")
        else:  # summary
            instr = "请生成一段不超过 120 字的中文摘要，写入 summary。"
        return head + "要求：" + instr + "\n内容：\n" + text[:4000]

    @staticmethod
    def _parse_enhance(out: str) -> Dict[str, Any]:
        import re as _re
        s = (out or "").strip()
        m = _re.search(r"\{.*\}", s, _re.S)
        if not m:
            return {"summary": s[:200]}
        try:
            d = json.loads(m.group(0))
        except Exception:
            return {"summary": s[:200]}
        return {
            "summary": str(d.get("summary", "")),
            "ai_tags": [str(x) for x in (d.get("ai_tags") or [])],
            "key_points": [str(x) for x in (d.get("key_points") or [])],
        }

    def _h_skill_library_list(self, params):
        entries, err = self._skill_library().list_entries(
            self.skills, force=bool(params.get("force", False)))
        if err:
            return {"ok": False, "error": err, "items": []}
        items = [{
            "id": e.id, "name": e.name, "version": e.version,
            "author": e.author, "summary": e.summary, "category": e.category,
            "description": e.description, "tags": e.tags, "installed_version": e.installed_version,
            "installed": e.installed_version is not None,
            "installed_via": e.installed_via or "",
        } for e in entries]
        return {"ok": True, "items": items}

    def _h_skill_library_install(self, params):
        sid = params.get("id") or params.get("name")
        if not sid:
            return {"ok": False, "error": "缺少技能 id"}
        skill, err = self._skill_library().install(
            sid, self.skills, force=bool(params.get("force", False)))
        if err:
            return {"ok": False, "error": err}
        self._sync_skill_tools()
        return {"ok": True, "item": self._skill_dict(skill)}

    def _h_skill_library_uninstall(self, params):
        sid = params.get("id") or params.get("name")
        if not sid:
            return {"ok": False, "error": "缺少技能 id"}
        ok, err = self._skill_library().uninstall(sid, self.skills)
        if not ok:
            return {"ok": False, "error": err}
        self._sync_skill_tools()
        return {"ok": True}

    def _h_skill_library_upgrade(self, params):
        sid = params.get("id") or params.get("name")
        if not sid:
            return {"ok": False, "error": "缺少技能 id"}
        before = next((s.version for s in self.skills.all() if s.source == sid), None)
        skill, err = self._skill_library().upgrade(sid, self.skills)
        if err:
            return {"ok": False, "error": err}
        self._sync_skill_tools()
        after = skill.version
        return {"ok": True, "item": self._skill_dict(skill),
                "upgraded": bool(before) and _split_version_safe(after) > _split_version_safe(before),
                "from": before, "to": after}

    def _h_skill_library_review(self, params):
        """安装前审阅：下载完整技能包（含 content）供 UI 展示，不写入本地。"""
        sid = params.get("id") or params.get("name")
        if not sid:
            return {"ok": False, "error": "缺少技能 id"}
        skill, err = self._skill_library().review(
            sid, self.skills, force=bool(params.get("force", False)))
        if err:
            return {"ok": False, "error": err}
        return {"ok": True, "item": {
            "id": sid, "name": skill.name, "version": skill.version,
            "author": skill.author, "summary": skill.summary,
            "description": skill.description, "category": skill.category,
            "license": skill.license, "trigger": list(skill.trigger),
            "tags": list(skill.tags), "content": skill.content,
        }}

    def _h_skill_library_import_preview(self, params):
        """导入前预览：只读解析 .brick 包（zip 结构 + manifest + sha256 校验），
        返回逐积木摘要供 UI 确认弹窗展示。不写入、不注册、不改动任何状态。
        """
        from ..package import collect_brick_paths, inspect
        paths = params.get("files") or params.get("paths") or []
        if isinstance(paths, str):
            paths = [paths]
        bricks = collect_brick_paths([str(p) for p in paths])
        if not bricks:
            return {"ok": False, "error": "未找到 .brick 积木包（支持文件或文件夹）",
                    "items": []}
        items = []
        for bp in bricks:
            manifest, err = inspect(bp)
            if err:
                items.append({"path": str(bp), "ok": False, "error": err})
                continue
            for ent in manifest.get("entries") or []:
                items.append({
                    "path": str(bp), "ok": True,
                    "id": str(ent.get("id") or ""),
                    "name": str(ent.get("name") or ""),
                    "version": str(ent.get("version") or ""),
                    "author": str(ent.get("author") or ""),
                    "summary": str(ent.get("summary") or ""),
                    "category": str(ent.get("category") or ""),
                    "tags": list(ent.get("tags") or []),
                    "packed_by": str(manifest.get("packed_by") or ""),
                })
        return {"ok": True, "items": items, "total": len(items)}

    def _h_skill_library_import(self, params):
        """离线导入 .brick 积木包（一个或多个；支持文件夹递归）。

        入参：{"files": [<路径>...]}，文件或文件夹均可。
        逐包流程：校验 zip 结构 + manifest + sha256（防篡改）→ 校验 Skill 契约 →
        注册进 skills.json（source=清单 id，与远程市场安装对齐）→ 桥接 provides_tool。
        单包失败不拖死批量；返回逐包结果供 UI 逐块反馈（成功/失败可重试）。
        """
        from ..package import collect_brick_paths, inspect, unpack
        paths = params.get("files") or params.get("paths") or []
        if isinstance(paths, str):
            paths = [paths]
        bricks = collect_brick_paths([str(p) for p in paths])
        if not bricks:
            return {"ok": False, "error": "未找到 .brick 积木包（支持文件或文件夹）",
                    "items": [], "imported": 0}
        results = []
        imported = []
        for bp in bricks:
            manifest, err = inspect(bp)
            if err:
                results.append({"path": str(bp), "ok": False, "error": err})
                continue
            raws, err = unpack(bp)
            if err:
                results.append({"path": str(bp), "ok": False, "error": err})
                continue
            for ent, raw in zip(manifest.get("entries") or [], raws):
                try:
                    skill = validate_skill_package(raw)
                except SkillPackageError as e:
                    results.append({"path": str(bp), "ok": False,
                                    "id": str(ent.get("id") or ""),
                                    "name": str(ent.get("name") or ""),
                                    "error": f"积木校验失败：{e}"})
                    continue
                skill.source = str(ent.get("id") or skill.name)
                skill.installed_via = "offline"
                skill.installed_at = time.strftime("%Y-%m-%dT%H:%M:%S",
                                                   time.localtime())
                self.skills.register(skill)  # 同名覆盖（重导 = 更新）
                imported.append(skill)
                results.append({"path": str(bp), "id": skill.source,
                                "name": skill.name, "version": skill.version,
                                "ok": True})
        if imported:
            self.skills.save(self.config.home / "skills.json")
            self._sync_skill_tools()
        return {"ok": True, "items": results,
                "imported": sum(1 for r in results if r.get("ok")),
                "total": len(results)}

    def _sync_skill_tools(self) -> None:
        """把已装技能声明的 provides_tool 桥接进工具注册表（§4.4 / §4.5）。

        - 先清掉上一轮同步进来的技能工具，避免卸载后残留。
        - 遍历已装技能：provides_tool 非空 → 从 ToolProviderRegistry 取 handler 注册。
        - provides_tool 无对应内置 handler → 打 warning，按纯提示技能处理。
        调用时机：启动、技能安装 / 卸载 / 升级后。
        """
        from . import tool_providers
        # 清掉旧一轮技能工具
        for name in list(self._skill_tool_names):
            self.tools.unregister(name)
        self._skill_tool_names.clear()
        for sk in self.skills.all():
            pt = (sk.provides_tool or "").strip()
            if not pt:
                continue
            home = getattr(self, "config", None)
            home = getattr(home, "home", None) if home else None
            tool = tool_providers.ToolProviderRegistry.get(
                pt, home=home, skill=sk)
            if tool is None:
                print(f"[skill-tool] 警告：技能 {sk.name} 声明 provides_tool="
                      f"{pt}，但内置模块池无对应 handler，已忽略")
                continue
            self.tools.register(tool)
            self._skill_tool_names.add(tool.name)

    def _h_tool_trigger(self, params):
        """手动触发工具。无可执行实现时如实返回，不假装成功。"""
        name = params["name"]
        t = self.tools.get(name)
        if t is None:
            raise KeyError(f"工具不存在：{name}")
        if t.disabled:
            return {"ok": False, "output": f"工具「{name}」已停用。"}
        if t.handler is None:
            return {"ok": False,
                    "output": f"工具「{name}」未提供可执行实现（仅参与上下文筛选）。"}
        try:
            out = t.handler(**(params.get("args") or {}))
        except Exception as e:  # noqa: BLE001 - 工具失败不应拖垮服务
            return {"ok": False, "output": f"{type(e).__name__}: {e}"}
        return {"ok": True, "output": "" if out is None else str(out)}

    # ----- 确认弹窗 IPC（§3.4：MEDIUM/HIGH 风险工具阻塞等用户裁决）-----
    def _h_confirm_next(self, params):
        """Swift 长轮询：等到一个待确认项并返回其只读信息，否则超时返回 None。

        wait（秒）默认 25，避免 socket 长时间空占；Swift 侧循环重发即可。
        """
        try:
            wait = float(params.get("wait", 25))
        except (TypeError, ValueError):
            wait = 25.0
        item = self._confirm_broker.next_pending(wait_timeout=wait)
        return {"confirmation": item}

    def _h_confirm_resolve(self, params):
        """Swift 裁决：id + decision(bool) + 可选 remember(本次会话记住决定)。

        remember=True 时，把该工具名（由 Swift 在弹窗时已掌握的 tool_name 一并提供）
        记入会话级「记住决定」，后续同类调用跳过弹窗直接采信。
        """
        cid = params.get("id")
        decision = bool(params.get("decision", False))
        if not cid:
            raise KeyError("缺少确认 id")
        ok = self._confirm_broker.resolve(cid, decision)
        if not ok:
            raise KeyError(f"确认项不存在或已失效：{cid}")
        # 会话级「记住决定」（§5 确认弹窗打磨）
        if params.get("remember"):
            name = params.get("name") or ""
            if name:
                self._confirm_gateway.remember_decision(name, decision)
        return {"ok": True}

    def _h_mcp_list(self, params):
        """列出已接入的 MCP 服务器工具 + 接入错误（远程默认关的提示在此可见）。"""
        return {
            "tools": [t.name for t in self._mcp.tools()],
            "errors": list(self._mcp.errors),
        }

    def _h_set_mode(self, params):
        """切换执行模式（§5 模式切换）：normal / plan / accept_edits。

        落盘 config.json（持久化），并同步到共享确认网关（立即生效，跨聊天有效）。
        """
        raw = str(params.get("mode", "")).strip().lower()
        mode = Mode.from_str(raw)
        self.config.mode = mode.value
        self.config.save()
        self._confirm_gateway.set_mode(mode)
        return {"ok": True, "mode": mode.value}

    # ----- §P2 后台异步 agent / 多 agent 编排 -----
    def _h_task_submit(self, params):
        """派发一个后台任务（不阻塞）。返回 task_id 与初始状态。"""
        prompt = params.get("prompt") or params.get("message") or ""
        if not prompt.strip():
            raise ValueError("prompt 必填")
        t = self.scheduler.submit(prompt, project=params.get("project", ""))
        return {"task_id": t.id, "status": t.status.value}

    def _h_task_list(self, params):
        status = params.get("status")
        items = [{
            "id": t.id,
            "prompt": t.prompt[:200],
            "project": t.project,
            "status": t.status.value,
            "result": (t.result or "")[:400],
            "error": t.error,
            "created_at": t.created_at,
            "finished_at": t.finished_at,
            "parent_id": t.parent_id,
            "subtasks": list(t.subtasks),
        } for t in self.scheduler.list(status)]
        return {"items": items}

    def _h_task_get(self, params):
        t = self.scheduler.get(params.get("task_id", ""))
        if t is None:
            return {"task": None}
        return {"task": t.to_dict()}

    def _h_task_cancel(self, params):
        ok = self.scheduler.cancel(params.get("task_id", ""))
        return {"ok": ok}

    # ----- 运行状态摘要（§3.4，只做本地探测，绝不发探测请求）-----

    def _h_status(self, params):
        e: EngineConfig = self.config.engine
        local_path, local_ok = self._probe_local_model()
        last = self._read_daemon_last()
        return {
            "engine": {
                "backend": e.backend,
                "local_available": local_ok,
                "local_model_path": local_path,
                "network_configured": bool(e.api_url and e.api_key),
                "api_model": e.api_model,
                "api_name": e.api_name,
            },
            "daemon": {
                "running": self._daemon.is_running() if self._daemon else False,
                "last_consolidation": last,
            },
            "counts": {
                "sessions": self.sessions.count(),
                "drawers": len(self.memory.list_drawers() or []),
                "skills": len(self.skills.all()),
                "tools": len(self.tools.all()),
            },
        }

    def _h_interoception_state(self, params):
        """§4.5 III：暴露内感受当前状态给 UI（doctor 体感分段 + 聊天窗提示条）。

        状态为运行时数据（~/.brickery/interoception/state.json），全新安装为空。
        空态也返回 ok=True（前端据此显示「尚未采集」），不报错。
        """
        try:
            from .interoception import (InteroceptionSystem, EmergenceDecision,
                                        SensorReading)
            intero_sys = InteroceptionSystem(self.config.home)
            st = intero_sys.get_state()
            if st is None:
                return {"available": False, "state": {}, "trend": {},
                        "alerts": [], "summary": "", "readings": [],
                        "intensity": "none", "should_emerge": False,
                        "updated_at": 0}
            data = st.to_dict()
            readings = [
                SensorReading(
                    sensor_id=r.get("sensor_id", ""),
                    value=float(r.get("value", 0.0)),
                    baseline=float(r.get("baseline", 0.0)),
                    deviation=float(r.get("deviation", 0.0)),
                    confidence=float(r.get("confidence", 0.0)),
                )
                for r in data.get("readings", [])
            ]
            data["intensity"] = EmergenceDecision.intensity(
                data["state"], data["trend"], readings)
            data["should_emerge"] = EmergenceDecision.should_emerge(
                data["state"], data["trend"], readings)
            data["available"] = True
            return data
        except Exception as e:  # noqa: BLE001
            return {"available": False, "error": str(e), "intensity": "none",
                    "should_emerge": False}

    def _probe_local_model(self):
        """探测本地 GGUF 是否就位。纯文件系统检查，不加载模型、不联网。"""
        cfg = self.config.engine.local_model
        if cfg:
            p = Path(cfg)
            if not p.is_absolute():
                p = self.config.models_root / "gguf" / cfg
            return str(p), p.exists()
        gguf_dir = self.config.models_root / "gguf"
        if gguf_dir.is_dir():
            found = sorted(gguf_dir.glob("*.gguf"))
            if found:
                return str(found[0]), True
        return str(gguf_dir), False

    def _read_daemon_last(self):
        try:
            f = self.config.home / "daemon.status"
            if f.exists():
                return json.loads(f.read_text(encoding="utf-8")).get(
                    "last_consolidation")
        except (json.JSONDecodeError, OSError, ValueError):
            pass
        return None

    # ----- 本地模型目录 / 推荐 / 一键下载（§4.3 本地优先）-----
    def _h_models_list(self, params):
        installed = model_catalog.list_installed(self.config.models_root)
        catalog = model_catalog.GGUF_MODELS
        return {"installed": installed, "catalog": catalog,
                "mirror": model_catalog._mirror_base()}

    def _h_model_recommend(self, params):
        ram = params.get("ram_gb") or model_catalog.detect_ram_gb()
        coding = bool(params.get("coding", False))
        installed = model_catalog.list_installed(self.config.models_root)
        return model_catalog.recommend_for_ram(float(ram), coding=coding,
                                              installed=installed)

    def _h_model_download_start(self, params):
        model_id = (params.get("model_id") or "").strip()
        if not model_id:
            raise ValueError("model_id 必填")
        return model_catalog.start_download(model_id, self.config.models_root)

    def _h_model_download_status(self, params):
        model_id = (params.get("model_id") or "").strip()
        if not model_id:
            raise ValueError("model_id 必填")
        return model_catalog.download_status(model_id)

    def _h_model_download_pause(self, params):
        model_id = (params.get("model_id") or "").strip()
        if not model_id:
            raise ValueError("model_id 必填")
        return model_catalog.pause_download(model_id)

    def _h_model_download_cancel(self, params):
        model_id = (params.get("model_id") or "").strip()
        if not model_id:
            raise ValueError("model_id 必填")
        return model_catalog.cancel_download(model_id)

    def _h_model_download_resume(self, params):
        model_id = (params.get("model_id") or "").strip()
        if not model_id:
            raise ValueError("model_id 必填")
        return model_catalog.resume_download(model_id, self.config.models_root)

    def _h_model_delete(self, params):
        name = (params.get("name") or "").strip()
        if not name:
            raise ValueError("name 必填")
        return model_catalog.delete_model_file(name, self.config.models_root)

    # ----- 文件柜 / 项目抽屉 + 项目图谱（§9，clean room 重写）-----

    def _h_drawer_list(self, params):
        return {"items": self.memory.list_drawers()}

    def _h_drawer_get(self, params):
        did = params.get("drawer_id", "")
        d = self.memory.get_drawer(did)
        if d is None:
            return {"drawer": None}
        d["nodes"] = self.memory.list_nodes(did)
        d["edges"] = self.memory.list_edges(did)
        d["recordbook"] = self.memory.recordbook_text(did)
        return {"drawer": d}

    def _h_drawer_create(self, params):
        import uuid
        did = params.get("drawer_id") or f"drw_{uuid.uuid4().hex[:12]}"
        title = params.get("title") or "未命名项目"
        kit = params.get("kit") or []
        d = self.memory.create_drawer(did, title, kit=kit)
        return {"drawer": d}

    def _h_drawer_update(self, params):
        did = params.get("drawer_id", "")
        d = self.memory.update_drawer(
            did, title=params.get("title"), kit=params.get("kit"))
        return {"drawer": d}

    def _h_drawer_delete(self, params):
        ok = self.memory.delete_drawer(params.get("drawer_id", ""))
        return {"deleted": ok}

    def _h_node_add(self, params):
        node = self.memory.add_node(
            params["drawer_id"], params["type"], params.get("label", ""),
            content=params.get("content", ""),
            namespace=params.get("namespace", ""))
        self.memory.sync_recordbook(params["drawer_id"])
        return {"node": node}

    def _h_node_update(self, params):
        node = self.memory.update_node(
            params["node_id"], label=params.get("label"),
            content=params.get("content"), node_type=params.get("type"))
        return {"node": node}

    def _h_node_delete(self, params):
        ok = self.memory.delete_node(params.get("node_id", ""))
        return {"deleted": ok}

    def _h_edge_add(self, params):
        edge = self.memory.add_edge(
            params["drawer_id"], params["source"], params["target"],
            relation=params.get("relation", ""))
        return {"edge": edge}

    def _h_edge_delete(self, params):
        ok = self.memory.delete_edge(params.get("edge_id", ""))
        return {"deleted": ok}

    def _h_recordbook_sync(self, params):
        self.memory.sync_recordbook(params.get("drawer_id", ""))
        return {"ok": True}

    def _h_recordbook_get(self, params):
        return {"text": self.memory.recordbook_text(params.get("drawer_id", ""))}

    def _h_explain_node(self, params):
        # 无引擎 → 降级返回原始内容（零外连）；有则优先网络 API（质量更稳），
        # API 不可用才降级本地 GGUF（与主对话 backend=api 策略一致）。
        engine = self._make_api_engine() or self._make_local_engine()
        return {"result": self.memory.explain_node(
            params.get("node_id", ""), engine=engine)}

    def _h_drawer_chat(self, params):
        """项目独立聊天输入（规格第 5 条）：以 drawer_id 为 project 命名空间，
        反馈显示在工作台内部，不回跳日常聊天。"""
        message = params.get("message", "")
        drawer_id = params.get("drawer_id", "")
        self._stop_event.clear()
        # 项目会话独立成流：以抽屉 id 派生会话，不与日常聊天混
        sid = self.sessions.ensure(params.get("session_id") or f"drawer_{drawer_id}",
                                   project=drawer_id)
        self.sessions.append(sid, "user", message)
        history = self.sessions.history(sid, limit=21)[:-1]
        loop = self._new_loop(sid)
        try:
            reply = loop.run(message, project=drawer_id, history=history)
        except InterruptedError:
            return {"reply": "（已取消）", "session_id": sid, "interrupted": True,
                    "used_tools": [], "used_skills": []}
        self.sessions.append(sid, "assistant", reply,
                             used_tools=loop.last_tools,
                             used_skills=loop.last_skills)
        return {"reply": reply, "session_id": sid,
                "used_tools": loop.last_tools, "used_skills": loop.last_skills}

    def _h_recommend_detect(self, params):
        return {"result": self.memory.detect_recommendation(
            params.get("text", ""))}

    # ----- 文件柜文件级检索（§8 filing 经 cabinet 一致暴露）-----

    def _h_file_index(self, params):
        self.memory.index_file(
            params["doc_id"], params.get("path", ""),
            params.get("title", ""), params.get("content", ""))
        return {"ok": True}

    def _h_file_update(self, params):
        self.memory.update_file(
            params["doc_id"], title=params.get("title"),
            content=params.get("content"), path=params.get("path"))
        return {"ok": True}

    def _h_file_remove(self, params):
        self.memory.remove_file(params.get("doc_id", ""))
        return {"ok": True}

    def _h_file_search(self, params):
        return {"items": self.memory.search_files(
            params.get("query", ""), limit=params.get("limit", 10))}

    def _h_config_get(self, params):
        e: EngineConfig = self.config.engine
        ny = self.config.nightly
        return {
            "engine": {
                "backend": e.backend,
                "local_model": e.local_model,
                "api_url": e.api_url,
                "api_key": e.api_key,
                "api_model": e.api_model,
                "api_name": e.api_name,
            },
            "nightly": {
                "enabled": ny.enabled,
                "use_local_model": ny.use_local_model,
                "local_model": ny.local_model,
            },
            "tools_enabled": self.config.tools_enabled,
            "skills_enabled": self.config.skills_enabled,
            "mode": self.config.mode,
            "max_context_tokens": self.config.max_context_tokens,
            "home": str(self.config.home),
            "models_root": str(self.config.models_root),
            "backup_dir": str(self.config.backup_dir),
            "output_dir": str(self.config.output_dir),
            "profiles": self.config.profiles,
            "active_profile_id": self.config.active_profile_id,
            "chat_model": self.config.chat_model,
        }

    @staticmethod
    def _is_mask(value: str) -> bool:
        """判断是否为掩码回显。

        三态语义（前代曾在此踩坑：用「空集是任何集合子集」判掩码 → 恒真 →
        空串被当掩码跳过，api_key 永远清不掉）：
          - 字段缺省      → 保持原值（由调用方不传实现）
          - ""            → 清除（**不是**掩码）
          - 非空且全为 '*' → 掩码，保持原值
          - 其它非空       → 覆盖写入
        """
        return bool(value) and set(value) == {"*"}

    def _h_config_set(self, params):
        with self._lock:
            # —— 多预设路径 —— 若传了 profiles，先把 active profile 投影成单组 params，
            # 后续原逻辑照常应用到 engine（热切换生效）；并把 profiles/active 持久进 config。
            if "profiles" in params and isinstance(params["profiles"], list) and params["profiles"]:
                # F2 加固：先过滤掉缺 id 的畸形预设（手工改配置/传输截断可触发），
                # 绝不让无 id 的预设 (`None` id) 进入 profiles 或成为活跃预设。
                clean_profiles = [p for p in params["profiles"] if isinstance(p, dict) and p.get("id")]
                if clean_profiles:
                    # 过滤结果写回 params，避免原始含畸形项的列表被持久进 config.json
                    params = dict(params)
                    params["profiles"] = clean_profiles
                    active_id = params.get("active_profile_id") or self.config.active_profile_id
                    ids = [p["id"] for p in clean_profiles]
                    if active_id not in ids:
                        active_id = ids[0] if ids else "default"
                    self.config.profiles = clean_profiles
                    self.config.active_profile_id = active_id
                    ap = next((p for p in clean_profiles if p.get("id") == active_id), None)
                    if ap and isinstance(ap, dict):
                        # 顶层显式字段优先（前端 saveConfig 会带 backend/api_url/api_key/...，
                        # 单组变量是用户界面直接绑定的权威值）；profile 仅作兜底。
                        # 敏感字段 api_key 额外保守：profile 的空 key 一律不覆盖 engine 已存值
                        # （清除只能由顶层显式空串触发，避免「active 预设空 key → 顺带清空 engine」
                        # 的丢配置 bug，0.3.17 只发 profiles 时必现）。
                        params.setdefault("backend", ap.get("backend", "api"))
                        for k in ("local_model", "api_url", "api_model", "api_name"):
                            params.setdefault(k, ap.get(k, ""))
                        if "api_key" not in params and ap.get("api_key"):
                            params["api_key"] = ap["api_key"]
                # 过滤后为空（传入全畸形）：请求不可信，跳过整个多预设分支，不覆盖原预设、不投影
            backend = params.get("backend", self.config.engine.backend)
            kwargs = {k: params[k] for k in
                      ("local_model", "api_url", "api_key", "api_model")
                      if k in params}
            # 备份/产出目录（§用户数据管理）：显式填则持久化；空串/未设=走默认派生
            if "backup_dir" in params and params["backup_dir"]:
                self.config.backup_dir = Path(str(params["backup_dir"])).expanduser()
            if "output_dir" in params and params["output_dir"]:
                self.config.output_dir = Path(str(params["output_dir"])).expanduser()
            # 新会话开场上下文（§跨会话记忆）：随 config 持久化的纯开关
            if "open_session_context" in params:
                self.config.open_session_context = bool(params["open_session_context"])
            # 会话栏选用的模型（§UI 固化）：随 config 持久化，重启恢复上次所选
            if "chat_model" in params:
                self.config.chat_model = str(params["chat_model"])
            # 夜间记忆整理配置（§7）：独立于主引擎，可单独开关 / 指定模型
            ny = params.get("nightly")
            if isinstance(ny, dict):
                if "enabled" in ny:
                    self.config.nightly.enabled = bool(ny["enabled"])
                if "use_local_model" in ny:
                    self.config.nightly.use_local_model = bool(ny["use_local_model"])
                if "local_model" in ny:
                    self.config.nightly.local_model = ny["local_model"]
            # 掩码回显的敏感字段不覆盖原值；空串则是明确的「清除」
            if "api_key" in kwargs and self._is_mask(kwargs["api_key"]):
                kwargs.pop("api_key")
            # App 内显示名称（仅 UI 展示，不参与推理）：随 config 持久化
            if "api_name" in params:
                self.config.engine.api_name = str(params["api_name"])
            # 切到 api 但未重填 url 时，沿用已存端点（set_backend 强制非空 url）
            if backend == "api" and not kwargs.get("api_url"):
                kwargs["api_url"] = self.config.engine.api_url
            router = EngineRouter(self.config.engine)
            router.set_backend(backend, **kwargs)  # 复用 ValueError 契约
            self.config.save()
        return {"ok": True, "backend": self.config.engine.backend}

    def _h_feishu_setup(self, params):
        """从引导 UI 接收飞书 app_id/app_secret，写入 ~/.brickery/config/feishu.json。

        极简引导：用户只填这两个字段。其余由连接器自动处理：
        - ws_url 由连接器用 tenant_access_token 自动向 portal 拉取；
        - 第一个对自己 bot 说话的飞书账号自动成为授权用户（auto_bind_owner）。
        凭据仅存本地 config，不进 git、不进记忆库。OFF 之外的启用由写入 enabled=true 完成。
        """
        from .paths import get_config_dir
        app_id = (params.get("app_id") or "").strip()
        app_secret = (params.get("app_secret") or "").strip()
        if not app_id or not app_secret:
            raise ValueError("app_id 与 app_secret 均为必填")
        cfg_path = Path(get_config_dir()) / "feishu.json"
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "enabled": True,
            "app_id": app_id,
            "app_secret": app_secret,
            "allowed_user_ids": [],
            "event_mode": "websocket",
            "base_url": "https://open.feishu.cn",
            "session_prefix": "feishu_",
            "ws_url": "",
            "auto_bind_owner": True,
        }
        cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        return {
            "ok": True,
            "message": "飞书已配置。重启 Shadeling 后，首次用飞书给你 bot 发消息即自动绑定你的账号。",
        }

    def _h_telegram_setup(self, params):
        """从引导 UI 接收 Telegram bot_token，写入 ~/.brickery/config/telegram.json。

        极简引导：用户只填 bot_token（由 @BotFather 创建 bot 获得）。其余由连接器自动处理：
        - 首个对自己 bot 说话的 Telegram 账号自动成为授权用户（auto_bind_owner）；
        - api_base 默认官方端点，可改镜像。
        凭据仅存本地 config，不进 git、不进记忆库。OFF 之外的启用由写入 enabled=true 完成。
        与飞书同构（见 docs/connectors_feishu_design.md）。
        """
        from .paths import get_config_dir
        bot_token = (params.get("bot_token") or "").strip()
        if not bot_token:
            raise ValueError("bot_token 为必填")
        cfg_path = Path(get_config_dir()) / "telegram.json"
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "enabled": True,
            "bot_token": bot_token,
            "allowed_user_ids": [],
            "auto_bind_owner": True,
            "session_prefix": "telegram_",
            "api_base": "https://api.telegram.org",
        }
        cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        return {
            "ok": True,
            "message": "Telegram 已配置。重启 Shadeling 后，首次用 Telegram 给你 bot 发消息即自动绑定你的账号。",
        }

    def _h_doctor(self, params):
        return self._run_doctor()

    def _run_doctor(self) -> dict:
        checks = []
        # 运行环境（macOS 版本 / 架构）——纯信息项，始终 ok；
        # Apple Silicon 上报 Metal 可用，Intel 上提示本地推理不可用（建议网络 API）
        mac_ver = platform.mac_ver()[0] or "未知"
        arch = platform.machine()  # arm64 / x86_64
        is_apple_silicon = (arch == "arm64")
        if is_apple_silicon:
            checks.append({"name": "运行环境（macOS / 架构）", "ok": True,
                           "detail": f"macOS {mac_ver} · Apple Silicon ({arch}) · Metal 本地推理可用"})
        else:
            checks.append({"name": "运行环境（macOS / 架构）", "ok": True,
                           "detail": f"macOS {mac_ver} · Intel ({arch}) · 本地 Metal 推理不可用，建议用网络 API"})
        # Python 可用
        checks.append({"name": "Python 运行时", "ok": True,
                       "detail": f"{os.sys.version.split()[0]}"})
        # llama_cpp：import 失败需区分「真没装」与「装了但不兼容当前芯片」
        # （llama_cpp 为 Metal 后端编译，仅 Apple Silicon 可加载；Intel 上为架构不兼容，属预期行为，非安装失败）
        try:
            import llama_cpp  # noqa: F401
            checks.append({"name": "本地推理依赖 llama-cpp-python",
                           "ok": True, "detail": "已安装"})
        except Exception as e:
            if arch == "x86_64":
                checks.append({"name": "本地推理依赖 llama-cpp-python",
                               "ok": False,
                               "detail": "已安装但当前为 Intel 芯片，Metal 后端不兼容，本地 GGUF 推理不可用（预期行为，非安装失败）；请改用网络 API"})
            else:
                checks.append({"name": "本地推理依赖 llama-cpp-python",
                               "ok": False,
                               "detail": f"未安装（{self._doctor_short(e)}）；本地 GGUF 推理不可用，可改用网络 API"})
        # 模型文件
        if self.config.engine.backend == "local":
            eng = LocalGGUFEngine(self.config.engine.local_model or None)
            mp = eng._resolve_model()
            if mp:
                checks.append({"name": "本地 GGUF 模型",
                               "ok": True, "detail": mp})
            else:
                checks.append({"name": "本地 GGUF 模型",
                               "ok": False,
                               "detail": "未找到；请放置 .gguf 到模型目录"})
        else:
            eng = self.config.engine
            if not eng.api_url:
                checks.append({"name": "网络 API 端点", "ok": False,
                               "detail": "未配置（如需网络模型，去设置填写端点）"})
            else:
                # 连通性实探：发一次最小请求，按返回分类根因（纯代码、带超时、零模型依赖）
                ok, detail = self._doctor_probe_api(eng)
                checks.append({"name": "网络 API 连通性", "ok": ok, "detail": detail})
        # 路径可写
        for label, p in (("运行时目录", self.config.home),
                         ("模型目录", self.config.models_root)):
            try:
                p.mkdir(parents=True, exist_ok=True)
                checks.append({"name": f"{label} 可写", "ok": True,
                               "detail": str(p)})
            except OSError as e:
                checks.append({"name": f"{label} 可写", "ok": False,
                               "detail": str(e)})

        # 磁盘剩余空间（GGUF 下载 / 解压需余量；低于阈值告警，纯代码、不阻断）
        ok, detail = self._doctor_disk_space()
        checks.append({"name": "磁盘剩余空间", "ok": ok, "detail": detail})

        # 飞书依赖（仅启用时检查；未启用直接跳过，避免制造无关告警）
        feishu_cfg = self.config.home / "config" / "feishu.json"
        feishu_enabled = False
        if feishu_cfg.exists():
            try:
                _fc = json.loads(feishu_cfg.read_text(encoding="utf-8"))
                feishu_enabled = bool(_fc.get("enabled", False))
            except Exception:
                feishu_enabled = False
        if feishu_enabled:
            try:
                import websocket  # websocket-client
                checks.append({"name": "飞书长连接依赖 websocket-client",
                               "ok": True, "detail": "已安装"})
            except ImportError:
                checks.append({"name": "飞书长连接依赖 websocket-client",
                               "ok": False,
                               "detail": "未安装（飞书连接器无法工作，需 pip install websocket-client）"})
            # 凭证实探：验证 app_id/secret 是否有效，区分"凭证错"与"网络不可达"
            try:
                _fc = json.loads(feishu_cfg.read_text(encoding="utf-8"))
                if _fc.get("app_id") and _fc.get("app_secret"):
                    ok, detail = self._doctor_probe_feishu(_fc)
                    checks.append({"name": "飞书凭证有效性", "ok": ok, "detail": detail})
            except Exception:
                pass
        else:
            checks.append({"name": "飞书依赖", "ok": True,
                           "detail": "未启用，跳过"})

        # 数据目录初始化（首启识别）
        first_run = not (self.config.home / "config.json").exists()
        if self.config.home.exists():
            checks.append({"name": "数据目录", "ok": True,
                           "detail": f"{self.config.home} 已存在"})
        else:
            checks.append({"name": "数据目录", "ok": False,
                           "detail": f"{self.config.home} 不存在，将自动创建"})

        all_ok = all(c["ok"] for c in checks)
        return {"all_ok": all_ok, "first_run": first_run, "checks": checks}

    # ----- doctor 增强：纯代码确定性探测（零模型依赖）-----

    @staticmethod
    def _doctor_short(e: Exception) -> str:
        s = str(e)
        return s if len(s) <= 80 else s[:77] + "..."

    def _doctor_probe_api(self, eng) -> tuple[bool, str]:
        """对绑定的网络 API 发起一次最小 /chat/completions 请求，按返回分类根因。

        这是纯代码确定性检查：不加载模型、不依赖任何推理服务。
        分类意图：让用户在「填了但连不通」时立刻看到是 key 失效 / 限速 / 网络，
        而非静默失败。超时短（8s）以免拖慢诊断。
        """
        url = eng.api_url.rstrip("/") + "/chat/completions"
        payload = json.dumps({
            "model": eng.api_model or "gpt-4o-mini",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {eng.api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                code = resp.status
                if 200 <= code < 300:
                    return True, f"连通正常（{code}）"
                return False, f"服务端返回 {code}"
        except urllib.error.HTTPError as e:
            code = e.code
            if code in (401, 403):
                return False, "API Key 无效或权限不足"
            if code == 429:
                return False, "额度用尽或被限速"
            if 400 <= code < 500:
                return False, f"请求 / 端点错误（HTTP {code}）"
            return False, f"服务端错误（HTTP {code}）"
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            return False, f"网络不可达（可能需代理）：{self._doctor_short(e)}"
        except Exception as e:  # noqa: BLE001
            return False, f"请求异常：{self._doctor_short(e)}"

    def _doctor_probe_feishu(self, fc: dict) -> tuple[bool, str]:
        """验证飞书 app_id/secret 是否有效（tenant_access_token/internal）。

        区分两类失败：凭证错（飞书返回非 0 code） vs 网络不可达（需代理）。
        纯代码、带超时（6s），不加载任何模型。
        """
        app_id = (fc.get("app_id") or "").strip()
        app_secret = (fc.get("app_secret") or "").strip()
        if not app_id or not app_secret:
            return False, "app_id / app_secret 未填写"
        base = (fc.get("base_url") or "https://open.feishu.cn").rstrip("/")
        url = base + "/open-apis/auth/v3/tenant_access_token/internal"
        payload = json.dumps({"app_id": app_id,
                              "app_secret": app_secret}).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload, method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("code", -1) == 0 and data.get("tenant_access_token"):
                    return True, "凭证有效"
                return False, f"飞书返回错误码 {data.get('code')}：{data.get('msg')}"
        except urllib.error.HTTPError as e:
            return False, f"飞书接口 HTTP {e.code}"
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            return False, f"飞书接口网络不可达（可能需代理）：{self._doctor_short(e)}"
        except Exception as e:  # noqa: BLE001
            return False, f"飞书凭证校验异常：{self._doctor_short(e)}"

    def _doctor_disk_space(self) -> tuple[bool, str]:
        """检查模型目录所在磁盘剩余空间（GGUF 需 4–5GB 余量）。纯代码。"""
        try:
            total, used, free = shutil.disk_usage(self.config.models_root)
            free_gb = free // (1024 ** 3)
            if free_gb < 10:
                return False, f"剩余 {free_gb} GB，低于 10GB 告警线（GGUF 需 4–5GB 余量）"
            return True, f"剩余 {free_gb} GB"
        except Exception as e:  # noqa: BLE001
            return True, f"无法读取（跳过）：{self._doctor_short(e)}"

    # ----- 数据与备份（封装第 11 节手册命令，提供 UI 入口）-----

    def _h_backup_export(self, params):
        """把数据目录（self.config.home）整体备份到用户指定的目标目录下的时间戳子目录。

        只备份用户数据（会话/记忆/配置/文件柜），不含模型权重
        （权重在 ~/shadeling-runtime/models，体积大，可单独处理）。
        """
        dest = (params.get("dest_dir") or "").strip()
        if not dest:
            raise ValueError("dest_dir 必填（导出目标目录）")
        dest_dir = Path(dest).expanduser()
        if not dest_dir.exists():
            dest_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
        out = dest_dir / f"Shadeling_{ts}"
        if out.exists():
            raise ValueError(f"目标已存在：{out}")
        src = self.config.home
        if not src.exists():
            raise ValueError(f"数据目录不存在，无需备份：{src}")
        try:
            shutil.copytree(src, out)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"备份失败：{e}") from e
        n = sum(1 for _ in out.rglob("*"))
        return {
            "ok": True,
            "dest": str(out),
            "detail": f"已备份 {n} 个项目到 {out}",
            "note": "仅含用户数据（会话/记忆/配置/文件柜）；模型权重在 ~/shadeling-runtime/models，需单独处理。",
        }

    def _h_backup_restore(self, params):
        """从指定备份目录恢复数据到 self.config.home（按顶层条目覆盖）。

        先校验目录像不像 Shadeling 备份，避免误覆盖；逐项覆盖，不递归删整目录，
        因此不会误伤 home 下可能存在的其它文件。
        """
        src = (params.get("src_dir") or "").strip()
        if not src:
            raise ValueError("src_dir 必填（备份目录）")
        src_dir = Path(src).expanduser()
        if not src_dir.exists() or not src_dir.is_dir():
            raise ValueError(f"备份目录不存在：{src_dir}")
        markers = ["config.json", "config", "memory.db", "sessions.db",
                   "memory", "cabinet.db", "filing.db", "consolidation.db"]
        if not any((src_dir / m).exists() for m in markers):
            raise ValueError("该目录不像 Brickery 备份（缺少 config/config.json/memory 等标记），已拒绝以防误覆盖。")
        home = self.config.home
        home.mkdir(parents=True, exist_ok=True)
        copied = 0
        for item in src_dir.iterdir():
            target = home / item.name
            if item.is_dir():
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
            copied += 1
        return {
            "ok": True,
            "message": f"已从 {src_dir} 恢复 {copied} 个项目到 {home}。重启 Brickery 后生效。",
        }

    def _h_backup_default(self, params):
        """一键备份到默认位置（self.config.backup_dir），无需每次手选目录。

        复用 _h_backup_export 同套 copytree 逻辑；目录不存在自动创建；
        同分钟防冲突加序号。
        """
        dest_dir = self.config.backup_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
        out = dest_dir / f"Shadeling_{ts}"
        i = 1
        while out.exists():
            out = dest_dir / f"Shadeling_{ts}_{i}"
            i += 1
        src = self.config.home
        if not src.exists():
            raise ValueError(f"数据目录不存在，无需备份：{src}")
        try:
            shutil.copytree(src, out)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"备份失败：{e}") from e
        n = sum(1 for _ in out.rglob("*"))
        return {
            "ok": True,
            "dest": str(out),
            "detail": f"已备份 {n} 个项目到 {out}",
            "note": "仅含用户数据（会话/记忆/配置/文件柜）；模型权重在 ~/shadeling-runtime/models，需单独处理。",
        }

    def _h_backup_list(self, params):
        """列出默认备份目录（self.config.backup_dir）下所有备份，供「查看备份列表」按钮用。

        只读遍历 Shadeling_* 目录，返回时间戳 + 项目数；目录不存在返回空列表。
        """
        dest_dir = self.config.backup_dir
        items = []
        if dest_dir.exists():
            for p in sorted(dest_dir.glob("Shadeling_*"), reverse=True):
                if not p.is_dir():
                    continue
                try:
                    n = sum(1 for _ in p.rglob("*"))
                except OSError:
                    n = 0
                items.append({
                    "path": str(p),
                    "name": p.name,
                    "items": n,
                    "created": p.stat().st_mtime,
                })
        return {"ok": True, "items": items, "backup_dir": str(dest_dir)}

    # ----- 持久规则（rules.json / SHADERULES.md，rules 积木用）-----
    def _rules_path(self):
        return self.config.home / "rules.json"

    def _h_rules_list(self, params):
        return {"rules": list(self.rules),
                "rules_json": self._rules_path().exists(),
                "shaderules_md": (self.config.home / "SHADERULES.md").exists()}

    def _h_rules_add(self, params):
        rule = (params.get("rule") or "").strip()
        if not rule:
            return {"ok": False, "error": "缺少 rule"}
        p = self._rules_path()
        data = {"rules": []}
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                data = {"rules": []}
        rs = data.get("rules") or []
        if not isinstance(rs, list):
            rs = []
        rs.append(rule)
        data["rules"] = rs
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.rules = load_rules(self.config.home)
        return {"ok": True, "count": len(rs)}

    def _h_rules_remove(self, params):
        idx = params.get("index")
        if idx is None:
            return {"ok": False, "error": "缺少 index"}
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            return {"ok": False, "error": "index 须为整数"}
        p = self._rules_path()
        if not p.exists():
            return {"ok": False, "error": "rules.json 不存在"}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {"ok": False, "error": "rules.json 解析失败"}
        rs = data.get("rules") or []
        if not (0 <= idx < len(rs)):
            return {"ok": False, "error": f"index 越界（共 {len(rs)} 条）"}
        removed = rs.pop(idx)
        data["rules"] = rs
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.rules = load_rules(self.config.home)
        return {"ok": True, "removed": removed, "count": len(rs)}

    def _h_rules_reload(self, params):
        self.rules = load_rules(self.config.home)
        return {"ok": True, "count": len(self.rules)}

    def _h_open_folder(self, params):
        """在文件管理器中打开指定目录（macOS 用 `open`），便于用户直达备份/产出文件夹。

        path 为空时按 kind 回退到 backup_dir / output_dir。
        """
        kind = (params.get("kind") or "").strip()
        path = (params.get("path") or "").strip()
        if not path:
            if kind == "backup":
                path = str(self.config.backup_dir)
            elif kind == "output":
                path = str(self.config.output_dir)
            else:
                raise ValueError("path 或 kind(backup/output) 必填其一")
        p = Path(path).expanduser()
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
        if platform.system() == "Darwin":
            cmd = ["open", str(p)]
        elif platform.system() == "Windows":
            cmd = ["explorer", str(p)]
        else:
            cmd = ["xdg-open", str(p)]
        try:
            subprocess.run(cmd, check=False)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"打开文件夹失败：{e}") from e
        return {"ok": True, "path": str(p), "detail": f"已在文件管理器中打开：{p}"}

    def _enqueue_nightly(self) -> None:
        """空闲整理入队（§7，2026-08-09 改：归纳默认开）。

        - nightly 未开启 → 完全不 enqueue（整理暂停）。
        - 始终 enqueue 一个 `prune`（纯规则骨架整理，无模型即可运行；防重：已有
          pending prune 则跳过，避免 daemon 循环期 flood 队列）。
        - 归纳引擎可用（API 或本地）→ 额外 enqueue 每个待归纳会话
          一个 `summarize`（归纳增强，写回干净摘要）+ `auto_core_fill`
          （固定核智能槽自动填充，高置信规律自动写入）。
        """
        ny = self.config.nightly
        if not ny.enabled:
            return
        with consolidation_conn() as c:
            pending_prune = c.execute(
                "SELECT COUNT(*) AS n FROM queue WHERE status='pending' AND item_type='prune'"
            ).fetchone()["n"]
        if not pending_prune:
            self.memory.enqueue("prune")

        eng = self._make_nightly_engine()
        if eng is not None:
            with consolidation_conn() as c:
                pending_sum = c.execute(
                    "SELECT COUNT(*) AS n FROM queue WHERE status='pending' "
                    "AND item_type='summarize'"
                ).fetchone()["n"]
            if not pending_sum:
                for sid, text in self.memory.nightly_pending_sessions():
                    self.memory.enqueue("summarize", {"session_id": sid, "text": text})
            # auto_core_fill：归纳引擎可用时自动推固定核智能槽
            if getattr(ny, 'auto_core_fill', True):
                self._auto_fill_core(eng)

    def _auto_fill_core(self, eng) -> None:
        """O8' 固定核智能槽自动填充（2026-08-09）。

        从近期对话中提取高置信规律：
        - 重复出现 ≥2 次的项目名/偏好/习惯用语 → 自动写入智能槽
        - 中置信（单次决策陈述）→ 推候选待确认（暂写入 pending_candidates 表）
        - 高敏感（密码/私钥格式）→ 硬拦截不入槽

        使用归纳引擎做语义提取（降级：纯规则关键词计数）。
        """
        from brickery.memory.fixed_core import set_smart_slot, _is_sensitive

        try:
            # 取近期对话摘要（最多 5 个会话）
            sessions = list(self.memory.nightly_pending_sessions())
            if not sessions:
                return
            # 拼接文本供归纳引擎提取规律
            text = "\n".join(t[:500] for _, t in sessions[:5])
            if len(text) < 50:
                return

            # 尝试用归纳引擎提取规律
            patterns = self._extract_patterns(eng, text)
            for p in patterns:
                label = p.get("label", "").strip()
                value = p.get("value", "").strip()
                conf = float(p.get("confidence", 0.5))
                if not label or not value:
                    continue
                if _is_sensitive(value):
                    continue  # 硬拦截
                if conf >= 0.7:
                    set_smart_slot(label, value, confidence=conf)
                elif conf >= 0.5:
                    # 中置信推候选（UI 待确认区）
                    self._push_core_candidate(label, value, conf)
        except Exception:
            pass  # 智能槽填充失败不拖垮整理流程

    def _extract_patterns(self, eng, text: str) -> list:
        """用归纳引擎从文本中提取重复规律。

        降级策略：引擎不可用或归纳失败时，退化为规则关键词计数。
        """
        # 尝试用引擎做语义提取
        try:
            prompt = (
                "从以下对话摘要中提取用户反复出现的长期规律（项目名/偏好/习惯用语）。"
                "规则：\n"
                "1. 只在明确出现≥2次时才提取\n"
                "2. 不提取任何密码/密钥/身份证号/手机号\n"
                "3. 每条输出为 JSON 行："
                '{"label":"规律名称","value":"具体内容","confidence":0.0-1.0}\n'
                "4. 置信度：≥2次明确出现=0.9，模糊暗示=0.5-0.6\n\n"
                f"{text}"
            )
            result = eng.complete(prompt, temperature=0.1, max_tokens=300)
            if not result:
                return []
            # 解析 JSON 行
            patterns = []
            for line in result.strip().split("\n"):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        obj = json.loads(line)
                        if "label" in obj and "value" in obj:
                            patterns.append(obj)
                    except json.JSONDecodeError:
                        continue
            return patterns
        except Exception:
            pass
        # 降级：纯规则关键词计数
        return self._rule_based_patterns(text)

    def _rule_based_patterns(self, text: str) -> list:
        """纯规则降级：关键词计数提取高频项目名/偏好。"""
        import re as _re
        from collections import Counter
        patterns = []
        # 提取可能的项目名（连续大写/驼峰/中文词组）
        proj = _re.findall(r'\b([A-Z][a-zA-Z]+(?:[ -][A-Z][a-zA-Z]+)*)\b', text)
        # 提取偏好句式：「我喜欢/我习惯/我通常/我一般」+ 后续
        pref = _re.findall(r'(?:我喜欢|我习惯|我通常|我一般|我更偏好)[^。，\n]{2,30}', text)
        # 项目名计数
        proj_counter = Counter(proj)
        for name, cnt in proj_counter.items():
            if cnt >= 2 and len(name) >= 3:
                patterns.append({"label": f"在办项目：{name}", "value": f"用户反复提及项目 {name}",
                                 "confidence": min(0.9, 0.6 + cnt * 0.1)})
        # 偏好计数
        pref_counter = Counter(pref)
        for p, cnt in pref_counter.items():
            if cnt >= 2:
                patterns.append({"label": "习惯偏好", "value": p.strip(),
                                 "confidence": min(0.85, 0.6 + cnt * 0.1)})
        return patterns

    def _push_core_candidate(self, label: str, value: str, confidence: float) -> None:
        """推候选到待确认区（UI 可轮询展示）。"""
        try:
            from brickery.memory.db import memory_conn
            with memory_conn() as c:
                # 确保 pending_candidates 表存在
                c.execute(
                    "CREATE TABLE IF NOT EXISTS pending_candidates("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "label TEXT NOT NULL, value TEXT NOT NULL,"
                    "confidence REAL DEFAULT 0.5,"
                    "created_at TEXT NOT NULL,"
                    "status TEXT DEFAULT 'pending')"
                )
                c.execute(
                    "INSERT INTO pending_candidates(label,value,confidence,created_at) "
                    "VALUES(?,?,?,?)",
                    (label, value, confidence, _now_iso_str()),
                )
        except Exception:
            pass

    def mark_activity(self) -> None:
        """标记一次前台活动（对话）。空闲整理据此让路。"""
        self._last_activity = time.monotonic()

    def idle_seconds(self) -> float:
        return time.monotonic() - self._last_activity

    def _nightly_job(self) -> dict:
        """daemon 每轮调用：**空闲达标才开工**，否则立刻返回。

        桌面 App 没有真正的「夜间」，触发条件改为「距上次对话已空闲 N 分钟」
        （NightlyConfig.idle_minutes，0 表示不设门槛）。未达标时直接跳过——
        不开数据库、不入队、不推理，确保整理永远不与前台对话抢 CPU / GPU。
        """
        required = max(0.0, float(self.config.nightly.idle_minutes)) * 60.0
        idle = self.idle_seconds()
        if required > 0 and idle < required:
            return {"skipped": "busy",
                    "idle_seconds": round(idle, 1),
                    "required_seconds": required}
        self._enqueue_nightly()
        return self.memory.run_consolidation(engine=self._make_nightly_engine())

    def _h_daemon_start(self, params):
        with self._lock:
            if self._daemon is None:
                self._daemon = Daemon(
                    self.memory, self.config,
                    consolidate=self._nightly_job)
            if not self._daemon.is_running():
                self._daemon.start(block=False)
        return {"running": self._daemon.is_running()}

    def _h_daemon_stop(self, params):
        if self._daemon is not None:
            self._daemon.stop()
        return {"running": False if self._daemon is None
                else self._daemon.is_running()}

    def _h_daemon_status(self, params):
        running = self._daemon.is_running() if self._daemon else False
        return {"running": running,
                "last_consolidation": self._read_daemon_last()}


# --------------------------------------------------------------------------
# 独立启动入口（供 Swift 子进程调用：python -m runtime.ipc --port <p>）
# --------------------------------------------------------------------------

def _ensure_agent_home(home: Path, app_resources: Optional[Path]) -> None:
    """安装态首次启动初始化：从 .app 内部 Resources 复制模板到数据目录。

    幂等：以 home/agent.json 存在为"已初始化"标记，已初始化则完全跳过，
    绝不覆盖用户已有数据。config.json / sessions.db 由 load_config /
    SessionStore 自动兜底（缺失回退安全默认 / 自动建库建表），无需在此生成。
    开发态（run.sh 直跑 agent 目录）不传 --app-resources，本函数直接返回。
    """
    if not app_resources or not app_resources.is_dir():
        return
    home.mkdir(parents=True, exist_ok=True)
    marker = home / "agent.json"
    if marker.exists():
        return
    src_agent = app_resources / "agent.json"
    src_bricks = app_resources / "bricks"
    if src_agent.exists():
        shutil.copy2(src_agent, marker)
    if src_bricks.is_dir():
        dst_bricks = home / "bricks"
        if not dst_bricks.exists():
            shutil.copytree(src_bricks, dst_bricks)
    print(f"[Brickery] 首次启动：已从安装包初始化数据目录 {home}", flush=True)


def main(argv: Optional[list] = None) -> int:
    import argparse
    import faulthandler
    import signal
    import time
    faulthandler.register(signal.SIGUSR1)  # 诊断：SIGUSR1 dump 全线程栈
    ap = argparse.ArgumentParser(description="Brickery 本地 IPC 服务")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--home", default=None)
    ap.add_argument("--app-resources", default=None)
    args = ap.parse_args(argv)
    home = Path(args.home).expanduser() if args.home else None
    app_resources = Path(args.app_resources).expanduser() if args.app_resources else None
    if app_resources is not None:
        home = home or paths.get_home()
        _ensure_agent_home(home, app_resources)
    srv = IpcServer(host=args.host, port=args.port, home=home)
    srv.start()
    print(f"[Brickery IPC] 监听 {srv.host}:{srv.port}", flush=True)
    # 自动拉起 daemon（记忆整理后台任务）：保证桌面 App 打开即用，
    # 聊天界面不因 daemon 未启动而空白。失败仅告警，不影响核心引擎。
    try:
        srv._h_daemon_start({})
        print("[Brickery IPC] daemon 已自动启动", flush=True)
    except Exception as _e:  # noqa: BLE001
        print(f"[Brickery IPC] daemon 自动启动失败：{_e}", flush=True)
    # 拉起已注册的平台网关连接器（飞书 / Telegram 等；OFF by default，无配置不拉起）。
    # 单连接器故障（如飞书缺 websocket-client）绝不能拖垮整个后端：import / 构造 / 注册
    # 全部隔离，失败仅告警，核心引擎（ipc + 主推理后端）照常运行。
    # BRICKERY_SKIP_CONNECTORS=1 跳过连接器启动（冒烟测试/CI 用）。
    if os.environ.get("BRICKERY_SKIP_CONNECTORS") != "1":
        try:
            from .connectors.feishu import FeishuConnector
            from .connectors.telegram import TelegramConnector
            from .gateway import GatewayRegistry
            GatewayRegistry.register(FeishuConnector(ipc_port=args.port))
            GatewayRegistry.register(TelegramConnector(ipc_port=args.port))
            for _gw in GatewayRegistry.all():
                try:
                    _gw.on_start()
                except Exception as _e:  # noqa: BLE001
                    print(f"[Brickery] 网关 {getattr(_gw, 'name', '?')} 启动失败：{_e}", flush=True)
        except Exception as _e:  # noqa: BLE001
            print(f"[Brickery] 连接器模块加载失败（飞书/Telegram 不可用，不影响核心引擎）：{_e}", flush=True)
    # 优雅退出：SIGTERM（来自宿主 App 的 Process.terminate）/ SIGINT 都收，
    # 关闭监听 socket 与守护进程，避免退出后留下孤儿子进程。
    # 父进程（宿主 App）守护：若宿主被杀/异常退出，本子进程被 reparent，
    # ppid 变化即自杀，绝不遗留孤儿（Daemon 红线）。这是对所有退出场景都有效的兜底。
    _parent_pid = os.getppid()
    _stop = threading.Event()

    def _watchdog():
        while not _stop.is_set():
            time.sleep(2)
            if os.getppid() != _parent_pid:
                srv.stop()
                os._exit(0)

    # launcher 双击启动场景：IPC 作为独立服务存活，不随 launcher 退出自杀。
    # launcher 启动 IPC 时设置 BRICKERY_NO_WATCHDOG=1（launcher 只是启动器，
    # 退出后 IPC 被 reparent，ppid 变化会误触发自杀）。Swift 宿主托管路径
    # 不设该变量，watchdog 照常生效（宿主退出 → 清理子进程）。
    if os.environ.get("BRICKERY_NO_WATCHDOG") != "1":
        threading.Thread(target=_watchdog, daemon=True).start()

    def _sig_handler(signum, frame):
        # 先停网关连接器（飞书等），再停引擎二进制，最后停 IPC 服务
        from .gateway import GatewayRegistry
        for _gw in GatewayRegistry.all():
            try:
                _gw.on_stop()
            except Exception:  # noqa: BLE001
                pass
        # 关闭所有由 BinaryManager 跟踪的引擎进程（SIGTERM -> SIGKILL，不留孤儿）
        try:
            from .binary_manager import shutdown_all
            cleaned = shutdown_all()
            if cleaned:
                print(f"[Brickery] 已清理 {cleaned} 个引擎子进程", flush=True)
        except Exception:  # noqa: BLE001
            pass
        srv.stop()
        _stop.set()

    signal.signal(signal.SIGTERM, _sig_handler)
    signal.signal(signal.SIGINT, _sig_handler)
    try:
        while not _stop.is_set():
            _stop.wait(3600)
    except KeyboardInterrupt:
        srv.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
