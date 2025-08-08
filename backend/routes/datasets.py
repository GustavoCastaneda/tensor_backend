from fastapi import APIRouter, Depends, HTTPException, Query
from uuid import uuid4, UUID
import datetime, os, io
import polars as pl
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
