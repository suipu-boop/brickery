"""发布版通用工具：DocRead / TableStat / ImageOps。

依据《Shadeling 发布版内置工具冻结清单 v1.0》实现，全部遵守零第三方依赖纪律：

* **DocRead** — docx/xlsx/pptx 本质是 zip + XML，用 stdlib 的 ``zipfile`` +
  ``xml.etree`` 即可干净解析；csv/md/txt 直读。不引入 python-docx / openpyxl。
* **TableStat** — csv + statistics，纯 stdlib。
* **ImageOps** — 方案 A：调用 macOS 自带 ``/usr/bin/sips``（随系统分发，非第三方依赖）。
  非 macOS 或 sips 缺失时给出明确错误，不静默失败（发布纪律：不交半成品）。

所有 handler 返回字符串，出错返回 ``[错误] ...`` / ``[沙箱拒绝] ...``，不抛异常。
"""
from __future__ import annotations

import csv
import io
import os
import shutil
import statistics
import subprocess
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple
from xml.etree import ElementTree as ET

from .sandbox import Sandbox, default_sandbox
from .tools import RiskLevel, Tool

# 输出上限（防止一个大文档撑爆 context）
_DOC_CHARS = 20000
_ROWS_PREVIEW = 50
_SIPS = "/usr/bin/sips"


# ---------------------------------------------------------------------------
# 公共辅助
# ---------------------------------------------------------------------------
def _guard_read(path: str, sandbox: Sandbox) -> Tuple[Optional[Path], str]:
    """沙箱读校验 + 存在性检查。返回 (Path 或 None, 错误信息)。"""
    ok, reason = sandbox.check_path_read(path)
    if not ok:
        return None, f"[沙箱拒绝] {reason}"
    p = Path(path).expanduser()
    if not p.exists():
        return None, f"[错误] 路径不存在：{path}"
    if not p.is_file():
        return None, f"[错误] 不是文件：{path}"
    return p, ""


def _iter_local(elem, local: str):
    """按「本地名」遍历所有后代元素，忽略命名空间。

    不能用 elem.iter 配「花括号星号」通配命名空间——那是 ElementPath 语法，
    只在 find/findall/iterfind 里生效；iter() 只做字面匹配，永远无命中。
    按本地名匹配还顺带兼容 OOXML **Strict** 格式（命名空间 URI 与
    Transitional 不同），比写死 URI 更稳。
    """
    for e in elem.iter():
        tag = e.tag
        if isinstance(tag, str) and tag.rsplit("}", 1)[-1] == local:
            yield e


def _find_local(elem, local: str):
    """取第一个匹配本地名的**直接子元素**，无则 None。"""
    for e in list(elem):
        tag = e.tag
        if isinstance(tag, str) and tag.rsplit("}", 1)[-1] == local:
            return e
    return None


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[已截断 {len(text) - limit} 字符]"


# ---------------------------------------------------------------------------
# DocRead — Office 文档 / csv / 纯文本
# ---------------------------------------------------------------------------
def _docx_text(p: Path) -> str:
    with zipfile.ZipFile(p) as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    paras: List[str] = []
    for para in _iter_local(root, "p"):
        runs = [(t.text or "") for t in _iter_local(para, "t")]
        line = "".join(runs).strip()
        if line:
            paras.append(line)
    return "\n".join(paras) if paras else "[空文档]"


def _xlsx_shared_strings(z: zipfile.ZipFile) -> List[str]:
    try:
        xml = z.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(xml)
    out: List[str] = []
    for si in _iter_local(root, "si"):
        out.append("".join((t.text or "") for t in _iter_local(si, "t")))
    return out


def _xlsx_sheet_names(z: zipfile.ZipFile) -> List[str]:
    try:
        root = ET.fromstring(z.read("xl/workbook.xml"))
    except (KeyError, ET.ParseError):
        return []
    return [s.get("name", "") for s in _iter_local(root, "sheet")]


