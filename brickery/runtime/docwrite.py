"""DocWrite —— 纯 stdlib 文档生成（docx/xlsx/pptx）。

依据《DocWrite 规格》实现。零第三方依赖：``zipfile`` + ``xml.etree``（与 DocRead
同栈）。AI 只产出结构化内容决策（标题/段落/表格），本模块负责确定性拼装最小合法
OOXML，全程零 LLM token（方案 C 落地）。

纪律：
- 所有 XML 用 ``ElementTree`` 构建，不拼字符串（防注入）。
- 命名空间用 ``register_namespace`` 预注册，输出合法 OOXML。
- 生成后用 ``DocRead``（同栈工具）自验证内容可回读；并校验 zip 内关系引用完整。
- handler 返回字符串，出错返回 ``[DocWrite][错误] ...`` / ``[DocWrite][沙箱拒绝] ...``，
  不抛异常。

调用约定（关键）：``_mk(uri, tag, *children, text=None, **attrib)`` 中，**位置子元素
（嵌套 ``_mk(...)``、``*[...]``、``text=``）必须写在关键字属性（``attr="x"``、``**{...}``）
之前**。违反此约定会触发 SyntaxError，本文件所有调用均已遵守。
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import List, Optional
from xml.etree import ElementTree as ET

from .docwrite_templates import DocTemplate, get_template
from .sandbox import Sandbox, default_sandbox
from .tools import RiskLevel, Tool

# ---------------------------------------------------------------------------
# 命名空间
# ---------------------------------------------------------------------------
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
X = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"

_RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
REL = {
    "officeDocument": _RT + "officeDocument",
    "core-properties": "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
    "extended-properties": _RT + "extended-properties",
    "slide": _RT + "slide",
    "slideLayout": _RT + "slideLayout",
    "slideMaster": _RT + "slideMaster",
    "theme": _RT + "theme",
    "styles": _RT + "styles",
    "footer": _RT + "footer",
    "worksheet": _RT + "worksheet",
}

for _prefix, _uri in [
    ("w", W), ("r", R), ("ct", CT), ("pr", PR), ("x", X),
    ("p", P), ("a", A),
]:
    ET.register_namespace(_prefix, _uri)

_NOW = "2026-01-01T00:00:00Z"  # 元数据时间戳（生成时不必真时戳，避免泄露）


# ---------------------------------------------------------------------------
# 公共辅助
# ---------------------------------------------------------------------------
def _mk(uri: str, tag: str, *children, text: Optional[str] = None, **attrib):
    """按命名空间构造元素。text 设文本，children 追加子元素。"""
    e = ET.Element(f"{{{uri}}}{tag}")
    if text is not None:
        e.text = text
    for c in children:
        if c is not None:
            e.append(c)
    for k, v in attrib.items():
        if v is not None:
            e.set(k, str(v))
    return e


def _serialize(elem) -> bytes:
    """序列化带 XML 声明（双引号，standalone=yes）。"""
    return (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            + ET.tostring(elem, encoding="UTF-8"))


def _col_letter(n: int) -> str:
    """0 基列号 → Excel 列字母（0→A, 25→Z, 26→AA）。"""
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _iter_local(elem, local: str):
    """按「本地名」遍历所有后代，忽略命名空间前缀。"""
    for e in elem.iter():
        tag = e.tag
        if isinstance(tag, str) and tag.rsplit("}", 1)[-1] == local:
            yield e


# ===========================================================================
# docx 生成
# ===========================================================================
def _docx_styles(t: DocTemplate) -> bytes:
    w_attr = lambda sid: {f"{{{W}}}type": "paragraph", f"{{{W}}}styleId": sid}
    styles = _mk(W, "styles",
        _mk(W, "docDefaults",
            _mk(W, "rPrDefault",
                _mk(W, "rPr",
                    _mk(W, "sz", val=str(t.body_size)),
                    _mk(W, "rFonts", ascii="Microsoft YaHei", hAnsi="Microsoft YaHei",
                        eastAsia="Microsoft YaHei"),
                    _mk(W, "color", val=t.text_color))),
            _mk(W, "pPrDefault",
                _mk(W, "pPr",
                    _mk(W, "spacing", after=str(t.space_after),
                        line=str(t.line_spacing), lineRule="auto")))),
        _mk(W, "style",
            _mk(W, "name", val="Normal"),
            **w_attr("Normal")),
        _mk(W, "style",
            _mk(W, "name", val="heading 1"),
            _mk(W, "basedOn", val="Normal"),
            _mk(W, "pPr",
                _mk(W, "spacing", before="240", after=str(t.space_after)),
                _mk(W, "outlineLvl", val="0")),
            _mk(W, "rPr",
                _mk(W, "b"),
                _mk(W, "sz", val=str(t.heading1_size)),
                _mk(W, "color", val=t.primary_color)),
            **w_attr("Heading1")),
        _mk(W, "style",
            _mk(W, "name", val="heading 2"),
            _mk(W, "basedOn", val="Normal"),
            _mk(W, "pPr",
                _mk(W, "spacing", before="160", after=str(t.space_after)),
                _mk(W, "outlineLvl", val="1")),
            _mk(W, "rPr",
                _mk(W, "b"),
                _mk(W, "sz", val=str(t.heading2_size)),
                _mk(W, "color", val=t.accent_color)),
            **w_attr("Heading2")),
        _mk(W, "style",
            _mk(W, "name", val="heading 3"),
            _mk(W, "basedOn", val="Normal"),
            _mk(W, "pPr",
                _mk(W, "spacing", before="120", after=str(t.space_after)),
                _mk(W, "outlineLvl", val="2")),
            _mk(W, "rPr",
                _mk(W, "b"),
                _mk(W, "sz", val=str(t.heading3_size)),
                _mk(W, "color", val=t.accent_color)),
            **w_attr("Heading3")),
        _mk(W, "style",
            _mk(W, "name", val="Title"),
            _mk(W, "basedOn", val="Normal"),
            _mk(W, "pPr",
                _mk(W, "spacing", after="240"),
                _mk(W, "jc", val="center")),
            _mk(W, "rPr",
                _mk(W, "b"),
                _mk(W, "sz", val="40"),
                _mk(W, "color", val=t.primary_color)),
            **w_attr("Title")),
    )
    return _serialize(styles)


def _docx_footer() -> bytes:
    ftr = _mk(W, "ftr",
        _mk(W, "p",
            _mk(W, "pPr", _mk(W, "jc", val="center")),
            _mk(W, "r", _mk(W, "fldChar", fldCharType="begin")),
            _mk(W, "r", _mk(W, "instrText", text=" PAGE ",
                                **{f"{{{W}}}space": "preserve"})),
            _mk(W, "r", _mk(W, "fldChar", fldCharType="end"))))
    return _serialize(ftr)


def _docx_para(style_id: str, text: str) -> ET.Element:
    return _mk(W, "p",
        _mk(W, "pPr", _mk(W, "pStyle", val=style_id)),
        _mk(W, "r", _mk(W, "t", text=str(text))))


def _docx_bullet(text: str) -> ET.Element:
    return _mk(W, "p",
        _mk(W, "pPr", _mk(W, "ind", left="720")),
        _mk(W, "r", _mk(W, "t", text="• " + str(text))))


def _docx_table(t: DocTemplate, headers: List[str],
                rows: List[List[str]]) -> ET.Element:
    ncol = max(len(headers), *(len(r) for r in rows)) if rows else len(headers)
    ncol = max(ncol, 1)
    colw = 9000 // ncol  # 可用宽度约 9000 twips
    borders = _mk(W, "tblBorders",
        _mk(W, "top", val="single", sz="4", space="0", color="auto"),
        _mk(W, "bottom", val="single", sz="4", space="0", color="auto"),
        _mk(W, "left", val="single", sz="4", space="0", color="auto"),
        _mk(W, "right", val="single", sz="4", space="0", color="auto"),
        _mk(W, "insideH", val="single", sz="4", space="0", color="auto"),
        _mk(W, "insideV", val="single", sz="4", space="0", color="auto"))
    tbl_pr = _mk(W, "tblPr",
        _mk(W, "tblW", w="5000", type="pct"),
        borders,
        _mk(W, "tblLook", val="04A0", firstRow="1", lastRow="0",
            firstColumn="1", lastColumn="0", noHBand="0", noVBand="1"))
    grid = _mk(W, "tblGrid", *[_mk(W, "gridCol", w=str(colw))
                                for _ in range(ncol)])

    def cell(text, fill, bold, color):
        tc_pr = _mk(W, "tcPr",
            _mk(W, "tcW", w=str(colw), type="dxa"),
            _mk(W, "shd", val="clear", color="auto", fill=fill))
        rpr = _mk(W, "rPr")
        if bold:
            rpr.append(_mk(W, "b"))
        rpr.append(_mk(W, "color", val=color))
        rpr.append(_mk(W, "sz", val=str(t.table_header_size)))
        return _mk(W, "tc", tc_pr,
            _mk(W, "p", _mk(W, "r", rpr, _mk(W, "t", text=str(text)))))

    trs = []
    if headers:
        trs.append(_mk(W, "tr", *[cell(h, t.primary_color, True, "FFFFFF")
                                   for h in headers]))
    for i, row in enumerate(rows):
        zebra = t.zebra_color if (t.table_zebra and i % 2 == 1) else "FFFFFF"
        trs.append(_mk(W, "tr", *[cell(c, zebra, False, t.text_color)
                                   for c in row]))
    return _mk(W, "tbl", tbl_pr, grid, *trs)


def _docx_body(t: DocTemplate, title: str,
               sections: List[dict]) -> List[ET.Element]:
    out: List[ET.Element] = []
    if title:
        out.append(_docx_para("Title", title))
    for sec in sections or []:
        stype = (sec or {}).get("type", "paragraph")
        if stype in ("heading1", "heading2", "heading3"):
            out.append(_docx_para(stype.capitalize(), sec.get("text", "")))
        elif stype == "paragraph":
            out.append(_docx_para("Normal", sec.get("text", "")))
        elif stype == "bullet_list":
            for it in sec.get("items", []) or []:
                out.append(_docx_bullet(it))
        elif stype == "table":
            out.append(_docx_table(t, sec.get("headers", []) or [],
                                   sec.get("rows", []) or []))
    return out


def _build_docx(t: DocTemplate, title: str, sections: List[dict]) -> dict:
    body = _docx_body(t, title, sections)
    bg = [_mk(W, "background", val=t.page_bg)] if t.page_bg != "FFFFFF" else []
    document = _mk(W, "document",
        _mk(W, "body", *body,
            _mk(W, "sectPr",
                _mk(W, "footerReference", type="default",
                    **{f"{{{R}}}id": "rId2"}),
                _mk(W, "pgSz", w="11906", h="16838"),
                _mk(W, "pgMar", top="1440", right="1440", bottom="1440",
                    left="1440", header="720", footer="720", gutter="0"))),
        *bg)
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        '</Types>'
    ).encode("utf-8")
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        '</Relationships>'
    ).encode("utf-8")
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>'
        '</Relationships>'
    ).encode("utf-8")
    footer_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="document.xml"/>'
        '</Relationships>'
    ).encode("utf-8")
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<dc:title>{_xml_escape(title or "")}</dc:title>'
        '<dc:creator>Shadeling</dc:creator>'
        '<cp:lastModifiedBy>Shadeling</cp:lastModifiedBy>'
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{_NOW}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{_NOW}</dcterms:modified>'
        '</cp:coreProperties>'
    ).encode("utf-8")
    app = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        '<Application>Shadeling</Application></Properties>'
    ).encode("utf-8")
    return {
        "[Content_Types].xml": content_types,
        "_rels/.rels": root_rels,
        "word/document.xml": _serialize(document),
        "word/styles.xml": _docx_styles(t),
        "word/footer1.xml": _docx_footer(),
        "word/_rels/document.xml.rels": doc_rels,
        "word/_rels/footer1.xml.rels": footer_rels,
        "docProps/core.xml": core,
        "docProps/app.xml": app,
    }


# ===========================================================================
# xlsx 生成
# ===========================================================================
def _xlsx_cell_value(v) -> tuple:
    s = "" if v is None else str(v)
    if s.startswith("="):
        return ("f", s[1:])
    try:
        float(s)
        return ("n", s)
    except ValueError:
        return ("s", s)


def _xlsx_styles(primary: str) -> bytes:
    fill_primary = "FF" + primary  # ARGB
    styles = _mk(X, "styleSheet",
        _mk(X, "fonts",
            _mk(X, "font", _mk(X, "sz", val="11"), _mk(X, "name", val="Calibri")),
            _mk(X, "font", _mk(X, "b"), _mk(X, "sz", val="11"),
                _mk(X, "color", rgb="FFFFFFFF"), _mk(X, "name", val="Calibri"))),
        _mk(X, "fills",
            _mk(X, "fill", _mk(X, "patternFill", patternType="none")),
            _mk(X, "fill", _mk(X, "patternFill", patternType="gray125")),
            _mk(X, "fill", _mk(X, "patternFill",
                _mk(X, "fgColor", rgb=fill_primary),
                _mk(X, "bgColor", indexed="64"),
                patternType="solid"))),
        _mk(X, "borders", _mk(X, "border",
            _mk(X, "left"), _mk(X, "right"), _mk(X, "top"),
            _mk(X, "bottom"), _mk(X, "diagonal"))),
        _mk(X, "cellStyleXfs", _mk(X, "xf", numFmtId="0", fontId="0",
            fillId="0", borderId="0")),
        _mk(X, "cellXfs",
            _mk(X, "xf", numFmtId="0", fontId="0", fillId="0",
                borderId="0", xfId="0"),
            _mk(X, "xf", _mk(X, "alignment", horizontal="center"),
                numFmtId="0", fontId="1", fillId="2", borderId="0",
                xfId="0", applyFont="1", applyFill="1", applyAlignment="1")),
        _mk(X, "cellStyles", _mk(X, "cellStyle", name="Normal",
            xfId="0", builtinId="0")))
    return _serialize(styles)


def _build_xlsx(t: DocTemplate, sheets: List[dict]) -> dict:
    shared: List[str] = []
    shared_idx: dict = {}

    def sidx(s: str) -> int:
        if s in shared_idx:
            return shared_idx[s]
        i = len(shared)
        shared.append(s)
        shared_idx[s] = i
        return i

    sheet_files: dict = {}
    n = max(len(sheets), 1)
    for si in range(n):
        sh = sheets[si] if si < len(sheets) else {}
        rows_xml = []
        r = 1
        headers = sh.get("headers", []) or []
        if headers:
            cells = []
            for ci, h in enumerate(headers):
                ref = f"{_col_letter(ci)}{r}"
                idx = sidx(str(h))
                cells.append(_mk(X, "c", _mk(X, "v", text=str(idx)),
                                 r=ref, t="s"))
            rows_xml.append(_mk(X, "row", *cells, r=str(r)))
            r += 1
        for row in (sh.get("rows", []) or []):
            cells = []
            for ci, val in enumerate(row):
                ref = f"{_col_letter(ci)}{r}"
                kind, content = _xlsx_cell_value(val)
                if kind == "s":
                    idx = sidx(content)
                    cells.append(_mk(X, "c", _mk(X, "v", text=str(idx)),
                                     r=ref, t="s"))
                elif kind == "f":
                    cells.append(_mk(X, "c",
                        _mk(X, "f", text=content),
                        _mk(X, "v"), r=ref, t="str"))
                else:
                    cells.append(_mk(X, "c", _mk(X, "v", text=content), r=ref))
            rows_xml.append(_mk(X, "row", *cells, r=str(r)))
            r += 1
        sheet_xml = _mk(X, "worksheet", _mk(X, "sheetData", *rows_xml))
        sheet_files[f"xl/worksheets/sheet{si + 1}.xml"] = _serialize(sheet_xml)

    sis = [_mk(X, "si", _mk(X, "t", text=s)) for s in shared]
    shared_xml = _mk(X, "sst", *sis)

    sheets_xml = []
    for i in range(n):
        name = (sheets[i].get("name") if i < len(sheets) else None) or f"Sheet{i + 1}"
        sheets_xml.append(_mk(X, "sheet", name=str(name), sheetId=str(i + 1),
                              **{f"{{{R}}}id": f"rId{i + 1}"}))
    workbook = _mk(X, "workbook", _mk(X, "sheets", *sheets_xml))

    rels = [_mk(PR, "Relationship", Id=f"rId{i + 1}", Type=REL["worksheet"],
                Target=f"worksheets/sheet{i + 1}.xml") for i in range(n)]
    workbook_rels = _mk(PR, "Relationships", *rels)

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + "".join(
            f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(n))
        + '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        '</Types>'
    ).encode("utf-8")
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        '</Relationships>'
    ).encode("utf-8")
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        '<dc:title>Shadeling</dc:title>'
        '<dc:creator>Shadeling</dc:creator>'
        '<cp:lastModifiedBy>Shadeling</cp:lastModifiedBy>'
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{_NOW}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{_NOW}</dcterms:modified>'
        '</cp:coreProperties>'
    ).encode("utf-8")
    app = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        '<Application>Shadeling</Application></Properties>'
    ).encode("utf-8")
    parts = {
        "[Content_Types].xml": content_types,
        "_rels/.rels": root_rels,
        "xl/workbook.xml": _serialize(workbook),
        "xl/_rels/workbook.xml.rels": _serialize(workbook_rels),
        "xl/styles.xml": _xlsx_styles(t.primary_color),
        "xl/sharedStrings.xml": _serialize(shared_xml),
        "docProps/core.xml": core,
        "docProps/app.xml": app,
    }
    parts.update(sheet_files)
    return parts


# ===========================================================================
# pptx 生成
# ===========================================================================
def _a_run(text: str, size: int = 1800, bold: bool = False,
           color: Optional[str] = None):
    rpr = _mk(A, "rPr", sz=str(size))
    if bold:
        rpr.append(_mk(A, "b"))
    if color:
        rpr.append(_mk(A, "solidFill", _mk(A, "srgbClr", val=color)))
    return _mk(A, "r", rpr, _mk(A, "t", text=text))


def _a_para(runs, bullet: bool = False):
    pPr = _mk(A, "pPr")
    if bullet:
        pPr.append(_mk(A, "buChar", char="•"))
    return _mk(A, "p", pPr, *runs)


def _p_shape(sp_id: int, name: str, x: int, y: int, w: int, h: int,
             paras) -> ET.Element:
    return _mk(P, "sp",
        _mk(P, "nvSpPr",
            _mk(P, "cNvPr", id=str(sp_id), name=name),
            _mk(P, "cNvSpPr"),
            _mk(P, "nvPr")),
        _mk(P, "spPr",
            _mk(A, "xfrm",
                _mk(A, "off", x=str(x), y=str(y)),
                _mk(A, "ext", cx=str(w), cy=str(h))),
            _mk(A, "prstGeom", _mk(A, "avLst"), prst="rect")),
        _mk(P, "txBody",
            _mk(A, "bodyPr"),
            _mk(A, "lstStyle"),
            *paras))


def _p_slide(t: DocTemplate, slide: dict) -> bytes:
    stype = slide.get("type", "content")
    paras = []
    if stype == "title":
        paras.append(_a_para([_a_run(slide.get("title", ""), size=4400,
                                      bold=True, color=t.primary_color)]))
        sub = slide.get("subtitle", "")
        if sub:
            paras.append(_a_para([_a_run(sub, size=2400, color=t.text_color)]))
    elif stype == "table":
        paras.append(_a_para([_a_run(slide.get("title", ""), size=3600,
                                      bold=True, color=t.primary_color)]))
        headers = slide.get("headers", []) or []
        rows = slide.get("rows", []) or []
        for row in ([headers] if headers else []) + rows:
            line = " | ".join(str(c) for c in row)
            paras.append(_a_para([_a_run(line, size=1800, color=t.text_color)]))
    else:  # content
        paras.append(_a_para([_a_run(slide.get("title", ""), size=3600,
                                      bold=True, color=t.primary_color)]))
        for it in slide.get("items", []) or []:
            paras.append(_a_para([_a_run(it, size=2000, color=t.text_color)],
                                 bullet=True))
    sp = _p_shape(2, "TextBox 1", 838200, 365125, 10515600, 4670250, paras)
    sld = _mk(P, "sld",
        _mk(P, "cSld", _mk(P, "spTree",
            _mk(P, "nvGrpSpPr",
                _mk(P, "cNvPr", id="1", name=""),
                _mk(P, "cNvGrpSpPr"),
                _mk(P, "nvPr")),
            _mk(P, "grpSpPr"),
            sp)),
        _mk(P, "clrMapOvr", _mk(A, "masterClrMapping")))
    return _serialize(sld)


def _p_theme(t: DocTemplate) -> bytes:
    theme = _mk(A, "theme",
        _mk(A, "themeElements",
            _mk(A, "clrScheme",
                _mk(A, "dk1", _mk(A, "sysClr", val="windowText", lastClr="000000")),
                _mk(A, "lt1", _mk(A, "sysClr", val="window", lastClr="FFFFFF")),
                _mk(A, "dk2", _mk(A, "srgbClr", val=t.primary_color)),
                _mk(A, "lt2", _mk(A, "srgbClr", val="FFFFFF")),
                _mk(A, "accent1", _mk(A, "srgbClr", val=t.primary_color)),
                _mk(A, "accent2", _mk(A, "srgbClr", val=t.accent_color)),
                _mk(A, "accent3", _mk(A, "srgbClr", val=t.zebra_color)),
                _mk(A, "accent4", _mk(A, "srgbClr", val="E0E0E0")),
                _mk(A, "accent5", _mk(A, "srgbClr", val="8E44AD")),
                _mk(A, "accent6", _mk(A, "srgbClr", val="C55A11")),
                _mk(A, "hlink", _mk(A, "srgbClr", val="0563C1")),
                _mk(A, "folHlink", _mk(A, "srgbClr", val="954F72")),
                name="Office"),
            _mk(A, "fontScheme",
                _mk(A, "majorFont",
                    _mk(A, "latin", typeface="Microsoft YaHei"),
                    _mk(A, "ea", typeface=""),
                    _mk(A, "cs", typeface="")),
                _mk(A, "minorFont",
                    _mk(A, "latin", typeface="Microsoft YaHei"),
                    _mk(A, "ea", typeface=""),
                    _mk(A, "cs", typeface="")),
                name="Office"),
            _mk(A, "fmtScheme",
                _mk(A, "fillStyleLst",
                    _mk(A, "solidFill", _mk(A, "schemeClr", val="phClr")),
                    _mk(A, "solidFill", _mk(A, "schemeClr", val="phClr")),
                    _mk(A, "solidFill", _mk(A, "schemeClr", val="phClr"))),
                _mk(A, "lnStyleLst",
                    _mk(A, "ln", _mk(A, "solidFill", _mk(A, "schemeClr", val="phClr")),
                        w="6350", cap="flat", cmpd="sng", algn="ctr"),
                    _mk(A, "ln", _mk(A, "solidFill", _mk(A, "schemeClr", val="phClr")),
                        w="12700", cap="flat", cmpd="sng", algn="ctr"),
                    _mk(A, "ln", _mk(A, "solidFill", _mk(A, "schemeClr", val="phClr")),
                        w="19050", cap="flat", cmpd="sng", algn="ctr")),
                _mk(A, "effectStyleLst",
                    _mk(A, "effectStyle", _mk(A, "effectLst")),
                    _mk(A, "effectStyle", _mk(A, "effectLst")),
                    _mk(A, "effectStyle", _mk(A, "effectLst"))),
                _mk(A, "bgFillStyleLst",
                    _mk(A, "solidFill", _mk(A, "schemeClr", val="phClr")),
                    _mk(A, "solidFill", _mk(A, "schemeClr", val="phClr")),
                    _mk(A, "solidFill", _mk(A, "schemeClr", val="phClr"))),
                name="Office")),
        _mk(A, "objectDefaults"),
        _mk(A, "extraClrSchemeLst"))
    return _serialize(theme)


def _build_pptx(t: DocTemplate, slides: List[dict]) -> dict:
    n = max(len(slides), 1)
    slide_files: dict = {}
    slide_rel_files: dict = {}
    for i in range(n):
        slide = slides[i] if i < len(slides) else {"type": "content", "title": ""}
        slide_files[f"ppt/slides/slide{i + 1}.xml"] = _p_slide(t, slide)
        slide_rel_files[f"ppt/slides/_rels/slide{i + 1}.xml.rels"] = _serialize(
            _mk(PR, "Relationships",
                _mk(PR, "Relationship", Id="rId1", Type=REL["slideLayout"],
                    Target="../slideLayouts/slideLayout1.xml")))

    layout = _mk(P, "sldLayout",
        _mk(P, "cSld", _mk(P, "name", text="Blank"),
            _mk(P, "spTree",
                _mk(P, "nvGrpSpPr",
                    _mk(P, "cNvPr", id="1", name=""),
                    _mk(P, "cNvGrpSpPr"),
                    _mk(P, "nvPr")),
                _mk(P, "grpSpPr"))),
        _mk(P, "clrMapOvr", _mk(A, "masterClrMapping")),
        **{f"{{{P}}}type": "blank", f"{{{P}}}preserve": "1"})

    bg = _mk(P, "bg", _mk(P, "bgPr",
        _mk(A, "solidFill", _mk(A, "srgbClr", val=t.page_bg)),
        _mk(A, "effectLst")))
    master = _mk(P, "sldMaster",
        _mk(P, "cSld", bg,
            _mk(P, "spTree",
                _mk(P, "nvGrpSpPr",
                    _mk(P, "cNvPr", id="1", name=""),
                    _mk(P, "cNvGrpSpPr"),
                    _mk(P, "nvPr")),
                _mk(P, "grpSpPr"),
                _mk(P, "sp",
                    _mk(P, "nvSpPr",
                        _mk(P, "cNvPr", id="2", name="Title Placeholder 1"),
                        _mk(P, "cNvSpPr"),
                        _mk(P, "nvPr", _mk(P, "ph", type="title"))),
                    _mk(P, "spPr"),
                    _mk(P, "txBody",
                        _mk(A, "bodyPr"),
                        _mk(A, "lstStyle"),
                        _mk(A, "p"))))),
        _mk(P, "clrMap", bg1="lt1", tx1="dk1", bg2="lt2", tx2="dk2",
            accent1="accent1", accent2="accent2", accent3="accent3",
            accent4="accent4", accent5="accent5", accent6="accent6",
            hlink="hlink", folHlink="folHlink"),
        _mk(P, "sldLayoutIdLst",
            _mk(P, "sldLayoutId", id="2147483649",
                **{f"{{{R}}}id": "rId1"})))

    sld_ids = [_mk(P, "sldId", id=str(256 + i),
                  **{f"{{{R}}}id": f"rId{i + 1}"}) for i in range(n)]
    presentation = _mk(P, "presentation",
        _mk(P, "sldMasterIdLst",
            _mk(P, "sldMasterId", id="2147483648",
                **{f"{{{R}}}id": f"rId{n + 1}"})),
        _mk(P, "sldIdLst", *sld_ids),
        _mk(P, "sldSz", cx="12192000", cy="6858000",
            **{f"{{{P}}}type": "screen16x9"}),
        _mk(P, "notesSz", cx="6858000", cy="9144000"))

    pres_rels = _mk(PR, "Relationships",
        *[_mk(PR, "Relationship", Id=f"rId{i + 1}", Type=REL["slide"],
              Target=f"slides/slide{i + 1}.xml") for i in range(n)],
        _mk(PR, "Relationship", Id=f"rId{n + 1}", Type=REL["slideMaster"],
            Target="slideMasters/slideMaster1.xml"))
    master_rels = _mk(PR, "Relationships",
        _mk(PR, "Relationship", Id="rId1", Type=REL["slideLayout"],
            Target="../slideLayouts/slideLayout1.xml"),
        _mk(PR, "Relationship", Id="rId2", Type=REL["theme"],
            Target="../theme/theme1.xml"))
    layout_rels = _mk(PR, "Relationships",
        _mk(PR, "Relationship", Id="rId1", Type=REL["slideMaster"],
            Target="../slideMasters/slideMaster1.xml"))

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
        + "".join(
            f'<Override PartName="/ppt/slides/slide{i + 1}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
            for i in range(n))
        + '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
        '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
        '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-drawingml.theme+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        '</Types>'
    ).encode("utf-8")
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        '</Relationships>'
    ).encode("utf-8")
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        '<dc:title>Shadeling</dc:title>'
        '<dc:creator>Shadeling</dc:creator>'
        '<cp:lastModifiedBy>Shadeling</cp:lastModifiedBy>'
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{_NOW}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{_NOW}</dcterms:modified>'
        '</cp:coreProperties>'
    ).encode("utf-8")
    app = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        '<Application>Shadeling</Application></Properties>'
    ).encode("utf-8")

    parts = {
        "[Content_Types].xml": content_types,
        "_rels/.rels": root_rels,
        "ppt/presentation.xml": _serialize(presentation),
        "ppt/_rels/presentation.xml.rels": _serialize(pres_rels),
        "ppt/slideLayouts/slideLayout1.xml": _serialize(layout),
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": _serialize(layout_rels),
        "ppt/slideMasters/slideMaster1.xml": _serialize(master),
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": _serialize(master_rels),
        "ppt/theme/theme1.xml": _p_theme(t),
        "docProps/core.xml": core,
        "docProps/app.xml": app,
    }
    parts.update(slide_files)
    parts.update(slide_rel_files)
    return parts


# ===========================================================================
# 结构校验 + 入口
# ===========================================================================
def _validate_ooxml(zip_path: str) -> str:
    """校验 zip 内各 .rels 的 Target 均存在。返回 "" 表示通过，否则为错误串。"""
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = set(z.namelist())
            for name in names:
                if not name.endswith(".rels"):
                    continue
                root = ET.fromstring(z.read(name))
                # .rels 路径为 <dir>/_rels/<file>.rels，其 Target 相对 <dir>
                base_dir = name[:name.index("/_rels/")] if "/_rels/" in name else ""
                for rel in _iter_local(root, "Relationship"):
                    target = rel.get("Target", "")
                    if not target:
                        continue
                    if target.startswith("/"):
                        tgt = target.lstrip("/")
                    else:
                        tgt = (base_dir + "/" + target).replace("//", "/")
                    parts = []
                    for p in tgt.split("/"):
                        if p == "..":
                            if parts:
                                parts.pop()
                        elif p not in ("", "."):
                            parts.append(p)
                    tgt = "/".join(parts)
                    if tgt not in names:
                        return (f"关系引用缺失：{target}"
                                f"（在 {name} 中指向 {tgt}）")
        return ""
    except Exception as e:  # noqa: BLE001
        return f"结构校验失败：{type(e).__name__}: {e}"


def doc_write(format: str, path: str, sandbox: Sandbox,
              template: str = "business-blue", title: str = "",
              sections: Optional[List[dict]] = None,
              sheets: Optional[List[dict]] = None,
              slides: Optional[List[dict]] = None) -> str:
    """生成文档。返回中文成功/错误串，不抛异常。"""
    if not path:
        return "[DocWrite][错误] 缺少输出路径 path"
    ok, reason = sandbox.check_path_write(path)
    if not ok:
        return f"[DocWrite][沙箱拒绝] {reason}"

    fmt = (format or "").strip().lower()
    if fmt not in ("docx", "xlsx", "pptx"):
        return f"[DocWrite][错误] 不支持的格式：{format or '(空)'}（可选 docx/xlsx/pptx）"

    t = get_template(template)
    out = Path(path).expanduser()
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        return f"[DocWrite][错误] 无法创建输出目录：{e}"

    try:
        if fmt == "docx":
            parts = _build_docx(t, title or "", sections or [])
        elif fmt == "xlsx":
            parts = _build_xlsx(t, sheets or [])
        else:
            parts = _build_pptx(t, slides or [])
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in parts.items():
                z.writestr(name, data)
    except Exception as e:  # noqa: BLE001
        return f"[DocWrite][错误] 生成失败：{type(e).__name__}: {e}"

    err = _validate_ooxml(str(out))
    if err:
        return f"[DocWrite][错误] {err}"

    # 用 DocRead 自验证内容可回读
    try:
        from .doc_tools import doc_read
        verify = doc_read(str(out), sandbox)
    except Exception as e:  # noqa: BLE001
        verify = f"[错误] 自验证异常：{type(e).__name__}: {e}"
    if verify.startswith("[错误]") or verify.startswith("[沙箱拒绝]"):
        return f"[DocWrite][错误] 自验证失败：{verify}"

    return f"[DocWrite] 已生成 {fmt} 文档：{out}（{len(parts)} 个部分，模板 {t.name}）"


def build_docwrite_tool(sandbox: Optional[Sandbox] = None) -> Tool:
    """工厂：构造 DocWrite 工具。"""
    sb = sandbox or default_sandbox()

    def handler(format=None, path=None, template="business-blue", title="",
                sections=None, sheets=None, slides=None, **_):
        return doc_write(format or "docx", path or "", sb,
                         template or "business-blue", title or "",
                         sections or [], sheets or [], slides or [])

    return Tool(
        name="DocWrite",
        description="生成 docx/xlsx/pptx 文档文件（纯 stdlib 拼装，零 LLM token）",
        keywords=["生成文档", "写word", "做ppt", "生成excel", "docwrite",
                  "生成报告", "做表格", "写文档", "做幻灯片"],
        handler=handler,
        parameters={
            "type": "object",
            "properties": {
                "format": {"type": "string", "enum": ["docx", "xlsx", "pptx"],
                           "description": "文档格式"},
                "path": {"type": "string", "description": "输出文件路径"},
                "template": {"type": "string",
                             "description": "模板ID，默认 business-blue",
                             "enum": ["business-blue", "forest-green",
                                      "sunset-orange", "mono-gray",
                                      "royal-purple", "midnight-dark"]},
                "title": {"type": "string", "description": "文档标题"},
                "sections": {"type": "array",
                             "description": "docx 内容段落数组",
                             "items": {"type": "object", "properties": {
                                 "type": {"type": "string",
                                          "enum": ["heading1", "heading2",
                                                   "heading3", "paragraph",
                                                   "bullet_list", "table"]},
                                 "text": {"type": "string",
                                          "description": "段落文本"},
                                 "items": {"type": "array",
                                           "items": {"type": "string"},
                                           "description": "列表项"},
                                 "headers": {"type": "array",
                                             "items": {"type": "string"},
                                             "description": "表头"},
                                 "rows": {"type": "array",
                                          "items": {"type": "array",
                                                    "items": {"type": "string"}},
                                          "description": "表格数据行"}}}},
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
            },
            "required": ["format", "path"],
        },
        risk=RiskLevel.MEDIUM,
    )
