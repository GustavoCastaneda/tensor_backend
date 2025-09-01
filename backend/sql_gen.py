# backend/sql_gen.py
"""
Generación básica de SQL para consultas simples.
Esta es una implementación básica para mantener la funcionalidad funcionando.
"""

def generate_sql(question: str, columns_hint: list, relevant_cols: list = None) -> tuple[str, str]:
    """
    Genera SQL básico basado en la pregunta y columnas disponibles.
    """
    # Implementación básica - solo genera SELECT * por ahora
    sql = "SELECT * FROM data LIMIT 100"
    explanation = "Consulta básica para explorar los datos"

    return sql, explanation

