# backend/ingest_document.py
import os, io
from uuid import UUID
from typing import List
from sqlmodel import Session, select

from backend.db import engine
from backend.supabase_client import get_supabase
from backend.models import Document, DocChunk, DocumentFormula

# Colas
from redis import Redis
from rq import Queue

redis_conn   = Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
q_embeddings = Queue("embeddings", connection=redis_conn)

BUCKET = os.getenv("STORAGE_BUCKET", "uploadeddocs")

# Límites y parámetros (configurables por env)
MAX_PAGES              = int(os.getenv("DOCLING_MAX_PAGES", "500"))
MAX_CHARS_PER_PAGE     = int(os.getenv("DOCLING_MAX_CHARS_PER_PAGE", "50000"))
CHUNK_MAX_CHARS        = int(os.getenv("CHUNK_MAX_CHARS", "1200"))
CHUNK_OVERLAP          = int(os.getenv("CHUNK_OVERLAP", "200"))
SAVE_MD_TO_STORAGE     = os.getenv("DOCLING_SAVE_MD", "false").lower() in ("1", "true", "on")


def _chunk_text(text: str, max_chars: int = CHUNK_MAX_CHARS, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """
    Chunking v2: Crea sub-chunks por página para maximizar recall.
    Mantiene anclaje por página (base 1) para citas en el visor.
    """
    text = (text or "").strip()
    if not text:
        return []
    
    # Si el texto es muy corto, no chunkear
    if len(text) <= max_chars:
        return [text]
    
    chunks, start, n = [], 0, len(text)
    while start < n:
        end = min(start + max_chars, n)
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end == n:
            break
        start = max(0, end - overlap)
    return chunks


def process_document(document_id: UUID):
    """
    Descarga PDF/DOCX desde Storage, lo parsea con Docling (incluyendo fórmulas matemáticas),
    trocea a chunks y los persiste. Luego encola embeddings.
    """
    from backend.parsers.docling_adapter import extract_pages_and_formulas  # import diferido

    supa = get_supabase()

    with Session(engine) as session:
        doc = session.exec(select(Document).where(Document.id == document_id)).first()
        if not doc:
            print(f"[doc] {document_id} no encontrado")
            return
        if doc.status not in ("processing", "ready_for_embeddings", "error"):
            # si ya está listo para chat, evita reingesta accidental
            print(f"[doc] {document_id} estado={doc.status}, omito reingesta")
            return

        # 1) Descargar objeto
        try:
            key = doc.storage_url.split("/", 1)[1]
            raw = supa.storage.from_(BUCKET).download(key)
        except Exception as e:
            doc.status = "error"
            session.commit()
            print("[doc] download error:", e)
            return

        # 2) Parse con Docling adapter
        fname = (doc.filename or "").lower()
        ext = "pdf" if fname.endswith(".pdf") else "docx" if fname.endswith(".docx") else ""
        try:
            pages, formulas = extract_pages_and_formulas(raw, ext=ext)
        except Exception as e:
            doc.status = "error"
            session.commit()
            print("[doc] parse error:", e)
            return

        # Sanea y limita
        pages = [(p or "").strip() for p in pages if (p or "").strip()]
        if not pages:
            doc.status = "error"
            session.commit()
            print("[doc] parse empty: no pages extracted")
            return

        if len(pages) > MAX_PAGES:
            pages = pages[:MAX_PAGES]
        # recorta páginas muy grandes
        pages = [p[:MAX_CHARS_PER_PAGE] for p in pages]

        # 2.1) (Opcional) guardar un markdown simple para depuración/preview extendido
        if SAVE_MD_TO_STORAGE:
            try:
                md = ("\n\n---\n\n").join(pages)
                supa.storage.from_(BUCKET).upload(
                    f"docassets/{document_id}/out.md",
                    md.encode("utf-8"),
                    file_options={"upsert": "true"},
                )
            except Exception as e:
                # no es crítico
                print("[doc] save md to storage failed:", e)

        # 3) Guardar metadatos
        total_chars = sum(len(p) for p in pages)
        doc.pages_count = len(pages)
        doc.text_chars  = total_chars
        session.commit()

        # 4) Idempotencia: borra chunks previos si existen (reintentos)
        old_chunks = session.exec(
            select(DocChunk).where(DocChunk.document_id == document_id)
        ).all()
        if old_chunks:
            for oc in old_chunks:
                session.delete(oc)
            session.commit()

        # 5) Troceo y persistencia (bulk) - Chunking v2
        to_add: List[DocChunk] = []
        chunk_count = 0
        for page_idx, page_text in enumerate(pages, start=1):
            parts = _chunk_text(page_text, max_chars=CHUNK_MAX_CHARS, overlap=CHUNK_OVERLAP)
            for ci, ch in enumerate(parts):
                to_add.append(DocChunk(
                    document_id  = document_id,
                    workspace_id = doc.workspace_id,  # Incluir workspace_id
                    page_number  = page_idx,          # Anclaje por página (1-based)
                    chunk_index  = ci,                # Sub-chunk dentro de la página (0-based)
                    content      = ch,
                ))
                chunk_count += 1

        if not to_add:
            doc.status = "error"
            session.commit()
            print("[doc] chunking empty after parse")
            return

        session.add_all(to_add)
        session.commit()

        # 5.1) Guardar fórmulas matemáticas extraídas
        if formulas:
            formulas_to_add = []
            for idx, formula_info in enumerate(formulas):
                formula = DocumentFormula(
                    document_id=document_id,
                    page_number=formula_info.get('page_number', 0),
                    formula_index=idx,
                    latex_code=formula_info.get('latex_code', ''),
                    original_text=formula_info.get('original_text'),
                    confidence_score=formula_info.get('confidence_score'),
                )
                formulas_to_add.append(formula)

            if formulas_to_add:
                session.add_all(formulas_to_add)
                session.commit()
                print(f"[doc] saved {len(formulas_to_add)} formulas to database")

        # 5.2) Actualizar contador de fórmulas en el documento
        doc.formulas_count = len(formulas) if formulas else 0

        # 6) Estado listo para embeddings + encolar
        doc.status = "ready_for_embeddings"
        session.commit()

        try:
            from backend.tasks.doc_embeddings import generate_doc_embeddings
            q_embeddings.enqueue(generate_doc_embeddings, str(document_id))
        except Exception as e:
            # No es fatal: el doc ya quedó parseado
            print("[doc] enqueue doc embeddings failed:", e)

        # Document processing completed successfully

        print(f"[doc] {document_id} → pages={len(pages)} chunks={chunk_count} formulas={len(formulas) if formulas else 0}")
