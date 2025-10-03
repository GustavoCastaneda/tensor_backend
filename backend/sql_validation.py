# backend/sql_validation.py
"""
Sistema de validación y optimización de consultas SQL.
Valida sintaxis, nombres de columnas y optimiza consultas.
"""
import re
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass


@dataclass
class ValidationResult:
    """Resultado de validación SQL."""
    is_valid: bool
    errors: List[str]
    warnings: List[str]
    optimized_sql: Optional[str] = None
    suggestions: List[str] = None


class SQLValidator:
    """Validador y optimizador de consultas SQL."""
    
    def __init__(self):
        self.dangerous_keywords = [
            "DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE", 
            "TRUNCATE", "EXEC", "EXECUTE", "CALL", "GRANT", "REVOKE"
        ]
        
        self.aggregation_functions = [
            "SUM", "AVG", "COUNT", "MIN", "MAX", "STDDEV", "VARIANCE"
        ]
    
    def validate_sql(self, sql: str, available_columns: List[str]) -> ValidationResult:
        """
        Valida una consulta SQL contra columnas disponibles.
        
        Args:
            sql: Consulta SQL a validar
            available_columns: Lista de columnas disponibles en el dataset
            
        Returns:
            ValidationResult con resultado de validación
        """
        errors = []
        warnings = []
        suggestions = []
        
        # Normalizar SQL
        sql_clean = sql.strip()
        sql_upper = sql_clean.upper()
        
        # 1. Validación básica de sintaxis
        basic_validation = self._validate_basic_syntax(sql_clean)
        if not basic_validation["is_valid"]:
            errors.extend(basic_validation["errors"])
            return ValidationResult(False, errors, warnings)
        
        # 2. Validación de seguridad
        security_validation = self._validate_security(sql_upper)
        if not security_validation["is_valid"]:
            errors.extend(security_validation["errors"])
            return ValidationResult(False, errors, warnings)
        
        # 3. Validación de columnas
        column_validation = self._validate_columns(sql_clean, available_columns)
        errors.extend(column_validation["errors"])
        warnings.extend(column_validation["warnings"])
        
        # 4. Análisis de optimización
        optimization_analysis = self._analyze_optimization(sql_clean, available_columns)
        suggestions.extend(optimization_analysis["suggestions"])
        
        # 5. Generar SQL optimizado si es posible
        optimized_sql = self._optimize_sql(sql_clean, available_columns)
        
        is_valid = len(errors) == 0
        
        return ValidationResult(
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
            optimized_sql=optimized_sql if optimized_sql != sql_clean else None,
            suggestions=suggestions
        )
    
    def _validate_basic_syntax(self, sql: str) -> Dict[str, Any]:
        """Valida sintaxis básica de SQL."""
        errors = []
        
        # Debe empezar con SELECT
        if not sql.upper().strip().startswith("SELECT"):
            errors.append("La consulta debe empezar con SELECT")
        
        # Debe tener FROM
        if "FROM" not in sql.upper():
            errors.append("La consulta debe incluir una cláusula FROM")
        
        # Verificar paréntesis balanceados
        if sql.count("(") != sql.count(")"):
            errors.append("Los paréntesis no están balanceados")
        
        # Verificar comillas balanceadas
        single_quotes = sql.count("'")
        if single_quotes % 2 != 0:
            errors.append("Las comillas simples no están balanceadas")
        
        return {
            "is_valid": len(errors) == 0,
            "errors": errors
        }
    
    def _validate_security(self, sql_upper: str) -> Dict[str, Any]:
        """Valida que el SQL no contenga comandos peligrosos."""
        errors = []
        
        for keyword in self.dangerous_keywords:
            if keyword in sql_upper:
                errors.append(f"Comando peligroso detectado: {keyword}")
        
        return {
            "is_valid": len(errors) == 0,
            "errors": errors
        }
    
    def _validate_columns(self, sql: str, available_columns: List[str]) -> Dict[str, Any]:
        """Valida que las columnas usadas existan."""
        errors = []
        warnings = []
        
        # Extraer nombres de columnas del SQL
        used_columns = self._extract_column_names(sql)
        
        # Normalizar nombres de columnas disponibles
        available_columns_lower = [col.lower() for col in available_columns]
        available_columns_original = {col.lower(): col for col in available_columns}
        
        for col in used_columns:
            col_lower = col.lower()
            
            # Verificar si la columna existe (case-insensitive)
            if col_lower not in available_columns_lower:
                errors.append(f"Columna '{col}' no encontrada en el dataset")
            else:
                # Verificar si el case es correcto
                original_col = available_columns_original[col_lower]
                if col != original_col:
                    warnings.append(f"Considera usar '{original_col}' en lugar de '{col}'")
        
        return {
            "errors": errors,
            "warnings": warnings
        }
    
    def _extract_column_names(self, sql: str) -> List[str]:
        """Extrae nombres de columnas del SQL."""
        columns = []
        
        # Patrón para encontrar nombres de columnas en SELECT
        select_pattern = r"SELECT\s+(.*?)\s+FROM"
        select_match = re.search(select_pattern, sql, re.IGNORECASE | re.DOTALL)
        
        if select_match:
            select_clause = select_match.group(1)
            
            # Dividir por comas y limpiar
            parts = [part.strip() for part in select_clause.split(",")]
            
            for part in parts:
                # Remover alias (AS alias) - NO agregar alias a la lista de columnas
                if " AS " in part.upper():
                    part = part.split(" AS ")[0].strip()
                
                # Remover funciones de agregación
                for func in self.aggregation_functions:
                    if part.upper().startswith(f"{func}("):
                        # Extraer contenido dentro de paréntesis
                        inner = part[part.find("(") + 1:part.rfind(")")]
                        if inner.strip() != "*":
                            columns.append(inner.strip())
                        continue
                
                # Si no es una función, es una columna directa
                if not any(part.upper().startswith(f"{func}(") for func in self.aggregation_functions):
                    if part.strip() != "*":
                        columns.append(part.strip())
        
        # Extraer columnas de WHERE
        where_pattern = r"WHERE\s+(.*?)(?:\s+GROUP\s+BY|\s+ORDER\s+BY|\s+HAVING|\s+LIMIT|$)"
        where_match = re.search(where_pattern, sql, re.IGNORECASE | re.DOTALL)
        
        if where_match:
            where_clause = where_match.group(1)
            # Buscar nombres de columnas en condiciones WHERE
            # Patrón mejorado: palabra seguida de operador (incluye LIKE, IN, etc.)
            where_columns = re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:[=<>!]|LIKE|IN|BETWEEN)', where_clause, re.IGNORECASE)
            columns.extend(where_columns)
        
        # Extraer columnas de GROUP BY
        group_by_pattern = r"GROUP\s+BY\s+(.*?)(?:\s+ORDER\s+BY|\s+HAVING|\s+LIMIT|$)"
        group_by_match = re.search(group_by_pattern, sql, re.IGNORECASE | re.DOTALL)
        
        if group_by_match:
            group_by_clause = group_by_match.group(1)
            group_columns = [col.strip() for col in group_by_clause.split(",")]
            columns.extend(group_columns)
        
        # Extraer columnas de ORDER BY - MEJORADO
        order_by_pattern = r"ORDER\s+BY\s+(.*?)(?:\s+HAVING|\s+LIMIT|$)"
        order_by_match = re.search(order_by_pattern, sql, re.IGNORECASE | re.DOTALL)
        
        if order_by_match:
            order_by_clause = order_by_match.group(1)
            order_columns = [col.strip() for col in order_by_clause.split(",")]
            # Limpiar columnas de ORDER BY (remover ASC/DESC)
            for col in order_columns:
                # Primero remover punto y coma, luego ASC/DESC
                clean_col = col.rstrip(';').strip()
                clean_col = re.sub(r'\s+(ASC|DESC)\s*$', '', clean_col, flags=re.IGNORECASE).strip()
                # Remover backticks si existen
                clean_col = clean_col.strip('`').strip()
                
                # Solo agregar si es una columna real (no un alias de SELECT)
                # Los alias de SELECT no deben validarse contra las columnas del dataset
                if clean_col and not self._is_select_alias(clean_col, sql):
                    columns.append(clean_col)
        
        # Limpiar y deduplicar
        clean_columns = []
        for col in columns:
            col = col.strip().strip('"').strip("'").strip('`').rstrip(';')  # Remover punto y coma y backticks
            if col and col not in clean_columns:
                clean_columns.append(col)
        
        return clean_columns
    
    def _is_select_alias(self, column_name: str, sql: str) -> bool:
        """Verifica si un nombre de columna es un alias de SELECT."""
        # Buscar en la cláusula SELECT por alias
        select_pattern = r"SELECT\s+(.*?)\s+FROM"
        select_match = re.search(select_pattern, sql, re.IGNORECASE | re.DOTALL)
        
        if select_match:
            select_clause = select_match.group(1)
            parts = [part.strip() for part in select_clause.split(",")]
            
            for part in parts:
                if " AS " in part.upper():
                    alias = part.split(" AS ")[1].strip()
                    if alias.lower() == column_name.lower():
                        return True
        
        return False
    
    def _analyze_optimization(self, sql: str, available_columns: List[str]) -> Dict[str, Any]:
        """Analiza oportunidades de optimización."""
        suggestions = []
        
        sql_upper = sql.upper()
        
        # Sugerir LIMIT si no existe
        if "LIMIT" not in sql_upper and "SELECT *" in sql_upper:
            suggestions.append("Considera agregar LIMIT para limitar resultados")
        
        # Sugerir índices para columnas en WHERE
        if "WHERE" in sql_upper:
            suggestions.append("Las consultas con WHERE se benefician de índices en las columnas filtradas")
        
        # Sugerir agregaciones para consultas exploratorias
        if "SELECT *" in sql_upper and "GROUP BY" not in sql_upper:
            suggestions.append("Para análisis, considera usar funciones de agregación (SUM, AVG, COUNT)")
        
        return {
            "suggestions": suggestions
        }
    
    def _optimize_sql(self, sql: str, available_columns: List[str]) -> Optional[str]:
        """Optimiza la consulta SQL si es posible."""
        optimized = sql
        
        # Agregar LIMIT si no existe y es SELECT *
        if "SELECT *" in sql.upper() and "LIMIT" not in sql.upper():
            optimized = f"{sql.rstrip(';')} LIMIT 100;"
        
        return optimized if optimized != sql else None


def validate_sql(sql: str, available_columns: List[str]) -> ValidationResult:
    """
    Función de conveniencia para validar SQL.
    
    Args:
        sql: Consulta SQL a validar
        available_columns: Lista de columnas disponibles
        
    Returns:
        ValidationResult con resultado de validación
    """
    validator = SQLValidator()
    return validator.validate_sql(sql, available_columns)
