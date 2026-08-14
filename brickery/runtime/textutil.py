"""轻量分词工具（中文 2-gram + 西文整词），供工具 / 技能筛选复用。

纯自研、无外部依赖。仅服务「工具/技能关键词筛选」这一场景，
与记忆子系统的分词（memory/engine.py，可选 jieba）互相独立、互不影响。

设计要点（2026-08-07 修订，修复发布级误命中缺陷）
------------------------------------------------
旧实现先删除所有非字母数字字符（含空格），再对整串滑动切 2-gram。
这对中文正确，但对西文是灾难：``read`` → {re, ea, ad}、``web`` → {we, eb}，
于是任意英文句子都能撞上工具关键词。实测「hello how are you today」
命中 8/12 个工具，每轮白烧约 700 token，且显著提高选错工具的概率。

现实现按字符类别切段：

* **CJK / 非 ASCII 字母段**：保持 2-gram 滑窗（中文没有词边界，必须如此），
  整段也作为词元加入，便于「神经网络」这类精确子串匹配。
* **ASCII 字母数字段**：按整词处理，小写归一，**不切 gram**；
  长度 ≥4 且以 s 结尾时额外加入去复数形式（files → file）。
* 其余字符（空格、标点、符号）一律作为分隔符。

这样「read the file」仍能命中 read/file 关键词，而「hello how are you」
不会命中任何工具。
"""
from __future__ import annotations


def tokenize(text: str) -> set[str]:
    """返回文本的词元集合，用于关键词重叠匹配。

    - 中文（及其它非 ASCII 文字）按 2-gram 滑窗切分，整段也加入。
    - 西文/数字按整词切分并小写归一，不切 gram（避免子串误命中）。
    - 空文本返回空集。
    """
    if not text:
        return set()

    out: set[str] = set()
    buf: list[str] = []
    kind: str | None = None

    def flush() -> None:
        nonlocal buf, kind
        if not buf:
            return
        seg = "".join(buf)
        if kind == "ascii":
            word = seg.lower()
            out.add(word)
            # 轻量复数归一：files → file，避免关键词表被迫穷举单复数
            if len(word) >= 4 and word.endswith("s"):
                out.add(word[:-1])
        else:
            out.add(seg)
            for i in range(len(seg) - 1):
                out.add(seg[i:i + 2])
        buf = []
        kind = None

    for ch in text:
        if ch.isalnum():
            k = "ascii" if ch.isascii() else "cjk"
            if kind is not None and k != kind:
                flush()
            kind = k
            buf.append(ch)
        else:
            flush()
    flush()
    return out
