# backend/ingest_dataset.py
import os, tempfile, io, datetime
import polars as pl
from uuid import UUID
from sqlmodel import Session, select

from backend.db import engine
from backend.supabase_client import get_supabase
from backend.models import Dataset, Column          # Column = tabla "columns"

BUCKET = os.getenv("STORAGE_BUCKET", "uploadeddocs")

# ──────────────────────────────────────────────────────────────────────────
def to_jsonable(val):
    """Convierte valores a tipos serializables por JSON."""
    if isinstance(val, (datetime.date, datetime.datetime)):
        return val.isoformat()
    if isinstance(val, (bytes, bytearray)):
        return val.decode("utf-8", errors="ignore")
    return val

# ──────────────────────────────────────────────────────────────────────────
def process_dataset(dataset_id: UUID):
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
            raw_bytes = supa.storage.from_(BUCKET).download(
                ds.storage_url.split("/", 1)[1]
            )
        except Exception as e:
            print("download error:", e)
            return

        buffer = io.BytesIO(raw_bytes)

        # 2️⃣ Lee con Polars
        fname = ds.filename.lower().strip()
        if fname.endswith((".xlsx", ".xls")):
            df = pl.read_excel(buffer)          # requiere fastexcel
        elif fname.endswith(".csv"):
            df = pl.read_csv(buffer)
        else:
            ds.status = "error"
            session.commit()
            print("Unsupported file type:", fname)
            return

        # 3️⃣ Escribe Parquet y sube
        pq_path = f"{dataset_id}.parquet"
        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            df.write_parquet(tmp.name)
            tmp.seek(0)
            data = tmp.read()

            supa.storage.from_(BUCKET).upload(
                pq_path,
                data,
                file_options={"upsert": "true"}   # str, no bool
            )

        ds.parquet_url = f"{BUCKET}/{pq_path}"

        # 4️⃣ Rellena tabla columns
        for col in df.columns:
            dtype = str(df[col].dtype)
            sample_vals = [to_jsonable(v) for v in df[col].head(5).to_list()]

            session.add(
                Column(
                    dataset_id     = dataset_id,
                    original_name  = col,
                    detected_type  = dtype,
                    sample_values  = sample_vals,
                )
            )

        # 5️⃣ Actualiza dataset
        ds.rows_count = df.height
        ds.status     = "ready"
        session.commit()

        print(f"Dataset {dataset_id} → ready ({df.height} rows, {len(df.columns)} cols)")

# ──────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys, uuid
    if len(sys.argv) != 2:
        print("Usage: poetry run python backend/ingest_dataset.py <dataset_id>")
        sys.exit(1)

    process_dataset(uuid.UUID(sys.argv[1]))
