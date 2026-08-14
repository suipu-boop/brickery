"""推理引擎抽象（clean room）。

记忆子系统不绑定任何具体引擎、不发起任何网络推理。
运行时层（阶段三）注入一个实现了 chat(messages) -> str 的对象；
本层仅依赖该协议。测试中以 mock 注入，满足 §9.5（不发起真实网络推理）。

KeywordExtractor 为无 LLM 的兜底抽取器：从文本提取关键词与摘要，
用于离线运行与单测，使子系统在没有强模型时仍可工作、可测。
"""
from __future__ import annotations

import json
import re
from typing import Iterable, List, Protocol, Tuple


class Engine(Protocol):
    """任何推理引擎只需实现 chat。"""

    def chat(self, messages: List[dict]) -> str:  # pragma: no cover - 协议
        ...


_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "else", "for", "to", "of",
    "in", "on", "at", "by", "with", "from", "as", "is", "are", "was", "were", "be",
    "this", "that", "these", "those", "it", "its", "we", "you", "i", "he", "she",
    "they", "them", "his", "her", "their", "my", "your", "our", "do", "does", "did",
    "have", "has", "had", "will", "would", "can", "could", "should", "about", "into",
    "的", "了", "和", "与", "或", "在", "是", "我", "你", "他", "她", "它", "我们",
    "你们", "他们", "这", "那", "这个", "那个", "一个", "一种", "可以", "如何", "怎么",
    "什么", "为什么", "因为", "所以", "但", "而", "及", "以及", "对", "把", "被", "让",
}


try:
    import jieba  # type: ignore
    JIEBA_OK = True
except Exception:  # noqa: BLE001
    JIEBA_OK = False


class KeywordExtractor:
    """无 LLM 兜底：频率 + 长度启发式抽取关键词，并生成截断式摘要。"""

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        # 英文按词；中文优先 jieba 分词（更准），不可用时回落 2-gram 粗切。
        toks: List[str] = []
        for m in re.finditer(r"[a-zA-Z][a-zA-Z0-9_]+", text.lower()):
            toks.append(m.group(0))
        for seg in re.findall(r"[一-鿿]+", text):
            if JIEBA_OK:
                for w in jieba.lcut(seg):  # type: ignore[name-defined]
                    w = w.strip()
                    if w:
                        toks.append(w)
            elif len(seg) >= 2:
                for i in range(len(seg) - 1):
                    toks.append(seg[i:i + 2])
        return [t for t in toks if t not in _STOPWORDS and len(t) >= 1]

    def extract(self, texts: Iterable[str]) -> Tuple[str, List[str]]:
        joined = "\n".join(texts)
        if not joined.strip():
            return "", []
        # 摘要：取前 120 字符（不含换行）
        flat = re.sub(r"\s+", " ", joined).strip()
        summary = (flat[:120] + "…") if len(flat) > 120 else flat
        # 关键词：词频 Top-8
        freq: dict[str, int] = {}
        for t in self._tokenize(joined):
            freq[t] = freq.get(t, 0) + 1
        ranked = sorted(freq.items(), key=lambda kv: kv[1], reverse=True)[:8]
        keywords = [k for k, _ in ranked]
        return summary, keywords


def extract_via(engine: Engine | None, texts: Iterable[str]) -> Tuple[str, List[str]]:
    """优先用引擎抽取；无引擎时回落到 KeywordExtractor。"""
    if engine is not None:
        try:
            prompt = (
                "请从以下对话中提取：1) 一句话主题摘要；2) 不超过8个关键词（逗号分隔）。\n"
                "严格按如下格式返回：\nSUMMARY: <摘要>\nKEYWORDS: <k1>,<k2>,...\n\n"
                + "\n".join(texts)
            )
            out = engine.chat([{"role": "user", "content": prompt}])
            summary, keywords = _parse_extraction(out)
            if summary or keywords:
                return summary, keywords
        except Exception:
            pass  # 引擎失败回落兜底，不中断存档
    return KeywordExtractor().extract(texts)


def _parse_extraction(out: str) -> Tuple[str, List[str]]:
    summary, keywords = "", []
    for line in out.splitlines():
        if line.startswith("SUMMARY:"):
            summary = line[len("SUMMARY:"):].strip()
        elif line.startswith("KEYWORDS:"):
            raw = line[len("KEYWORDS:"):].strip()
            keywords = [k.strip() for k in raw.split(",") if k.strip()]
    return summary, keywords
