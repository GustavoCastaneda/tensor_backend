#!/usr/bin/env python3
"""
Borra todos los documentos, chunks, memorias y colecciones de Qdrant de un usuario.

Uso:
  poetry run python scripts/clear_user_documents.py --user_id <USER_ID> [--skip-qdrant] [--qdrant-timeout 2.0]

Variables de entorno relevantes:
  QDRANT_URL (ej. http://10.0.13.104:6333)
"""

import os
import sys
import argparse
from typing import List

from sqlmodel import select, delete

from backend.db import get_session
from backend.models import (
    Document,
    DocChunk,
    DocumentFormula,
    UploadedFile,
    NormalizedEvent,
    Embedding,
    Dataset,
    Column,
)

try:
    from backend.models import MemoryUnit
except Exception:
    MemoryUnit = None  # opcional

from qdrant_client import QdrantClient


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user_id", required=True, help="ID del usuario (Clerk sub)")
    parser.add_argument("--skip-qdrant", action="store_true", help="No intentar borrar colecciones en Qdrant")
    parser.add_argument("--qdrant-timeout", type=float, default=float(os.getenv("QDRANT_TIMEOUT_SECONDS", "2.0")), help="Timeout por request a Qdrant (s)")
    args = parser.parse_args()

    user_id = args.user_id
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    qc = None
    if not args.skip_qdrant:
        try:
            qc = QdrantClient(url=qdrant_url, timeout=args.qdrant_timeout)
            # ping rápido
            _ = qc.get_collections()
        except Exception as e:
            print(f"⚠ Qdrant no accesible ({e}). Continuo con DB y memorias; omito Qdrant.")
            args.skip_qdrant = True

    print(f"→ Limpiando datos del usuario: {user_id}")
    print(f"→ Qdrant: {qdrant_url}")

    with next(get_session()) as session:
        docs: List[Document] = session.exec(
            select(Document).where(Document.user_id == user_id)
        ).all()

        if not docs:
            print("No hay documentos en DB para este usuario.")
            doc_ids = []

        if docs:
            doc_ids = [str(d.id) for d in docs]
            print(f"Documentos encontrados en DB: {len(doc_ids)}")

        # 1) Eliminar fórmulas primero por FK
        try:
            session.exec(delete(DocumentFormula).where(DocumentFormula.document_id.in_(doc_ids)))
            print(f"✔ Fórmulas eliminadas para {len(doc_ids)} documentos")
        except Exception as e:
            print(f"⚠ Error eliminando fórmulas: {e}")

        # 2) Eliminar chunks (en bloque)
        try:
            session.exec(delete(DocChunk).where(DocChunk.document_id.in_(doc_ids)))
            print(f"✔ Chunks eliminados para {len(doc_ids)} documentos")
        except Exception as e:
            print(f"⚠ Error eliminando chunks: {e}")

        # 3) Eliminar datasets/tabular (si existen) del usuario
        try:
            # Columns → Datasets (por FK)
            ds_ids = [d.id for d in session.exec(select(Dataset).where(Dataset.user_id == user_id)).all()]
            if ds_ids:
                session.exec(delete(Column).where(Column.dataset_id.in_(ds_ids)))
                session.exec(delete(Dataset).where(Dataset.id.in_(ds_ids)))
                print(f"✔ Datasets y columnas eliminados: {len(ds_ids)} datasets")
        except Exception as e:
            print(f"⚠ Error eliminando datasets/columns: {e}")

        # 4) Eliminar archivos subidos y eventos normalizados (si existen)
        try:
            file_ids = [f.id for f in session.exec(select(UploadedFile).where(UploadedFile.user_id == user_id)).all()]
            if file_ids:
                # Embeddings → NormalizedEvent → UploadedFile
                ev_ids = [e.id for e in session.exec(select(NormalizedEvent).where(NormalizedEvent.file_id.in_(file_ids))).all()]
                if ev_ids:
                    session.exec(delete(Embedding).where(Embedding.event_id.in_(ev_ids)))
                    session.exec(delete(NormalizedEvent).where(NormalizedEvent.id.in_(ev_ids)))
                session.exec(delete(UploadedFile).where(UploadedFile.id.in_(file_ids)))
                print(f"✔ Uploads y eventos eliminados: {len(file_ids)} archivos")
        except Exception as e:
            print(f"⚠ Error eliminando uploads/events: {e}")

        # 5) Eliminar documentos
        try:
            session.exec(delete(Document).where(Document.user_id == user_id))
            print("✔ Documentos eliminados")
        except Exception as e:
            print(f"⚠ Error eliminando documentos: {e}")

        # 6) Eliminar memorias (opcional)
        if MemoryUnit is not None:
            try:
                session.exec(delete(MemoryUnit).where(MemoryUnit.workspace_id == user_id))
                print("✔ Memorias eliminadas (workspace)")
            except Exception as e:
                print(f"⚠ Error eliminando memorias: {e}")

        session.commit()

        # 7) Eliminar colecciones de Qdrant (best-effort)
        if not args.skip_qdrant and qc is not None:
            deleted, failed, skipped = 0, 0, 0
            candidates: List[str] = []
            if doc_ids:
                candidates = doc_ids
            else:
                # Enumerar todas las colecciones y chequear payload workspace_id == user
                try:
                    cols = qc.get_collections()
                    for c in getattr(cols, "collections", []) or []:
                        candidates.append(c.name)
                except Exception as e:
                    print(f"⚠ No se pudo listar colecciones de Qdrant: {e}")

            for cid in candidates:
                # Si no tenemos certeza que la colección es del usuario, validamos con count + filtro
                should_delete = False
                if not doc_ids:
                    try:
                        cnt = qc.count(
                            collection_name=cid,
                            count_filter={
                                "must": [{"key": "workspace_id", "match": {"value": user_id}}]
                            },
                        )
                        should_delete = bool(getattr(cnt, "count", 0))
                    except Exception as e:
                        print(f"  ⚠ No se pudo contar en {cid}: {e}")
                        skipped += 1
                        continue
                else:
                    should_delete = True

                if should_delete:
                    try:
                        qc.delete_collection(cid, timeout=args.qdrant_timeout)
                        print(f"  ✔ Qdrant colección {cid} eliminada")
                        deleted += 1
                    except Exception as e:
                        print(f"  ⚠ No se pudo eliminar colección {cid}: {e}")
                        failed += 1
                else:
                    skipped += 1
            print(f"Resumen Qdrant → eliminadas: {deleted}, fallidas: {failed}, omitidas: {skipped}")

        print("✅ Limpieza completada (DB)" + (" + Qdrant" if (not args.skip_qdrant and qc is not None) else " (Qdrant omitido)"))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


