"""本地小模型增强（P9 可选积木 memory-smol）。

提供两个增强能力：
- summarize：本地 GGUF 小模型做内容总结（无引擎回落 KeywordExtractor）
- semantic_recall：语义召回（无嵌入模型回落关键词打分）

clean room：不依赖任何外部项目；引擎经 MemorySystem.engine 注入。
无引擎时安全降级，不影响核心闭环。
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Tuple

from .engine import Engine, KeywordExtractor


def summarize(texts: Iterable[str],
              engine: Optional[Engine] = None) -> Tuple[str, List[str]]:
    """内容总结：优先引擎（小模型），无引擎回落 KeywordExtractor。"""
    joined = "\n".join(texts)
    if not joined.strip():
        return "", []
    if engine is not None:
        try:
            prompt = (
                "请用一句话总结以下内容，并给出不超过8个关键词（逗号分隔）。\n"
                "严格按如下格式返回：\nSUMMARY: <摘要>\nKEYWORDS: <k1>,<k2>,...\n\n"
                + joined
            )
            out = engine.chat([{"role": "user", "content": prompt}])
            summary, keywords = _parse_summary(out)
            if summary or keywords:
                return summary, keywords
        except Exception:  # noqa: BLE001 —— 引擎失败回落兜底
            pass
    return KeywordExtractor().extract(texts)


def semantic_recall(query: str, texts: Iterable[str],
                    engine: Optional[Engine] = None,
                    top_k: int = 5) -> List[Tuple[str, float]]:
    """语义召回：优先引擎（嵌入模型），无嵌入模型回落关键词打分。

    返回 [(text, score), ...] 按分数降序，最多 top_k 条。
    """
    items = [t for t in texts if t and t.strip()]
    if not items:
        return []
    if engine is not None and hasattr(engine, "embed"):
        try:
            qv = engine.embed(query)
            scored = []
            for it in items:
                tv = engine.embed(it)
                scored.append((it, _cosine(qv, tv)))
            scored.sort(key=lambda x: -x[1])
            return scored[:top_k]
        except Exception:  # noqa: BLE001 —— 嵌入失败回落关键词
            pass
    # 回落：关键词/子串打分
    ql = query.lower()
    scored = []
    for it in items:
        blob = it.lower()
        score = blob.count(ql) + (2.0 if ql in blob else 0.0)
        if score:
            scored.append((it, score))
    scored.sort(key=lambda x: -x[1])
    return scored[:top_k]


def _parse_summary(out: str) -> Tuple[str, List[str]]:
    summary, keywords = "", []
    for line in out.splitlines():
        # 兼容中英文标签：小模型（0.5B）常把 SUMMARY/KEYWORDS 输出成 摘要/关键词
        if line.startswith("SUMMARY:") or line.startswith("摘要:"):
            summary = line.split(":", 1)[1].strip()
        elif line.startswith("KEYWORDS:") or line.startswith("关键词:"):
            raw = line.split(":", 1)[1].strip()
            keywords = [k.strip() for k in re.split(r"[,，]", raw) if k.strip()]
    return summary, keywords


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
