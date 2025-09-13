# backend/models.py
from datetime import datetime
from typing import Optional, List
from uuid import UUID, uuid4

from sqlmodel import SQLModel, Field
from sqlalchemy import Column as SAColumn, Float as SAFloat
from sqlalchemy.dialects.postgresql import ARRAY, JSONB


# ───────────────────── Usuarios / Uploads ─────────────────────

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

    # Guarda la fila original como JSONB
    raw: Optional[dict] = Field(default=None, sa_column=SAColumn(JSONB))


class Embedding(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    event_id: int = Field(foreign_key="normalizedevent.id")
    # Nota: si migras a pgvector, ajusta este tipo en la migración
    vector: list[float] = Field(sa_column=SAColumn(ARRAY(SAFloat)))


# ───────────────────── Datasets tabulares ─────────────────────

class Dataset(SQLModel, table=True):
    __tablename__ = "datasets"

    id: UUID | None = Field(default=None, primary_key=True)
    user_id: str
    filename: str | None = None
    storage_url: str | None = None
    parquet_url: str | None = None
    # Estados esperados por el backend: processing → ready_for_embeddings → ready_for_chat | error
    status: str = "processing"
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

    # Lista de ejemplos como JSONB
    sample_values: Optional[List] = Field(default=None, sa_column=SAColumn(JSONB))

    description: Optional[str] = None


# ───────────────────── Documentos (PDF/DOCX) ─────────────────────

class Document(SQLModel, table=True):
    __tablename__ = "documents"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    user_id: str = Field(index=True)

    filename: str
    storage_url: str

    # processing → ready_for_embeddings → ready_for_chat | error
    status: str = Field(default="processing", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # métricas del parseo
    pages_count: Optional[int] = 0
    text_chars: Optional[int] = 0
    formulas_count: Optional[int] = 0  # cantidad de fórmulas matemáticas encontradas


class DocChunk(SQLModel, table=True):
    __tablename__ = "doc_chunks"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)

    # FK explícita a documents.id
    document_id: UUID = Field(foreign_key="documents.id", index=True)

    page_number: int = 0           # página original (1-based)
    chunk_index: int = 0           # índice dentro de la página (0-based)
    content: str                   # texto/chunk (markdown o texto plano)


class DocumentFormula(SQLModel, table=True):
    __tablename__ = "document_formulas"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)

    # FK a documents.id
    document_id: UUID = Field(foreign_key="documents.id", index=True)

    page_number: int = 0           # página donde se encontró la fórmula (1-based)
    formula_index: int = 0         # índice de la fórmula en la página (0-based)
    latex_code: str               # código LaTeX de la fórmula
    original_text: Optional[str] = None  # texto original de la fórmula
    confidence_score: Optional[float] = None  # confianza del reconocimiento (0-1)
