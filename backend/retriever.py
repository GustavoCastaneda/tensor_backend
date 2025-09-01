# backend/retriever.py
import os
from typing import List, Dict, Optional, Union
from uuid import UUID

from qdrant_client import QdrantClient, models
from sqlmodel import Session, select

from backend.db import engine
from backend.models import Column

# OpenAI (opcional): si no hay API key, hacemos fallback léxico
try:
    from openai import OpenAI  # openai>=1.0
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore

# ───────────────────────── Clientes (lazy) ─────────────────────────
_qdrant: Optional[QdrantClient] = None
_llm: Optional["OpenAI"] = None  # type: ignore


def _get_qdrant() -> QdrantClient:
    global _qdrant
    if _qdrant is None:
        _qdrant = QdrantClient(url=os.getenv("QDRANT_URL", "http://qdrant:6333"))
    return _qdrant


def _get_openai() -> Optional["OpenAI"]:  # type: ignore
    global _llm
    if _llm is not None:
        return _llm
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or OpenAI is None:
        return None
    _llm = OpenAI(api_key=api_key)
    return _llm


def _embed(text: str) -> Optional[List[float]]:
    client = _get_openai()
    if client is None:
        return None
    try:
        out = client.embeddings.create(input=text, model="text-embedding-3-small")
        return out.data[0].embedding  # 1536 dims
    except Exception:
        return None


# ───────────────────────── Helpers ─────────────────────────
def _normalize_dataset_id(dataset_id: Union[str, UUID]) -> str:
    return str(dataset_id)


def _fallback_lexical(dataset_id: Union[str, UUID], question: str, top_k: int) -> List[Dict]:
    """
    Fallback sin embeddings: rankea columnas por solapamiento de tokens en el nombre/tipo.
    """
    tokens = {t for t in question.lower().replace("/", " ").replace("_", " ").split() if t}
    if not tokens:
        tokens = set(question.lower())

    with Session(engine) as session:
        rows: List[Column] = session.exec(
            select(Column).where(Column.dataset_id == dataset_id)
        ).all()

    scored = []
    for c in rows:
        name = (c.original_name or "").lower()
        dtype = (c.detected_type or "").lower()
        bag = set(name.replace("-", " ").replace("_", " ").split()) | set(dtype.split())
        score = len(tokens & bag) / (len(tokens) + 1e-6)
        if score > 0:
            scored.append((score, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for s, c in scored[:top_k]:
        out.append(
            {
                "column_id": str(c.id),
                "score": float(s),
                "original_name": c.original_name,
                "detected_type": c.detected_type,
                "description": getattr(c, "description", None),
            }
        )
    return out


# ───────────────────────── API principal ─────────────────────────
def retrieve_relevant_columns(
    dataset_id: Union[str, UUID],
    question: str,
    top_k: int = 5,
    min_score: float = 0.0,
) -> List[Dict]:
    """
    Busca columnas relevantes para la `question`.
    - Si existe **colección por dataset**: usa `collection_name = <dataset_id>`.
    - Si usas **colección compartida** (env `QDRANT_COLLECTION=table_chunks`):
        aplica filtro por `dataset_id` en el payload.
    - Si no hay OPENAI/Qdrant, cae a un **fallback léxico** sobre los nombres de columnas.

    Devuelve: [{ column_id, score, original_name, detected_type, description }]
    """
    dsid = _normalize_dataset_id(dataset_id)

    # Embedding de la pregunta (si no hay, hacemos fallback)
    query_vec = _embed(question)
    if query_vec is None:
        return _fallback_lexical(dataset_id, question, top_k)

    qc = _get_qdrant()

    # Determinar colección
    shared_collection = os.getenv("QDRANT_COLLECTION")  # e.g., "table_chunks"
    use_shared = bool(shared_collection)

    collection_name = shared_collection or dsid
    query_filter = None

    if use_shared:
        # Filtrar por dataset_id en payload si usamos colección compartida
        query_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="dataset_id",
                    match=models.MatchValue(value=dsid),
                )
            ]
        )

    # Hacer búsqueda
    try:
        results = qc.search(
            collection_name=collection_name,
            query_vector=query_vec,
            limit=max(1, top_k * 2),  # pedimos extra para deduplicar por columna
            with_payload=True,
            query_filter=query_filter,
        )
    except Exception:
        # Si no existe colección (o Qdrant no disponible) → fallback
        return _fallback_lexical(dataset_id, question, top_k)

    # Extraer column_ids (dedupe)
    best_by_col: Dict[str, float] = {}
    payloads: Dict[str, Dict] = {}
    for r in results or []:
        payload = r.payload or {}
        cid = payload.get("column_id")
        if not cid:
            continue
        score = float(r.score or 0.0)
        if cid not in best_by_col or score > best_by_col[cid]:
            best_by_col[cid] = score
            payloads[cid] = payload

    if not best_by_col:
        return _fallback_lexical(dataset_id, question, top_k)

    # Traer metadata desde Postgres
    col_ids = list(best_by_col.keys())
    with Session(engine) as session:
        rows: List[Column] = session.exec(
            select(Column).where(Column.id.in_(col_ids))
        ).all()

    by_id: Dict[str, Column] = {str(r.id): r for r in rows}

    ranked = [
        (
            best_by_col[cid],
            {
                "column_id": cid,
                "score": best_by_col[cid],
                "original_name": by_id.get(cid).original_name if by_id.get(cid) else payloads[cid].get("column_name"),
                "detected_type": by_id.get(cid).detected_type if by_id.get(cid) else payloads[cid].get("detected_type"),
                "description": getattr(by_id.get(cid), "description", None) if by_id.get(cid) else payloads[cid].get("description"),
            },
        )
        for cid in col_ids
        if best_by_col.get(cid, 0.0) >= min_score
    ]

    ranked.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in ranked[:top_k]]
