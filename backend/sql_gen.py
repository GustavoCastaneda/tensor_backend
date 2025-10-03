# backend/sql_gen.py
"""
Generación inteligente de SQL para consultas sobre datasets.
Utiliza LLM para generar SQL real basado en la pregunta y columnas disponibles.
"""
import os
from typing import List, Dict, Any, Optional
from openai import OpenAI


def _get_openai_client() -> OpenAI:
    """Obtiene cliente OpenAI reutilizando el patrón del proyecto."""
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def _build_columns_context(columns_hint: List[Dict[str, Any]], relevant_cols: Optional[List[str]] = None) -> str:
    """
    Construye contexto de columnas para el prompt del LLM.
    
    Args:
        columns_hint: Lista de columnas con metadata
        relevant_cols: Columnas específicas identificadas como relevantes
        
    Returns:
        String con contexto de columnas formateado
    """
    if not columns_hint:
        return "No hay información de columnas disponible."
    
    context_parts = []
    
    # Agregar columnas relevantes primero si están disponibles
    if relevant_cols:
        context_parts.append("COLUMNAS RELEVANTES IDENTIFICADAS:")
        for col_name in relevant_cols:
            col_info = next((c for c in columns_hint if c.get("original_name") == col_name), None)
            if col_info:
                context_parts.append(f"- {col_name} ({col_info.get('detected_type', 'unknown')})")
                if col_info.get('sample_values'):
                    samples = col_info['sample_values'][:3]  # Primeros 3 valores
                    context_parts.append(f"  Ejemplos: {samples}")
        context_parts.append("")
    
    # Agregar todas las columnas disponibles
    context_parts.append("TODAS LAS COLUMNAS DISPONIBLES:")
    for col in columns_hint:
        col_name = col.get("original_name", "unknown")
        col_type = col.get("detected_type", "unknown")
        context_parts.append(f"- {col_name} ({col_type})")
        
        if col.get('sample_values'):
            samples = col['sample_values'][:3]
            context_parts.append(f"  Ejemplos: {samples}")
    
    return "\n".join(context_parts)


def _build_sql_prompt(question: str, columns_context: str) -> str:
    """
    Construye el prompt para generar SQL.
    
    Args:
        question: Pregunta del usuario
        columns_context: Contexto de columnas formateado
        
    Returns:
        Prompt completo para el LLM
    """
    return f"""Eres un experto en SQL. Genera una consulta SQL para responder la pregunta del usuario.

REGLAS IMPORTANTES:
1. La tabla se llama 'data' (no uses FROM dataset, FROM table, etc.)
2. Usa solo las columnas disponibles en el contexto
3. Para agregaciones, usa funciones estándar: SUM(), AVG(), COUNT(), MIN(), MAX()
4. Para filtros, usa WHERE con condiciones apropiadas
5. Para agrupación, usa GROUP BY
6. Para ordenamiento, usa ORDER BY
7. Limita resultados con LIMIT cuando sea apropiado
8. Usa nombres de columnas exactos (respetando mayúsculas/minúsculas)
9. Si la pregunta es exploratoria, usa SELECT * LIMIT 20
10. Si pide conteos o agregaciones, incluye explicación clara

CONTEXTO DE COLUMNAS:
{columns_context}

PREGUNTA DEL USUARIO: {question}

Genera SOLO la consulta SQL (sin explicación adicional):
"""


def _extract_sql_from_response(response_text: str) -> str:
    """
    Extrae SQL del texto de respuesta del LLM.
    
    Args:
        response_text: Texto completo de la respuesta
        
    Returns:
        SQL limpio sin markdown ni texto adicional
    """
    # Limpiar el texto
    sql = response_text.strip()
    
    # Remover markdown si está presente
    if sql.startswith("```sql"):
        sql = sql[6:]
    elif sql.startswith("```"):
        sql = sql[3:]
    
    if sql.endswith("```"):
        sql = sql[:-3]
    
    # Limpiar espacios y saltos de línea
    sql = sql.strip()
    
    # Asegurar que termine con punto y coma
    if not sql.endswith(';'):
        sql += ';'
    
    return sql


def _generate_explanation(question: str, sql: str) -> str:
    """
    Genera explicación de la consulta SQL generada.
    
    Args:
        question: Pregunta original del usuario
        sql: SQL generado
        
    Returns:
        Explicación clara de la consulta
    """
    # Explicaciones básicas basadas en patrones SQL
    if "SELECT *" in sql.upper():
        return f"Consulta exploratoria para ver los datos disponibles y responder: {question}"
    elif "SUM(" in sql.upper() or "AVG(" in sql.upper() or "COUNT(" in sql.upper():
        return f"Consulta de agregación para calcular totales/promedios/conteos: {question}"
    elif "GROUP BY" in sql.upper():
        return f"Consulta agrupada para analizar datos por categorías: {question}"
    elif "WHERE" in sql.upper():
        return f"Consulta con filtros para encontrar datos específicos: {question}"
    elif "ORDER BY" in sql.upper():
        return f"Consulta ordenada para mostrar datos en secuencia: {question}"
    else:
        return f"Consulta SQL para responder: {question}"


def generate_sql(question: str, columns_hint: list, relevant_cols: list = None) -> tuple[str, str]:
    """
    Genera SQL real basado en la pregunta y columnas disponibles.
    
    Args:
        question: Pregunta del usuario
        columns_hint: Lista de columnas con metadata (original_name, detected_type, sample_values)
        relevant_cols: Columnas específicas identificadas como relevantes
        
    Returns:
        Tupla con (sql, explanation)
    """
    try:
        # Construir contexto de columnas
        columns_context = _build_columns_context(columns_hint, relevant_cols)
        
        # Construir prompt
        prompt = _build_sql_prompt(question, columns_context)
        
        # Obtener cliente OpenAI
        client = _get_openai_client()
        
        # Intentar con Responses API (patrón del proyecto)
        try:
            resp = client.responses.create(
                model=os.getenv("LLM_MODEL", "gpt-4o"),
                input=prompt,
                max_output_tokens=500,
                text={"format": {"type": "text"}}
            )
            
            # Extraer contenido de la respuesta
            response_text = getattr(resp, "output_text", None)
            if not response_text:
                response_text = getattr(resp, "text", None)
            if not response_text and hasattr(resp, "output") and resp.output:
                first = resp.output[0]
                if getattr(first, "content", None):
                    part = first.content[0]
                    response_text = getattr(part, "text", None)
            
            if not response_text:
                raise RuntimeError("No se pudo extraer texto de la respuesta")
                
        except Exception as e:
            print(f"Error con Responses API: {e}, usando fallback")
            # Fallback a chat.completions (patrón del proyecto)
            resp = client.chat.completions.create(
                model=os.getenv("LLM_MODEL", "gpt-4o"),
                messages=[
                    {"role": "system", "content": "Eres un experto en SQL. Responde solo con la consulta SQL solicitada."},
                    {"role": "user", "content": prompt}
                ],
                max_completion_tokens=500,
                temperature=0.1
            )
            response_text = resp.choices[0].message.content
        
        # Extraer SQL y generar explicación
        sql = _extract_sql_from_response(response_text)
        explanation = _generate_explanation(question, sql)
        
        return sql, explanation
        
    except Exception as e:
        print(f"Error generando SQL: {e}")
        # Fallback a consulta básica
        return "SELECT * FROM data LIMIT 20", f"Error generando SQL: {e}. Consulta básica para explorar datos."

