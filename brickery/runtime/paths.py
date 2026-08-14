"""Brickery 路径与运行时根目录管理（自 Shadeling config/paths.py 迁入，B1 纯数据层）。

所有持久化位置统一由环境变量 BRICKERY_HOME 派生，默认 ~/.brickery。
不硬编码任何外部框架路径，也不引用任何既有 agent 项目的目录。
产出 agent 独立运行时使用本模块，与 Shadeling 的 ~/.shadeling 完全隔离。
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_HOME = "BRICKERY_HOME"
DEFAULT_HOME = Path.home() / ".brickery"

ENV_MODELS = "BRICKERY_MODELS"
DEFAULT_MODELS = Path.home() / "brickery-runtime" / "models"

ENV_BACKUP = "BRICKERY_BACKUP_DIR"
DEFAULT_BACKUP = Path.home() / "Documents" / "Brickery" / "Backups"

ENV_OUTPUT = "BRICKERY_OUTPUT_DIR"
DEFAULT_OUTPUT = Path.home() / "Documents" / "Brickery" / "Output"


def get_home() -> Path:
    """返回 Brickery 运行时根目录，不存在则创建。"""
    raw = os.environ.get(ENV_HOME)
    home = Path(raw).expanduser() if raw else DEFAULT_HOME
    home.mkdir(parents=True, exist_ok=True)
    return home


def get_memory_db() -> Path:
    return get_home() / "memory.db"


def get_filing_db() -> Path:
    return get_home() / "filing.db"


def get_consolidation_db() -> Path:
    return get_home() / "consolidation.db"


def get_cabinet_db() -> Path:
    return get_home() / "cabinet.db"


def get_config_dir() -> Path:
    d = get_home() / "config"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_log_dir() -> Path:
    d = get_home() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_cache_dir() -> Path:
    d = get_home() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_models_root() -> Path:
    """模型权重根目录：优先 BRICKERY_MODELS，否则用本项目独立 runtime 目录。

    大模型文件不进仓库 / 不进 iCloud，统一放本机独立 runtime 目录
    （默认 ~/brickery-runtime/models）。本函数不引用任何外部项目路径。
    """
    raw = os.environ.get(ENV_MODELS)
    p = Path(raw).expanduser() if raw else DEFAULT_MODELS
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_backup_dir() -> Path:
    """用户数据备份目录：优先 BRICKERY_BACKUP_DIR，否则 ~/Documents/Brickery/Backups。

    不主动创建（首次执行备份时由备份逻辑创建，避免空目录占位）。
    """
    raw = os.environ.get(ENV_BACKUP)
    return Path(raw).expanduser() if raw else DEFAULT_BACKUP


def get_output_dir() -> Path:
    """文档产出目录：优先 BRICKERY_OUTPUT_DIR，否则 ~/Documents/Brickery/Output。

    确保存在，便于 DocWritePro 等直接写入用户能找到的位置。
    """
    raw = os.environ.get(ENV_OUTPUT)
    p = Path(raw).expanduser() if raw else DEFAULT_OUTPUT
    p.mkdir(parents=True, exist_ok=True)
    return p
