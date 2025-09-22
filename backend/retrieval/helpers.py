from typing import List

from qdrant_client import models


def light_rerank(query: str, items: List[models.ScoredPoint]) -> None:
    q = query.lower()
    if any(w in q for w in ["definición", "qué es", "significa", "concepto"]):
        prefer = {"text"}
    elif any(w in q for w in ["cálculo", "calcular", "fórmula", "ecuación"]):
        prefer = {"formula", "table"}
    elif any(w in q for w in ["tabla", "datos", "números", "estadísticas"]):
        prefer = {"table", "figure"}
    else:
        prefer = {"text", "table", "formula"}
    for it in items:
        bt = (it.payload or {}).get("block_type")
        if bt in prefer:
            it.score = float(it.score or 0.0) * 1.2


def normalize_snippet(text: str, max_chars: int = 320) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    cutoff = text.rfind(" ", 0, max_chars)
    if cutoff == -1:
        cutoff = max_chars
    return text[:cutoff].rstrip() + "…"


def confidence_badge(score: float) -> str:
    if score >= 0.7:
        return "alto"
    if score >= 0.5:
        return "medio"
    return "bajo"


def hash_content(content: str) -> str:
    import hashlib

    return hashlib.md5((content or "").encode("utf-8")).hexdigest()


