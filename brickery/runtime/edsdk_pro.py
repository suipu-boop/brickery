"""editor_sdk 高配引擎客户端（Shadeling 内部封装，零第三方依赖）。

通过本机 HTTP 直连 editor_sdk 的 MCP 端点（默认 127.0.0.1:39099/mcp），
把「生成 docx/xlsx/pptx」的高级需求翻译为 editor_sdk 的
create -> 元素级编辑 -> save 工作流。

设计要点：
- 本地直连，绕开环境代理（urllib ProxyHandler({})，等价于 curl --noproxy）。
- 引擎二进制由技能市场安装时落盘到 BRICKERY_HOME/bin/<source>/，
  运行时由 ensure_engine 复用已运行的实例或自行拉起。
- 所有响应走人读文本 / JSON，用正则 + JSON 双解析，容错。
- 不抛未捕获异常；上层 handler 统一把失败转成 [DocWritePro] 提示串。

仅被 docwrite_pro.py 的 DocWritePro 工具调用。
"""
from __future__ import annotations

import csv
import io
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_OPENER = urlopen  # 占位，下面用 ProxyHandler 重建

try:
    from urllib.request import ProxyHandler, build_opener
    _OPENER = build_opener(ProxyHandler({}))  # 绕过 127.0.0.1 的代理拦截
except Exception:  # pragma: no cover
    pass

DEFAULT_PORT = 39099
_DISCOVER_TIMEOUT = 2
_RPC_TIMEOUT = 60


def _endpoint(port: int) -> str:
    return f"http://127.0.0.1:{port}/mcp"


def _mcp_call(port: int, method: str, params: Optional[dict] = None,
              timeout: int = _RPC_TIMEOUT) -> dict:
    """发一条 JSON-RPC 请求，返回 result 字典。"""
    data = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params or {}}).encode("utf-8")
    req = Request(_endpoint(port), data=data,
                  headers={"Content-Type": "application/json",
                           "Accept": "application/json"}, method="POST")
    with _OPENER.open(req, timeout=timeout) as resp:
        obj = json.loads(resp.read().decode("utf-8"))
    if isinstance(obj, dict) and obj.get("error"):
        raise RuntimeError(f"JSON-RPC 错误 {obj['error'].get('code')}: "
                           f"{obj['error'].get('message')}")
    return obj.get("result", {}) if isinstance(obj, dict) else {}


def _result_text(r: dict) -> str:
    """从 tools/call 的 result.content[].text 提取文本。"""
    if not isinstance(r, dict):
        return ""
    parts = [c.get("text", "") for c in (r.get("content") or [])
             if isinstance(c, dict) and c.get("type") == "text"]
    return "\n".join(parts)


def _mcp_alive(port: int) -> bool:
    try:
        r = _mcp_call(port, "tools/list", timeout=_DISCOVER_TIMEOUT)
    except (URLError, HTTPError, TimeoutError, ValueError, OSError):
        return False
    return isinstance(r, dict) and isinstance(r.get("tools"), list)


def _extract_file_id(text: str) -> str:
    m = re.search(r"file_id=([^\s,\)]+)", text or "")
    return m.group(1) if m else ""


def _extract_last_index(text: str) -> Optional[int]:
    # 优先解析 JSON（doc_insert_text 返回 {"last_edit_index":N,...}）
    try:
        d = json.loads(text)
        if isinstance(d, dict):
            for k in ("last_edit_index", "position"):
                if isinstance(d.get(k), int):
                    return d[k]
    except (ValueError, TypeError):
        pass
    m = re.search(r"last_edit_index[\"':\s]+(\d+)", text or "")
    if m:
        return int(m.group(1))
    return None


def _extract_sheet_id(text: str) -> str:
    try:
        d = json.loads(text)
        if isinstance(d, dict):
            sheets = d.get("sheets") or []
            if sheets and isinstance(sheets[0], dict):
                return str(sheets[0].get("sheet_id", ""))
    except (ValueError, TypeError):
        pass
    m = re.search(r"sheet_id=([^\s,]+)", text or "")
    return m.group(1) if m else ""


