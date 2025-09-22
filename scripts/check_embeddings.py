#!/usr/bin/env python3
"""
Script para verificar el estado de los embeddings en Qdrant
Diagnostica si los documentos están correctamente indexados
"""

import os
import sys
from pathlib import Path

# Agregar el directorio backend al path
sys.path.append(str(Path(__file__).parent.parent))

from qdrant_client import QdrantClient
from qdrant_client.http import models
from backend.query_embeddings import get_query_embedding
from backend.db import get_session
from backend.models import Document as DBDocument

def check_collection_status(collection_name: str, qdrant: QdrantClient):
    """Verifica el estado de una colección específica"""
    try:
        # Obtener información de la colección
        collection_info = qdrant.get_collection(collection_name)
        
        print(f"\n=== COLECCIÓN: {collection_name} ===")
        print(f"Puntos totales: {collection_info.points_count}")
        print(f"Vectores indexados: {collection_info.indexed_vectors_count}")
        print(f"Status: {collection_info.status}")
        
        # Verificar configuración de vectores
        if collection_info.config:
            print(f"Tamaño de vector: {collection_info.config.params.vectors.size}")
            print(f"Distancia: {collection_info.config.params.vectors.distance}")
        
        # Verificar payload schema
        if collection_info.payload_schema:
            print(f"Campos de payload: {list(collection_info.payload_schema.keys())}")
            
            # Verificar si hay índice de texto
            if "text" in collection_info.payload_schema:
                text_index = collection_info.payload_schema["text"]
                print(f"Índice de texto: {text_index}")
            else:
                print("⚠️  No hay índice de texto para búsqueda lexical")
        
        # Obtener algunos puntos de muestra
        points, _ = qdrant.scroll(
            collection_name=collection_name,
            limit=3,
            with_payload=True,
            with_vectors=False
        )
        
        print(f"\nMuestra de {len(points)} puntos:")
        for i, point in enumerate(points):
            payload = point.payload or {}
            print(f"  Punto {i+1}:")
            print(f"    ID: {point.id}")
            print(f"    Score: {point.score}")
            print(f"    Texto: {payload.get('text', 'NO_TEXT')[:100]}...")
            print(f"    Doc ID: {payload.get('doc_id', 'NO_DOC_ID')}")
            print(f"    Página: {payload.get('page', 'NO_PAGE')}")
            print(f"    Chunk: {payload.get('chunk_seq', 'NO_CHUNK')}")
            print(f"    Workspace: {payload.get('workspace_id', 'NO_WORKSPACE')}")
            print()
        
        return True
        
    except Exception as e:
        print(f"❌ Error verificando colección {collection_name}: {e}")
        return False

def test_search_functionality(collection_name: str, qdrant: QdrantClient):
    """Prueba la funcionalidad de búsqueda"""
    try:
        print(f"\n=== PRUEBA DE BÚSQUEDA: {collection_name} ===")
        
        # Test 1: Búsqueda vectorial básica
        test_query = "DESARROLLO E IMPLEMENTACION DEL SISTEMA"
        print(f"Query de prueba: '{test_query}'")
        
        # Generar embedding
        query_vector = get_query_embedding(test_query)
        print(f"Vector generado: {len(query_vector)} dimensiones")
        
        # Búsqueda vectorial
        search_results = qdrant.search(
            collection_name=collection_name,
            query_vector=query_vector,
            limit=3,
            with_payload=True
        )
        
        print(f"Resultados de búsqueda vectorial: {len(search_results)}")
        for i, result in enumerate(search_results):
            payload = result.payload or {}
            text_preview = payload.get('text', '')[:100]
            print(f"  {i+1}. Score: {result.score:.4f} | Texto: '{text_preview}...'")
        
        # Test 2: Búsqueda con filtros
        print(f"\n--- Búsqueda con filtros ---")
        filtered_results = qdrant.search(
            collection_name=collection_name,
            query_vector=query_vector,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="workspace_id",
                        match=models.MatchValue(value="87c233a0-8579-4d95-9eaa-5d475b09d5ac")
                    )
                ]
            ),
            limit=3,
            with_payload=True
        )
        
        print(f"Resultados filtrados: {len(filtered_results)}")
        for i, result in enumerate(filtered_results):
            payload = result.payload or {}
            text_preview = payload.get('text', '')[:100]
            print(f"  {i+1}. Score: {result.score:.4f} | Texto: '{text_preview}...'")
        
        # Test 3: Búsqueda lexical (si hay índice de texto)
        print(f"\n--- Búsqueda lexical ---")
        try:
            lexical_results = qdrant.search(
                collection_name=collection_name,
                query_vector=[0.0] * 1536,  # Vector dummy
                query_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="workspace_id",
                            match=models.MatchValue(value="87c233a0-8579-4d95-9eaa-5d475b09d5ac")
                        ),
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
                text_preview = payload.get('text', '')[:100]
                print(f"  {i+1}. Score: {result.score:.4f} | Texto: '{text_preview}...'")
                
        except Exception as e:
            print(f"⚠️  Búsqueda lexical falló: {e}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error en prueba de búsqueda: {e}")
        return False

def check_database_documents(workspace_id: str):
    """Verifica los documentos en la base de datos"""
    try:
        print(f"\n=== DOCUMENTOS EN BD: {workspace_id} ===")
        
        session = next(get_session())
        documents = session.query(DBDocument).filter(
            DBDocument.workspace_id == workspace_id
        ).all()
        
        print(f"Documentos encontrados en BD: {len(documents)}")
        
        for doc in documents:
            print(f"  - ID: {doc.id}")
            print(f"    Título: {doc.title}")
            print(f"    Estado: {doc.status}")
            print(f"    Creado: {doc.created_at}")
            print(f"    Tamaño: {doc.file_size_bytes} bytes")
            print()
        
        return documents
        
    except Exception as e:
        print(f"❌ Error verificando BD: {e}")
        return []

def main():
    """Función principal de diagnóstico"""
    print("🔍 DIAGNÓSTICO DE EMBEDDINGS EN QDRANT")
    print("=" * 50)
    
    # Configuración
    workspace_id = "87c233a0-8579-4d95-9eaa-5d475b09d5ac"
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    
    try:
        # Conectar a Qdrant
        print(f"Conectando a Qdrant en {qdrant_url}...")
        qdrant = QdrantClient(url=qdrant_url, timeout=10.0)
        
        # Verificar conexión
        collections = qdrant.get_collections()
        print(f"✅ Conectado. Colecciones disponibles: {len(collections.collections)}")
        
        # Listar colecciones
        print("\nColecciones encontradas:")
        for col in collections.collections:
            print(f"  - {col.name}")
        
        # Verificar documentos en BD
        documents = check_database_documents(workspace_id)
        
        if not documents:
            print(f"❌ No se encontraron documentos para workspace {workspace_id}")
            return
        
        # Verificar cada colección de documento
        for doc in documents:
            collection_name = str(doc.id)
            
            # Verificar si la colección existe
            try:
                qdrant.get_collection(collection_name)
                print(f"\n✅ Colección {collection_name} existe")
                
                # Verificar estado
                check_collection_status(collection_name, qdrant)
                
                # Probar búsqueda
                test_search_functionality(collection_name, qdrant)
                
            except Exception as e:
                print(f"❌ Colección {collection_name} no existe o error: {e}")
        
        print("\n" + "=" * 50)
        print("✅ Diagnóstico completado")
        
    except Exception as e:
        print(f"❌ Error general: {e}")

if __name__ == "__main__":
    main()
