# backend/query_embeddings.py
"""
Generación de embeddings para consultas de usuario
"""

import os
from typing import List
from openai import OpenAI

# Configuración
EMB_MODEL = os.getenv("EMBEDDINGS_MODEL", "text-embedding-3-small")
llm = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def get_query_embedding(query: str) -> List[float]:
    """
    Genera embedding para una consulta de usuario
    
    Args:
        query: Consulta del usuario
        
    Returns:
        Vector de embedding (1536 dimensiones)
    """
    try:
        response = llm.embeddings.create(
            model=EMB_MODEL,
            input=query
        )
        return response.data[0].embedding
    except Exception as e:
        print(f"Error generando embedding para consulta: {e}")
        # Retornar vector cero como fallback
        return [0.0] * 1536
