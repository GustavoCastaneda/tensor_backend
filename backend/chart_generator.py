# backend/chart_generator.py
"""
Sistema de generación de gráficos con configuración Recharts.
Genera configuraciones automáticas basadas en datos SQL y consulta del usuario.
"""

import os
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from backend.sql_gen import _get_openai_client


class ChartConfig(BaseModel):
    """Configuración de gráfico para Recharts."""
    type: str = Field(..., description="Tipo de gráfico: bar, line, pie, area, scatter")
    title: str = Field(..., description="Título del gráfico")
    description: str = Field(..., description="Descripción detallada del gráfico")
    xKey: str = Field(..., description="Clave para el eje X o categoría")
    yKeys: List[str] = Field(..., description="Claves para el eje Y (valores cuantitativos)")
    colors: List[str] = Field(default_factory=lambda: [
        "#8884d8", "#82ca9d", "#ffc658", "#ff7300", "#00ff00", "#ff00ff"
    ], description="Colores para las series de datos")
    legend: bool = Field(default=True, description="Mostrar leyenda")
    responsive: bool = Field(default=True, description="Gráfico responsivo")
    
    # Configuraciones específicas por tipo
    multipleLines: Optional[bool] = Field(None, description="Para gráficos de línea: múltiples líneas")
    lineCategories: Optional[List[str]] = Field(None, description="Categorías para líneas múltiples")
    measurementColumn: Optional[str] = Field(None, description="Columna de medición para líneas")
    
    # Insights y análisis
    insights: List[str] = Field(default_factory=list, description="Insights clave de los datos")
    takeaway: str = Field(..., description="Conclusión principal del gráfico")


