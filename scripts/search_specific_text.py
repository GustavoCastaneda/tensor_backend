#!/usr/bin/env python3
"""
Script para buscar texto específico en los chunks vectorizados
"""

import os
import sys
from pathlib import Path

# Agregar el directorio backend al path
sys.path.append(str(Path(__file__).parent.parent))

from qdrant_client import QdrantClient
from qdrant_client.http import models

def search_specific_text():
    """Busca texto específico en todos los chunks"""
    workspace_id = "87c233a0-8579-4d95-9eaa-5d475b09d5ac"
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    
    # Textos específicos a buscar
    search_terms = [
        "DESARROLLO E IMPLEMENTACION DEL SISTEMA",
        "DESARROLLO E IMPLEMENTACION DEL SISTEMA $ 650,000.00",
        "650,000.00",
        "costo único",
        "IMPLEMENTACION DEL SISTEMA"
    ]
    
    try:
        # Conectar a Qdrant
        qdrant = QdrantClient(url=qdrant_url, timeout=10.0)
        collection_name = workspace_id
        
        print(f"🔍 Buscando texto específico en colección: {collection_name}")
        print("=" * 60)
        
        # Obtener TODOS los puntos de la colección
        all_points = []
        offset = None
        
        while True:
            points, next_offset = qdrant.scroll(
                collection_name=collection_name,
                limit=100,  # Obtener de a 100 puntos
                offset=offset,
                with_payload=True,
                with_vectors=False
            )
            
            if not points:
                break
                
            all_points.extend(points)
            offset = next_offset
            
            if next_offset is None:
                break
        
        print(f"📊 Total de chunks encontrados: {len(all_points)}")
        print()
        
        # Buscar cada término específico
        for term in search_terms:
            print(f"🔍 Buscando: '{term}'")
            print("-" * 40)
            
            found_matches = []
            
            for i, point in enumerate(all_points):
                payload = point.payload or {}
                text = payload.get('text', '')
                
                if term.upper() in text.upper():
                    found_matches.append({
                        'chunk_id': i + 1,
                        'point_id': point.id,
                        'text': text,
                        'payload': payload
                    })
            
            if found_matches:
                print(f"✅ Encontrado en {len(found_matches)} chunk(s):")
                for match in found_matches:
                    print(f"\n  📄 Chunk {match['chunk_id']} (ID: {match['point_id']})")
                    print(f"     Texto completo: {match['text'][:300]}...")
                    print(f"     Metadatos: {match['payload']}")
            else:
                print(f"❌ No encontrado")
            
            print()
        
        # Mostrar resumen de todos los chunks
        print("📋 RESUMEN DE TODOS LOS CHUNKS:")
        print("=" * 60)
        
        for i, point in enumerate(all_points):
            payload = point.payload or {}
            text = payload.get('text', '')
            
            print(f"\n--- Chunk {i+1} ---")
            print(f"ID: {point.id}")
            print(f"Texto: {text[:200]}...")
            print(f"Longitud: {len(text)} caracteres")
            print(f"Metadatos: {payload}")
            
            # Verificar si contiene alguna de las palabras clave
            keywords = ["DESARROLLO", "IMPLEMENTACION", "SISTEMA", "650,000", "costo"]
            found_keywords = [kw for kw in keywords if kw.upper() in text.upper()]
            if found_keywords:
                print(f"🔑 Palabras clave encontradas: {found_keywords}")
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    search_specific_text()
