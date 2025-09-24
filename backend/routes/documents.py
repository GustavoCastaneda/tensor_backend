# backend/routes/documents.py
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from uuid import UUID, uuid4
from typing import Optional
import datetime
import os
import time
from sqlalchemy import func
from sqlmodel import Session, select

from backend.security import get_current_user
from backend.db import get_session
from backend.supabase_client import get_supabase

from backend.document_router import route_document_processing
from backend.models import Document, DocChunk, DocumentFormula, Workspace

# Cola dedicada para parseo de documentos
from redis import Redis
from rq import Queue
redis_conn = Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
q_doc = Queue("doc_parse", connection=redis_conn)

router = APIRouter(prefix="/documents", tags=["documents"])

# ------------------------------------------------------------------ #
# 1) Generar URL pre-firmada + encolar parseo
# ------------------------------------------------------------------ #
def _route_after_upload(doc_id: UUID, bucket: str, key: str, filename: str):
    """Intenta descargar el archivo tras el upload y enrutar a light/heavy.
    Reintenta por un tiempo acotado para evitar la condición de carrera.
    """
    try:
        supa = get_supabase()
        ext = (filename.split(".")[-1] or "").lower()

        max_wait_seconds = int(os.getenv("ROUTER_MAX_WAIT_SECONDS", "90"))
        interval_seconds = float(os.getenv("ROUTER_POLL_INTERVAL", "1.5"))

        start = time.time()
        last_err = None
        while time.time() - start < max_wait_seconds:
            try:
                raw = supa.storage.from_(bucket).download(key)
                # Enrutar una vez disponible
                from backend.document_router import route_document_processing
                queue_name, service_type, detected_patterns = route_document_processing(
                    str(doc_id), raw, ext
                )
                print(f"[DEBUG] Documento {doc_id} enrutado a {service_type} ({queue_name})")
                if detected_patterns:
                    print(f"[DEBUG] Patrones detectados: {detected_patterns[:3]}")
                return
            except Exception as e:  # noqa: BLE001 - loggear y reintentar
                last_err = e
                time.sleep(interval_seconds)
        print(f"[ERROR] No se pudo descargar {bucket}/{key} tras espera: {last_err}")
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] Router background failed: {e}")


@router.post("/upload-url")
def upload_url(
    filename: str = Query(..., description="Nombre del archivo (pdf o docx)"),
    workspace_id: Optional[str] = Query(
        None,
        description="ID del workspace destino. Si se omite se usa el workspace personal del usuario.",
    ),
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
    background_tasks: BackgroundTasks = None,
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

    target_workspace = (workspace_id or user["sub"]).strip()

    if not target_workspace:
        raise HTTPException(400, "workspace_id inválido")

    if workspace_id:
        existing_ws = session.get(Workspace, target_workspace)
        if existing_ws and existing_ws.owner_user_id != user["sub"]:
            raise HTTPException(403, "No tienes permisos sobre este workspace")
        if existing_ws is None:
            placeholder_name = workspace_id.strip() or target_workspace
            session.add(
                Workspace(
                    id=target_workspace,
                    owner_user_id=user["sub"],
                    name=placeholder_name,
                )
            )
            session.flush()

    doc = Document(
        id           = doc_id,
        user_id      = user["sub"],
        workspace_id = target_workspace,
        filename     = filename,
        storage_url  = f"{bucket}/{object_key}",
        status       = "processing",
        created_at   = datetime.datetime.utcnow(),
    )
    session.add(doc)
    session.commit()

    # Enrutar en background tras la subida para evitar condición de carrera
    if background_tasks is not None:
        key = doc.storage_url.split("/", 1)[1]
        background_tasks.add_task(_route_after_upload, doc_id, bucket, key, filename)

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
