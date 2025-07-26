from datetime import datetime
from typing import Optional, List
from sqlmodel import SQLModel, Field, Column
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy import Column as SAColumn, Float
from sqlalchemy.dialects.postgresql import JSONB   # añade arriba
from sqlalchemy import Float, Column              # añade Float
from sqlalchemy.dialects.postgresql import ARRAY
from uuid import UUID
from uuid import uuid4




class User(SQLModel, table=True):
    id: str = Field(primary_key=True)          # Clerk user_id
    email: str

class UploadedFile(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str = Field(foreign_key="user.id")
    filename: str
    storage_path: str
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)

    

class NormalizedEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    file_id: int = Field(foreign_key="uploadedfile.id")
    date: Optional[datetime] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    description: Optional[str] = None

    # 👇 mapea dict ⇄ jsonb
    raw: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSONB)
    )             # JSONB para conservar la fila original

class Embedding(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    event_id: int = Field(foreign_key="normalizedevent.id")
    vector: list[float] = Field(
        sa_column=Column(ARRAY(Float))         # pgvector usa tipo vector; aquí usamos ARRAY<float> y lo mapeamos vía pgvector en la migración
    )


class Dataset(SQLModel, table=True):
    __tablename__ = "datasets"
    id: UUID | None = Field(default=None, primary_key=True)
    user_id: str
    filename: str | None = None
    storage_url: str | None = None
    parquet_url: str | None = None
    status: str = "processing"          # processing | ready | error
    rows_count: int | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Column(SQLModel, table=True):
    __tablename__ = "columns"

    # PK autogenerada
    id: UUID = Field(default_factory=uuid4, primary_key=True)

    dataset_id: UUID = Field(foreign_key="datasets.id")

    original_name:  str
    canonical_name: Optional[str] = None
    detected_type:  Optional[str] = None

    # Guarda lista de ejemplos como JSONB
    sample_values: Optional[List] = Field(
        default=None,
        sa_column=SAColumn(JSONB)
    )

    description: Optional[str] = None