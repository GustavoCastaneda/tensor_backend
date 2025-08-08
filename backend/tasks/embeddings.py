import os
from rq import get_current_job
from qdrant_client import QdrantClient, models
from openai import OpenAI

from backend.db import SessionLocal
from backend.models import Dataset, Column

qdrant = QdrantClient(url=os.getenv("QDRANT_URL", "http://qdrant:6333"))
llm    = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def generate_embeddings(dataset_id: str):
    """Genera embeddings de las columnas y los sube a Qdrant."""
    job = get_current_job()

    db   = SessionLocal()
    ds   = db.query(Dataset).get(dataset_id)
    cols = db.query(Column).filter(Column.dataset_id == dataset_id).all()

    if not cols:
        ds.status = "error"
        db.commit()
        return

    points = []
    for col in cols:
        text = col.original_name + (": " + col.description if col.description else "")

        # Obtener embedding con la SDK 1.x
        resp = llm.embeddings.create(
            input=text,
            model="text-embedding-3-small",
        )
        emb = resp.data[0].embedding

        points.append(
            models.PointStruct(
                id=str(col.id),
                vector=emb,
                payload={"dataset_id": dataset_id, "column_id": str(col.id)},
            )
        )

    # Crear/recrear colección (firma nueva: vectors_config)
    if not qdrant.collection_exists(dataset_id):
        qdrant.recreate_collection(
            collection_name=dataset_id,
            vectors_config=models.VectorParams(
                size=len(points[0].vector),
                distance=models.Distance.COSINE,
            ),
        )

    # Subir puntos
    qdrant.upsert(collection_name=dataset_id, points=points)

    # Marcar dataset listo para chat
    ds.status = "ready_for_chat"
    db.commit()
    db.close()

    job.meta["rows_embedded"] = len(points)
    job.save_meta()
