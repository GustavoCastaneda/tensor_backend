#!/usr/bin/env python3
"""
Script para probar que el contexto se está pasando correctamente al LLM
con el texto completo sin truncación
"""

import os
import sys
import requests
import json
from typing import Dict, Any

# Configuración
API_BASE_URL = "http://localhost:8000"
TEST_WORKSPACE_ID = "87c233a0-8579-4d95-9eaa-5d475b09d5ac"
TEST_QUERY = "¿Cuál es el costo del desarrollo e implementación del sistema?"

def test_context_length():
    """Prueba que el contexto se pasa completo al LLM"""
    print("🧪 Probando longitud del contexto en chat semántico")
    print("=" * 60)
    
    # Headers para la petición
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer test-token"  # Ajustar según tu auth
    }
    
    # Payload de la petición
    payload = {
        "workspace_id": TEST_WORKSPACE_ID,
        "message": TEST_QUERY,
        "include_debug": True,
        "hybrid_enabled": True,
        "hybrid_alpha": 0.7,
        "k": 8,
        "min_score": 0.35
    }
    
    try:
        print(f"📤 Enviando consulta: '{TEST_QUERY}'")
        print(f"📁 Workspace ID: {TEST_WORKSPACE_ID}")
        print()
        
        # Hacer la petición
        response = requests.post(
            f"{API_BASE_URL}/chat/semantic",
            headers=headers,
            json=payload,
            timeout=30
        )
        
        if response.status_code != 200:
            print(f"❌ Error en la petición: {response.status_code}")
            print(f"Response: {response.text}")
            return False
        
        data = response.json()
        
        print("✅ Respuesta recibida exitosamente")
        print()
        
        # Analizar la respuesta
        answer = data.get("answer", "")
        reasoning = data.get("reasoning", "")
        citations = data.get("citations", [])
        debug = data.get("debug", {})
        
        print("📊 ANÁLISIS DE LA RESPUESTA:")
        print("-" * 40)
        print(f"Longitud de la respuesta: {len(answer)} caracteres")
        print(f"Longitud del razonamiento: {len(reasoning)} caracteres")
        print(f"Número de citas: {len(citations)}")
        print()
        
        # Verificar si la respuesta contiene el texto específico
        target_text = "DESARROLLO E IMPLEMENTACION DEL SISTEMA"
        cost_text = "650,000.00"
        
        print("🔍 VERIFICACIÓN DE CONTENIDO:")
        print("-" * 40)
        
        if target_text in answer:
            print(f"✅ Encontrado '{target_text}' en la respuesta")
        else:
            print(f"❌ NO encontrado '{target_text}' en la respuesta")
        
        if cost_text in answer:
            print(f"✅ Encontrado '{cost_text}' en la respuesta")
        else:
            print(f"❌ NO encontrado '{cost_text}' en la respuesta")
        
        print()
        
        # Analizar las citas
        print("📋 ANÁLISIS DE CITAS:")
        print("-" * 40)
        for i, citation in enumerate(citations):
            text_snippet = citation.get("text_snippet", "")
            print(f"Cita {i+1}:")
            print(f"  Longitud: {len(text_snippet)} caracteres")
            print(f"  Doc ID: {citation.get('doc_id', 'N/A')}")
            print(f"  Página: {citation.get('page', 'N/A')}")
            print(f"  Score: {citation.get('score', 'N/A')}")
            
            # Verificar si contiene el texto específico
            if target_text in text_snippet:
                print(f"  ✅ Contiene '{target_text}'")
            else:
                print(f"  ❌ NO contiene '{target_text}'")
            
            if cost_text in text_snippet:
                print(f"  ✅ Contiene '{cost_text}'")
            else:
                print(f"  ❌ NO contiene '{cost_text}'")
            
            print(f"  Preview: {text_snippet[:200]}...")
            print()
        
        # Verificar debug info
        if debug:
            print("🐛 INFORMACIÓN DE DEBUG:")
            print("-" * 40)
            search_r1 = debug.get("search_r1_full", {})
            if search_r1:
                print(f"Queries ejecutadas: {len(search_r1.get('queries_executed', []))}")
                print(f"Pasos ejecutados: {search_r1.get('executed_steps', 'N/A')}")
                print(f"Señal de parada: {search_r1.get('stop_signal', 'N/A')}")
            
            candidates = debug.get("candidates", [])
            print(f"Candidatos encontrados: {len(candidates)}")
            
            if candidates:
                print("Top 3 candidatos:")
                for i, candidate in enumerate(candidates[:3]):
                    print(f"  {i+1}. Doc: {candidate.get('doc_id', 'N/A')}, "
                          f"Página: {candidate.get('page', 'N/A')}, "
                          f"Score: {candidate.get('score', 'N/A')}")
        
        print()
        print("🎯 RESULTADO FINAL:")
        print("-" * 40)
        
        # Determinar si la prueba fue exitosa
        success = (
            target_text in answer and 
            cost_text in answer and
            len(citations) > 0
        )
        
        if success:
            print("✅ PRUEBA EXITOSA: El contexto se está pasando correctamente")
            print("   - El texto específico se encuentra en la respuesta")
            print("   - El costo se encuentra en la respuesta")
            print("   - Se generaron citas relevantes")
        else:
            print("❌ PRUEBA FALLIDA: El contexto no se está pasando correctamente")
            print("   - Revisar la configuración de chunking")
            print("   - Verificar que los embeddings estén actualizados")
            print("   - Comprobar que el documento esté completamente procesado")
        
        return success
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Error de conexión: {e}")
        return False
    except Exception as e:
        print(f"❌ Error inesperado: {e}")
        return False

if __name__ == "__main__":
    print("🚀 Iniciando prueba de longitud de contexto")
    print("=" * 60)
    
    success = test_context_length()
    
    print()
    print("=" * 60)
    if success:
        print("🎉 Prueba completada exitosamente")
    else:
        print("💥 Prueba falló - revisar configuración")
    
    sys.exit(0 if success else 1)