def _extract_sheet_name(text: str) -> str:
    try:
        d = json.loads(text)
        if isinstance(d, dict):
            sheets = d.get("sheets") or []
            if sheets and isinstance(sheets[0], dict):
                return str(sheets[0].get("name", ""))
    except (ValueError, TypeError):
        pass
    return ""


def ensure_engine(home, skill) -> Tuple[Optional[int], Optional[str]]:
    """确保 editor_sdk 引擎在运行。委托给 BinaryManager 单例。

    BinaryManager 负责：
    - 复用已运行实例（含外部已启动的，如 WorkBuddy）
    - 拉起本地二进制 + 健康检查
    - 崩溃自动重启（最多 1 次）
    - App 退出时 SIGTERM -> SIGKILL 清理（不留孤儿）

    返回 (port, 错误)。错误为 None 表示成功。
    """
    from .binary_manager import get_manager
    return get_manager().ensure_running(home, skill)


def _create(kind: str, port: int) -> str:
    r = _mcp_call(port, "tools/call",
                 {"name": f"create_{kind}", "arguments": {}})
    fid = _extract_file_id(_result_text(r))
    if fid:
        # 引擎异步 open 文档，需短暂等待方可写入（实测 ~0.5s 就绪）。
        time.sleep(0.8)
    return fid


def _doc_insert(port: int, fid: str, idx: int, text: str) -> int:
    r = _mcp_call(port, "tools/call",
                 {"name": "doc_insert_text",
                  "arguments": {"file_id": fid, "idx": idx, "text": text}})
    nxt = _extract_last_index(_result_text(r))
    return nxt if nxt is not None else idx + max(1, len(text))


def _doc_insert_table(port: int, fid: str, idx: int,
                      headers, rows) -> int:
    buf = io.StringIO()
    w = csv.writer(buf)
    if headers:
        w.writerow(headers)
    for row in (rows or []):
        w.writerow(row)
    csv_data = buf.getvalue().strip()
    r = _mcp_call(port, "tools/call",
                 {"name": "doc_insert_table_by_csv",
                  "arguments": {"file_id": fid, "idx": idx,
                               "csv_data": csv_data}})
    nxt = _extract_last_index(_result_text(r))
    return nxt if nxt is not None else idx


def _save(port: int, fid: str, out_path: str) -> str:
    r = _mcp_call(port, "tools/call",
                 {"name": "save_file",
                  "arguments": {"file_id": fid, "file_path": out_path}})
    return _result_text(r)


def generate_docx(port: int, out_path: str, title: str, sections) -> str:
    """生成 docx。sections: [{type, text, items, headers, rows}, ...]。"""
    fid = _create("doc", port)
    idx = 0
    if title:
        idx = _doc_insert(port, fid, idx, title)
    for sec in (sections or []):
        t = (sec or {}).get("type", "paragraph")
        if t in ("heading1", "heading2", "heading3", "paragraph"):
            text = (sec or {}).get("text", "")
            if text:
                idx = _doc_insert(port, fid, idx, text)
        elif t == "bullet_list":
            for it in (sec or {}).get("items", []):
                idx = _doc_insert(port, fid, idx, f"- {it}")
        elif t == "table":
            idx = _doc_insert_table(
                port, fid, idx,
                (sec or {}).get("headers", []),
                (sec or {}).get("rows", []))
    _save(port, fid, out_path)
    return out_path


def _set_cell(port: int, fid: str, sid: str, row: int, col: int, val) -> None:
    if isinstance(val, bool):
        cell = {"row": row, "col": col, "value_type": "BOOL",
                "bool_value": val}
    elif isinstance(val, (int, float)):
        cell = {"row": row, "col": col, "value_type": "NUMBER",
                "number_value": val}
    else:
        cell = {"row": row, "col": col, "value_type": "STRING",
                "string_value": str(val)}
    _mcp_call(port, "tools/call",
              {"name": "sheet_set_cell_value",
               "arguments": {"file_id": fid, "sheet_id": sid, "cell": cell}})


