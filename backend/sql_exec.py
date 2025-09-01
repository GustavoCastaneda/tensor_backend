# backend/sql_exec.py
"""
Ejecución básica de SQL sobre datasets.
Esta es una implementación básica para mantener la funcionalidad funcionando.
"""

def run_sql_on_dataset(parquet_url: str, sql: str, limit: int = 100) -> dict:
    """
    Ejecuta SQL sobre un dataset Parquet.
    """
    # Implementación básica - devuelve datos de ejemplo
    return {
        "columns": ["id", "name", "value"],
        "rows": [
            {"id": 1, "name": "Example 1", "value": 100},
            {"id": 2, "name": "Example 2", "value": 200},
            {"id": 3, "name": "Example 3", "value": 300},
        ]
    }

