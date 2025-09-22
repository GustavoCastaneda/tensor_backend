#!/usr/bin/env python3
"""
Script para verificar una colección específica en Qdrant
"""

import os
import sys
from pathlib import Path

# Agregar el directorio backend al path
sys.path.append(str(Path(__file__).parent.parent))

from qdrant_client import QdrantClient
from qdrant_client.http import models
from backend.query_embeddings import get_query_embedding

def check_specific_collection():
    """Verifica la colección específica del workspace"""
    workspace_id = "87c233a0-8579-4d95-9eaa-5d475b09d5ac"
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    
    try:
        # Conectar a Qdrant
        qdrant = QdrantClient(url=qdrant_url, timeout=10.0)
        
        # Verificar la colección específica
        collection_name = workspace_id
        print(f"🔍 Verificando colección: {collection_name}")
        
        # Obtener información de la colección
        collection_info = qdrant.get_collection(collection_name)
        print(f"✅ Colección existe")
        print(f"Puntos totales: {collection_info.points_count}")
        print(f"Vectores indexados: {collection_info.indexed_vectors_count}")
        
        if collection_info.points_count == 0:
            print("❌ La colección está vacía - no hay embeddings")
            return
        
        # Obtener algunos puntos de muestra
        points, _ = qdrant.scroll(
            collection_name=collection_name,
            limit=5,
            with_payload=True,
            with_vectors=False
        )
        
        print(f"\n📄 Muestra de {len(points)} puntos:")
        for i, point in enumerate(points):
            payload = point.payload or {}
            print(f"\n--- Punto {i+1} ---")
            print(f"ID: {point.id}")
            print(f"Score: {getattr(point, 'score', 'N/A')}")
            print(f"Doc ID: {payload.get('doc_id', 'NO_DOC_ID')}")
            print(f"Workspace: {payload.get('workspace_id', 'NO_WORKSPACE')}")
            print(f"Página: {payload.get('page', 'NO_PAGE')}")
            print(f"Chunk: {payload.get('chunk_seq', 'NO_CHUNK')}")
            print(f"Título: {payload.get('title', 'NO_TITLE')}")
            print(f"Tipo: {payload.get('block_type', 'NO_TYPE')}")
            print(f"Idioma: {payload.get('lang', 'NO_LANG')}")
            print(f"Texto: {payload.get('text', 'NO_TEXT')[:200]}...")
        
        # Probar búsqueda específica
        print(f"\n🔍 Probando búsqueda para 'DESARROLLO E IMPLEMENTACION DEL SISTEMA'")
        
        query_vector = get_query_embedding("DESARROLLO E IMPLEMENTACION DEL SISTEMA")
        search_results = qdrant.search(
            collection_name=collection_name,
            query_vector=query_vector,
            limit=5,
            with_payload=True
        )
        
        print(f"Resultados encontrados: {len(search_results)}")
        for i, result in enumerate(search_results):
            payload = result.payload or {}
            text_preview = payload.get('text', '')[:150]
            print(f"  {i+1}. Score: {result.score:.4f}")
            print(f"     Texto: '{text_preview}...'")
            print(f"     Página: {payload.get('page', 'N/A')}")
            print()
        
        # Verificar si hay índice de texto
        if collection_info.payload_schema and "text" in collection_info.payload_schema:
            print("✅ Índice de texto disponible para búsqueda lexical")
            
            # Probar búsqueda lexical
            print(f"\n🔍 Probando búsqueda lexical...")
            try:
                lexical_results = qdrant.search(
                    collection_name=collection_name,
                    query_vector=[0.0] * 1536,  # Vector dummy
                    query_filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="text",
                                match=models.MatchText(text="DESARROLLO")
                            )
                        ]
                    ),
                    limit=3,
                    with_payload=True
                )
                
                print(f"Resultados lexicales: {len(lexical_results)}")
                for i, result in enumerate(lexical_results):
                    payload = result.payload or {}
                    text_preview = payload.get('text', '')[:150]
                    print(f"  {i+1}. Score: {result.score:.4f} | Texto: '{text_preview}...'")
                    
            except Exception as e:
                print(f"⚠️  Búsqueda lexical falló: {e}")
        else:
            print("⚠️  No hay índice de texto - solo búsqueda vectorial disponible")
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    check_specific_collection()
