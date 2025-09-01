# backend/routes/documents.py
from fastapi import APIRouter, Depends, HTTPException, Query
from uuid import uuid4, UUID
import os, datetime
from sqlalchemy import func
from sqlmodel import Session, select

from backend.security import get_current_user
from backend.db import get_session
from backend.supabase_client import get_supabase

from backend.ingest_document import process_document
from backend.models import Document, DocChunk

# Cola dedicada para parseo de documentos
from redis import Redis
from rq import Queue
redis_conn = Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
q_doc = Queue("doc_parse", connection=redis_conn)

router = APIRouter(prefix="/documents", tags=["documents"])

# ------------------------------------------------------------------ #
# 1) Generar URL pre-firmada + encolar parseo
# ------------------------------------------------------------------ #
@router.post("/upload-url")
def upload_url(
    filename: str = Query(..., description="Nombre del archivo (pdf o docx)"),
    user      = Depends(get_current_user),
    session:   Session = Depends(get_session),
):
    ext = (filename.split(".")[-1] or "").lower()
    if ext not in ("pdf", "docx"):
        raise HTTPException(400, "Formato no soportado. Usa PDF o DOCX.")

    doc_id     = uuid4()
    object_key = f"{doc_id}.{ext}"

    supa   = get_supabase()
    bucket = os.getenv("STORAGE_BUCKET", "uploadeddocs")

    signed     = supa.storage.from_(bucket).create_signed_upload_url(object_key)
    upload_url = signed.get("signed_url") or signed.get("signedUrl") or signed.get("url")
    if not upload_url:
        raise HTTPException(500, f"No pude generar URL firmada para {object_key}")

    doc = Document(
        id          = doc_id,
        user_id     = user["sub"],
        filename    = filename,
        storage_url = f"{bucket}/{object_key}",
        status      = "processing",
        created_at  = datetime.datetime.utcnow(),
    )
    session.add(doc)
    session.commit()

    q_doc.enqueue(process_document, str(doc_id), job_timeout="10m", result_ttl=500)

    return {"document_id": str(doc_id), "upload_url": upload_url, "object_key": object_key}

# ------------------------------------------------------------------ #
# 2) Estado general del documento
# ------------------------------------------------------------------ #
@router.get("/{document_id}/status")
def get_doc_status(
    document_id: UUID,
    user      = Depends(get_current_user),
    session:   Session = Depends(get_session),
):
    doc = session.exec(select(Document).where(Document.id == document_id)).first()
    if not doc or doc.user_id != user["sub"]:
        raise HTTPException(404, "Documento no encontrado")
    return {
        "status": doc.status,
        "pages_count": doc.pages_count,
        "text_chars": doc.text_chars,
    }

# ------------------------------------------------------------------ #
# 3) Preview de chunks (full o paginado)
# ------------------------------------------------------------------ #
@router.get("/{document_id}/preview")
def preview_doc(
    document_id: UUID,
    full: bool = Query(False, description="Si true, devuelve todos los chunks"),
    limit: int = Query(12, ge=1, le=5000, description="Máx. de chunks si full=false"),
    offset: int = Query(0, ge=0, description="Desplazamiento si full=false"),
    user      = Depends(get_current_user),
    session:   Session = Depends(get_session),
):
    doc = session.exec(select(Document).where(Document.id == document_id)).first()
    if not doc or doc.user_id != user["sub"]:
        raise HTTPException(404, "Documento no encontrado")
    if doc.status not in ("ready_for_embeddings", "ready_for_chat"):
        raise HTTPException(409, "El documento aún no está listo para preview")

    # total de chunks
    total_chunks = session.exec(
        select(func.count()).select_from(
            select(DocChunk.id).where(DocChunk.document_id == document_id).subquery()
        )
    ).one()

    base_q = (
        select(DocChunk)
        .where(DocChunk.document_id == document_id)
        .order_by(DocChunk.page_number, DocChunk.chunk_index)
    )

    if not full:
        base_q = base_q.offset(offset).limit(limit)

    chunks = session.exec(base_q).all()

    preview = [
        {
            "page_number": c.page_number,
            "chunk_index": c.chunk_index,
            "content": c.content,  # sin truncar para ver la estructura completa
        }
        for c in chunks
    ]
    return {
        "preview": preview,
        "full": full,
        "returned": len(preview),
        "offset": 0 if full else offset,
        "total_chunks": total_chunks,
    }