def generate_xlsx(port: int, out_path: str, sheets) -> str:
    """生成 xlsx。sheets: [{name, headers, rows}, ...]。

    create_sheet 默认已建一个子表，第一个用户 sheet 复用该默认子表
    （避免重名冲突）；其余才新增子表，并对重名自动加后缀。
    """
    fid = _create("sheet", port)
    info = _mcp_call(port, "tools/call",
                     {"name": "sheet_get_sheet_info",
                      "arguments": {"file_id": fid}})
    info_text = _result_text(info)
    default_sid = _extract_sheet_id(info_text) or "000001"
    default_name = _extract_sheet_name(info_text) or "Sheet1"
    used = {default_name}
    for i, sh in enumerate(sheets or []):
        sh = sh or {}
        name = sh.get("name")
        if i == 0 and (not name or name == default_name):
            sid = default_sid  # 复用默认子表
        else:
            nm = name or f"Sheet{i + 1}"
            while nm in used:
                nm = f"{nm}_{i}"
            r = _mcp_call(port, "tools/call",
                          {"name": "sheet_add_sheet",
                           "arguments": {"file_id": fid, "name": nm}})
            nsid = _extract_sheet_id(_result_text(r))
            sid = nsid or default_sid
            used.add(nm)
        all_rows = ([sh.get("headers", [])] if sh.get("headers") else []) \
                   + (sh.get("rows", []) or [])
        for ri, row in enumerate(all_rows):
            for ci, val in enumerate(row):
                _set_cell(port, fid, sid, ri, ci, val)
    _save(port, fid, out_path)
    return out_path


def generate_pptx(port: int, out_path: str, slides) -> str:
    """生成 pptx。slides: [{type, title, subtitle, items, headers, rows}, ...]。"""
    fid = _create("slide", port)
    for i, sl in enumerate(slides or []):
        if i > 0:
            _mcp_call(port, "tools/call",
                      {"name": "slide_add_slide",
                       "arguments": {"file_id": fid, "index": -1,
                                     "layout_index": 1}})
        title = (sl or {}).get("title", "")
        if title:
            _mcp_call(port, "tools/call",
                      {"name": "slide_add_text",
                       "arguments": {"file_id": fid, "page_index": i,
                                     "x": 50, "y": 50, "w": 600, "h": 60,
                                     "text": title, "font_size": 28}})
        items = (sl or {}).get("items", [])
        for j, it in enumerate(items):
            _mcp_call(port, "tools/call",
                      {"name": "slide_add_text",
                       "arguments": {"file_id": fid, "page_index": i,
                                     "x": 60, "y": 130 + j * 40, "w": 600,
                                     "h": 35, "text": f"• {it}",
                                     "font_size": 18}})
    _save(port, fid, out_path)
    return out_path


# ====================== 命令驱动高级生成 ======================
# 把 editor_sdk 复杂能力封装为「命令列表」模型，供 DocWritePro 工具直接消费。
# 命令由 AI/用户以结构化 JSON 描述，handler 翻译成 editor_sdk 调用序列。
# 设计原则：失败不抛（交给上层 handler 统一转提示串）；坐标用 end_index 链式推进。

def _extract_shape_id(text: str):
    if not text:
        return None
    try:
        d = json.loads(text)
        if isinstance(d.get("shape_id"), str):
            return d["shape_id"]
    except (ValueError, TypeError):
        pass
    m = re.search(r"shape_id[\"':\s]+([A-Za-z0-9_]+)", text or "")
    return m.group(1) if m else None


