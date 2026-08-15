"""§3.4 增强：repo_map —— 结构化代码符号索引（零依赖，本地优先）。

为代码任务提供「文件 → 符号 → 行范围」的轻量索引，让 agent 能快速定位
函数 / 类 / 方法，不必每次全量读文件。对标 Aider 的 repo map，但用**零依赖**
实现（Python 用 ast 精确解析，其它语言用通用正则兜底），契合 Shadeling 的
「本地优先、最小依赖」原则（MCP 客户端同样纯 stdlib）。

tree-sitter 可作为可选升级：装好 `tree_sitter` + 对应语言 grammar 后，可在
`_symbols_for_file` 处插入 tree-sitter 解析分支以获得更全的多语言符号覆盖；
当前默认路径不引入任何外部依赖，保证开箱即跑。
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Dict, List, Tuple

# 单文件解析字节上限（超此视为非源码/过大，跳过）
_MAX_FILE_CHARS = 200 * 1024
# 单仓库符号上限（防爆炸）
_MAX_SYMBOLS = 400

# 遍历时需跳过的目录
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".build", "build",
              "dist", "target", "venv", ".venv", "Pods", ".brickery",
              ".idea", ".vscode"}

# 纳入索引的代码扩展名
_CODE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs",
              ".c", ".cpp", ".h", ".hpp", ".swift", ".kt", ".rb", ".php",
              ".m", ".mm", ".sh", ".sql", ".scala", ".lua"}

# Python 用 ast 精确抽取；其余语言用正则兜底
_PY_EXT = {".py"}

# 通用声明正则（函数 / fn / func / def / class / 裸方法定义）
_DECL_RE = re.compile(
    r'^\s*(?:'
    r'(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_]\w*)|'   # JS/TS function
    r'(?:pub(?:\([^)]*\))?\s+)?fn\s+([A-Za-z_]\w*)|'           # Rust/go fn
    r'(?:func|sub)\s+([A-Za-z_]\w*)|'                            # Go func / Perl sub
    r'def\s+([A-Za-z_]\w*)|'                                     # Python def（ast 优先）
    r'class\s+([A-Za-z_]\w*)|'                                   # class
    r'([A-Za-z_]\w*)\s*\([^)]*\)\s*\{'                           # 裸方法/对象简写 method（JS/TS class 内）
    r')',
    re.M,
)

# C/Java/Kotlin 风格「返回类型 方法名(...) {」兜底（排除控制关键字）
_METHOD_RE = re.compile(
    r'^\s*(?:[A-Za-z_][\w<>\[\],\.\s*&:]*?\s+)([A-Za-z_]\w*)\s*\([^;{]*\)\s*(?:const\s*)?\{',
    re.M,
)
_CONTROL = {"if", "for", "while", "switch", "catch", "do", "else", "return",
            "typeof", "sizeof", "new", "foreach", "lock", "using", "try",
            "with", "finally"}


def _py_symbols(src: str) -> List[Tuple[str, int, int]]:
    """用 ast 抽取 Python 符号（类 / 函数 / 方法 + 行号）。返回 [(name, lineno, endlineno)]。"""
    out: List[Tuple[str, int, int]] = []
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return out
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
            lineno = getattr(node, "lineno", 0) or 0
            end = getattr(node, "end_lineno", lineno) or lineno
            out.append((name, lineno, end))
    return out


def _regex_symbols(src: str) -> List[Tuple[str, int, int]]:
    out: List[Tuple[str, int, int]] = []
    seen: set = set()
    for i, line in enumerate(src.splitlines(), 1):
        for rx in (_DECL_RE, _METHOD_RE):
            m = rx.match(line)
            if not m:
                continue
            name = next((g for g in m.groups() if g), None)
            if not name or name in _CONTROL or name in seen:
                continue
            seen.add(name)
            out.append((name, i, i))
            break
    return out


def _symbols_for_file(path: Path) -> List[Tuple[str, int, int]]:
    try:
        if path.stat().st_size > _MAX_FILE_CHARS:
            return []
        src = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return []
    if path.suffix in _PY_EXT:
        syms = _py_symbols(src)
        if syms:
            return syms
    return _regex_symbols(src)


def _fmt_block(rel: str, syms: List[Tuple[str, int, int]]) -> str:
    lines = [f"{rel}:"]
    for name, lo, hi in syms:
        lines.append(f"  {name} (L{lo}-{hi})" if hi > lo else f"  {name} (L{lo})")
    return "\n".join(lines)


def build_repo_map(root: str, max_depth: int = 6) -> str:
    """遍历 root（文件或目录），产出符号索引文本。失败返回带 [repo_map] 前缀的错误串。"""
    base = Path(root).expanduser()
    if not base.exists():
        return f"[repo_map] 路径不存在：{root}"

    if base.is_file():
        syms = _symbols_for_file(base)
        if not syms:
            return f"[repo_map] 未在 {root} 中识别到代码符号"
        return _fmt_block(base.name, syms)

    total = 0
    blocks: List[str] = []
    try:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            depth = dirpath.count(os.sep) - str(base).count(os.sep)
            if depth > max_depth:
                dirnames[:] = []
                continue
            for fn in sorted(filenames):
                if total >= _MAX_SYMBOLS:
                    blocks.append("...[符号已达上限，省略剩余]")
                    return "\n".join(blocks)
                fp = Path(dirpath) / fn
                if fp.suffix.lower() not in _CODE_EXTS:
                    continue
                syms = _symbols_for_file(fp)
                if not syms:
                    continue
                try:
                    rel = fp.relative_to(base)
                except ValueError:
                    rel = fp
                blocks.append(_fmt_block(str(rel), syms))
                total += len(syms)
    except OSError as e:
        return f"[repo_map] 遍历失败：{e}"

    if not blocks:
        return f"[repo_map] 在 {root} 下未识别到代码符号"
    return "\n".join(blocks)