def _xlsx_rows(z: zipfile.ZipFile, member: str,
               shared: List[str]) -> List[List[str]]:
    root = ET.fromstring(z.read(member))
    rows: List[List[str]] = []
    for row in _iter_local(root, "row"):
        cells: List[str] = []
        for c in _iter_local(row, "c"):
            ctype = c.get("t")
            if ctype == "s":
                v = _find_local(c, "v")
                idx = int(v.text) if (v is not None and v.text) else -1
                cells.append(shared[idx] if 0 <= idx < len(shared) else "")
            elif ctype == "inlineStr":
                cells.append("".join(
                    (t.text or "") for t in _iter_local(c, "t")))
            else:
                v = _find_local(c, "v")
                cells.append(v.text if (v is not None and v.text) else "")
        rows.append(cells)
    return rows


def _xlsx_text(p: Path, sheet: str = "") -> str:
    with zipfile.ZipFile(p) as z:
        shared = _xlsx_shared_strings(z)
        names = _xlsx_sheet_names(z)
        members = sorted(
            n for n in z.namelist()
            if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
        if not members:
            return "[错误] 未找到工作表"
        picked = list(zip(names, members)) if len(names) == len(members) \
            else [(f"sheet{i + 1}", m) for i, m in enumerate(members)]
        if sheet:
            picked = [(n, m) for n, m in picked if n == sheet]
            if not picked:
                return (f"[错误] 未找到工作表「{sheet}」，"
                        f"可选：{', '.join(names) or '(未知)'}")
        blocks: List[str] = []
        for name, member in picked:
            rows = _xlsx_rows(z, member, shared)
            body = "\n".join("\t".join(r) for r in rows[:_ROWS_PREVIEW])
            more = f"\n...[共 {len(rows)} 行，仅显示前 {_ROWS_PREVIEW} 行]" \
                if len(rows) > _ROWS_PREVIEW else ""
            blocks.append(f"## 工作表：{name}（{len(rows)} 行）\n{body}{more}")
    return "\n\n".join(blocks)


def _pptx_text(p: Path) -> str:
    with zipfile.ZipFile(p) as z:
        slides = sorted(
            n for n in z.namelist()
            if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
        blocks: List[str] = []
        for i, member in enumerate(slides, 1):
            root = ET.fromstring(z.read(member))
            texts = [(t.text or "").strip() for t in _iter_local(root, "t")]
            body = "\n".join(t for t in texts if t)
            blocks.append(f"## 第 {i} 页\n{body or '(无文本)'}")
    return "\n\n".join(blocks) if blocks else "[空演示文稿]"


def _csv_text(p: Path) -> str:
    with p.open("r", encoding="utf-8", errors="replace", newline="") as f:
        sample = f.read(8192)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.reader(f, dialect))
    body = "\n".join("\t".join(r) for r in rows[:_ROWS_PREVIEW])
    more = f"\n...[共 {len(rows)} 行，仅显示前 {_ROWS_PREVIEW} 行]" \
        if len(rows) > _ROWS_PREVIEW else ""
    return f"（{len(rows)} 行）\n{body}{more}"


def doc_read(path: str, sandbox: Sandbox, sheet: str = "",
             max_chars: int = _DOC_CHARS) -> str:
    p, err = _guard_read(path, sandbox)
    if p is None:
        return err
    ext = p.suffix.lower()
    try:
        if ext == ".docx":
            text = _docx_text(p)
        elif ext == ".xlsx":
            text = _xlsx_text(p, sheet)
        elif ext == ".pptx":
            text = _pptx_text(p)
        elif ext in (".csv", ".tsv"):
            text = _csv_text(p)
        elif ext in (".md", ".txt", ".markdown", ".text", ".log", ".json"):
            text = p.read_text(encoding="utf-8", errors="replace")
        elif ext in (".doc", ".xls", ".ppt"):
            return (f"[错误] {ext} 是旧版二进制格式，不支持。"
                    f"请在 Office/WPS 中另存为 {ext}x 后重试。")
        else:
            return (f"[错误] 不支持的扩展名 {ext or '(无)'}。"
                    "支持：docx/xlsx/pptx/csv/tsv/md/txt/json")
    except zipfile.BadZipFile:
        return f"[错误] 文件损坏或不是有效的 Office 文档：{path}"
    except (ET.ParseError, KeyError) as e:
        return f"[错误] 文档结构解析失败：{type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
        return f"[错误] 读取失败：{type(e).__name__}: {e}"
    try:
        limit = max(1000, min(int(max_chars), 200000))
    except (TypeError, ValueError):
        limit = _DOC_CHARS
    return _clip(text, limit)


# ---------------------------------------------------------------------------
# TableStat — 表格统计
# ---------------------------------------------------------------------------
def _load_table(p: Path, sheet: str = "") -> Tuple[List[str], List[List[str]], str]:
    """返回 (表头, 数据行, 错误)。"""
    ext = p.suffix.lower()
    if ext in (".csv", ".tsv"):
        with p.open("r", encoding="utf-8", errors="replace", newline="") as f:
            sample = f.read(8192)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            rows = list(csv.reader(f, dialect))
    elif ext == ".xlsx":
        with zipfile.ZipFile(p) as z:
            shared = _xlsx_shared_strings(z)
            names = _xlsx_sheet_names(z)
            members = sorted(
                n for n in z.namelist()
                if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
            if not members:
                return [], [], "[错误] 未找到工作表"
            member = members[0]
            if sheet and len(names) == len(members):
                for n, m in zip(names, members):
                    if n == sheet:
                        member = m
                        break
                else:
                    return [], [], f"[错误] 未找到工作表「{sheet}」"
            rows = _xlsx_rows(z, member, shared)
    else:
        return [], [], f"[错误] TableStat 仅支持 csv/tsv/xlsx，收到 {ext or '(无)'}"
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        return [], [], "[错误] 表格为空"
    return [str(c).strip() for c in rows[0]], rows[1:], ""


def _as_float(s: str) -> Optional[float]:
    t = str(s).strip().replace(",", "")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def table_stat(path: str, sandbox: Sandbox, group_by: str = "",
               agg: str = "", sheet: str = "") -> str:
    p, err = _guard_read(path, sandbox)
    if p is None:
        return err
    try:
        header, rows, err = _load_table(p, sheet)
    except zipfile.BadZipFile:
        return f"[错误] 文件损坏或不是有效的 xlsx：{path}"
    except Exception as e:  # noqa: BLE001
        return f"[错误] 表格解析失败：{type(e).__name__}: {e}"
    if err:
        return err

    ncol = len(header)
    out: List[str] = [f"行数：{len(rows)}　列数：{ncol}",
                      f"列名：{', '.join(header)}", "", "## 各列概况"]
    for i, name in enumerate(header):
        col = [r[i] if i < len(r) else "" for r in rows]
        missing = sum(1 for v in col if not str(v).strip())
        nums = [x for x in (_as_float(v) for v in col) if x is not None]
        filled = len(col) - missing
        if filled and len(nums) >= filled * 0.8:
            line = (f"- {name}（数值）：非空 {filled}，缺失 {missing}，"
                    f"min={min(nums):g}，max={max(nums):g}，"
                    f"mean={statistics.fmean(nums):.4g}，"
                    f"median={statistics.median(nums):g}")
        else:
            uniq = len({str(v).strip() for v in col if str(v).strip()})
            top = ""
            if uniq:
                counts: dict = {}
                for v in col:
                    k = str(v).strip()
                    if k:
                        counts[k] = counts.get(k, 0) + 1
                best = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
                top = "，高频：" + "、".join(f"{k}({n})" for k, n in best)
            line = (f"- {name}（文本）：非空 {filled}，缺失 {missing}，"
                    f"不同值 {uniq}{top}")
        out.append(line)

    if group_by:
        if group_by not in header:
            out.append(f"\n[警告] 分组列「{group_by}」不存在，已跳过分组统计")
        else:
            gi = header.index(group_by)
            groups: dict = {}
            for r in rows:
                key = str(r[gi]).strip() if gi < len(r) else ""
                groups.setdefault(key or "(空)", []).append(r)
            ai = header.index(agg) if agg and agg in header else -1
            if agg and ai < 0:
                out.append(f"\n[警告] 汇总列「{agg}」不存在，仅统计行数")
            title = f"\n## 按「{group_by}」分组（{len(groups)} 组）"
            if ai >= 0:
                title += f"，汇总列「{agg}」"
            out.append(title)
            for k, g in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:20]:
                if ai < 0:
                    out.append(f"- {k}：{len(g)} 行")
                    continue
                vals = [x for x in
                        (_as_float(r[ai] if ai < len(r) else "") for r in g)
                        if x is not None]
                if vals:
                    out.append(f"- {k}：{len(g)} 行，sum={sum(vals):g}，"
                               f"mean={statistics.fmean(vals):.4g}")
                else:
                    # 降级：非数值列不报错，退回计数（冻结清单 §3.2）
                    uniq = len({str(r[ai]).strip() for r in g
                                if ai < len(r) and str(r[ai]).strip()})
                    out.append(f"- {k}：{len(g)} 行，非数值列（不同值 {uniq}）")
            if len(groups) > 20:
                out.append(f"...[共 {len(groups)} 组，仅显示前 20]")
    return _clip("\n".join(out), _DOC_CHARS)


# ---------------------------------------------------------------------------
# ImageOps — 基于 macOS sips（方案 A）
# ---------------------------------------------------------------------------
def _sips_available() -> bool:
    return os.path.exists(_SIPS) or bool(shutil.which("sips"))


def _sips_bin() -> str:
    return _SIPS if os.path.exists(_SIPS) else (shutil.which("sips") or _SIPS)


def _run_sips(args: List[str]) -> Tuple[int, str, str]:
    try:
        r = subprocess.run([_sips_bin()] + args, capture_output=True,
                           text=True, timeout=60, shell=False)
        return r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        return 1, "", "sips 执行超时（60s）"
    except Exception as e:  # noqa: BLE001
        return 1, "", f"{type(e).__name__}: {e}"


def image_ops(action: str, path: str, sandbox: Sandbox, output: str = "",
              format: str = "", width: int = 0, height: int = 0) -> str:
    if not _sips_available():
        return ("[错误] ImageOps 依赖 macOS 自带的 sips 命令，当前环境不可用。"
                "非 macOS 系统请改用 MCP 图像服务器。")
    p, err = _guard_read(path, sandbox)
    if p is None:
        return err
    act = (action or "info").strip().lower()

    if act == "info":
        code, out, errtxt = _run_sips(
            ["-g", "pixelWidth", "-g", "pixelHeight", "-g", "format",
             "-g", "dpiWidth", str(p)])
        if code != 0:
            return f"[错误] 读取图片信息失败：{errtxt.strip() or out.strip()}"
        size = p.stat().st_size
        return f"{out.strip()}\n  fileSize: {size / 1024:.1f} KB"

    if act not in ("convert", "resize"):
        return f"[错误] 未知 action「{action}」，可选：info / convert / resize"

    if not output:
        return "[错误] convert / resize 需要提供 output 输出路径"
    ok, reason = sandbox.check_path_write(output)
    if not ok:
        return f"[沙箱拒绝] {reason}"
    outp = Path(output).expanduser()
    outp.parent.mkdir(parents=True, exist_ok=True)

    if act == "convert":
        fmt = (format or outp.suffix.lstrip(".")).strip().lower()
        alias = {"jpg": "jpeg", "tif": "tiff"}
        fmt = alias.get(fmt, fmt)
        allowed = {"jpeg", "png", "gif", "tiff", "bmp", "pdf"}
        if fmt not in allowed:
            return (f"[错误] 不支持的目标格式「{fmt or '(空)'}」，"
                    f"可选：{', '.join(sorted(allowed))}")
        code, out, errtxt = _run_sips(
            ["-s", "format", fmt, str(p), "--out", str(outp)])
    else:  # resize
        try:
            w, h = int(width or 0), int(height or 0)
        except (TypeError, ValueError):
            return "[错误] width / height 必须是整数"
        if w <= 0 and h <= 0:
            return "[错误] resize 需要提供 width 或 height（正整数）"
        if w > 0 and h > 0:
            args = ["-z", str(h), str(w)]          # 精确尺寸（高 宽）
        else:
            args = ["-Z", str(w or h)]             # 等比缩放到最长边
        code, out, errtxt = _run_sips(args + [str(p), "--out", str(outp)])

    if code != 0:
        return f"[错误] sips 执行失败：{errtxt.strip() or out.strip()}"
    if not outp.exists():
        return f"[错误] 输出文件未生成：{outp}"
    return f"完成：{outp}（{outp.stat().st_size / 1024:.1f} KB）"


# ---------------------------------------------------------------------------
# 注册段 —— description / keywords / parameters 均取自
# 《Shadeling 发布版内置工具冻结清单 v1.0》§3，不得自由发挥。
#
# 纪律：description 只写「模型选型需要知道的」，不写执行层修饰
# （沙箱/确认/超时由 Sandbox + ConfirmationGateway 在执行层拦截）。
# ---------------------------------------------------------------------------
def _parse_size(size: str) -> Tuple[int, int]:
    """把 '800x600' / '800' 解析成 (width, height)，失败返回 (0, 0)。"""
    s = str(size or "").strip().lower().replace("×", "x").replace("*", "x")
    if not s:
        return 0, 0
    parts = [t.strip() for t in s.split("x") if t.strip()]
    try:
        if len(parts) == 1:
            return int(parts[0]), 0
        return int(parts[0]), int(parts[1])
    except ValueError:
        return 0, 0


def build_doc_tools(sandbox: Optional[Sandbox] = None) -> List[Tool]:
    """工厂：返回发布版通用工具（DocRead / TableStat / ImageOps）。"""
    sb = sandbox or default_sandbox()

    def doc_read_handler(path: str, sheet: str = "", **_):
        return doc_read(path, sb, sheet=sheet or "")

    def table_stat_handler(path: str, group_by: str = "",
                           agg: str = "", **_):
        return table_stat(path, sb, group_by=group_by or "", agg=agg or "")

    def image_ops_handler(path: str, action: str = "info",
                          out: str = "", size: str = "", **_):
        w, h = _parse_size(size)
        return image_ops(action, path, sb, output=out or "",
                         width=w, height=h)

    return [
        Tool(name="DocRead",
             description="读取 docx/xlsx/pptx/csv 文档的文本",
             keywords=["docx", "xlsx", "pptx", "word", "excel",
                       "幻灯片", "文档", "表格"],
             handler=doc_read_handler,
             parameters={"type": "object", "properties": {
                 "path": {"type": "string", "description": "文档路径"},
                 "sheet": {"type": "string",
                           "description": "仅 xlsx：工作表名，默认首个"}},
                 "required": ["path"]},
             risk=RiskLevel.LOW),
        Tool(name="TableStat",
             description="统计 csv/xlsx 表格：行列数、缺失值、分组汇总",
             keywords=["统计", "汇总", "求和", "平均", "分组", "数据分析"],
             handler=table_stat_handler,
             parameters={"type": "object", "properties": {
                 "path": {"type": "string", "description": "表格路径"},
                 "group_by": {"type": "string", "description": "分组列名"},
                 "agg": {"type": "string", "description": "汇总列名"}},
                 "required": ["path"]},
             risk=RiskLevel.LOW),
        Tool(name="ImageOps",
             description="查看图片信息或转换格式、缩放尺寸",
             keywords=["图片", "图像", "png", "jpg", "缩放", "压缩", "尺寸"],
             handler=image_ops_handler,
             parameters={"type": "object", "properties": {
                 "path": {"type": "string", "description": "图片路径"},
                 "action": {"type": "string",
                            "description": "info / convert / resize"},
                 "out": {"type": "string", "description": "输出路径"},
                 "size": {"type": "string", "description": "如 800x600"}},
                 "required": ["path", "action"]},
             risk=RiskLevel.MEDIUM),
    ]
