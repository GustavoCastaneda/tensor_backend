from fastapi import APIRouter, Depends, HTTPException, Query
from uuid import uuid4, UUID
import datetime, os
import io
import polars as pl
from sqlmodel import Session, select

# Importa tu cola RQ y la función de procesamientorom backend.task_queue import ingest_queue
from backend.ingest_dataset import process_dataset
from backend.task_queue import ingest_queue
from backend.security import get_current_user
from backend.db import get_session
from backend.models import Dataset
from backend.supabase_client import get_supabase

router = APIRouter(prefix="/datasets")

@router.post("/upload-url")
def upload_url(
    filename: str = Query(..., description="Nombre del archivo (p.ej. sample.xlsx)"),
    user      = Depends(get_current_user),
    session:   Session = Depends(get_session),
):
    # 1) Genera un nuevo dataset_id y object_key
    dataset_id = uuid4()
    file_ext   = filename.split(".")[-1]
    object_key = f"{dataset_id}.{file_ext}"

    # 2) Instancia Supabase y obtiene bucket
    supa   = get_supabase()
    bucket = os.getenv("STORAGE_BUCKET", "uploads")

    # 3) Solicita URL pre-firmada
    signed = supa.storage.from_(bucket).create_signed_upload_url(object_key)
    upload_url = (
        signed.get("signed_url")    # snake_case
        or signed.get("signedUrl")  # camelCase
        or signed.get("url")        # fallback
    )
    if not upload_url:
        # Imprime el dict completo para debugging
        print("signed dict:", signed)
        raise HTTPException(500, f"Could not create presigned URL for {object_key}")

    # 4) Persiste el registro en la base de datos
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

    # 5) Encola la tarea de ingesta de forma asíncrona
    ingest_queue.enqueue(
        process_dataset,
        str(dataset_id),
        job_timeout="1h",
        result_ttl=500,
    )

    # 6) Retorna la información al cliente
    return {
        "dataset_id": str(dataset_id),
        "upload_url": upload_url,
        "object_key": object_key,
    }

@router.get("/{dataset_id}/status")
def get_dataset_status(
    dataset_id: UUID,
    user      = Depends(get_current_user),
    session:   Session = Depends(get_session),
):
    """
    Devuelve el estado actual (processing | ready | error) y opcionalmente rows_count
    """
    ds = session.exec(
        select(Dataset).where(Dataset.id == dataset_id)
    ).first()
    if not ds:
        raise HTTPException(404, f"Dataset {dataset_id} not found")
    if ds.user_id != user["sub"]:
        raise HTTPException(403, "No tienes permiso para ver este dataset")

    return {"status": ds.status, "rows_count": ds.rows_count}


@router.get("/{dataset_id}/preview")
def get_dataset_preview(
    dataset_id: UUID,
    session: Session = Depends(get_session),
    user      = Depends(get_current_user),
):
    # 1) Recupera el registro
    ds = session.exec(
        select(Dataset).where(Dataset.id == dataset_id)
    ).first()
    if not ds or ds.user_id != user["sub"]:
        raise HTTPException(404, "Dataset no encontrado")
    if ds.status != "ready":
        raise HTTPException(409, "Dataset aún no está listo")

    # 2) Descarga el Parquet desde Storage
    bucket = os.getenv("STORAGE_BUCKET", "uploads")
    key    = ds.parquet_url.split("/", 1)[1]
    supa   = get_supabase()
    try:
        raw = supa.storage.from_(bucket).download(key)
    except Exception:
        raise HTTPException(500, "Error descargando el preview")

    # 3) Lee con Polars y devuelve las primeras filas
    buf = io.BytesIO(raw)
    df  = pl.read_parquet(buf)
    preview_rows = df.head(10).to_dicts()

    return { "preview": preview_rows }