def _resolve_image_content(c: dict):
    """从命令中提取图片 content（data URI）。支持 data / base64 / path。"""
    data = c.get("data")
    if data:
        return data if data.startswith("data:image") else "data:image/png;base64," + data
    p = c.get("path")
    if p:
        import base64
        from pathlib import Path
        pp = Path(p).expanduser()
        if pp.exists():
            return "data:image/png;base64," + base64.b64encode(pp.read_bytes()).decode()
    return None


# ---------- Word 高层原语 ----------
def _doc_add_para(port, fid, idx, text, level=0, ptype=0, numbering_lvl=1):
    args = {"file_id": fid, "idx": idx, "text": text,
            "level": level, "type": ptype}
    # 列表类别（type>=1）必须与 numbering_lvl 配合使用，否则报
    # "invalid numbering level"。
    if ptype >= 1:
        args["numbering_lvl"] = numbering_lvl
    r = _mcp_call(port, "tools/call", {"name": "doc_insert_paragraph_with_text",
                  "arguments": args})
    nxt = _extract_last_index(_result_text(r))
    return nxt if nxt is not None else idx + max(1, len(text))


def _doc_style_range(port, fid, begin, end, **kw):
    if begin is None or end is None:
        return
    kw = {"file_id": fid, "ranges": [{"begin": begin, "end": end}], **kw}
    _mcp_call(port, "tools/call", {"name": "doc_update_text_property", "arguments": kw})


def _doc_add_table(port, fid, idx, headers, rows):
    """插入表格，返回 (表格首格坐标 for 染色, 表格后插入点)。"""
    buf = io.StringIO()
    w = csv.writer(buf)
    if headers:
        w.writerow(headers)
    for row in (rows or []):
        w.writerow(row)
    csv_data = buf.getvalue().strip()
    r = _mcp_call(port, "tools/call", {"name": "doc_insert_table_by_csv",
                  "arguments": {"file_id": fid, "idx": idx, "csv_data": csv_data}})
    nxt = _extract_last_index(_result_text(r))
    after = nxt if nxt is not None else idx
    # 用表头首格完整文本定位表格（cell_match 需唯一，避免歧义匹配）。
    anchor = str(headers[0]) if headers else ""
    tbl_idx = after + 1
    if anchor:
        try:
            gi = _mcp_call(port, "tools/call", {"name": "doc_get_table_info",
                       "arguments": {"file_id": fid,
                                     "table_locate": {"cell_match": anchor}}})
            gd = json.loads(_result_text(gi))
            if isinstance(gd.get("block", {}).get("idx"), int):
                tbl_idx = gd["block"]["idx"]
        except Exception:
            pass
    return tbl_idx, after


def _doc_table_style(port, fid, idx, header_fill=None, borders=True, align="center"):
    kw = {"file_id": fid, "idx": idx, "alignment": align}
    if header_fill:
        kw["cell_fills"] = [{"condition": "first_row", "color": header_fill}]
    if borders:
        kw["borders"] = {"top": {"val": "single", "sz": 4, "color": "BFBFBF"},
                         "bottom": {"val": "single", "sz": 4, "color": "BFBFBF"},
                         "left": {"val": "single", "sz": 4, "color": "BFBFBF"},
                         "right": {"val": "single", "sz": 4, "color": "BFBFBF"},
                         "inside_h": {"val": "single", "sz": 2, "color": "D9D9D9"},
                         "inside_v": {"val": "single", "sz": 2, "color": "D9D9D9"}}
    _mcp_call(port, "tools/call", {"name": "doc_set_table_properties", "arguments": kw})


def _doc_add_image(port, fid, idx, content, w=None, h=None):
    kw = {"file_id": fid, "idx": idx, "content": content}
    if w:
        kw["w"] = w
    if h:
        kw["h"] = h
    r = _mcp_call(port, "tools/call", {"name": "doc_insert_image", "arguments": kw})
    nxt = _extract_last_index(_result_text(r))
    return nxt if nxt is not None else idx


