# backend/intent_detector.py
"""
Detector de intención simple para separar consultas SQL de RAG.
Por ahora usa reglas simples, después se puede mejorar con LLM.
"""

from enum import Enum
from typing import Literal


class Intent(str, Enum):
    """Tipos de intención detectados."""
    SQL = "sql"      # Consultas sobre datasets Excel/CSV
    RAG = "rag"      # Consultas sobre documentos PDF


def detect_intent(message: str) -> Literal["sql", "rag"]:
    """
    Detecta la intención del usuario basado en el mensaje.
    
    Reglas actuales:
    - Si empieza con "EXCL" (case insensitive) → SQL
    - Cualquier otra cosa → RAG
    
    Args:
        message: Mensaje del usuario
        
    Returns:
        "sql" o "rag"
    """
    if not message or not isinstance(message, str):
        return Intent.RAG
    
    # Limpiar y normalizar el mensaje
    clean_message = message.strip().upper()
    
    # Regla simple: "EXCL" al inicio = SQL
    if clean_message.startswith("EXCL"):
        return Intent.SQL
    
    # Todo lo demás = RAG
    return Intent.RAG


def extract_sql_query(message: str) -> str:
    """
    Extrae la consulta SQL del mensaje removiendo el prefijo "EXCL".
    
    Args:
        message: Mensaje original del usuario
        
    Returns:
        Mensaje sin el prefijo "EXCL"
    """
    if not message:
        return ""
    
    # Remover "EXCL" del inicio (case insensitive)
    clean_message = message.strip()
    if clean_message.upper().startswith("EXCL"):
        # Remover "EXCL" y cualquier espacio después
        return clean_message[4:].strip()
    
    return clean_message
