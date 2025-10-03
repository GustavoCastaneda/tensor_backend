# backend/sql_exec.py
"""
Ejecución real de SQL sobre datasets Parquet usando DuckDB.
Reutiliza el patrón de descarga de Supabase Storage del proyecto.
"""
import os
import io
import tempfile
from typing import Dict, Any, List
import duckdb
from backend.supabase_client import get_supabase
from backend.sql_validation import validate_sql


def _download_parquet_from_storage(parquet_url: str) -> bytes:
    """
    Descarga archivo Parquet desde Supabase Storage.
    Reutiliza el patrón de process_dataset().
    
    Args:
        parquet_url: URL del archivo en formato "BUCKET/key"
        
    Returns:
        Bytes del archivo Parquet
    """
    supa = get_supabase()
    
    # Extraer bucket y clave de la URL
    if "/" in parquet_url:
        bucket, storage_key = parquet_url.split("/", 1)
    else:
        # Si no hay "/", usar bucket por defecto
        bucket = os.getenv("STORAGE_BUCKET", "uploads")
        storage_key = parquet_url
    
    try:
        raw_bytes = supa.storage.from_(bucket).download(storage_key)
        return raw_bytes
    except Exception as e:
        raise RuntimeError(f"Error descargando Parquet desde storage: {e}")


def _execute_sql_with_duckdb(parquet_data: bytes, sql: str, limit: int) -> Dict[str, Any]:
    """
    Ejecuta SQL sobre datos Parquet usando DuckDB.
    
    Args:
        parquet_data: Bytes del archivo Parquet
        sql: Consulta SQL a ejecutar
        limit: Límite de resultados
        
    Returns:
        Diccionario con columns y rows
    """
    tmp_path = None
    try:
        # Crear conexión DuckDB en memoria
        conn = duckdb.connect()
        
        # Cargar datos Parquet en memoria
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp_file:
            tmp_file.write(parquet_data)
            tmp_file.flush()
            tmp_path = tmp_file.name
            
            # Cargar Parquet en DuckDB como tabla 'data'
            conn.execute(f"CREATE TABLE data AS SELECT * FROM read_parquet('{tmp_path}')")
        
        # Aplicar límite si no está en la consulta
        if "LIMIT" not in sql.upper():
            sql_with_limit = f"{sql.rstrip(';')} LIMIT {limit}"
        else:
            sql_with_limit = sql
        
        # Ejecutar consulta
        result = conn.execute(sql_with_limit).fetchall()
        
        # Obtener nombres de columnas
        columns_info = conn.execute(f"DESCRIBE data").fetchall()
        column_names = [col[0] for col in columns_info]
        
        # Formatear resultados
        rows = []
        for row in result:
            row_dict = {}
            for i, value in enumerate(row):
                column_name = column_names[i] if i < len(column_names) else f"col_{i}"
                # Convertir tipos para JSON serialization
                if value is None:
                    row_dict[column_name] = None
                elif isinstance(value, (int, float, str, bool)):
                    row_dict[column_name] = value
                else:
                    # Convertir otros tipos a string
                    row_dict[column_name] = str(value)
            rows.append(row_dict)
        
        conn.close()
        
        # Limpiar archivo temporal
        try:
            os.unlink(tmp_path)
        except:
            pass
        
        return {
            "columns": column_names,
            "rows": rows,
            "row_count": len(rows)
        }
        
    except Exception as e:
        # Limpiar archivo temporal en caso de error
        try:
            os.unlink(tmp_path)
        except:
            pass
        raise RuntimeError(f"Error ejecutando SQL con DuckDB: {e}")


def _validate_sql_basic(sql: str) -> bool:
    """
    Validación básica de SQL.
    
    Args:
        sql: Consulta SQL a validar
        
    Returns:
        True si es válida, False si no
    """
    sql_upper = sql.upper().strip()
    
    # Verificar que empiece con SELECT
    if not sql_upper.startswith("SELECT"):
        return False
    
    # Verificar que no contenga comandos peligrosos
    dangerous_keywords = ["DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE", "TRUNCATE"]
    for keyword in dangerous_keywords:
        if keyword in sql_upper:
            return False
    
    return True


def run_sql_on_dataset(parquet_url: str, sql: str, limit: int = 100, available_columns: List[str] = None) -> dict:
    """
    Ejecuta SQL real sobre un dataset Parquet.
    
    Args:
        parquet_url: URL del archivo Parquet en Supabase Storage
        sql: Consulta SQL a ejecutar
        limit: Límite máximo de resultados
        available_columns: Lista de columnas disponibles para validación
        
    Returns:
        Diccionario con 'columns', 'rows' y metadata de validación
        
    Raises:
        RuntimeError: Si hay error en descarga o ejecución
        ValueError: Si el SQL no es válido
    """
    try:
        # Validación básica de SQL
        if not _validate_sql_basic(sql):
            raise ValueError("SQL no válido: debe empezar con SELECT y no contener comandos peligrosos")
        
        # Validación avanzada si tenemos columnas disponibles
        validation_result = None
        if available_columns:
            validation_result = validate_sql(sql, available_columns)
            if not validation_result.is_valid:
                error_msg = f"SQL no válido: {'; '.join(validation_result.errors)}"
                raise ValueError(error_msg)
        
        # Descargar Parquet desde Supabase Storage
        parquet_data = _download_parquet_from_storage(parquet_url)
        
        # Ejecutar SQL con DuckDB
        result = _execute_sql_with_duckdb(parquet_data, sql, limit)
        
        # Agregar metadata de validación si está disponible
        if validation_result:
            result["validation"] = {
                "warnings": validation_result.warnings,
                "suggestions": validation_result.suggestions or [],
                "optimized_sql": validation_result.optimized_sql
            }
        
        return result
        
    except Exception as e:
        print(f"Error en run_sql_on_dataset: {e}")
        # Fallback a datos de ejemplo en caso de error
        return {
            "columns": ["error", "message"],
            "rows": [
                {"error": "SQL_ERROR", "message": str(e)}
            ]
        }