# ---------- PPT 高层原语 ----------
def _ppt_new_slide(port, fid, page_index):
    # 新建一页（追加到末尾）。封面 page0 由 create_slide 已建，调用方控制是否调用本函数。
    _mcp_call(port, "tools/call", {"name": "slide_add_slide",
                  "arguments": {"file_id": fid, "index": -1, "layout_index": 1}})


def _ppt_bg(port, fid, page_index, color):
    _mcp_call(port, "tools/call", {"name": "slide_set_page_properties",
              "arguments": {"file_id": fid, "page_index": page_index,
                            "fill_type": "solid", "fill_color": color}})


def _ppt_text(port, fid, page_index, **kw):
    kw = {"file_id": fid, "page_index": page_index, **kw}
    r = _mcp_call(port, "tools/call", {"name": "slide_add_text", "arguments": kw})
    return _extract_shape_id(_result_text(r))


def _ppt_shape(port, fid, page_index, **kw):
    kw = {"file_id": fid, "page_index": page_index, **kw}
    r = _mcp_call(port, "tools/call", {"name": "slide_add_shape", "arguments": kw})
    return _extract_shape_id(_result_text(r))


def _ppt_chart(port, fid, page_index, **kw):
    kw = {"file_id": fid, "page_index": page_index, **kw}
    r = _mcp_call(port, "tools/call", {"name": "slide_add_chart", "arguments": kw})
    return _extract_shape_id(_result_text(r))


def _ppt_pagenum(port, fid, page_index):
    _mcp_call(port, "tools/call", {"name": "slide_add_page_number",
              "arguments": {"file_id": fid, "page_index": page_index}})


def _ppt_image(port, fid, page_index, **kw):
    kw = {"file_id": fid, "page_index": page_index, **kw}
    _mcp_call(port, "tools/call", {"name": "slide_add_image", "arguments": kw})


def _ppt_bold(port, fid, page_index, shape_id):
    _mcp_call(port, "tools/call", {"name": "slide_set_text_property",
              "arguments": {"file_id": fid, "page_index": page_index,
                            "shape_id": shape_id, "index": 0, "count": -1,
                            "bold": True}})


# ---------- 命令驱动主入口 ----------
def build_document(kind: str, port: int, out_path: str, commands) -> str:
    """命令驱动生成文档。

    kind: 'doc' / 'slide' / 'sheet'。commands: list[dict]，每项含 'op'。
    返回输出文件路径。失败由上层 handler 转提示串。
    """
    fid = _create(kind, port)
    cmds = commands or []
    if kind == "doc":
        _run_doc_commands(port, fid, cmds)
    elif kind == "slide":
        _run_slide_commands(port, fid, cmds)
    _save(port, fid, out_path)
    return out_path


