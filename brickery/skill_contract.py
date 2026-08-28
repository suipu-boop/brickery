"""积木契约（Skill 数据类）· 平台侧共享契约。

从 Shadeling `runtime/skills.py` 提取的**纯数据契约**：Skill 是 brick.json 的
dataclass 直映射（P0 契约），平台（brickery）与宿主内核（Shadeling）通过同一份
brick.json schema 对齐，本文件只保留数据定义，不含 SkillRegistry 的
save/load 副作用逻辑（那是宿主内核的职责）。

迁移说明：Shadeling 内核保留自己的 skills.py（含 SkillRegistry 匹配/落盘），
brickery 用本契约做静态组装与动态激活协议；两边字段以 brick.json 为唯一事实源。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class Skill:
    name: str
    trigger: List[str]          # 触发关键词
    content: str = ""           # 注入主循环的完整上下文提示（A+B：按需展开）
    disabled: bool = False
    # —— 以下为 marketplace / 分级注入扩展字段，全部可选，向后兼容旧 skills.json ——
    summary: str = ""           # 一句话描述：UI 展示 + A+B 轻量注入（替代全量 content）
    version: str = ""           # 语义化版本，如 "1.0.0"
    author: str = ""            # 作者 / 来源署名
    description: str = ""       # 较长介绍（UI 详情用）
    category: str = ""          # 分类（UI 分组/标签）
    tags: List[str] = field(default_factory=list)
    license: str = ""           # 许可证标识，如 "MIT"
    source: str = ""            # 来源：""=本地手写；否则为库 id 或源 URL（provenance）
    installed_at: str = ""      # 安装时间戳（ISO），仅 marketplace 安装写入
    provides_tool: str = ""     # 声明携带的内置工具名；非空时从 ToolProviderRegistry 取 handler
    # —— 二进制扩展（市场高配技能）——
    binary_url: str = ""        # 二进制下载地址（http/https/file）
    binary_size: int = 0        # 字节；0=未声明
    binary_sha256: str = ""     # 可选校验和（策展源应提供）
    binary_launch: dict = field(default_factory=dict)  # 启动配置

    # —— P0 brick 契约新增字段（缺省安全，向后兼容旧 skills.json）——
    capabilities: List[str] = field(default_factory=list)   # 能力标签（机器匹配）
    dependencies: List[dict] = field(default_factory=list)  # 依赖声明（依赖体检）
    resources: dict = field(default_factory=dict)           # 资源需求（冲突检测）
    risk_level: str = "low"                                  # 风险分级
    composition: dict = field(default_factory=dict)          # 组合规则 + 记忆域归属

    # —— UI 注册扩展（Step1：积木自带按钮 + 导航动态分区，缺省安全）——
    buttons: List[dict] = field(default_factory=list)  # UI 按钮卡：{label, action, args?, view?}
    views: List[dict] = field(default_factory=list)    # 动态分区视图：{nav_title, view_id, handler, icon?}
