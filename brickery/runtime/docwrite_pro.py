"""DocWritePro —— 高配文档生成（走 editor_sdk 引擎，复杂排版）。

朴素版 DocWrite（纯 stdlib）的升级路径：安装「高配文档引擎」技能后，
客户端自动下载 ~193MB editor_sdk 引擎，本工具在运行时拉起引擎，
把 docx/xlsx/pptx 生成需求翻译为 editor_sdk 的 create -> 编辑 -> save 工作流。

handler 参数与 DocWrite 对齐（format/path/title/sections/sheets/slides），
可作为「升级替换」使用。失败一律转成 [DocWritePro] 提示串，不抛异常。

安全：路径经 sandbox 解析（同 DocWrite）；引擎二进制来自策展源且经 SHA256 校验。
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Optional

from .config import load_config


def _resolve_output_dir() -> Path:
    """默认产出目录：取 config.output_dir（失败回退 ~/Documents/Shadeling/Output）。"""
    try:
        return load_config().output_dir
    except Exception:
        return Path.home() / "Documents" / "Shadeling" / "Output"

from .edsdk_pro import (ensure_engine, generate_docx, generate_pptx,
                        generate_xlsx, build_document)
from .sandbox import Sandbox, default_sandbox
from .tools import RiskLevel, Tool


def build_docwrite_pro_tool(home=None, skill=None,
                            sandbox: Optional[Sandbox] = None) -> Tool:
    """工厂：构造 DocWritePro 工具。

    home/skill 由 ipc._sync_skill_tools 在桥接已装技能时按名传入，
    用于运行时定位并启动引擎二进制。sandbox 用于路径安全解析。
    """
    sb = sandbox or default_sandbox()

    def handler(format=None, path=None, template="business-blue", title="",
                sections=None, sheets=None, slides=None, commands=None, **_):
        fmt = (format or "docx").lower()
        if fmt not in ("docx", "xlsx", "pptx"):
            return f"[DocWritePro][错误] 不支持的格式：{fmt}"
        # 路径解析：空 → 默认产出目录；相对 → 相对产出目录；绝对 → 直接用
        if not path:
            out_dir = _resolve_output_dir()
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = str(out_dir / f"DocWritePro_{ts}.{fmt}")
        else:
            p = Path(path).expanduser()
            if not p.is_absolute():
                path = str(_resolve_output_dir() / p)
        ok, reason = sb.check_path_write(path)
        if not ok:
            return f"[DocWritePro][沙箱拒绝] {reason}"
        out = Path(path)
        if home is None or skill is None:
            return "[DocWritePro][错误] 工具未绑定技能上下文（请重新安装技能）"
        port, err = ensure_engine(home, skill)
        if err:
            return f"[DocWritePro][引擎启动失败] {err}"
        try:
            if commands is not None:
                # 命令驱动模式：commands 可为 list[dict] 或 JSON 字符串
                cmds = commands
                if isinstance(cmds, str):
                    cmds = json.loads(cmds)
                if not isinstance(cmds, list):
                    return "[DocWritePro][错误] commands 必须是命令数组"
                if fmt == "docx":
                    kind = "doc"
                elif fmt == "pptx":
                    kind = "slide"
                else:
                    return ("[DocWritePro][错误] commands 模式仅支持 docx/pptx；"
                            "xlsx 请使用 sheets 参数")
                build_document(kind, port, str(out), cmds or [])
            elif fmt == "docx":
                generate_docx(port, str(out), title or "", sections or [])
            elif fmt == "xlsx":
                generate_xlsx(port, str(out), sheets or [])
            else:
                generate_pptx(port, str(out), slides or [])
        except Exception as e:  # noqa: BLE001
            return f"[DocWritePro][生成失败] {type(e).__name__}: {e}"
        return f"[DocWritePro] 已生成：{out}"

    return Tool(
        name="DocWritePro",
        description="生成带复杂排版的 docx/xlsx/pptx（走 editor_sdk 高配引擎）。"
                    "commands 命令驱动模式（docx/pptx）：用结构化命令列表描述"
                    "标题/段落/染色表格/图片/页眉页码（Word），以及图表/形状/"
                    "主题配色/页码（PPT），排版保真度高于简单 sections/slides。",
        keywords=["生成文档", "写word", "做ppt", "生成excel", "docwrite-pro",
                  "复杂排版", "生成报告", "做表格", "做幻灯片"],
        handler=handler,
        parameters={
            "type": "object",
            "properties": {
                "format": {"type": "string", "enum": ["docx", "xlsx", "pptx"],
                           "description": "文档格式"},
                "path": {"type": "string", "description": "输出文件路径（可选；留空则默认写入产出目录 ~/Documents/Shadeling/Output，文件名自动加时间戳）"},
                "title": {"type": "string", "description": "文档标题"},
                "sections": {"type": "array",
                             "description": "docx 内容段落数组",
                             "items": {"type": "object", "properties": {
                                 "type": {"type": "string",
                                          "enum": ["heading1", "heading2",
                                                   "heading3", "paragraph",
                                                   "bullet_list", "table"]},
                                 "text": {"type": "string"},
                                 "items": {"type": "array",
                                           "items": {"type": "string"}},
                                 "headers": {"type": "array",
                                             "items": {"type": "string"}},
                                 "rows": {"type": "array",
                                          "items": {"type": "array",
                                                    "items": {"type": "string"}}}}}},
                "sheets": {"type": "array",
                           "description": "xlsx 工作表数组",
                           "items": {"type": "object", "properties": {
                               "name": {"type": "string"},
                               "headers": {"type": "array",
                                           "items": {"type": "string"}},
                               "rows": {"type": "array",
                                        "items": {"type": "array",
                                                  "items": {"type": "string"}}}}}},
                "slides": {"type": "array",
                           "description": "pptx 幻灯片数组",
                           "items": {"type": "object", "properties": {
                               "type": {"type": "string",
                                        "enum": ["title", "content", "table"]},
                               "title": {"type": "string"},
                               "subtitle": {"type": "string"},
                               "items": {"type": "array",
                                         "items": {"type": "string"}},
                               "headers": {"type": "array",
                                           "items": {"type": "string"}},
                               "rows": {"type": "array",
                                        "items": {"type": "array",
                                                  "items": {"type": "string"}}}}}},
                "commands": {"type": "array",
                             "description": "命令驱动模式（docx/pptx 推荐）。"
                                            "数组元素为 {op, ...}："
                                            "Word 支持 heading/paragraph/bullets/"
                                            "numbering/table(可 header_fill+borders)/"
                                            "image( path 或 data)/page_break/header/"
                                            "page_number；PPT 支持 slide(bg)/title/text/"
                                            "shape/chart/chart_type=clusteredColumn|pie/"
                                            "page_number/image。",
                             "items": {"type": "object", "properties": {
                                 "op": {"type": "string"},
                                 "text": {"type": "string"},
                                 "level": {"type": "integer"},
                                 "color": {"type": "string"},
                                 "size": {"type": "integer"},
                                 "bold": {"type": "boolean"},
                                 "items": {"type": "array",
                                           "items": {"type": "string"}},
                                 "headers": {"type": "array",
                                             "items": {"type": "string"}},
                                 "rows": {"type": "array",
                                          "items": {"type": "array"}},
                                 "header_fill": {"type": "string"},
                                 "borders": {"type": "boolean"},
                                 "path": {"type": "string"},
                                 "data": {"type": "string"},
                                 "w": {"type": "integer"},
                                 "h": {"type": "integer"},
                                 "bg": {"type": "string"},
                                 "x": {"type": "integer"},
                                 "y": {"type": "integer"},
                                 "shape_type": {"type": "string"},
                                 "fill": {"type": "string"},
                                 "alpha": {"type": "integer"},
                                 "border": {"type": "string"},
                                 "chart_type": {"type": "string"},
                                 "title": {"type": "string"},
                                 "categories": {"type": "array",
                                                "items": {"type": "string"}},
                                 "series": {"type": "array",
                                            "items": {"type": "object"}}}}},
            },
            "required": ["format", "path"],
        },
        risk=RiskLevel.MEDIUM,
    )
