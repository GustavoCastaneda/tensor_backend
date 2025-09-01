# backend/tasks/doc_embeddings.py
import os
from typing import List
from uuid import UUID
from sqlmodel import Session, select

from backend.db import engine
from backend.models import Document, DocChunk
from qdrant_client import QdrantClient, models
from openai import OpenAI

QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
EMB_MODEL  = os.getenv("EMBEDDINGS_MODEL", "text-embedding-3-small")

qdrant = QdrantClient(url=QDRANT_URL)
llm    = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def _ensure_collection(name: str, vector_size: int = 1536):
    try:
        qdrant.get_collection(name)
    except Exception:
        qdrant.recreate_collection(
            collection_name=name,
            vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
        )

def generate_doc_embeddings(document_id: str):
    coll = str(document_id)
    _ensure_collection(coll, vector_size=1536)

    with Session(engine) as session:
        # Obtener el documento para actualizar su estado
        doc = session.exec(select(Document).where(Document.id == UUID(document_id))).first()
        if not doc:
            print(f"[doc-emb] Document {document_id} not found")
            return
            
        chunks: List[DocChunk] = session.exec(
            select(DocChunk).where(DocChunk.document_id == UUID(document_id)).order_by(DocChunk.page_number, DocChunk.chunk_index)
        ).all()

    if not chunks:
        print(f"[doc-emb] No chunks found for document {document_id}")
        return

    try:
        B = 64
        for i in range(0, len(chunks), B):
            batch = chunks[i:i+B]
            texts = [c.content for c in batch]
            embs  = llm.embeddings.create(model=EMB_MODEL, input=texts).data
            vectors = [e.embedding for e in embs]

            points = [
                models.PointStruct(
                    id=str(c.id),
                    vector=vec,
                    payload={
                        "document_id": str(c.document_id),
                        "page_number": c.page_number,
                        "chunk_index": c.chunk_index,
                        "text": c.content[:512],
                        "type": "doc_chunk",
                    },
                )
                for c, vec in zip(batch, vectors)
            ]
            qdrant.upsert(collection_name=coll, points=points)

        # Actualizar el estado del documento a ready_for_chat
        with Session(engine) as session:
            doc = session.exec(select(Document).where(Document.id == UUID(document_id))).first()
            if doc:
                doc.status = "ready_for_chat"
                session.commit()
                print(f"[doc-emb] {document_id} → {len(chunks)} chunks, status updated to ready_for_chat")
            else:
                print(f"[doc-emb] Could not update status for document {document_id}")

    except Exception as e:
        print(f"[doc-emb] Error processing embeddings for document {document_id}: {e}")
        # En caso de error, marcar como error
        with Session(engine) as session:
            doc = session.exec(select(Document).where(Document.id == UUID(document_id))).first()
            if doc:
                doc.status = "error"
                session.commit()
                print(f"[doc-emb] {document_id} → error status set due to embedding failure")
