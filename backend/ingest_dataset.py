# backend/ingest_dataset.py
import os, tempfile, io, datetime, uuid
import polars as pl
from uuid import UUID
from typing import Optional, Dict, Any
from sqlmodel import Session, select, delete

from backend.db import engine
from backend.supabase_client import get_supabase
from backend.models import Dataset, Column

# ────────────── colas Redis / RQ ───────────────────────────────────
from redis import Redis
from rq import Queue

redis_conn   = Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
q_embeddings = Queue("embeddings", connection=redis_conn)
q_ingest     = Queue("ingest", connection=redis_conn)  # <- NUEVA cola opcional para ingesta
# -------------------------------------------------------------------

BUCKET = os.getenv("STORAGE_BUCKET", "uploadeddocs")
STREAMING_SIZE_MB = int(os.getenv("STREAMING_SIZE_MB", "50"))

def to_jsonable(val):
    if isinstance(val, (datetime.date, datetime.datetime)):
        return val.isoformat()
    if isinstance(val, (bytes, bytearray)):
        return val.decode("utf-8", errors="ignore")
    return val


def process_dataset(dataset_id: UUID | str):
    """
    Procesa el dataset:
      1) descarga el archivo crudo de Supabase Storage
      2) lo lee con Polars (xlsx/csv/parquet)
      3) escribe y sube Parquet
      4) crea/actualiza columnas de perfilado
      5) marca status y encola embeddings
    """
    # Acepta también strings desde RQ
    if isinstance(dataset_id, str):
        dataset_id = UUID(dataset_id)

    supa = get_supabase()

    with Session(engine) as session:
        ds: Dataset | None = session.exec(
            select(Dataset).where(Dataset.id == dataset_id)
        ).first()

        if not ds or ds.status != "processing":
            print("Dataset not found or already processed")
            return

        # 1️⃣ descarga objeto a memoria
        try:
            # ds.storage_url se guarda como "BUCKET/key"; necesitamos solo el "key"
            storage_key = ds.storage_url.split("/", 1)[1] if "/" in ds.storage_url else ds.storage_url
            raw_bytes = supa.storage.from_(BUCKET).download(storage_key)
        except Exception as e:
            print("download error:", e)
            ds.status = "error"
            session.commit()
            return

        buffer = io.BytesIO(raw_bytes)
        size_mb = max(1, int(len(raw_bytes) / (1024 * 1024)))

        # 2️⃣ Lee con Polars (xlsx/csv/parquet)
        fname = (ds.filename or "").lower().strip()
        try:
            if fname.endswith((".xlsx", ".xls")):
                # Excel: lectura directa; ajustar hoja/base de inferencia
                df = pl.read_excel(buffer, sheet_id=0, infer_schema_length=1000)
            elif fname.endswith(".csv"):
                if size_mb > STREAMING_SIZE_MB:
                    # CSV grande: escanear desde archivo temporal (lazy) y colectar
                    with tempfile.NamedTemporaryFile(suffix=".csv") as tmp:
                        tmp.write(raw_bytes)
                        tmp.flush()
                        df = pl.scan_csv(tmp.name).collect()
                else:
                    df = pl.read_csv(buffer, low_memory=True, try_parse_dates=True)
            elif fname.endswith((".parquet", ".pq")):
                if size_mb > STREAMING_SIZE_MB:
                    with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
                        tmp.write(raw_bytes)
                        tmp.flush()
                        df = pl.scan_parquet(tmp.name).collect()
                else:
                    df = pl.read_parquet(buffer)
            else:
                # intento heurístico por content sniffing mínimo
                try:
                    df = pl.read_csv(buffer)
                except Exception:
                    ds.status = "error"
                    session.commit()
                    print("Unsupported file type:", fname)
                    return
        except Exception as e:
            ds.status = "error"
            session.commit()
            print("read error:", e)
            return

        # 3️⃣ Escribe Parquet y sube
        pq_path = f"{dataset_id}.parquet"
        try:
            with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
                df.write_parquet(tmp.name)
                tmp.seek(0)
                data = tmp.read()

                supa.storage.from_(BUCKET).upload(
                    pq_path,
                    data,
                    file_options={"upsert": "true"}
                )
        except Exception as e:
            ds.status = "error"
            session.commit()
            print("parquet upload error:", e)
            return

        ds.parquet_url = f"{BUCKET}/{pq_path}"

        # 4️⃣ Rellena/actualiza tabla columns (limpia anteriores)
        try:
            # Borrado seguro de columnas previas del dataset
            session.exec(delete(Column).where(Column.dataset_id == dataset_id))
            session.commit()
        except Exception:
            # Si no existe la tabla o no aplica, continuamos creando
            pass

        for col in df.columns:
            dtype = str(df[col].dtype)
            sample_vals = [to_jsonable(v) for v in df[col].head(5).to_list()]

            session.add(
                Column(
                    dataset_id    = dataset_id,
                    original_name = col,
                    detected_type = dtype,
                    sample_values = sample_vals,
                )
            )

        # 5️⃣ Actualiza dataset
        ds.rows_count = df.height
        ds.status     = "ready_for_embeddings"
        session.commit()

        # 6️⃣ Encola embeddings (lazy-import para que la API no requiera qdrant/openai)
        try:
            from backend.tasks.embeddings import generate_embeddings   # ← import aquí
            q_embeddings.enqueue(generate_embeddings, str(dataset_id))
        except Exception as e:
            # Si no hay worker de embeddings aún, dejamos el dataset listo y registramos
            print("enqueue embeddings error:", e)

        print(f"Dataset {dataset_id} → ready ({df.height} rows, {len(df.columns)} cols)")


