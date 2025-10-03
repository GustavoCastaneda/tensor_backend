# backend/ingest_dataset.py
import os, tempfile, io, datetime, uuid
import polars as pl
from uuid import UUID, uuid4
from typing import Optional, Dict, Any
from sqlmodel import Session, select, delete
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

from backend.db import engine
from backend.supabase_client import get_supabase
from backend.models import Dataset, Column

# ────────────── colas Redis / RQ ───────────────────────────────────
from redis import Redis
from rq import Queue

redis_conn   = Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), ssl_cert_reqs=None)
q_embeddings = Queue("embeddings", connection=redis_conn)
q_ingest     = Queue("ingest", connection=redis_conn)  # <- NUEVA cola opcional para ingesta
# -------------------------------------------------------------------

def to_jsonable(val):
    """Convierte valores a JSON-serializable."""
    if val is None:
        return None
    if isinstance(val, (str, int, float, bool)):
        return val
    return str(val)


def process_dataset(dataset_id: UUID | str):
    """
    Procesa un dataset (CSV/Excel/Parquet) y genera embeddings.
    """
    ds_uuid = UUID(dataset_id) if isinstance(dataset_id, str) else dataset_id

    with Session(engine) as session:
        ds: Dataset | None = session.exec(
            select(Dataset).where(Dataset.id == ds_uuid)
        ).first()
        if not ds:
            print(f"Dataset {dataset_id} no encontrado")
            return

        if ds.status not in ("processing", "ready_for_embeddings", "error"):
            print(f"Dataset {dataset_id} estado={ds.status}, omito reingesta")
            return

        # 1) Descargar archivo desde Storage (con retry)
        supa = get_supabase()
        key = ds.storage_url.split("/", 1)[1]
        bucket = ds.storage_url.split("/", 1)[0]
        
        # Retry mechanism optimizado para archivos pequeños
        max_retries = 5   # Menos intentos
        base_delay = 1    # Delay base más corto
        max_delay = 5     # Delay máximo más corto
        
        for attempt in range(max_retries):
            try:
                # Intentar descargar directamente (más simple y confiable)
                raw = supa.storage.from_(bucket).download(key)
                if len(raw) == 0:
                    raise Exception("Archivo descargado está vacío")
                break  # Éxito, salir del loop
                
            except Exception as e:
                if attempt == max_retries - 1:  # Último intento
                    ds.status = "error"
                    session.commit()
                    print(f"Error descargando dataset {dataset_id} después de {max_retries} intentos: {e}")
                    return
                else:
                    # Delay exponencial con jitter
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    jitter = delay * 0.1 * (0.5 - __import__('random').random())  # ±10% jitter
                    actual_delay = max(1, delay + jitter)
                    
                    print(f"Intento {attempt + 1}/{max_retries} falló para dataset {dataset_id}: {e}")
                    print(f"Esperando {actual_delay:.1f} segundos antes del siguiente intento...")
                    import time
                    time.sleep(actual_delay)

        # 2) Parsear según extensión
        fname = (ds.filename or "").lower()
        buffer = io.BytesIO(raw)

        try:
            if fname.endswith((".csv", ".tsv")):
                df = pl.read_csv(buffer, separator="\t" if fname.endswith(".tsv") else ",")
            elif fname.endswith((".xlsx", ".xls")):
                excel_data = pl.read_excel(buffer, sheet_id=0, infer_schema_length=1000)
                
                # Polars devuelve un dict con las hojas, extraer la primera
                if isinstance(excel_data, dict):
                    sheet_name = list(excel_data.keys())[0]
                    df = excel_data[sheet_name]
                    print(f"Excel hoja '{sheet_name}' cargada: {df.height} filas, {len(df.columns)} columnas")
                else:
                    df = excel_data
            elif fname.endswith(".parquet"):
                df = pl.read_parquet(buffer)
            else:
                ds.status = "error"
                session.commit()
                print(f"Formato no soportado: {fname}")
                return

        except Exception as e:
            ds.status = "error"
            session.commit()
            print(f"Error parseando dataset {dataset_id}: {e}")
            return

        if df.height == 0:
            ds.status = "error"
            session.commit()
            print(f"Dataset {dataset_id} vacío")
            return

        # 3) Limpiar columnas existentes
        session.exec(delete(Column).where(Column.dataset_id == ds_uuid))
        session.commit()

        # 4) Crear columnas
        cols_to_add = []
        for i, col_name in enumerate(df.columns):
            col_data = df[col_name]
            sample_values = col_data.head(5).to_list()
            sample_values = [to_jsonable(v) for v in sample_values]

            cols_to_add.append(Column(
                dataset_id=ds_uuid,
                original_name=col_name,
                detected_type=str(col_data.dtype),
                sample_values=sample_values,
            ))

        session.add_all(cols_to_add)
        session.commit()

        # 5) Generar y subir archivo Parquet real
        try:
            # Crear archivo Parquet temporal
            with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp_file:
                df.write_parquet(tmp_file.name)
                
                # Subir a Supabase Storage
                parquet_key = f"{ds_uuid}.parquet"
                with open(tmp_file.name, 'rb') as f:
                    parquet_data = f.read()
                
                # Subir archivo Parquet
                supa.storage.from_(bucket).upload(parquet_key, parquet_data)
                
                # Actualizar parquet_url
                ds.parquet_url = f"{bucket}/{parquet_key}"
                
                # Limpiar archivo temporal
                os.unlink(tmp_file.name)
                
        except Exception as e:
            print(f"Error generando Parquet para {dataset_id}: {e}")
            ds.status = "error"
            session.commit()
            return
        
        # 6) Actualizar dataset
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
    Normaliza la URL de storage.
    Si storage_url está vacía, construye una URL basada en storage_key.
    """
    if storage_url and storage_url.strip():
        return storage_url.strip()
    
    if not storage_key or not storage_key.strip():
        raise ValueError("Se requiere storage_key o storage_url")
    
    BUCKET = os.getenv("STORAGE_BUCKET", "uploadeddocs")
    return f"{BUCKET}/{storage_key.strip()}"


def register_dataset(
    *,
    project_id: UUID | str,
    filename: str,
    storage_key: Optional[str] = None,
    storage_url: Optional[str] = None,
    enqueue: bool = True
) -> Dict[str, Any]:
    """
    Registra un nuevo dataset en la base de datos.
    
    Args:
        project_id: ID del proyecto
        filename: Nombre del archivo
        storage_key: Clave en el storage (opcional si se proporciona storage_url)
        storage_url: URL completa del archivo (opcional si se proporciona storage_key)
        enqueue: Si encolar procesamiento automático
        
    Returns:
        Dict con información del dataset creado
    """
    ds_id = uuid4()
    
    try:
        storage_url = _normalize_storage_url(
            storage_key=storage_key,
            storage_url=storage_url
        )
    except ValueError as e:
        raise ValueError(f"Error en storage: {e}")
    
    with Session(engine) as session:
        ds = Dataset(
            id=ds_id,
            project_id=UUID(project_id) if isinstance(project_id, str) else project_id,
            filename=filename,
            storage_url=storage_url,
            status="processing",
            created_at=datetime.datetime.utcnow(),
        )
        session.add(ds)
        session.commit()
        session.refresh(ds)
        
        # Encolar procesamiento si se solicita
        if enqueue:
            try:
                q_ingest.enqueue(process_dataset, str(ds_id))
            except Exception as e:
                print(f"Error encolando dataset {ds_id}: {e}")
        
        return {
            "dataset_id": str(ds_id),
            "filename": filename,
            "storage_url": storage_url,
            "status": "processing",
            "created_at": ds.created_at.isoformat() if ds.created_at else None,
        }