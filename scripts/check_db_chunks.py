#!/usr/bin/env python3
"""
Script para verificar chunks en la base de datos
"""

import os
import sys
from pathlib import Path

# Agregar el directorio backend al path
sys.path.append(str(Path(__file__).parent.parent))

from sqlmodel import Session, select
from backend.db import engine
from backend.models import Document, DocChunk

def check_db_chunks():
    """Verifica chunks en la base de datos para un documento específico"""
    document_id = "87c233a0-8579-4d95-9eaa-5d475b09d5ac"
    
    try:
        with Session(engine) as session:
            # Verificar el documento
            doc = session.exec(select(Document).where(Document.id == document_id)).first()
            if not doc:
                print(f"❌ Documento {document_id} no encontrado en la BD")
                return
            
            print(f"📄 Documento encontrado:")
            print(f"  ID: {doc.id}")
            print(f"  Filename: {doc.filename}")
            print(f"  Estado: {doc.status}")
            print(f"  Páginas: {doc.pages_count}")
            print(f"  Caracteres: {doc.text_chars}")
            print(f"  Fórmulas: {doc.formulas_count}")
            print(f"  Workspace: {doc.workspace_id}")
            print()
            
            # Verificar chunks en la BD
            chunks = session.exec(
                select(DocChunk).where(DocChunk.document_id == document_id)
            ).all()
            
            print(f"📊 Chunks en BD: {len(chunks)}")
            print("=" * 60)
            
            for i, chunk in enumerate(chunks):
                print(f"\n--- Chunk {i+1} ---")
                print(f"ID: {chunk.id}")
                print(f"Document ID: {chunk.document_id}")
                print(f"Workspace ID: {chunk.workspace_id}")
                print(f"Página: {chunk.page_number}")
                print(f"Chunk Index: {chunk.chunk_index}")
                print(f"Contenido: {chunk.content[:200]}...")
                print(f"Longitud: {len(chunk.content)} caracteres")
                
                # Buscar texto específico
                content_upper = chunk.content.upper()
                if "DESARROLLO E IMPLEMENTACION DEL SISTEMA" in content_upper:
                    print("🔑 ✅ ENCONTRADO: 'DESARROLLO E IMPLEMENTACION DEL SISTEMA'")
                if "650,000.00" in content_upper:
                    print("🔑 ✅ ENCONTRADO: '650,000.00'")
                if "costo único" in content_upper:
                    print("🔑 ✅ ENCONTRADO: 'costo único'")
            
            # Buscar específicamente el texto que buscamos
            print(f"\n🔍 BÚSQUEDA ESPECÍFICA:")
            print("-" * 40)
            
            search_terms = [
                "DESARROLLO E IMPLEMENTACION DEL SISTEMA",
                "650,000.00",
                "costo único",
                "IMPLEMENTACION DEL SISTEMA"
            ]
            
            for term in search_terms:
                found_chunks = []
                for chunk in chunks:
                    if term.upper() in chunk.content.upper():
                        found_chunks.append(chunk)
                
                if found_chunks:
                    print(f"✅ '{term}' encontrado en {len(found_chunks)} chunk(s):")
                    for chunk in found_chunks:
                        print(f"  - Página {chunk.page_number}, Chunk {chunk.chunk_index}")
                        print(f"    Texto: {chunk.content[:300]}...")
                else:
                    print(f"❌ '{term}' NO encontrado")
            
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    check_db_chunks()