def _normalize_storage_url(*, storage_key: Optional[str], storage_url: Optional[str]) -> str:
    """
    Normaliza a formato 'BUCKET/key' que espera process_dataset.
    """
    if storage_key:
        return f"{BUCKET}/{storage_key.lstrip('/')}"
    if storage_url:
        # si ya viene con el prefijo BUCKET/..., lo respetamos
        if storage_url.split("/", 1)[0] == BUCKET:
            return storage_url
        return f"{BUCKET}/{storage_url.lstrip('/')}"
    # fallback (poco común): usar solo bucket y filename
    return f"{BUCKET}/"


def register_dataset(
    *,
    project_id: UUID | str,
    filename: str,
    storage_key: Optional[str] = None,
    storage_url: Optional[str] = None,
    enqueue: bool = True
) -> Dict[str, Any]:
    """
    Crea el registro del Dataset con status='processing' y dispara su ingesta.
    Devuelve { dataset_id, status, storage_url }.
    - Usa Supabase Storage: guarda storage_url en formato 'BUCKET/key' (lo que espera process_dataset).
    - Si enqueue=True, encola en RQ ('ingest'); si falla, ejecuta sincrónico.
    """
    # project_id puede ser UUID o string; lo aceptamos tal cual para tu modelo
    norm_storage_url = _normalize_storage_url(storage_key=storage_key, storage_url=storage_url)

    with Session(engine) as session:
        ds = Dataset(
            project_id = project_id,
            filename   = filename,
            storage_url= norm_storage_url,
            status     = "processing",
        )
        session.add(ds)
        session.commit()
        session.refresh(ds)

        # Disparar ingesta
        try:
            if enqueue:
                # Encolamos; el worker llamará process_dataset(str(ds.id))
                q_ingest.enqueue(process_dataset, str(ds.id))
            else:
                # Procesar en línea (bloqueante)
                process_dataset(ds.id)
        except Exception as e:
            # Si la cola no está disponible, caemos a ejecución directa
            print("enqueue ingest error, falling back to sync:", e)
            process_dataset(ds.id)

        return {
            "dataset_id": str(ds.id),
            "status": ds.status,
            "storage_url": ds.storage_url
        }


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: poetry run python backend/ingest_dataset.py <dataset_id>")
        sys.exit(1)

    process_dataset(sys.argv[1])
