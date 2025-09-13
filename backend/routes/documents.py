# backend/routes/documents.py
from fastapi import APIRouter, Depends, HTTPException, Query
from uuid import uuid4, UUID
import os, datetime
from sqlalchemy import func
from sqlmodel import Session, select

from backend.security import get_current_user
from backend.db import get_session
from backend.supabase_client import get_supabase

from backend.document_router import route_document_processing
from backend.models import Document, DocChunk, DocumentFormula

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

    # Router inteligente para enrutar a servicio light o heavy
    print(f"[DEBUG] Enrutando documento {doc_id} para análisis")
    try:
        # Descargar el documento para análisis
        key = doc.storage_url.split("/", 1)[1]
        raw = supa.storage.from_(bucket).download(key)
        
        # Determinar extensión
        ext = (filename.split(".")[-1] or "").lower()
        
        # Enrutar usando el router inteligente
        queue_name, service_type, detected_patterns = route_document_processing(
            str(doc_id), raw, ext
        )
        
        print(f"[DEBUG] Documento enrutado a {service_type} service ({queue_name})")
        if detected_patterns:
            print(f"[DEBUG] Patrones detectados: {detected_patterns[:3]}")
            
    except Exception as e:
        print(f"[ERROR] Error al enrutar documento: {str(e)}")
        # En caso de error, usar servicio pesado por defecto
        try:
            from redis import Redis
            from rq import Queue
            redis_conn = Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
            q_heavy = Queue("doc_parse_heavy", connection=redis_conn)
            q_heavy.enqueue(
                "backend.ingest_document_heavy.process_document_heavy",
                str(doc_id),
                job_timeout="15m",
                result_ttl=500,
            )
            print(f"[DEBUG] Fallback a servicio pesado por error")
        except Exception as fallback_error:
            print(f"[ERROR] Error en fallback: {str(fallback_error)}")

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
        "formulas_count": doc.formulas_count or 0,
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


@router.get("/{document_id}/formulas")
def get_document_formulas(
    document_id: UUID,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """
    Obtiene todas las fórmulas matemáticas extraídas de un documento.
    """
    doc = session.exec(select(Document).where(Document.id == document_id)).first()
    if not doc or doc.user_id != user["sub"]:
        raise HTTPException(404, "Documento no encontrado")

    if doc.status not in ("ready_for_embeddings", "ready_for_chat"):
        raise HTTPException(409, "El documento aún no está listo")

    formulas = session.exec(
        select(DocumentFormula)
        .where(DocumentFormula.document_id == document_id)
        .order_by(DocumentFormula.page_number, DocumentFormula.formula_index)
    ).all()

    return {
        "document_id": str(document_id),
        "total_formulas": len(formulas),
        "formulas": [
            {
                "id": str(f.id),
                "page_number": f.page_number,
                "formula_index": f.formula_index,
                "latex_code": f.latex_code,
                "original_text": f.original_text,
                "confidence_score": f.confidence_score,
            }
            for f in formulas
        ]
    }
