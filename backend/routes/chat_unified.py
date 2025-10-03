# backend/routes/chat_unified.py
"""
Router unificado para chat que detecta intención y redirige a SQL o RAG.
Mantiene ambos sistemas completamente separados para testing independiente.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from backend.intent_detector import detect_intent, extract_sql_query
from backend.routes.chat_sql import chat_sql, ChatSQLRequest
from backend.routes.chat_semantic import chat_semantic, ChatSemanticRequest
from backend.security import get_current_user
from backend.db import get_session
from sqlmodel import Session


router = APIRouter(prefix="/chat", tags=["chat-unified"])


class ChatUnifiedRequest(BaseModel):
    """Request unificado para chat."""
    message: str
    workspace_id: str


@router.post("/unified")
def chat_unified(
    req: ChatUnifiedRequest,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """
    Endpoint unificado que detecta intención y redirige a SQL o RAG.
    
    Flujo:
    1. Detecta intención basado en el mensaje
    2. Redirige a /chat/sql o /chat/semantic
    3. Mantiene ambos sistemas completamente separados
    """
    print(f"[DEBUG] Chat unified request: {req.message[:50]}...")
    print(f"[DEBUG] Workspace: {req.workspace_id}")
    print(f"[DEBUG] User: {user.get('sub', 'unknown')}")
    
    if not req.message or not req.message.strip():
        print("[DEBUG] Error: mensaje vacío")
        raise HTTPException(400, "El mensaje no puede estar vacío")
    
    if not req.workspace_id:
        print("[DEBUG] Error: workspace_id requerido")
        raise HTTPException(400, "workspace_id es requerido")
    
    # 1. Detectar intención
    intent = detect_intent(req.message)
    print(f"[DEBUG] Intent detectado: {intent}")
    
    # 2. Redirigir según intención
    if intent == "sql":
        print("[DEBUG] Procesando como SQL")
        # Preparar request para SQL
        sql_message = extract_sql_query(req.message)
        if not sql_message:
            print("[DEBUG] Error: mensaje SQL vacío")
            raise HTTPException(400, "Mensaje SQL vacío después de remover 'EXCL'")
        
        sql_req = ChatSQLRequest(
            message=sql_message,
            workspace_id=req.workspace_id
        )
        
        print("[DEBUG] Ejecutando chat_sql...")
        response = chat_sql(sql_req, user, session)
        print(f"[DEBUG] Respuesta SQL: success={response.success}")
        return response.dict()
    
    else:  # intent == "rag"
        print("[DEBUG] Procesando como RAG")
        # Preparar request para RAG
        rag_req = ChatSemanticRequest(
            message=req.message,
            workspace_id=req.workspace_id
        )
        
        print("[DEBUG] Ejecutando chat_semantic...")
        response = chat_semantic(rag_req, user, session)
        print(f"[DEBUG] Respuesta RAG completada")
        return response


@router.post("/unified-test")
def chat_unified_test(
    req: ChatUnifiedRequest,
    session: Session = Depends(get_session),
):
    """
    Endpoint de prueba sin autenticación para testing.
    """
    # Simular usuario para testing (usar el mismo que el dataset)
    user = {"sub": "user_32tCyMkjy0aR72wiEBjaVm5Aq0l"}
    
    if not req.message or not req.message.strip():
        raise HTTPException(400, "El mensaje no puede estar vacío")
    
    if not req.workspace_id:
        raise HTTPException(400, "workspace_id es requerido")
    
    # 1. Detectar intención
    intent = detect_intent(req.message)
    
    # 2. Redirigir según intención
    if intent == "sql":
        # Preparar request para SQL
        sql_message = extract_sql_query(req.message)
        if not sql_message:
            raise HTTPException(400, "Mensaje SQL vacío después de remover 'EXCL'")
        
        sql_req = ChatSQLRequest(
            message=sql_message,
            workspace_id=req.workspace_id
        )
        
        response = chat_sql(sql_req, user, session)
        return response.dict()
    
    else:  # intent == "rag"
        # Preparar request para RAG
        rag_req = ChatSemanticRequest(
            message=req.message,
            workspace_id=req.workspace_id
        )
        
        return chat_semantic(rag_req, user, session)
