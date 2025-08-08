# Importaciones necesarias para el clasificador de intenciones
import os, re, json
from enum import Enum
from openai import OpenAI

# Inicializa el cliente de OpenAI con la API key desde variables de entorno
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Enum que define los tipos de intenciones que puede tener un mensaje
class Intent(str, Enum):
    sql      = "sql"      # Consultas numéricas, agregaciones, filtros, etc.
    semantic = "semantic" # Preguntas sobre significado de columnas o descripción textual
    mixed    = "mixed"    # Consultas que requieren aclarar columnas antes de calcular

# Prompt del sistema que instruye al LLM cómo clasificar los mensajes
SYS_PROMPT = """
Eres un clasificador. 
Devuelve sólo una palabra en minúsculas: "sql", "semantic" o "mixed".
- "sql": pregunta numérica, agregaciones, filtrado, orden, conteo, promedio, etc.
- "semantic": pregunta sobre significado de columnas o descripción textual.
- "mixed": primero hay que aclarar qué columna(s) se refiere el usuario y luego
           calcular algo (ej.: "Total por año de importe total").
No agregues texto extra.
"""

def route_message(message: str) -> Intent:
    """
    Clasifica un mensaje del usuario según su intención.
    
    Args:
        message (str): El mensaje del usuario a clasificar
        
    Returns:
        Intent: El tipo de intención detectada (sql, semantic, o mixed)
        
    Raises:
        ValueError: Si el modelo devuelve una respuesta inesperada
    """
    # ♥ Regla rápida: si detecta palabras clave SQL, clasifica directamente sin gastar tokens
    # Busca patrones como SELECT, SUM(, AVG(, etc. de forma case-insensitive
    if re.search(r"\bselect\b|\bsum\(|\bavg\(", message, re.I):
        return Intent.sql

    # Si no aplica la regla rápida, usa el LLM para clasificar
    resp = client.chat.completions.create(
        model=os.environ.get("ROUTER_MODEL", "gpt-4o-mini"),  # Modelo configurable, default gpt-4o-mini
        messages=[
            {"role": "system", "content": SYS_PROMPT},  # Instrucciones del sistema
            {"role": "user",   "content": message},     # Mensaje del usuario
        ],
        max_tokens=1,      # Solo necesitamos una palabra de respuesta
        temperature=0,      # Respuestas determinísticas
    )
    
    # Extrae la respuesta del modelo y la normaliza
    label = resp.choices[0].message.content.strip().lower()
    
    # Convierte la respuesta a un Intent (lanza ValueError si no es válida)
    return Intent(label)
