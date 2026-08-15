"""§3.4 阶段 C P0 真实工具（clean room，纯自研）。

实现 Read / Edit / Write / Glob / Grep / Bash 六个真实 handler，全部经沙箱裁决。
返回字符串给主循环（主循环再截断回填 context）。出错返回错误信息字符串，不抛异常。
"""
from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Optional

from .doc_tools import build_doc_tools
from .repo_map import build_repo_map
from .sandbox import Sandbox, default_sandbox
from .tools import RiskLevel, Tool, ToolRegistry
from .vault_store import VaultStore


# 单文件读取上限（Read 工具自身上限，独立于 bash 输出上限）
_READ_LIMIT = 200 * 1024
# Glob / Grep 结果条数上限
_LIST_LIMIT = 500
_GREP_LIMIT = 200
# web_search 返回条数上限
_SEARCH_LIMIT = 8


# ---------------------------------------------------------------------------
# §3.4 C+ 搜索（web_search）—— 显式启用、默认关，零外连红线下的可选外溢。
# 抽象出 SearchProvider，默认用 DuckDuckGo lite HTML（无需 API key）；
# 用户/测试可注入自定义 provider。网络不可用 / 解析失败均优雅降级为错误字符串。
# ---------------------------------------------------------------------------
class SearchProvider:
    """搜索 provider 抽象。search(query) -> List[{"title","url","snippet"}]。"""

    def search(self, query: str) -> List[dict]:
        raise NotImplementedError


class DuckDuckGoProvider(SearchProvider):
    """DuckDuckGo lite HTML 解析（best-effort，无 key）。网络失败返回 [{"error":...}]。"""

    def __init__(self, timeout: float = 10.0, ua: str = "Mozilla/5.0"):
        self.timeout = timeout
        self.ua = ua

    def search(self, query: str) -> List[dict]:
        q = urllib.parse.quote_plus(query)
        url = f"https://html.duckduckgo.com/html/?q={q}"
        req = urllib.request.Request(url, headers={"User-Agent": self.ua})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                html = resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            return [{"error": f"搜索请求失败：{type(e).__name__}: {e}"}]
        return _parse_ddg_html(html)


def _parse_ddg_html(html: str) -> List[dict]:
    """从 DDG lite 结果页提取标题/链接/摘要。解析失败返回空列表（不崩）。"""
    results: List[dict] = []
    # 每个结果块：<a class="result__a" href="...">标题</a> ... <a class="result__snippet">摘要</a>
    for m in re.finditer(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
        r'.*?class="result__snippet"[^>]*>(.*?)</a>',
        html, re.S,
    ):
        href = _html_unescape(m.group(1))
        # DDG 的跳转链接 uddg= 后跟真实 URL（URL 编码）
        real = _extract_ddg_real_url(href)
        title = _strip_tags(m.group(2))
        snippet = _strip_tags(m.group(3))
        results.append({"title": title, "url": real or href, "snippet": snippet})
        if len(results) >= _SEARCH_LIMIT:
            break
    return results


def _extract_ddg_real_url(href: str) -> Optional[str]:
    mm = re.search(r"uddg=([^&]+)", href)
    if mm:
        try:
            return urllib.parse.unquote_plus(mm.group(1))
        except Exception:
            return None
    return None


