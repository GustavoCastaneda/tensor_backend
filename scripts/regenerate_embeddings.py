#!/usr/bin/env python3
"""
Script para re-generar embeddings con payload completo
"""

import os
import sys
from pathlib import Path

# Agregar el directorio backend al path
sys.path.append(str(Path(__file__).parent.parent))

from backend.tasks.doc_embeddings import generate_doc_embeddings

def regenerate_embeddings():
    """Re-genera embeddings para un documento específico"""
    document_id = "87c233a0-8579-4d95-9eaa-5d475b09d5ac"
    
    print(f"🔄 Re-generando embeddings para documento: {document_id}")
    print("=" * 60)
    
    try:
        generate_doc_embeddings(document_id)
        print("✅ Embeddings re-generados exitosamente")
    except Exception as e:
        print(f"❌ Error re-generando embeddings: {e}")

if __name__ == "__main__":
    regenerate_embeddings()
