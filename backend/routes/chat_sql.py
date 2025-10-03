# backend/routes/chat_sql.py
"""
Sistema SQL completamente independiente para consultas sobre datasets.
No depende del sistema RAG, diseñado para testing separado.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from uuid import UUID
from sqlmodel import Session, select

from backend.security import get_current_user
from backend.db import get_session
from backend.models import Dataset, Column
from backend.routes.chat import _sql_flow
from backend.chart_generator import generate_chart_config


router = APIRouter(prefix="/chat", tags=["chat-sql"])


class ChatSQLRequest(BaseModel):
    """Request para consultas SQL."""
    message: str
    workspace_id: str


class ChatSQLResponse(BaseModel):
    """Response para consultas SQL - formato independiente."""
    success: bool
    message: str
    sql_query: Optional[str] = None
    explanation: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    chart_config: Optional[Dict[str, Any]] = None  # Configuración Recharts
    insights: Optional[List[str]] = None  # Insights clave
    takeaway: Optional[str] = None  # Conclusión principal
    error: Optional[str] = None


def _get_latest_dataset(session: Session, user_id: str, workspace_id: str) -> Optional[Dataset]:
    """
    Obtiene el dataset más reciente del workspace del usuario.
    
    Args:
        session: Sesión de base de datos
        user_id: ID del usuario
        workspace_id: ID del workspace
        
    Returns:
        Dataset más reciente o None si no hay datasets
    """
    dataset = session.exec(
        select(Dataset)
        .where(Dataset.user_id == user_id)
        .where(Dataset.workspace_id == workspace_id)
        .where(Dataset.status == "ready_for_chat")
        .where(Dataset.parquet_url.isnot(None))  # Asegurar que tiene Parquet
        .order_by(Dataset.created_at.desc())
        .limit(1)
    ).first()
    
    return dataset


@router.post("/sql", response_model=ChatSQLResponse)
def chat_sql(
    req: ChatSQLRequest,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """
    Endpoint SQL independiente para consultas sobre datasets.
    
    Flujo:
    1. Obtiene el dataset más reciente del workspace
    2. Genera y ejecuta SQL usando el sistema existente
    3. Devuelve respuesta en formato independiente
    """
    try:
        # 1. Obtener dataset más reciente
        dataset = _get_latest_dataset(session, user["sub"], req.workspace_id)
        
        
        if not dataset:
            return ChatSQLResponse(
                success=False,
                message="No hay datasets disponibles en este workspace",
                error="NO_DATASETS"
            )
        
        # 2. Ejecutar consulta SQL usando el sistema existente
        sql, explanation, table = _sql_flow(
            session, 
            dataset.id, 
            req.message, 
            user
        )
        
        # 3. Preparar datos de respuesta
        data = {
            "columns": table.columns,
            "rows": table.rows,
            "row_count": len(table.rows)
        }
        
        # 4. Generar configuración de gráfico Recharts
        chart_config = None
        insights = []
        takeaway = ""
        
        if table.rows:
            try:
                chart_config_obj = generate_chart_config(data, req.message)
                chart_config = chart_config_obj.dict()
                insights = chart_config_obj.insights
                takeaway = chart_config_obj.takeaway
            except Exception as e:
                print(f"Error generando configuración de gráfico: {e}")
                # Fallback a configuración básica
                chart_config = {
                    "type": "bar",
                    "title": "Gráfico de datos",
                    "description": "Visualización de los resultados",
                    "xKey": table.columns[0] if table.columns else "category",
                    "yKeys": [col for col in table.columns[1:3] if col] if len(table.columns) > 1 else ["value"],
                    "colors": ["#8884d8", "#82ca9d", "#ffc658"],
                    "legend": True,
                    "responsive": True
                }
                insights = ["Datos visualizados correctamente"]
                takeaway = "Los datos se han procesado y visualizado exitosamente"
        
        # 5. Devolver respuesta exitosa
        return ChatSQLResponse(
            success=True,
            message=f"Consulta ejecutada sobre {dataset.filename}",
            sql_query=sql,
            explanation=explanation,
            data=data,
            chart_config=chart_config,
            insights=insights,
            takeaway=takeaway
        )
        
    except Exception as e:
        return ChatSQLResponse(
            success=False,
            message=f"Error ejecutando consulta SQL: {str(e)}",
            error=str(e)
        )