def _strip_tags(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    return _html_unescape(s).strip()


def _html_unescape(s: str) -> str:
    # 仅处理常见实体，避免引入 html 模块依赖也能跑
    return (s.replace("&amp;", "&").replace("&lt;", "<")
             .replace("&gt;", ">").replace("&quot;", '"')
             .replace("&#x27;", "'").replace("&apos;", "'"))


def _web_search(query: str, provider: Optional[SearchProvider] = None) -> str:
    prov = provider or _default_search_provider
    try:
        results = prov.search(query)
    except Exception as e:  # noqa: BLE001
        return f"[搜索失败] {type(e).__name__}: {e}"
    if not results:
        return f"[无结果] 搜索「{query}」未返回结果"
    if "error" in results[0]:
        return f"[搜索失败] {results[0]['error']}"
    lines: List[str] = []
    for i, r in enumerate(results[:_SEARCH_LIMIT], 1):
        title = r.get("title", "")
        url = r.get("url", "")
        snippet = r.get("snippet", "")
        lines.append(f"{i}. {title}\n   {url}\n   {snippet}")
    return "\n".join(lines)


# 进程内默认 provider（可被测试 monkeypatch / 注册表工厂覆盖）
_default_search_provider = DuckDuckGoProvider()

# WebFetch 返回正文上限（独立于 bash 输出上限）
_FETCH_LIMIT = 60 * 1024


def _web_fetch(url: str, sandbox: Optional[Sandbox] = None) -> str:
    """抓取指定 URL 并抽取可读正文（纯 stdlib urllib + 轻量 HTML 清洗）。

    默认 disabled（隐私同 web_search，需用户在工具面板显式启用才联网）。
    仅允许 http/https；**拒绝 file:// 等本地 scheme** 以防被当作沙箱绕过通道
    去读本机文件。网络失败 / 解析失败均优雅降级为带 [web_fetch] 前缀的错误串。
    """
    u = (url or "").strip()
    if not u:
        return "[web_fetch] 缺少 url 参数"
    parsed = urllib.parse.urlparse(u)
    if parsed.scheme not in ("http", "https"):
        return (f"[web_fetch] 仅支持 http/https"
                f"（拒绝 {parsed.scheme or '空'} scheme，防本地文件绕过）")
    req = urllib.request.Request(
        u, headers={"User-Agent": "Mozilla/5.0 (Brickery)"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return f"[web_fetch] 请求失败：{type(e).__name__}: {e}"
    charset = "utf-8"
    mm = re.search(r"charset=([\w-]+)", ctype)
    if mm:
        charset = mm.group(1)
    text = raw.decode(charset, errors="replace")
    if "html" in ctype or "<" in text[:200]:
        text = _extract_readable(text)
    if len(text) > _FETCH_LIMIT:
        text = text[:_FETCH_LIMIT] + f"\n...[正文已截断 {len(text) - _FETCH_LIMIT} 字节]"
    return text


def _extract_readable(html: str) -> str:
    """抽取可读正文：优先取 <title>，再去掉 script/style/head 取 body 文本。best-effort。"""
    title = ""
    mt = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if mt:
        title = _strip_tags(mt.group(1))
    cleaned = re.sub(r"<(script|style|head|noscript)[^>]*>.*?</\1>",
                     " ", html, flags=re.S | re.I)
    body = re.sub(r"<[^>]+>", " ", cleaned)
    body = _html_unescape(body)
    body = re.sub(r"\s+", " ", body).strip()
    if title:
        return f"[title] {title}\n\n{body}"
    return body


def _repo_map(path: str, sandbox: Sandbox) -> str:
    """生成目录/文件的结构化代码符号索引（文件→类/函数→行范围）。本地零依赖。"""
    ok, reason = sandbox.check_path_read(path)
    if not ok:
        return f"[沙箱拒绝] {reason}"
    try:
        return build_repo_map(path)
    except Exception as e:  # noqa: BLE001
        return f"[repo_map] 失败：{type(e).__name__}: {e}"


def _read_file(path: str, sandbox: Sandbox) -> str:
    ok, reason = sandbox.check_path_read(path)
    if not ok:
        return f"[沙箱拒绝] {reason}"
    p = Path(path).expanduser()
    if not p.exists():
        return f"[错误] 路径不存在：{path}"
    if not p.is_file():
        return f"[错误] 不是文件：{path}"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return f"[错误] 读取失败：{type(e).__name__}: {e}"
    if len(text) > _READ_LIMIT:
        text = text[:_READ_LIMIT] + f"\n...[文件已截断 {len(text) - _READ_LIMIT} 字节]"
    return text


def _write_file(path: str, content: str, sandbox: Sandbox) -> str:
    ok, reason = sandbox.check_path_write(path)
    if not ok:
        return f"[沙箱拒绝] {reason}"
    p = Path(path).expanduser()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return f"[错误] 写入失败：{type(e).__name__}: {e}"
    return f"已写入 {len(content)} 字符到 {p}"


def _edit_file(path: str, old_string: str, new_string: str, sandbox: Sandbox) -> str:
    ok, reason = sandbox.check_path_write(path)
    if not ok:
        return f"[沙箱拒绝] {reason}"
    p = Path(path).expanduser()
    if not p.is_file():
        return f"[错误] 不是文件或不存在：{path}"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return f"[错误] 读取失败：{type(e).__name__}: {e}"
    if old_string not in text:
        return f"[错误] 未在文件中找到待替换文本：{old_string[:40]!r}…"
    new_text = text.replace(old_string, new_string, 1)
    try:
        p.write_text(new_text, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return f"[错误] 写回失败：{type(e).__name__}: {e}"
    delta = len(new_text) - len(text)
    return f"已替换 1 处（文件净变化 {delta:+d} 字符）：{p}"


def _glob(pattern: str, path: Optional[str], sandbox: Sandbox) -> str:
    base = path or str(Path.cwd())
    ok, reason = sandbox.check_path_read(base)
    if not ok:
        return f"[沙箱拒绝] {reason}"
    try:
        matches = sorted(str(Path(m).expanduser())
                         for m in Path(base).expanduser().glob(pattern))
    except Exception as e:  # noqa: BLE001
        return f"[错误] glob 失败：{type(e).__name__}: {e}"
    if not matches:
        return f"[无匹配] 在 {base} 下未匹配：{pattern}"
    if len(matches) > _LIST_LIMIT:
        matches = matches[:_LIST_LIMIT] + [f"...[仅显示前 {_LIST_LIMIT} 条]"]
    return "\n".join(matches)


def _grep(pattern: str, path: Optional[str], glob: Optional[str],
          sandbox: Sandbox) -> str:
    base = path or str(Path.cwd())
    ok, reason = sandbox.check_path_read(base)
    if not ok:
        return f"[沙箱拒绝] {reason}"
    try:
        rx = __import__("re").compile(pattern)
    except Exception as e:  # noqa: BLE001
        return f"[错误] 正则无效：{e}"
    root = Path(base).expanduser()
    results: List[str] = []
    try:
        files = [f for f in root.rglob(glob or "*") if f.is_file()]
    except Exception as e:  # noqa: BLE001
        return f"[错误] 遍历失败：{type(e).__name__}: {e}"
    for f in files:
        try:
            if f.stat().st_size > _READ_LIMIT:
                continue
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines, 1):
            if rx.search(line):
                results.append(f"{f}:{i}: {line}")
                if len(results) >= _GREP_LIMIT:
                    results.append(f"...[仅显示前 {_GREP_LIMIT} 条匹配]")
                    return "\n".join(results)
    if not results:
        return f"[无匹配] 在 {base} 下未匹配：{pattern}"
    return "\n".join(results)


def _bash(command: str, sandbox: Sandbox) -> str:
    rc, out, err = sandbox.run_captured(command)
    parts = []
    if out:
        parts.append(out)
    if err:
        parts.append(f"[stderr]\n{err}")
    parts.append(f"[returncode={rc}]")
    return "\n".join(parts)


# 受限代码运行（D P1）：在沙箱内的临时目录跑 python/node 片段，复用沙箱输出/超时上限。
# 不读用户键盘、不碰系统区；代码只在自己的 scratch 目录里跑。
import subprocess as _subprocess
import tempfile as _tempfile

_CODE_LANGS = {
    "python": ("python3", "py"),
    "python3": ("python3", "py"),
    "py": ("python3", "py"),
    "node": ("node", "js"),
    "javascript": ("node", "js"),
    "js": ("node", "js"),
}


def _code_run(language: str, code: str, sandbox: Optional[Sandbox] = None) -> str:
    lang = (language or "").strip().lower()
    if lang not in _CODE_LANGS:
        return ("[错误] 不支持的语言：{!r}（仅支持 python/python3/node/javascript）"
                .format(language))
    interpreter, ext = _CODE_LANGS[lang]
    sb = sandbox or default_sandbox()
    # scratch 目录落在 ~/.brickery 下（属写白名单），与用户文件隔离
    base = Path.home() / ".brickery" / ".coderun_tmp"
    try:
        base.mkdir(parents=True, exist_ok=True)
        tmp = Path(_tempfile.mkdtemp(prefix="run_", dir=str(base)))
        src = tmp / f"code.{ext}"
        src.write_text(code or "", encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return f"[错误] 准备运行环境失败：{type(e).__name__}: {e}"
    try:
        res = _subprocess.run([interpreter, str(src)], cwd=str(tmp),
                              capture_output=True, text=True,
                              timeout=sb.timeout)
        out = res.stdout or ""
        err = res.stderr or ""
        if len(out) > sb.output_limit:
            out = out[:sb.output_limit] + f"\n...[stdout 已截断 {len(out) - sb.output_limit} 字节]"
        if len(err) > sb.output_limit:
            err = err[:sb.output_limit] + f"\n...[stderr 已截断 {len(err) - sb.output_limit} 字节]"
        parts = []
        if out:
            parts.append(out)
        if err:
            parts.append(f"[stderr]\n{err}")
        parts.append(f"[returncode={res.returncode}]")
        return "\n".join(parts)
    except _subprocess.TimeoutExpired:
        return f"[沙箱] 代码运行超时（>{sb.timeout}s）已终止"
    except FileNotFoundError:
        return f"[错误] 解释器未安装：{interpreter}（请先安装）"
    except Exception as e:  # noqa: BLE001
        return f"[错误] 运行失败：{type(e).__name__}: {e}"


def build_p0_registry(sandbox: Optional[Sandbox] = None,
                      search_provider: Optional[SearchProvider] = None) -> ToolRegistry:
    """工厂：返回内置工具注册表（共 13 个）。

    文件/终端 6 个（Read/Glob/Grep/Edit/Write/Bash）+ CodeRun（受限代码运行）
    + web_search（默认关）+ WebFetch（默认关）+ repo_map（本地代码索引）
    + 发布版通用 3 个（DocRead/TableStat/ImageOps，见冻结清单 §3）。
    另有 SpawnAgent/WaitTask 在 ipc.py 注册，内置总数 15，硬上限 16。

    **核心工具常驻（方案 b，随朴 2026-08-07 批准）**：Read/Grep/Edit/Write/Bash
    设 always_available=True，每轮无论关键词是否命中都必推——这是发布版
    的安全地板，确保 Agent 永不致残（实测 0 核心时 ~20% 家常请求拿不到工具）。
    Glob/CodeRun/repo_map 仍走关键词路由（非核心），关键词已补常见说法覆盖。

    风险分级：Read/Glob/Grep/repo_map=LOW；Edit/Write/CodeRun/web_search/
    WebFetch=MEDIUM；Bash=HIGH（需确认）。web_search/WebFetch 默认 disabled
    （联网类，显式启用才触网，守住零默认外连红线）。
    """
    sb = sandbox or default_sandbox()
    provider = search_provider or _default_search_provider

    def read_handler(path: str, **_):
        return _read_file(path, sb)

    def write_handler(path: str, content: str, **_):
        return _write_file(path, content, sb)

    def edit_handler(path: str, old_string: str, new_string: str, **_):
        return _edit_file(path, old_string, new_string, sb)

    def glob_handler(pattern: str, path: Optional[str] = None, **_):
        return _glob(pattern, path, sb)

    def grep_handler(pattern: str, path: Optional[str] = None,
                     glob: Optional[str] = None, **_):
        return _grep(pattern, path, glob, sb)

    def bash_handler(command: str, **_):
        return _bash(command, sb)

    def code_run_handler(language: str, code: str, **_):
        return _code_run(language, code, sb)

    def web_search_handler(query: str, **_):
        return _web_search(query, provider=provider)

    def web_fetch_handler(url: str, **_):
        return _web_fetch(url, sb)

    def repo_map_handler(path: str, **_):
        return _repo_map(path, sb)

    def vault_query_handler(query: str, type: Optional[str] = None,
                           top_k: int = 5, upcoming_days: int = 0, **_):
        """检索用户 Vault 资产（证件/图片/收藏/技能/笔记），返回脱敏摘要。

        仅返回必要字段，不回证件号码全文（除非该资产未设敏感字段）。
        upcoming_days>0 时切换为「提醒模式」：返回未来 N 天内临近到期/生效的资产。
        """
        try:
            store = VaultStore()
            if int(upcoming_days) > 0:
                items = store.upcoming(window_days=int(upcoming_days))
                if not items:
                    return f"[Vault 无提醒] 未来 {upcoming_days} 天内没有临近到期/生效的资产"
                lines = []
                for it in items:
                    label = "到期" if it["field"] == "valid_to" else "生效"
                    lines.append(f"- [{it['type']}] {it['title']} {label}：{it['date']}"
                                 f"（还有 {it['days_left']} 天）doc_type={it.get('doc_type','')}")
                return "\n".join(lines)
            items = store.query(query, type=type, top_k=int(top_k))
        except Exception as e:  # noqa: BLE001
            return f"[Vault 检索失败] {type(e).__name__}: {e}"
        if not items:
            return f"[Vault 无结果] 未找到与「{query}」相关的资产"
        lines = []
        for it in items:
            typ = it.get("type", "")
            title = it.get("title", "")
            extra = ""
            if typ == "document":
                extra = f" 类型={it.get('doc_type','')} 有效期至={it.get('valid_to','')}"
            elif typ == "webpage":
                extra = f" 来源={it.get('source','')} url={it.get('url','')}"
            elif typ == "skill_snapshot":
                extra = f" 版本={it.get('version','')} 类别={it.get('category','')}"
            lines.append(f"- [{typ}] {title}{extra}")
        return "\n".join(lines)

    def vault_save_handler(type: str, title: str = "", content: str = "",
                          url: str = "", source: str = "", **_):
        """把对话中值得长期保存的内容存入用户本地 Vault。

        写入操作，主循环会经确认网关请用户批准，绝不强写。
        """
        typ = (type or "").strip().lower()
        if typ not in ("document", "image", "webpage", "skill_snapshot", "note"):
            return f"[Vault 保存失败] 不支持的类型：{type}"
        fields: Dict[str, Any] = {}
        if content:
            fields["text"] = content
        if url:
            fields["url"] = url
        if source:
            fields["source"] = source
        try:
            store = VaultStore()
            item = store.add({"type": typ, "title": title, "fields": fields})
        except Exception as e:  # noqa: BLE001
            return f"[Vault 保存失败] {type(e).__name__}: {e}"
        return f"[Vault 已保存] 类型={typ} 标题={item.get('title','')} id={item.get('id','')}"

    tools = [
        # —— 5 个核心工具常驻（always_available）：发布版安全地板 ——
        # 无论关键词是否命中，每轮必推，确保 Agent 永不致残（冻结清单方案 b，随朴 2026-08-07 批准）。
        Tool(name="Read", description="读取文件内容",
             keywords=["读文件", "查看文件", "read", "文件内容", "读一下", "打开看"],
             handler=read_handler,
             parameters={"type": "object", "properties": {
                 "path": {"type": "string", "description": "文件路径"}},
                 "required": ["path"]},
             always_available=True,
             risk=RiskLevel.LOW),
        Tool(name="Glob", description="按通配符列出文件或目录",
             keywords=["列出文件", "文件列表", "glob"],
             handler=glob_handler,
             parameters={"type": "object", "properties": {
                 "pattern": {"type": "string", "description": "glob 模式，如 **/*.py"},
                 "path": {"type": "string", "description": "起始目录，缺省当前目录"}},
                 "required": ["pattern"]},
             risk=RiskLevel.LOW),
        Tool(name="Grep", description="在文件中递归搜索文本或正则",
             keywords=["搜索内容", "正则", "grep", "搜代码", "搜", "查找", "找"],
             handler=grep_handler,
             parameters={"type": "object", "properties": {
                 "pattern": {"type": "string", "description": "正则模式"},
                 "path": {"type": "string", "description": "起始目录，缺省当前目录"},
                 "glob": {"type": "string", "description": "限定文件名模式，如 *.py"}},
                 "required": ["pattern"]},
             always_available=True,
             risk=RiskLevel.LOW),
        Tool(name="Edit", description="把文件中一处旧文本替换为新文本",
             keywords=["改文件", "替换", "edit", "修改", "改", "改动", "加注释", "改掉"],
             handler=edit_handler,
             parameters={"type": "object", "properties": {
                 "path": {"type": "string"},
                 "old_string": {"type": "string", "description": "待替换的旧文本"},
                 "new_string": {"type": "string", "description": "新文本"}},
                 "required": ["path", "old_string", "new_string"]},
             always_available=True,
             risk=RiskLevel.MEDIUM),
        Tool(name="Write", description="创建或覆盖写入文件",
             keywords=["写文件", "新建文件", "write", "创建文件", "新建", "写个", "落一个"],
             handler=write_handler,
             parameters={"type": "object", "properties": {
                 "path": {"type": "string"},
                 "content": {"type": "string", "description": "文件内容"}},
                 "required": ["path", "content"]},
             always_available=True,
             risk=RiskLevel.MEDIUM),
        Tool(name="Bash", description="执行 shell 命令",
             keywords=["执行命令", "命令行", "bash", "shell", "终端", "进程", "端口", "杀", "结束进程"],
             handler=bash_handler,
             parameters={"type": "object", "properties": {
                 "command": {"type": "string", "description": "要执行的 shell 命令"}},
                 "required": ["command"]},
             always_available=True,
             risk=RiskLevel.HIGH),
        Tool(name="CodeRun", description="运行 python 或 node 代码片段",
             keywords=["运行代码", "执行代码", "跑脚本", "code", "python", "算一下", "计算"],
             handler=code_run_handler,
             parameters={"type": "object", "properties": {
                 "language": {"type": "string", "description": "语言：python / python3 / node / javascript"},
                 "code": {"type": "string", "description": "要运行的代码片段"}},
                 "required": ["language", "code"]},
             risk=RiskLevel.MEDIUM),
        Tool(name="web_search", description="联网搜索网页",
             keywords=["联网搜索", "上网查", "搜一下", "查资料"],
             handler=web_search_handler,
             parameters={"type": "object", "properties": {
                 "query": {"type": "string", "description": "搜索关键词"}},
                 "required": ["query"]},
             risk=RiskLevel.MEDIUM,
             disabled=True),
        Tool(name="WebFetch", description="抓取网页并提取正文",
             keywords=["抓取网页", "网页正文", "抓url", "读链接"],
             handler=web_fetch_handler,
             parameters={"type": "object", "properties": {
                 "url": {"type": "string", "description": "要抓取的 http/https 链接"}},
                 "required": ["url"]},
             risk=RiskLevel.MEDIUM,
             disabled=True),
        Tool(name="repo_map", description="生成代码符号索引：文件到类/函数/行号",
             keywords=["代码索引", "符号", "代码结构", "定位函数", "仓库", "模块", "项目结构", "有哪些"],
             handler=repo_map_handler,
             parameters={"type": "object", "properties": {
                 "path": {"type": "string", "description": "目录或文件（沙箱白名单内）"}},
                 "required": ["path"]},
             risk=RiskLevel.LOW),
        Tool(name="vault_query", description="检索用户 Vault 个人资产（证件/图片/收藏网页/已装技能/笔记），返回脱敏摘要。也可用于「提醒」：设 upcoming_days>0 时返回未来 N 天内临近到期/生效的资产。当用户问到自己的证件、资料、收藏、已装能力或「有什么快到期的/提醒我」时使用。",
             keywords=["我的证件", "我存的", "我的资料", "我收藏", "查我", "我的技能", "驾照", "护照", "资格证", "身份证", "我有哪些", "我的图片", "我的笔记", "提醒", "到期", "快到期", "别忘了", "什么时候过期", "续期", "该续了"],
             handler=vault_query_handler,
             parameters={"type": "object", "properties": {
                 "query": {"type": "string", "description": "检索关键词，如「驾照」「护照有效期」"},
                 "type": {"type": "string", "description": "可选过滤：document/image/webpage/skill_snapshot/note"},
                 "top_k": {"type": "integer", "description": "返回条数，默认 5"},
                 "upcoming_days": {"type": "integer", "description": "提醒模式：>0 时返回未来 N 天内临近到期/生效的资产（如设 30）"}},
                 "required": ["query"]},
             risk=RiskLevel.LOW),
        Tool(name="vault_save",
             description="把对话中值得长期保存的内容存入用户本地 Vault（证件/合同/图片/收藏网页/笔记）。当用户贴了合同/证件信息、甩了有用链接、或说「记一下/存一下/收藏」时使用；此为写入操作，会请你确认后执行，绝不强写。",
             keywords=["记一下", "存一下", "收藏", "保存", "归档", "vault", "记下", "存进", "记录到", "留着", "存起来", "别丢了", "收好", "备份一下", "这份合同", "这个证件"],
             handler=vault_save_handler,
             always_available=True,
             parameters={"type": "object", "properties": {
                 "type": {"type": "string", "description": "资产类型：document/image/webpage/skill_snapshot/note"},
                 "title": {"type": "string", "description": "标题，省略则用默认名"},
                 "content": {"type": "string", "description": "要保存的正文/摘要文本"},
                 "url": {"type": "string", "description": "网页类资产的链接"},
                 "source": {"type": "string", "description": "来源说明"}},
                 "required": ["type"]},
             risk=RiskLevel.MEDIUM),
    ]
    # 发布版通用工具（DocRead / TableStat / ImageOps）——冻结清单 §3
    tools.extend(build_doc_tools(sb))
    reg = ToolRegistry()
    reg.register_many(tools)
    return reg