def _run_doc_commands(port, fid, cmds):
    idx = 0
    for c in cmds:
        c = c or {}
        # 首段（idx==0）插入坐标用 0（等价于 -1 开头，但 0 能被所有 API 接受，
        # 含 doc_update_text_property 要求 begin 为非负整数）。
        ins = 0 if idx == 0 else idx
        op = c.get("op", "paragraph")
        if op == "heading":
            nxt = _doc_add_para(port, fid, ins, c.get("text", ""),
                                level=int(c.get("level", 1)))
            if c.get("color"):
                _doc_style_range(port, fid, ins, nxt, color=c["color"])
            idx = nxt if nxt is not None else idx
        elif op == "paragraph":
            nxt = _doc_add_para(port, fid, ins, c.get("text", ""))
            kw = {}
            if c.get("color"):
                kw["color"] = c["color"]
            if c.get("size"):
                kw["font_size"] = int(c["size"])
            if c.get("bold"):
                kw["bold"] = True
            if kw:
                _doc_style_range(port, fid, ins, nxt, **kw)
            idx = nxt if nxt is not None else idx
        elif op == "bullets":
            for it in c.get("items", []):
                nxt = _doc_add_para(port, fid, ins, str(it), ptype=1)
                idx = nxt if nxt is not None else idx
        elif op == "numbering":
            for it in c.get("items", []):
                nxt = _doc_add_para(port, fid, ins, str(it), ptype=2)
                idx = nxt if nxt is not None else idx
        elif op == "table":
            tidx, after = _doc_add_table(port, fid, ins,
                                        c.get("headers", []), c.get("rows", []))
            _doc_table_style(port, fid, tidx,
                             header_fill=c.get("header_fill"),
                             borders=c.get("borders", True))
            idx = after
        elif op == "image":
            content = _resolve_image_content(c)
            if content:
                nxt = _doc_add_image(port, fid, ins, content,
                                     w=c.get("w"), h=c.get("h"))
                idx = nxt if nxt is not None else idx
        elif op == "page_break":
            r = _mcp_call(port, "tools/call", {"name": "doc_insert_page_break",
                      "arguments": {"file_id": fid, "idx": ins}})
            nxt = _extract_last_index(_result_text(r))
            idx = nxt if nxt is not None else idx
        elif op == "header":
            _mcp_call(port, "tools/call", {"name": "doc_insert_header",
                      "arguments": {"file_id": fid, "text": c.get("text", "")}})
        elif op == "page_number":
            _mcp_call(port, "tools/call", {"name": "doc_set_page_number",
                      "arguments": {"file_id": fid,
                                    "position": c.get("position", "right"),
                                    "format": c.get("format", "decimal")}})


def _run_slide_commands(port, fid, cmds):
    current = 0
    first = True
    for c in cmds:
        c = c or {}
        op = c.get("op", "text")
        if op == "slide":
            if not first:
                _ppt_new_slide(port, fid, current)
                current += 1
            first = False
            if c.get("bg"):
                _ppt_bg(port, fid, current, c["bg"])
            continue
        pi = current
        if op == "title":
            sid = _ppt_text(port, fid, pi, x=c.get("x", 50), y=c.get("y", 50),
                            w=c.get("w", 600), h=c.get("h", 60),
                            text=c.get("text", ""), font_size=int(c.get("size", 28)),
                            font_color=c.get("color", "333333"))
            if c.get("bold") and sid:
                _ppt_bold(port, fid, pi, sid)
        elif op == "text":
            kw = {"x": c.get("x", 60), "y": c.get("y", 130), "w": c.get("w", 600),
                  "h": c.get("h", 35), "text": c.get("text", ""),
                  "font_size": int(c.get("size", 18)),
                  "font_color": c.get("color", "333333")}
            if c.get("fill"):
                kw["fill_color"] = c["fill"]
            sid = _ppt_text(port, fid, pi, **kw)
            if c.get("bold") and sid:
                _ppt_bold(port, fid, pi, sid)
        elif op == "shape":
            kw = {"shape_type": c.get("shape_type", "rect"), "x": c.get("x", 0),
                  "y": c.get("y", 0), "w": c.get("w", 100), "h": c.get("h", 100),
                  "fill_color": c.get("fill", "DDDDDD"),
                  "fill_alpha": int(c.get("alpha", 0))}
            if c.get("border"):
                kw["border_color"] = c["border"]
                kw["border_width"] = 1
            _ppt_shape(port, fid, pi, **kw)
        elif op == "chart":
            _ppt_chart(port, fid, pi, x=c.get("x", 80), y=c.get("y", 130),
                       w=c.get("w", 540), h=c.get("h", 350),
                       chart_type=c.get("chart_type", "clusteredColumn"),
                       title=c.get("title", ""), categories=c.get("categories", []),
                       series=c.get("series", []))
        elif op == "page_number":
            _ppt_pagenum(port, fid, pi)
        elif op == "image":
            content = _resolve_image_content(c)
            if content:
                _ppt_image(port, fid, pi, x=c.get("x", 0), y=c.get("y", 0),
                           w=c.get("w", 200), h=c.get("h", 200), content=content)
