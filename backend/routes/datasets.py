from fastapi import APIRouter, Depends, HTTPException, Query
from uuid import uuid4, UUID
import datetime, os, io
import polars as pl
from pydantic import BaseModel
from sqlmodel import Session, select

from backend.task_queue import ingest_queue          # cola RQ
from backend.ingest_dataset import process_dataset   # función de ingesta
from backend.security import get_current_user
from backend.db import get_session
from backend.models import Dataset
from backend.supabase_client import get_supabase

router = APIRouter(prefix="/datasets")

# ------------------------------------------------------------------ #
# 1) Generar URL pre-firmada + encolar ingesta
# ------------------------------------------------------------------ #
@router.post("/upload-url")
def upload_url(
    filename: str = Query(..., description="Nombre del archivo"),
    user      = Depends(get_current_user),
    session:   Session = Depends(get_session),
):
    dataset_id = uuid4()
    file_ext   = filename.split(".")[-1]
    object_key = f"{dataset_id}.{file_ext}"

    supa   = get_supabase()
    bucket = os.getenv("STORAGE_BUCKET", "uploads")

    signed     = supa.storage.from_(bucket).create_signed_upload_url(object_key)
    upload_url = signed.get("signed_url") or signed.get("signedUrl") or signed.get("url")
    if not upload_url:
        print("signed dict:", signed)
        raise HTTPException(500, f"No pude generar URL firmada para {object_key}")

    ds = Dataset(
        id          = dataset_id,
        user_id     = user["sub"],
        filename    = filename,
        storage_url = f"{bucket}/{object_key}",
        status      = "processing",
        created_at  = datetime.datetime.utcnow(),
    )
    session.add(ds)
    session.commit()

    ingest_queue.enqueue(
        process_dataset,
        str(dataset_id),
        job_timeout="1h",
        result_ttl=500,
    )

    return {"dataset_id": str(dataset_id), "upload_url": upload_url, "object_key": object_key}

# ------------------------------------------------------------------ #
# 1.b) NUEVO — Registrar dataset (post-subida o flujo alterno)
#      - Si envías dataset_id: actualiza y encola solo si no estaba procesándose.
#      - Si NO envías dataset_id: crea uno nuevo y encola.
# ------------------------------------------------------------------ #
class DatasetRegisterRequest(BaseModel):
    filename: str
    object_key: str | None = None         # p.ej. "uploads/2025/08/mi_archivo.xlsx" o "<uuid>.xlsx"
    storage_url: str | None = None        # alternativo a object_key: "bucket/key"
    dataset_id: UUID | None = None

@router.post("/register")
def datasets_register(
    req: DatasetRegisterRequest,
    user    = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    bucket = os.getenv("STORAGE_BUCKET", "uploads")

    # Resolver storage_url final
    if req.storage_url:
        storage_url = req.storage_url
    elif req.object_key:
        # Si viene "bucket/key", respetar; si viene solo "key", prefijar bucket
        storage_url = req.object_key if "/" in req.object_key and req.object_key.split("/", 1)[0] != "" \
            else f"{bucket}/{req.object_key}"
    else:
        raise HTTPException(400, "Debes enviar 'object_key' o 'storage_url'.")

    # Caso A: actualizar dataset existente
    if req.dataset_id:
        ds = session.exec(select(Dataset).where(Dataset.id == req.dataset_id)).first()
        if not ds or ds.user_id != user["sub"]:
            raise HTTPException(404, f"Dataset {req.dataset_id} no encontrado")

        # Actualizar filename/storage_url si cambian
        changed = False
        if req.filename and req.filename != ds.filename:
            ds.filename = req.filename
            changed = True
        if storage_url and storage_url != ds.storage_url:
            ds.storage_url = storage_url
            changed = True

        # Encolar solo si aún no estaba en procesamiento o listo
        should_enqueue = False
        if ds.status not in ("processing", "ready_for_embeddings", "ready_for_chat"):
            ds.status = "processing"
            should_enqueue = True
            changed = True

        if changed:
            session.add(ds)
            session.commit()

        if should_enqueue:
            ingest_queue.enqueue(
                process_dataset,
                str(ds.id),
                job_timeout="1h",
                result_ttl=500,
            )

        return {"dataset_id": str(ds.id), "status": ds.status, "storage_url": ds.storage_url}

    # Caso B: crear dataset nuevo
    new_id = uuid4()
    ds = Dataset(
        id          = new_id,
        user_id     = user["sub"],
        filename    = req.filename,
        storage_url = storage_url,
        status      = "processing",
        created_at  = datetime.datetime.utcnow(),
    )
    session.add(ds)
    session.commit()

    ingest_queue.enqueue(
        process_dataset,
        str(new_id),
        job_timeout="1h",
        result_ttl=500,
    )

    return {"dataset_id": str(new_id), "status": "processing", "storage_url": storage_url}

# ------------------------------------------------------------------ #
# 2) Estado general del dataset
# ------------------------------------------------------------------ #
@router.get("/{dataset_id}/status")
def get_dataset_status(
    dataset_id: UUID,
    user      = Depends(get_current_user),
    session:   Session = Depends(get_session),
):
    ds = session.exec(select(Dataset).where(Dataset.id == dataset_id)).first()
    if not ds or ds.user_id != user["sub"]:
        raise HTTPException(404, f"Dataset {dataset_id} no encontrado")
    return {"status": ds.status, "rows_count": ds.rows_count}

# ------------------------------------------------------------------ #
# 3) NUEVO – Estado semántico (¿ya tiene embeddings?)
# ------------------------------------------------------------------ #
@router.get("/{dataset_id}/semantic-status")
def get_semantic_status(
    dataset_id: UUID,
    user      = Depends(get_current_user),
    session:   Session = Depends(get_session),
):
    ds = session.exec(select(Dataset).where(Dataset.id == dataset_id)).first()
    if not ds or ds.user_id != user["sub"]:
        raise HTTPException(404, f"Dataset {dataset_id} no encontrado")

    return {"ready_for_chat": ds.status == "ready_for_chat"}

# ------------------------------------------------------------------ #
# 4) Preview de las primeras filas
# ------------------------------------------------------------------ #
@router.get("/{dataset_id}/preview")
def get_dataset_preview(
    dataset_id: UUID,
    session:   Session = Depends(get_session),
    user      = Depends(get_current_user),
):
    ds = session.exec(select(Dataset).where(Dataset.id == dataset_id)).first()
    if not ds or ds.user_id != user["sub"]:
        raise HTTPException(404, "Dataset no encontrado")

    # Permitimos preview cuando ya se procesó el Parquet
    if ds.status not in ("ready_for_embeddings", "ready_for_chat"):
        raise HTTPException(409, "El dataset aún no está listo para preview")

    bucket = os.getenv("STORAGE_BUCKET", "uploads")
    key    = ds.parquet_url.split("/", 1)[1]
    supa   = get_supabase()

    try:
        raw = supa.storage.from_(bucket).download(key)
    except Exception:
        raise HTTPException(500, "Error descargando el preview")

    buf = io.BytesIO(raw)
    df  = pl.read_parquet(buf)
    return {"preview": df.head(10).to_dicts()}
