"""Choosing which active memories go into the prompt.

inject_all  every active memory (what many assistants do first)
lexical     character-bigram TF-IDF cosine, top-k, any overlap > 0 (offline, no model)
embedding   cosine over provided vectors, top-k above a fixed threshold (live; vectors cached)
router      an LLM picks relevant ids (live; see llm.py)
"""

import math
import re
from collections import Counter

from memlab.store import MemoryItem


def _grams(text: str) -> Counter:
    text = re.sub(r"[\s，。、；：？！,.;:?!（）()“”\"'「」]+", "", text.lower())
    words = re.findall(r"[a-z0-9]+", text)
    han = re.sub(r"[a-z0-9]+", "|", text)
    grams = [han[i:i + 2] for i in range(len(han) - 1) if "|" not in han[i:i + 2]]
    return Counter(grams + words)


def inject_all(question: str, items: list[MemoryItem], **_) -> list[MemoryItem]:
    return list(items)


def lexical(question: str, items: list[MemoryItem], k: int = 3, **_) -> list[MemoryItem]:
    if not items:
        return []
    docs = [_grams(m.text) for m in items]
    df = Counter(g for d in docs for g in d)
    n = len(docs)

    def vec(c):
        return {g: tf * (math.log((n + 1) / (df.get(g, 0) + 1)) + 1) for g, tf in c.items()}

    q = vec(_grams(question))
    qn = math.sqrt(sum(v * v for v in q.values())) or 1.0
    scored = []
    for m, d in zip(items, docs):
        v = vec(d)
        dot = sum(q.get(g, 0) * w for g, w in v.items())
        if dot > 0:
            scored.append((dot / (qn * (math.sqrt(sum(w * w for w in v.values())) or 1.0)), m))
    scored.sort(key=lambda x: -x[0])
    return [m for _, m in scored[:k]]


def embedding(question: str, items: list[MemoryItem], vectors: dict, k: int = 3, threshold: float = 0.35,
              **_) -> list[MemoryItem]:
    def cos(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        return dot / ((math.sqrt(sum(x * x for x in a)) or 1) * (math.sqrt(sum(y * y for y in b)) or 1))

    q = vectors[question]
    scored = sorted(((cos(q, vectors[m.text]), m) for m in items), key=lambda x: -x[0])
    return [m for s, m in scored[:k] if s >= threshold]


STRATEGIES = {"inject_all": inject_all, "lexical": lexical}
