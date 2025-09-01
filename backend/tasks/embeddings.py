# backend/tasks/embeddings.py
import os
from typing import List, Dict, Optional
from uuid import UUID

from sqlmodel import Session, select
from qdrant_client import QdrantClient, models

from backend.db import engine
from backend.models import Dataset, Column

# OpenAI (opcional)
try:
    from openai import OpenAI  # openai>=1.0
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore


def _get_openai() -> Optional["OpenAI"]:  # type: ignore
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or OpenAI is None:
        return None
    return OpenAI(api_key=api_key)


def _get_qdrant() -> QdrantClient:
    return QdrantClient(url=os.getenv("QDRANT_URL", "http://qdrant:6333"))


def _build_col_text(col: Column) -> str:
    # Texto compacto y robusto para embedder
    sv = col.sample_values or []
    sv_str = ", ".join(map(lambda v: str(v)[:64], sv[:5]))
    return f"column: {col.original_name}; type: {col.detected_type}; samples: [{sv_str}]"


def _ensure_collection(qc: QdrantClient, name: str, dim: int):
    try:
        qc.get_collection(name)
        return
    except Exception:
        pass
    qc.recreate_collection(
        collection_name=name,
        vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
    )


def generate_embeddings(dataset_id: str) -> Dict[str, any]:
    """
    Job encolado por ingest_dataset.process_dataset.
    Crea embeddings por columna y los guarda en Qdrant.
    Si no hay OPENAI_API_KEY o Qdrant, deja el dataset en ready_for_chat igualmente.
    """
    ds_uuid = UUID(dataset_id)

    with Session(engine) as session:
        ds: Dataset | None = session.exec(
            select(Dataset).where(Dataset.id == ds_uuid)
        ).first()
        if not ds:
            return {"ok": False, "reason": "dataset_not_found"}

        cols: List[Column] = session.exec(
            select(Column).where(Column.dataset_id == ds_uuid)
        ).all()

    if not cols:
        # Nada que indexar; aún así habilitamos chat (caerá a fallback léxico)
        with Session(engine) as session:
            ds = session.exec(select(Dataset).where(Dataset.id == ds_uuid)).first()
            ds.status = "ready_for_chat"
            session.commit()
        return {"ok": True, "vectors": 0, "fallback": True}

    client = _get_openai()
    qc = None
    try:
        qc = _get_qdrant()
    except Exception:
        qc = None

    # Sin OpenAI o Qdrant → marcar listo para chat (retriever hará fallback)
    if client is None or qc is None:
        with Session(engine) as session:
            ds = session.exec(select(Dataset).where(Dataset.id == ds_uuid)).first()
            ds.status = "ready_for_chat"
            session.commit()
        return {"ok": True, "vectors": 0, "fallback": True}

    # Embeddings en batch
    texts = [_build_col_text(c) for c in cols]
    try:
        emb_resp = client.embeddings.create(
            model=os.getenv("EMBED_MODEL", "text-embedding-3-small"),
            input=texts,
        )
        vectors = [d.embedding for d in emb_resp.data]
        dim = len(vectors[0]) if vectors else 1536
    except Exception as e:
        # Fallo al embeddear → aun así habilitar chat con fallback
        with Session(engine) as session:
            ds = session.exec(select(Dataset).where(Dataset.id == ds_uuid)).first()
            ds.status = "ready_for_chat"
            session.commit()
        return {"ok": False, "reason": f"embed_error:{e}", "fallback": True}

    # Colección: por dataset (o compartida si defines QDRANT_COLLECTION)
    collection = os.getenv("QDRANT_COLLECTION") or dataset_id
    try:
        _ensure_collection(qc, collection, dim)
    except Exception as e:
        with Session(engine) as session:
            ds = session.exec(select(Dataset).where(Dataset.id == ds_uuid)).first()
            ds.status = "ready_for_chat"
            session.commit()
        return {"ok": False, "reason": f"collection_error:{e}", "fallback": True}

    # Upsert
    points: List[models.PointStruct] = []
    for col, vec in zip(cols, vectors):
        payload = {
            "dataset_id": dataset_id,
            "column_id": str(col.id),
            "column_name": col.original_name,
            "detected_type": col.detected_type,
            "description": getattr(col, "description", None),
            "kind": "table_column",
        }
        points.append(models.PointStruct(id=str(col.id), vector=vec, payload=payload))

    try:
        qc.upsert(collection_name=collection, points=points)
    except Exception as e:
        # Si Qdrant falla, continuamos con fallback
        with Session(engine) as session:
            ds = session.exec(select(Dataset).where(Dataset.id == ds_uuid)).first()
            ds.status = "ready_for_chat"
            session.commit()
        return {"ok": False, "reason": f"upsert_error:{e}", "fallback": True}

    # Marcar listo para chat
    with Session(engine) as session:
        ds = session.exec(select(Dataset).where(Dataset.id == ds_uuid)).first()
        ds.status = "ready_for_chat"
        session.commit()

    return {"ok": True, "vectors": len(points), "collection": collection}
