"""BrickLike 运行时 · P4 动态激活层。

把静态组装方案（AssemblyPlan）里的积木，按 `specs/brick-runtime.md` 契约真正
「激活」进内核。四种积木形态归一成一个生命周期协议，委托内核现有机制，不另起
炉灶：

| 适配器         | 委托机制                                             |
|----------------|------------------------------------------------------|
| PromptBrick    | SkillRegistry（Skill.content + match / register）     |
| ConnectorBrick | Gateway.on_start / on_stop（FeishuConnector 等）      |
| ToolBrick      | Skill.provides_tool → ToolProviderRegistry → Tool     |
| BinaryBrick    | Skill.binary_launch → BinaryManager                   |

当前 6 个积木：ax / visualize / browser 属 PromptBrick，feishu 属
ConnectorBrick，docwrite 属 ToolBrick（provides_tool="DocWrite"），
BinaryBrick 待 DocWritePro 等二进制引擎积木化时使用。四型适配器齐备，
均委托内核现有机制，不重造轮子。

零新增依赖；仅 import 内核的 skills 模块，Tool/Binary 依赖惰性 import。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from .skill_contract import Skill


def _host_import(module: str, *attrs):
    """从本包（brickery.runtime）惰性导入能力。

    brickery 是「平台」，动态激活委托本包运行时机制（registry / factory）。
    优先尝试本包 <module>；不可用时返回 None，由调用方报「运行时未提供该能力」。
    """
    cand = f"{__package__}.{module}"
    try:
        mod = __import__(cand, fromlist=attrs)
        got = tuple(getattr(mod, a) for a in attrs)
        if len(got) == 1:
            return got[0]
        return got
    except (ImportError, AttributeError):
        return None

# Skill 数据类已含 brick.json 的全部字段（P0 契约），直映射即可。
_SKILL_FIELDS = set(Skill.__dataclass_fields__.keys())


class BrickState(str, Enum):
    UNLOADED = "unloaded"
    ACTIVE = "active"
    FAILED = "failed"
    DEACTIVATED = "deactivated"


@dataclass
class BrickResult:
    """BrickLike 统一返回：{ ok, data, error }（见 brick-runtime 契约）。"""

    ok: bool
    data: Any = None
    error: str = ""

    def as_dict(self) -> dict:
        return {"ok": self.ok, "data": self.data, "error": self.error}


@runtime_checkable
class BrickLike(Protocol):
    """统一积木生命周期：内核只面对这个接口。"""

    name: str
    state: BrickState

    def install(self, ctx: Optional[dict] = None) -> BrickResult: ...
    def activate(self, ctx: Optional[dict] = None) -> BrickResult: ...
    def invoke(self, req: dict) -> BrickResult: ...
    def health(self) -> BrickResult: ...
    def deactivate(self) -> BrickResult: ...


# --------------------------------------------------------------------------- #
# 形态一：PromptBrick —— 提示词积木（ax / visualize / browser）
# --------------------------------------------------------------------------- #
class PromptBrick:
    """包裹一个 Skill，委托 SkillRegistry 消费。

    activate = 注册进 SkillRegistry；invoke 无独立语义（由模型经 match 注入
    主循环后消费 content）；deactivate = 停用（match 不再命中）。
    """

    def __init__(self, skill: Skill, registry) -> None:
        self.name = skill.name
        self.skill = skill
        self._registry = registry
        self.state = BrickState.UNLOADED

    def install(self, ctx: Optional[dict] = None) -> BrickResult:
        # 内容已在 brick.json 内，落盘由上层负责；此处幂等占位。
        self.state = BrickState.UNLOADED
        return BrickResult(ok=True, data={"note": "content 内联于 brick.json，无独立安装步"})

    def activate(self, ctx: Optional[dict] = None) -> BrickResult:
        self._registry.register(self.skill)
        self.state = BrickState.ACTIVE
        return BrickResult(ok=True, data={"name": self.name, "registered": True})

    def invoke(self, req: dict) -> BrickResult:
        # 契约：prompt 型「由模型消费，无需直接 invoke」。
        return BrickResult(
            ok=True,
            data={"mode": "injected",
                  "note": "prompt 型积木经 SkillRegistry.match 注入主循环，无独立 invoke"},
        )

    def health(self) -> BrickResult:
        sk = self._registry.get(self.name)
        if sk is None:
            self.state = BrickState.FAILED
            return BrickResult(ok=False, error="未注册进 SkillRegistry")
        if sk.disabled:
            return BrickResult(ok=False, data={"status": "disabled"})
        return BrickResult(ok=True, data={"status": "active", "triggers": len(sk.trigger) or 0})

    def deactivate(self) -> BrickResult:
        self._registry.set_disabled(self.name, True)
        self.state = BrickState.DEACTIVATED
        return BrickResult(ok=True, data={"name": self.name, "disabled": True})


# --------------------------------------------------------------------------- #
# 形态二：ConnectorBrick —— 常驻连接器（feishu）
# --------------------------------------------------------------------------- #
class ConnectorBrick:
    """包裹一个 Gateway 型连接器，委托其 on_start / on_stop 生命周期。

    连接器对象通过 connector_factory(name) 惰性构造，避免 import 本模块时
    硬依赖 lark-oapi 等外部 SDK。连接器只需暴露 on_start / on_stop，以及
    供 health 读取的 config（enabled 字段）与 _thread（线程对象）。
    """

    def __init__(self, name: str, connector_factory=None) -> None:
        self.name = name
        self._factory = connector_factory
        self._connector: Any = None
        self.state = BrickState.UNLOADED

    def _ensure_connector(self) -> Optional[Any]:
        if self._connector is None and self._factory is not None:
            try:
                self._connector = self._factory(self.name)
            except Exception as e:  # noqa: BLE001 —— 故障域隔离，不拖死组装
                return None
        return self._connector

    def install(self, ctx: Optional[dict] = None) -> BrickResult:
        # 依赖（如 lark-oapi）由内核装配，此处仅校验工厂可用。
        if self._factory is None:
            return BrickResult(ok=False, error="未注入 connector_factory")
        return BrickResult(ok=True, data={"note": "依赖由内核装配，无独立安装步"})

    def activate(self, ctx: Optional[dict] = None) -> BrickResult:
        conn = self._ensure_connector()
        if conn is None:
            self.state = BrickState.FAILED
            return BrickResult(ok=False, error="连接器构造失败（缺 SDK 或凭据）")
        try:
            conn.on_start()
            self.state = BrickState.ACTIVE
            return BrickResult(ok=True, data={"name": self.name, "started": True})
        except Exception as e:  # noqa: BLE001
            self.state = BrickState.FAILED
            return BrickResult(ok=False, error=f"启动失败：{e}")

    def invoke(self, req: dict) -> BrickResult:
        # 消息投递由连接器内部事件循环（run_loop）处理，无独立 invoke 入口。
        return BrickResult(
            ok=False, error="连接器型积木无独立 invoke：消息投递由内部事件循环承接")

    def health(self) -> BrickResult:
        conn = self._connector
        if conn is None:
            return BrickResult(ok=False, data={"status": "unbuilt"}, error="连接器未构造")
        cfg = getattr(conn, "config", None)
        enabled = getattr(cfg, "enabled", True) if cfg is not None else True
        if not enabled:
            # 配置层停用是预期状态，不算故障。
            return BrickResult(ok=True, data={"status": "disabled-by-config"})
        thread = getattr(conn, "_thread", None)
        if thread is None:
            return BrickResult(ok=False, data={"status": "not-started"})
        if thread.is_alive():
            return BrickResult(ok=True, data={"status": "running"})
        self.state = BrickState.FAILED
        return BrickResult(ok=False, data={"status": "dead"}, error="连接线程已退出")

    def deactivate(self) -> BrickResult:
        conn = self._connector
        if conn is not None:
            try:
                conn.on_stop()
            except Exception as e:  # noqa: BLE001
                return BrickResult(ok=False, error=f"停止失败：{e}")
        self.state = BrickState.DEACTIVATED
        return BrickResult(ok=True, data={"name": self.name, "stopped": True})


# --------------------------------------------------------------------------- #
# 形态三：ToolBrick —— 工具积木（provides_tool，如 docwrite）
# --------------------------------------------------------------------------- #
class ToolBrick:
    """包裹一个声明 provides_tool 的 Skill，委托 ToolProviderRegistry + ToolRegistry。

    activate = 从内置模块池取 handler 注册进工具注册表；invoke = 转发到
    handler；deactivate = 卸载。handler 始终来自 app 内置模块（安全红线）。
    """

    def __init__(self, skill: Skill, tool_registry, home=None) -> None:
        self.name = skill.name
        self.skill = skill
        self._tool_registry = tool_registry
        self._home = home
        self._tool_name = str(skill.provides_tool or "").strip()
        self.state = BrickState.UNLOADED

    def install(self, ctx: Optional[dict] = None) -> BrickResult:
        ToolProviderRegistry = _host_import('tool_providers', "ToolProviderRegistry")
        if not self._tool_name:
            self.state = BrickState.FAILED
            return BrickResult(ok=False, error="provides_tool 为空，不构成 Tool 型积木")
        if self._tool_name not in ToolProviderRegistry.available_names():
            self.state = BrickState.FAILED
            return BrickResult(ok=False, error=f"provides_tool={self._tool_name} 无内置 handler")
        return BrickResult(ok=True, data={"tool": self._tool_name,
                                          "note": "handler 来自内置模块池，无独立安装步"})

    def activate(self, ctx: Optional[dict] = None) -> BrickResult:
        if self._tool_registry is None:
            self.state = BrickState.FAILED
            return BrickResult(ok=False, error="未注入 tool_registry")
        ToolProviderRegistry = _host_import('tool_providers', "ToolProviderRegistry")
        tool = ToolProviderRegistry.get(self._tool_name, home=self._home, skill=self.skill)
        if tool is None:
            self.state = BrickState.FAILED
            return BrickResult(ok=False, error=f"获取工具 {self._tool_name} 失败")
        self._tool_registry.register(tool)
        self.state = BrickState.ACTIVE
        return BrickResult(ok=True, data={"tool": self._tool_name, "registered": True})

    def invoke(self, req: dict) -> BrickResult:
        if self._tool_registry is None:
            return BrickResult(ok=False, error="未注入 tool_registry")
        tool = self._tool_registry.get(self._tool_name)
        if tool is None or tool.handler is None:
            return BrickResult(ok=False, error=f"工具 {self._tool_name} 未注册或无 handler")
        try:
            kwargs = req if isinstance(req, dict) else {"input": req}
            data = tool.handler(**kwargs)
            return BrickResult(ok=True, data=data)
        except Exception as e:  # noqa: BLE001 —— 故障域隔离
            return BrickResult(ok=False, error=f"工具调用失败：{e}")

    def health(self) -> BrickResult:
        if self._tool_registry is None:
            return BrickResult(ok=False, error="未注入 tool_registry")
        if self._tool_registry.get(self._tool_name) is None:
            self.state = BrickState.FAILED
            return BrickResult(ok=False, error="工具未注册")
        return BrickResult(ok=True, data={"status": "active", "tool": self._tool_name})

    def deactivate(self) -> BrickResult:
        if self._tool_registry is not None:
            self._tool_registry.unregister(self._tool_name)
        self.state = BrickState.DEACTIVATED
        return BrickResult(ok=True, data={"tool": self._tool_name, "unregistered": True})


# --------------------------------------------------------------------------- #
# 形态四：BinaryBrick —— 二进制引擎积木（binary_launch，如 DocWritePro）
# --------------------------------------------------------------------------- #
class BinaryBrick:
    """包裹一个声明 binary_launch 的 Skill，委托 BinaryManager 拉起/复用引擎。

    两种情况：
    - 同时声明 provides_tool（如 DocWritePro）：内部组合 ToolBrick，activate 注册
      handler / invoke 转发 handler / deactivate 卸载；引擎由 handler 运行时经
      ensure_engine 拉起并复用（BinaryManager 有复用逻辑，不会重复拉起）。
    - 纯二进制（无 provides_tool）：activate = ensure_running；health = 端口存活
      探测；deactivate = 停引擎；invoke 因协议未定，留协议位。
    """

    def __init__(self, skill: Skill, tool_registry=None, home=None) -> None:
        self.name = skill.name
        self.skill = skill
        self._home = home
        self._port: Optional[int] = None
        self.state = BrickState.UNLOADED
        self._tool_delegate: Optional[ToolBrick] = None
        if str(skill.provides_tool or "").strip():
            self._tool_delegate = ToolBrick(skill, tool_registry, home)

    def install(self, ctx: Optional[dict] = None) -> BrickResult:
        SkillLibrary = _host_import('skill_library', "SkillLibrary")
        try:
            bp = SkillLibrary.binary_path_for(self._home, self.skill)
        except Exception as e:  # noqa: BLE001
            return BrickResult(ok=False, error=f"二进制定位失败：{e}")
        if not bp or not Path(bp).exists():
            self.state = BrickState.FAILED
            return BrickResult(ok=False, error="引擎二进制未就绪（请先安装技能）")
        return BrickResult(ok=True, data={"bin_path": str(bp)})

    def activate(self, ctx: Optional[dict] = None) -> BrickResult:
        # 声明 provides_tool：注册 handler 即激活，引擎由 handler 运行时拉起。
        if self._tool_delegate is not None:
            res = self._tool_delegate.activate(ctx)
            self.state = BrickState.ACTIVE if res.ok else BrickState.FAILED
            return res
        # 纯二进制：显式拉起引擎。
        get_manager = _host_import('binary_manager', "get_manager")
        try:
            self._port, err = get_manager().ensure_running(self._home, self.skill)
        except Exception as e:  # noqa: BLE001
            self._port, err = None, str(e)
        if err:
            self.state = BrickState.FAILED
            return BrickResult(ok=False, error=err)
        self.state = BrickState.ACTIVE
        return BrickResult(ok=True, data={"port": self._port, "started": True})

    def invoke(self, req: dict) -> BrickResult:
        # 声明 provides_tool：转发 handler（handler 内部 ensure_engine 拉起/复用引擎）。
        if self._tool_delegate is not None:
            return self._tool_delegate.invoke(req)
        # 纯二进制：具体协议由二进制决定（如 editor_sdk 走 MCP），留协议位。
        return BrickResult(
            ok=False, error="BinaryBrick invoke 协议位：经端口/IPC 发请求，待二进制协议落地")

    def health(self) -> BrickResult:
        if self._tool_delegate is not None:
            return self._tool_delegate.health()
        if self._port is None:
            return BrickResult(ok=False, data={"status": "not-started"})
        get_manager = _host_import('binary_manager', "get_manager")
        alive = get_manager()._is_alive(self._port)
        if alive:
            return BrickResult(ok=True, data={"status": "running", "port": self._port})
        self.state = BrickState.FAILED
        return BrickResult(ok=False, data={"status": "dead", "port": self._port})

    def deactivate(self) -> BrickResult:
        if self._tool_delegate is not None:
            self._tool_delegate.deactivate()
        get_manager = _host_import('binary_manager', "get_manager")
        get_manager().shutdown_all()
        self._port = None
        self.state = BrickState.DEACTIVATED
        return BrickResult(ok=True, data={"name": self.name, "stopped": True})


# --------------------------------------------------------------------------- #
# 形态五：EngineBrick —— 推理后端积木（engine_kind=local / api）
# --------------------------------------------------------------------------- #
class EngineBrick:
    """推理后端积木，委托 EngineProviderRegistry + EngineRouter 接入。

    activate = EngineProviderRegistry.build(kind, config) 产出 EngineLike，
              经 EngineRouter.set_engine(kind, engine) 接入槽位；
    deactivate = EngineRouter.clear_engine(kind) 回退。
    首选后端（config.backend）由用户显式选择，积木不越权改（铁律：不携带端点）。
    """

    def __init__(self, raw: dict, engine_router=None, engine_config=None,
                 home=None, engine_factory=None) -> None:
        self.name = str(raw.get("name") or "").strip()
        self.engine_kind = str(raw.get("engine_kind") or "").strip()
        self._router = engine_router
        self._config = engine_config
        self._home = home
        self._factory = engine_factory
        self._engine = None
        self.state = BrickState.UNLOADED

    def install(self, ctx: Optional[dict] = None) -> BrickResult:
        EngineProviderRegistry = _host_import('engine_providers', "EngineProviderRegistry")
        if not self.engine_kind:
            self.state = BrickState.FAILED
            return BrickResult(ok=False, error="engine_kind 为空，不构成 Engine 型积木")
        if self.engine_kind not in EngineProviderRegistry.available_kinds():
            self.state = BrickState.FAILED
            return BrickResult(
                ok=False,
                error=f"engine_kind={self.engine_kind} 无内置构建器"
                      f"（可用：{EngineProviderRegistry.available_kinds()}）")
        return BrickResult(ok=True, data={
            "engine_kind": self.engine_kind,
            "note": "构建器来自内置引擎池，无独立安装步"})

    def activate(self, ctx: Optional[dict] = None) -> BrickResult:
        if self._router is None:
            self.state = BrickState.FAILED
            return BrickResult(ok=False, error="未注入 engine_router")
        EngineConfig = _host_import('config', "EngineConfig")
        EngineProviderRegistry = _host_import('engine_providers', "EngineProviderRegistry")
        cfg = self._config if self._config is not None else EngineConfig()
        # 优先走注入的 engine_factory（复用内核单例，避免装配路径与 _cached 缓存分裂），
        # factory 未注入 / 抛错 / 返回 None 时，退回注册表独立构建。
        engine = None
        if self._factory is not None:
            try:
                engine = self._factory(self.engine_kind)
            except Exception:  # noqa: BLE001 —— 工厂失败回落注册表
                engine = None
        if engine is None:
            engine = EngineProviderRegistry.build(self.engine_kind, cfg)
        if engine is None:
            self.state = BrickState.FAILED
            return BrickResult(ok=False, error=f"引擎 {self.engine_kind} 构建失败")
        try:
            self._router.set_engine(self.engine_kind, engine)
        except Exception as e:  # noqa: BLE001
            self.state = BrickState.FAILED
            return BrickResult(ok=False, error=f"引擎接入失败：{e}")
        self._engine = engine
        self.state = BrickState.ACTIVE
        return BrickResult(ok=True, data={
            "engine_kind": self.engine_kind, "attached": True})

    def invoke(self, req: dict) -> BrickResult:
        if self._engine is None:
            return BrickResult(ok=False, error="引擎未激活")
        _invoke = _host_import('engine_router', "_invoke")
        try:
            prompt = req.get("prompt", "") if isinstance(req, dict) else str(req)
            text = _invoke(self._engine, prompt)
            return BrickResult(ok=True, data={"text": text})
        except Exception as e:  # noqa: BLE001 —— 故障域隔离
            return BrickResult(ok=False, error=f"推理调用失败：{e}")

    def health(self) -> BrickResult:
        if self._engine is None:
            self.state = BrickState.FAILED
            return BrickResult(ok=False, error="引擎未接入")
        _engine_available = _host_import('engine_router', "_engine_available")
        if not _engine_available(self._engine):
            self.state = BrickState.FAILED
            return BrickResult(ok=False, data={"status": "unavailable"})
        return BrickResult(ok=True, data={
            "status": "available", "engine_kind": self.engine_kind})

    def deactivate(self) -> BrickResult:
        if self._router is not None:
            try:
                self._router.clear_engine(self.engine_kind)
            except Exception:  # noqa: BLE001
                pass
        self._engine = None
        self.state = BrickState.DEACTIVATED
        return BrickResult(ok=True, data={
            "engine_kind": self.engine_kind, "detached": True})


# --------------------------------------------------------------------------- #
# 形态六：MemoryBrick —— 记忆能力积木（memory_kind=core/portrait/...）
# --------------------------------------------------------------------------- #
class MemoryBrick:
    """记忆能力积木，委托 MemoryCapabilityRegistry + MemoryHost 接入。

    activate = MemoryHost.install_kind(memory_kind) 注册该能力的方法组；
    deactivate = MemoryHost.uninstall_kind(memory_kind) 摘除，方法变为不可用桩。
    memory-core 的 memory_kind="core" 一次性注册 archive+recall+surface 三能力。
    与 EngineBrick 同构：积木只声明 memory_kind，能力实现全在内核 memory/。
    """

    def __init__(self, raw: dict, memory_host=None, home=None) -> None:
        self.name = str(raw.get("name") or "").strip()
        self.memory_kind = str(raw.get("memory_kind") or "").strip()
        self._host = memory_host
        self._home = home
        self.state = BrickState.UNLOADED

    def install(self, ctx: Optional[dict] = None) -> BrickResult:
        MemoryCapabilityRegistry = _host_import('memory_providers', "MemoryCapabilityRegistry")
        if not self.memory_kind:
            self.state = BrickState.FAILED
            return BrickResult(ok=False, error="memory_kind 为空，不构成 Memory 型积木")
        if self.memory_kind not in MemoryCapabilityRegistry.available_kinds():
            self.state = BrickState.FAILED
            return BrickResult(
                ok=False,
                error=f"memory_kind={self.memory_kind} 无内置能力组"
                      f"（可用：{MemoryCapabilityRegistry.available_kinds()}）")
        return BrickResult(ok=True, data={
            "memory_kind": self.memory_kind,
            "note": "能力组来自内核记忆池，无独立安装步"})

    def activate(self, ctx: Optional[dict] = None) -> BrickResult:
        if self._host is None:
            self.state = BrickState.FAILED
            return BrickResult(ok=False, error="未注入 memory_host")
        try:
            self._host.install_kind(self.memory_kind)
        except Exception as e:  # noqa: BLE001 —— 故障域隔离
            self.state = BrickState.FAILED
            return BrickResult(ok=False, error=f"记忆能力接入失败：{e}")
        self.state = BrickState.ACTIVE
        return BrickResult(ok=True, data={
            "memory_kind": self.memory_kind,
            "installed": self._host.has_kind(self.memory_kind)})

    def invoke(self, req: dict) -> BrickResult:
        """记忆积木非工具，invoke 仅做能力自检，不承载对话调用。"""
        if self._host is None or not self._host.has_kind(self.memory_kind):
            return BrickResult(ok=False, error="记忆能力未激活")
        MemoryCapabilityRegistry = _host_import('memory_providers', "MemoryCapabilityRegistry")
        return BrickResult(ok=True, data={
            "memory_kind": self.memory_kind,
            "methods": MemoryCapabilityRegistry.methods_of(self.memory_kind)})

    def health(self) -> BrickResult:
        if self._host is None:
            self.state = BrickState.FAILED
            return BrickResult(ok=False, error="未注入 memory_host")
        if not self._host.has_kind(self.memory_kind):
            return BrickResult(ok=False, data={"status": "uninstalled"})
        return BrickResult(ok=True, data={
            "status": "installed", "memory_kind": self.memory_kind})

    def deactivate(self) -> BrickResult:
        if self._host is not None:
            try:
                self._host.uninstall_kind(self.memory_kind)
            except Exception:  # noqa: BLE001
                pass
        self.state = BrickState.DEACTIVATED
        return BrickResult(ok=True, data={
            "memory_kind": self.memory_kind, "uninstalled": True})


# --------------------------------------------------------------------------- #
# 形态七：ServiceBrick —— 常驻服务积木（service_kind=vault 等）
# --------------------------------------------------------------------------- #
class ServiceBrick:
    """常驻服务积木，委托内核内置服务（VaultStore 等）接入。

    activate = 挂载服务实例 + 注册服务工具组（如 vault_query）；
    deactivate = 摘除工具注册，服务实例保留（数据不销毁）。
    与 MemoryBrick 同构：积木只声明 service_kind，服务实现全在内核 runtime/。
    """

    _SERVICE_FACTORIES = {
        "vault": "_make_vault_service",
    }

    def __init__(self, raw: dict, tool_registry=None, home=None) -> None:
        self.name = str(raw.get("name") or "").strip()
        self.service_kind = str(raw.get("service_kind") or "").strip()
        self._tool_registry = tool_registry
        self._home = home
        self._service = None
        self.state = BrickState.UNLOADED

    def install(self, ctx: Optional[dict] = None) -> BrickResult:
        if not self.service_kind:
            self.state = BrickState.FAILED
            return BrickResult(ok=False, error="service_kind 为空，不构成 Service 型积木")
        if self.service_kind not in self._SERVICE_FACTORIES:
            self.state = BrickState.FAILED
            return BrickResult(
                ok=False,
                error=f"service_kind={self.service_kind} 无内置服务"
                      f"（可用：{sorted(self._SERVICE_FACTORIES)}）")
        return BrickResult(ok=True, data={
            "service_kind": self.service_kind,
            "note": "服务实现来自内核 runtime/，无独立安装步"})

    def activate(self, ctx: Optional[dict] = None) -> BrickResult:
        if self._tool_registry is None:
            self.state = BrickState.FAILED
            return BrickResult(ok=False, error="未注入 tool_registry")
        try:
            factory = getattr(self, self._SERVICE_FACTORIES[self.service_kind])
            self._service = factory()
            self._register_tools()
        except Exception as e:  # noqa: BLE001 —— 故障域隔离
            self.state = BrickState.FAILED
            return BrickResult(ok=False, error=f"服务接入失败：{e}")
        self.state = BrickState.ACTIVE
        return BrickResult(ok=True, data={
            "service_kind": self.service_kind,
            "service": self._service is not None})

    def _make_vault_service(self):
        VaultStore = _host_import('vault_store', "VaultStore")
        return VaultStore()

    def _register_tools(self) -> None:
        """按 service_kind 注册服务工具组（委托 ToolProviderRegistry 工厂）。"""
        ToolProviderRegistry = _host_import('tool_providers', "ToolProviderRegistry")
        if self.service_kind == "vault":
            tool = ToolProviderRegistry.get("VaultQuery", home=self._home)
            if tool is not None:
                self._tool_registry.register(tool)

    def invoke(self, req: dict) -> BrickResult:
        """服务积木非工具，invoke 仅做能力自检，不承载对话调用。"""
        if self._service is None:
            return BrickResult(ok=False, error="服务未激活")
        return BrickResult(ok=True, data={
            "service_kind": self.service_kind,
            "service": type(self._service).__name__})

    def health(self) -> BrickResult:
        if self._service is None:
            self.state = BrickState.FAILED
            return BrickResult(ok=False, error="服务未挂载")
        return BrickResult(ok=True, data={
            "status": "running", "service_kind": self.service_kind})

    def deactivate(self) -> BrickResult:
        if self._tool_registry is not None and self.service_kind == "vault":
            self._tool_registry.unregister("VaultQuery")
        self.state = BrickState.DEACTIVATED
        return BrickResult(ok=True, data={
            "service_kind": self.service_kind, "unregistered": True})


# --------------------------------------------------------------------------- #
# 工厂与编排器
# --------------------------------------------------------------------------- #
def skill_from_brick(raw: dict) -> Skill:
    """brick.json → Skill：只取 Skill 已知字段，缺省走默认值。"""
    return Skill(**{k: v for k, v in raw.items() if k in _SKILL_FIELDS})


def build_brick(raw: dict, skills_registry, connector_factory=None,
                tool_registry=None, home=None,
                engine_router=None, engine_config=None,
                memory_host=None, engine_factory=None) -> BrickLike:
    """按形态判定构造对应适配器（优先级从高到低）。

    Memory（memory_kind）→ Service（service_kind）→ Engine（engine_kind）→
    Binary（binary_launch / binary_url）→ Tool（provides_tool）→
    Connector（category=="connector" 或既无 content 也无 trigger）→
    Prompt（有 content）。
    """
    if raw.get("memory_kind"):
        return MemoryBrick(raw, memory_host, home)
    if raw.get("service_kind"):
        return ServiceBrick(raw, tool_registry, home)
    if raw.get("engine_kind"):
        return EngineBrick(raw, engine_router, engine_config, home, engine_factory)
    if raw.get("binary_launch") or raw.get("binary_url"):
        return BinaryBrick(skill_from_brick(raw), tool_registry, home)
    if raw.get("provides_tool"):
        return ToolBrick(skill_from_brick(raw), tool_registry, home)
    name = str(raw.get("name") or "").strip()
    is_connector = raw.get("category") == "connector" or (
        not raw.get("content") and not raw.get("trigger"))
    if is_connector:
        return ConnectorBrick(name, connector_factory)
    return PromptBrick(skill_from_brick(raw), skills_registry)


class BrickRuntime:
    """动态激活编排器：读积木完整清单 → 构造适配器 → 按 plan 激活 → 汇总状态。

    与 assembler.load_vault 的分工：后者产出「静态视图 + 方案」；本类产出
    「活着的适配器 + 运行时状态」。静态校验通过后再调用 activate。
    """

    def __init__(self, vault_root: str, skills_registry,
                 connector_factory=None, tool_registry=None, home=None,
                 engine_router=None, engine_config=None,
                 memory_host=None, engine_factory=None,
                 index_file: str = "index.json") -> None:
        self.vault_root = Path(vault_root)
        self.skills_registry = skills_registry
        self.connector_factory = connector_factory
        self.tool_registry = tool_registry
        self.home = home
        self.engine_router = engine_router
        self.engine_config = engine_config
        self.memory_host = memory_host
        self.engine_factory = engine_factory
        self.index_file = index_file
        self.bricks: Dict[str, BrickLike] = {}

    def load(self) -> Dict[str, BrickLike]:
        """读 index.json + 各 brick.json，按形态构造适配器。"""
        index_path = self.vault_root / self.index_file
        if not index_path.exists():
            raise FileNotFoundError(f"清单不存在：{index_path}")
        raw_index = json.loads(index_path.read_text(encoding="utf-8"))
        self.bricks = {}
        for entry in raw_index.get("bricks") or []:
            name = entry.get("name")
            manifest_dir = self.vault_root / (entry.get("path") or f"bricks/{name}/")
            manifest_path = manifest_dir / "brick.json"
            if not manifest_path.exists():
                raise FileNotFoundError(f"积木清单缺失：{manifest_path}")
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.bricks[str(raw.get("name") or name)] = build_brick(
                raw, self.skills_registry, self.connector_factory,
                self.tool_registry, self.home,
                engine_router=self.engine_router,
                engine_config=self.engine_config,
                memory_host=self.memory_host,
                engine_factory=self.engine_factory)
        return self.bricks

    def activate(self, order: List[str]) -> Dict[str, BrickResult]:
        """按拓扑序逐个 activate，返回逐积木结果。"""
        results: Dict[str, BrickResult] = {}
        for name in order:
            brick = self.bricks.get(name)
            if brick is None:
                results[name] = BrickResult(ok=False, error="未装载")
                continue
            results[name] = brick.activate()
        return results

    def status(self) -> Dict[str, dict]:
        """汇总每个积木的 state + health。"""
        out: Dict[str, dict] = {}
        for name, brick in self.bricks.items():
            health = brick.health()
            out[name] = {
                "state": brick.state.value,
                "health": health.as_dict(),
            }
        return out