def _analyze_data_structure(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Analiza la estructura de los datos para determinar el mejor tipo de gráfico.
    
    Args:
        data: Datos de la consulta SQL
        
    Returns:
        Análisis de la estructura de datos
    """
    if not data or not data.get('rows'):
        return {"type": "empty", "columns": [], "row_count": 0}
    
    rows = data['rows']
    columns = data.get('columns', [])
    row_count = len(rows)
    
    # Analizar tipos de columnas
    column_types = {}
    for col in columns:
        if rows:
            sample_value = rows[0].get(col)
            if isinstance(sample_value, (int, float)):
                column_types[col] = "numeric"
            elif isinstance(sample_value, str):
                column_types[col] = "text"
            else:
                column_types[col] = "other"
    
    # Identificar columnas numéricas y categóricas
    numeric_cols = [col for col, type_ in column_types.items() if type_ == "numeric"]
    text_cols = [col for col, type_ in column_types.items() if type_ == "text"]
    
    return {
        "type": "structured",
        "columns": columns,
        "row_count": row_count,
        "numeric_columns": numeric_cols,
        "text_columns": text_cols,
        "column_types": column_types
    }


def _determine_chart_type(data_analysis: Dict[str, Any], user_query: str) -> str:
    """
    Determina el mejor tipo de gráfico basado en los datos y la consulta.
    
    Args:
        data_analysis: Análisis de la estructura de datos
        user_query: Consulta original del usuario
        
    Returns:
        Tipo de gráfico recomendado
    """
    if data_analysis["type"] == "empty":
        return "bar"
    
    numeric_cols = data_analysis["numeric_columns"]
    text_cols = data_analysis["text_columns"]
    row_count = data_analysis["row_count"]
    
    # Palabras clave en la consulta
    query_lower = user_query.lower()
    
    # Reglas de decisión
    if "tendencia" in query_lower or "tiempo" in query_lower or "año" in query_lower or "mes" in query_lower:
        return "line"
    elif "distribución" in query_lower or "porcentaje" in query_lower or "proporción" in query_lower:
        return "pie"
    elif "comparar" in query_lower or "vs" in query_lower or "versus" in query_lower:
        return "bar"
    elif len(numeric_cols) == 1 and len(text_cols) == 1:
        return "bar"
    elif len(numeric_cols) >= 2:
        return "line"
    elif row_count <= 10:
        return "pie"
    else:
        return "bar"


def _generate_chart_prompt(data_analysis: Dict[str, Any], user_query: str) -> str:
    """
    Genera el prompt para el LLM que creará la configuración del gráfico.
    
    Args:
        data_analysis: Análisis de la estructura de datos
        user_query: Consulta original del usuario
        
    Returns:
        Prompt para el LLM
    """
    return f"""
Eres un experto en visualización de datos y Recharts. Tu trabajo es generar una configuración de gráfico que visualice mejor los datos y responda a la consulta del usuario.

CONTEXTO:
- Consulta del usuario: "{user_query}"
- Datos disponibles: {data_analysis['row_count']} filas
- Columnas: {data_analysis['columns']}
- Columnas numéricas: {data_analysis['numeric_columns']}
- Columnas de texto: {data_analysis['text_columns']}

INSTRUCCIONES:
1. Genera una configuración de gráfico Recharts que visualice mejor estos datos
2. Elige el tipo de gráfico más apropiado (bar, line, pie, area, scatter)
3. Mapea las columnas correctamente (xKey para categorías, yKeys para valores)
4. Proporciona un título descriptivo y claro
5. Incluye insights clave sobre los datos
6. Asegúrate de que la configuración sea válida para Recharts

TIPOS DE GRÁFICOS DISPONIBLES:
- bar: Para comparar categorías
- line: Para mostrar tendencias en el tiempo
- pie: Para mostrar proporciones (máximo 10 categorías)
- area: Para mostrar tendencias con área sombreada
- scatter: Para mostrar correlaciones entre dos variables

FORMATO DE RESPUESTA:
Responde SOLO con un JSON válido que contenga la configuración del gráfico.
El JSON debe incluir TODOS estos campos requeridos:
- type: tipo de gráfico
- title: título del gráfico
- description: descripción detallada
- xKey: clave para eje X
- yKeys: array de claves para eje Y
- colors: array de colores
- legend: boolean
- responsive: boolean
- insights: array de insights
- takeaway: conclusión principal

No incluyas texto adicional, solo el JSON.
"""


def generate_chart_config(data: Dict[str, Any], user_query: str) -> ChartConfig:
    """
    Genera configuración de gráfico Recharts basada en datos SQL y consulta del usuario.
    
    Args:
        data: Datos de la consulta SQL
        user_query: Consulta original del usuario
        
    Returns:
        Configuración de gráfico para Recharts
    """
    try:
        # Analizar estructura de datos
        data_analysis = _analyze_data_structure(data)
        
        if data_analysis["type"] == "empty":
            return ChartConfig(
                type="bar",
                title="Sin datos",
                description="No hay datos para visualizar",
                xKey="category",
                yKeys=["value"],
                takeaway="No se encontraron datos para la consulta"
            )
        
        # Generar prompt para LLM
        prompt = _generate_chart_prompt(data_analysis, user_query)
        
        # Obtener cliente OpenAI
        client = _get_openai_client()
        
        # Generar configuración con LLM
        response = client.chat.completions.create(
            model=os.getenv("LLM_MODEL", "gpt-4o"),
            messages=[
                {"role": "system", "content": "Eres un experto en visualización de datos y Recharts. Responde SOLO con JSON válido."},
                {"role": "user", "content": prompt}
            ],
            max_completion_tokens=1000,
            temperature=0.3
        )
        
        # Extraer JSON de la respuesta
        response_text = response.choices[0].message.content.strip()
        
        # Limpiar respuesta (remover markdown si existe)
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        
        # Parsear JSON
        import json
        config_dict = json.loads(response_text)
        
        # Asegurar campos requeridos
        if 'description' not in config_dict:
            config_dict['description'] = f"Visualización de {data_analysis['row_count']} registros"
        if 'takeaway' not in config_dict:
            config_dict['takeaway'] = "Datos analizados y visualizados correctamente"
        if 'insights' not in config_dict:
            config_dict['insights'] = ["Datos procesados exitosamente"]
        
        # Crear objeto ChartConfig
        chart_config = ChartConfig(**config_dict)
        
        return chart_config
        
    except Exception as e:
        print(f"Error generando configuración de gráfico: {e}")
        
        # Fallback a configuración básica
        data_analysis = _analyze_data_structure(data)
        chart_type = _determine_chart_type(data_analysis, user_query)
        
        return ChartConfig(
            type=chart_type,
            title="Gráfico de datos",
            description="Visualización de los resultados de la consulta",
            xKey=data_analysis["text_columns"][0] if data_analysis["text_columns"] else "category",
            yKeys=data_analysis["numeric_columns"][:2] if data_analysis["numeric_columns"] else ["value"],
            takeaway="Datos visualizados correctamente"
        )
