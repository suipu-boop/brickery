"""§6 配置管理（clean room，纯自研；自 Shadeling runtime/config.py 迁入，B1 纯数据层）。

集中管理 BRICKERY_HOME / BRICKERY_MODELS / 推理引擎后端 / 工具技能开关。
所有路径经 paths 派生，绝不硬编码任何外部项目路径。
红线：配置解析不得执行任意代码（仅用 json）；损坏配置安全回退默认 + 告警，不崩溃。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import paths


@dataclass
class EngineConfig:
    """推理引擎配置（随朴 2026-08-06 决策：首推 API 为主、本地为备选）。

    - 默认 backend=api：**用户显式指定的网络端点为首选**（如 DeepSeek / 通义 /
      智谱，国内可直连），质量最高、function-calling 最稳，是首版主力。
    - 本地 GGUF 作为 API 不可用时的**自动降级兜底**（断网 / 额度耗尽 / 鉴权失败），
      隐私安全、不出本机。
    - api_url / api_key / api_model 仅在用户**显式填写**时非空。
    - 红线（更新）：API 端点必须用户显式填写（不硬编码任何第三方推理地址）；
      本地 GGUF 仅作降级兜底，不偷偷外传记忆/内容；两个后端都不可用才抛
      NoEngineConfigured，绝不静默连外网。
    """
    backend: str = "api"             # api | local（api=首选，local=降级兜底）
    local_model: str = ""             # GGUF 文件名（相对 models_root）或绝对路径
    api_url: str = ""                 # 仅当用户显式填写时非空
    api_key: str = ""
    api_model: str = ""
    api_name: str = ""                # App 内显示名称（仅 UI 展示，不参与推理）


@dataclass
class NightlyConfig:
    """空闲记忆整理配置（§7）——「骨架常开 + 归纳默认开」两层设计（2026-08-09 改）。

    - enabled: 空闲整理总开关（默认开）。开启后，骨架整理**无需任何模型**即可运行：
        自动聚类压实、孤儿语义簇清理、共现噪声瘦身、过期冷标记。零资源、零隐私顾虑、
        永不崩溃、可完全离线。这是记忆「不丢、有结构、可检索」的基础保障。
    - induction_backend: 归纳引擎后端（\"api\" 优先走网络 API / \"local\" 强制本地 GGUF /
        \"auto\" 先试 API 再降级本地，默认 \"api\"）。归纳默认走与主对话同后端，
        API 不可用时自动降级为本地 fallback；两者皆不可用则退化为纯规则骨架整理。
        月均 API 归纳开销约 ¥0.1–1.2（空闲触发 2-10 次/天，约 800 输入+150 输出 token/次）。
    - use_local_model: 【2026-08-09 废弃】保留向后兼容，归纳引擎开关由 induction_backend 控制。
        设为 False 时 induction_backend 自动退化为 None（纯骨架）。
    - local_model: 用于归纳的本地模型路径；为空则复用 EngineConfig.local_model。
    - idle_minutes: 「空闲整理」门槛（分钟）。桌面 App 没有真正的「夜间」——用户关掉
        App 就不存在夜间；因此改以**空闲时长**为触发条件：距上次对话活动不足该时长
        时，守护轮询直接跳过（不开库、不推理）。红线：整理绝不与前台对话抢资源。
    - auto_core_fill: 固定核智能槽自动填充（2026-08-09 新增，默认开）。归纳引擎在空闲
        整理时自动检测「高置信规律」（重复≥2次的项目名/偏好/习惯用语），写入智能槽；
        中置信推候选→UI 待确认区；高敏感（密码/私钥格式）硬拦截。
    """
    enabled: bool = True
    induction_backend: str = "api"   # api | local | auto (auto=先 API 后本地)
    use_local_model: bool = True     # 【废弃】保留向后兼容，由 induction_backend 控制
    local_model: str = ""
    idle_minutes: float = 5.0
    auto_core_fill: bool = True      # 🆕 固定核智能槽自动填充


@dataclass
class Config:
    home: Path
    models_root: Path
    engine: EngineConfig = field(default_factory=EngineConfig)
    nightly: NightlyConfig = field(default_factory=NightlyConfig)
    tools_enabled: bool = True
    skills_enabled: bool = True
    mode: str = "normal"                     # 执行模式：normal / plan / accept_edits
    max_context_tokens: int = 8192          # n_ctx 治理：工具结果上限不得超此预算
    scheduler_max_workers: int = 2          # P2 调度内核 worker 池大小（M4 并发推理兜底）
    skill_repo_url: str = ""                # 在线技来源地址（空=不连市场；file:// 或 http(s)://）
    open_session_context: bool = True       # 新会话开场主动回顾近期上下文（消灭失忆感；可关）
    backup_dir: Path = field(default_factory=lambda: Path.home() / "Documents" / "Brickery" / "Backups")
    output_dir: Path = field(default_factory=lambda: Path.home() / "Documents" / "Brickery" / "Output")
    # 多模型预设：每个预设是一组完整引擎配置（id/name/backend/...）。
    # active_profile_id 指向当前生效预设；engine 字段始终等于 active 预设的镜像，
    # 保证 EngineRouter / status / 旧式单组读取向后兼容。
    profiles: list = field(default_factory=list)
    active_profile_id: str = "default"
    chat_model: str = ""                    # 会话栏选用的模型（空=后端默认；重启须恢复）
    bricks_enabled: bool = False            # P8 积木装配总开关（默认关；true 时装配引擎+记忆积木）

    @property
    def config_file(self) -> Path:
        return self.home / "config.json"

    def save(self) -> None:
        """写入 config.json（首次运行生成带默认值的模板）。"""
        # 迁移兜底：内存 profiles 为空（老用户首次保存）时，从当前 engine 生成默认预设，
        # 保证「多预设」升级不破坏老用户的单绑定配置。
        if not self.profiles:
            self.profiles = [_engine_to_profile(self.engine, "default", "默认")]
            self.active_profile_id = "default"
        data = {
            "engine": {
                "backend": self.engine.backend,
                "local_model": self.engine.local_model,
                "api_url": self.engine.api_url,
                "api_key": self.engine.api_key,
                "api_model": self.engine.api_model,
                "api_name": self.engine.api_name,
            },
            "nightly": {
                "enabled": self.nightly.enabled,
                "use_local_model": self.nightly.use_local_model,
                "local_model": self.nightly.local_model,
                "idle_minutes": self.nightly.idle_minutes,
            },
            "tools_enabled": self.tools_enabled,
            "skills_enabled": self.skills_enabled,
            "mode": self.mode,
            "max_context_tokens": self.max_context_tokens,
            "scheduler_max_workers": self.scheduler_max_workers,
            "skill_repo_url": self.skill_repo_url,
            "open_session_context": self.open_session_context,
            "backup_dir": str(self.backup_dir),
            "output_dir": str(self.output_dir),
            "profiles": self.profiles,
            "active_profile_id": self.active_profile_id,
            "chat_model": self.chat_model,
            "bricks_enabled": self.bricks_enabled,
        }
        self.config_file.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                    encoding="utf-8")


def load_config(home: Optional[Path] = None,
                models_root: Optional[Path] = None) -> Config:
    """加载配置：env > 显式参数 > 项目默认路径。

    损坏 / 非法 JSON 被检测并回退到安全默认 + 告警，不崩溃、不连外网。
    """
    home_path = Path(home) if home else paths.get_home()
    models_path = Path(models_root) if models_root else paths.resolve_models_root()

    engine = EngineConfig()
    nightly = NightlyConfig()
    tools_enabled = True
    skills_enabled = True
    mode = "normal"
    max_context_tokens = 8192
    scheduler_max_workers = 2
    skill_repo_url = ""
    open_session_context = True
    chat_model = ""
    bricks_enabled = False
    backup_dir = paths.get_backup_dir()
    output_dir = paths.get_output_dir()
    profiles = []                      # 多模型预设（list[dict]）；空=首次运行，下方迁移兜底
    active_profile_id = "default"

    cfg_file = home_path / "config.json"
    if cfg_file.exists():
        try:
            raw = json.loads(cfg_file.read_text(encoding="utf-8"))
            eng = raw.get("engine", {})
            engine.backend = eng.get("backend", "api")
            engine.local_model = eng.get("local_model", "")
            engine.api_url = eng.get("api_url", "")
            engine.api_key = eng.get("api_key", "")
            engine.api_model = eng.get("api_model", "")
            engine.api_name = eng.get("api_name", "")
            ny = raw.get("nightly", {})
            nightly.enabled = bool(ny.get("enabled", True))
            nightly.use_local_model = bool(ny.get("use_local_model", False))
            nightly.local_model = ny.get("local_model", "")
            try:
                nightly.idle_minutes = max(0.0, float(ny.get("idle_minutes", 5.0)))
            except (TypeError, ValueError):
                nightly.idle_minutes = 5.0
            tools_enabled = raw.get("tools_enabled", True)
            skills_enabled = raw.get("skills_enabled", True)
            bricks_enabled = bool(raw.get("bricks_enabled", False))
            mode = raw.get("mode", "normal")
            try:
                max_context_tokens = max(256, int(raw.get("max_context_tokens", 8192)))
            except (TypeError, ValueError):
                max_context_tokens = 8192
            try:
                scheduler_max_workers = max(1, int(raw.get("scheduler_max_workers", 2)))
            except (TypeError, ValueError):
                scheduler_max_workers = 2
            skill_repo_url = raw.get("skill_repo_url", "")
            open_session_context = bool(raw.get("open_session_context", True))
            chat_model = raw.get("chat_model", "")
            backup_dir = Path(raw.get("backup_dir", str(backup_dir))).expanduser()
            output_dir = Path(raw.get("output_dir", str(output_dir))).expanduser()
            # 多预设：解析 profiles；空则下方迁移（用现有 engine 生成默认预设）
            profiles_raw = raw.get("profiles", [])
            active_id = raw.get("active_profile_id", "default")
            if profiles_raw and isinstance(profiles_raw, list):
                profiles = profiles_raw
                if not any(isinstance(p, dict) and p.get("id") == active_id for p in profiles):
                    active_id = profiles[0].get("id", "default")
            else:
                profiles = [_engine_to_profile(engine, "default", "默认")]
                active_id = "default"
        except (json.JSONDecodeError, OSError, ValueError, AttributeError) as e:
            # 红线：损坏配置不得崩溃，回退默认 + 告警
            print(f"[Brickery 配置告警] 读取 config.json 失败，使用安全默认：{e}")

    # 兜底迁移：任何情况下 profiles 为空都从当前 engine 生成默认预设，绝不丢配置
    if not profiles:
        profiles = [_engine_to_profile(engine, "default", "默认")]
        active_profile_id = "default"

    return Config(home=home_path, models_root=models_path, engine=engine,
                  nightly=nightly,
                  tools_enabled=tools_enabled, skills_enabled=skills_enabled,
                  mode=mode, max_context_tokens=max_context_tokens,
                  scheduler_max_workers=scheduler_max_workers,
                  open_session_context=open_session_context,
                  backup_dir=backup_dir, output_dir=output_dir,
                  profiles=profiles, active_profile_id=active_profile_id,
                  chat_model=chat_model, bricks_enabled=bricks_enabled)


def _engine_to_profile(engine: EngineConfig, pid: str, name: str) -> dict:
    """把单组 EngineConfig 转换成「模型预设」字典（多预设体系的基础单元）。"""
    return {
        "id": pid,
        "name": name,
        "backend": engine.backend,
        "local_model": engine.local_model,
        "api_url": engine.api_url,
        "api_key": engine.api_key,
        "api_model": engine.api_model,
        "api_name": engine.api_name,
    }
