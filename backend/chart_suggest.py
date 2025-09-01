# backend/chart_suggest.py
"""
Sugerencia básica de gráficos para datos tabulares.
Esta es una implementación básica para mantener la funcionalidad funcionando.
"""

def suggest_chart(data: dict) -> dict:
    """
    Sugiere un tipo de gráfico basado en los datos disponibles.
    """
    columns = data.get("columns", [])
    rows = data.get("rows", [])

    if not columns or not rows:
        return None

    # Lógica básica: si hay columnas numéricas, sugerir un gráfico de barras
    numeric_columns = []
    for col in columns:
        if any(isinstance(row.get(col), (int, float)) for row in rows[:5]):
            numeric_columns.append(col)

    if len(numeric_columns) >= 1:
        return {
            "type": "bar",
            "x": columns[0],
            "y": numeric_columns[:2]  # Máximo 2 columnas numéricas
        }

    return None

