from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from backend.security import get_current_user        # helper de autenticación
from backend.chat_router import route_message, Intent

router = APIRouter(prefix="/chat", tags=["chat"])

class ChatRequest(BaseModel):
    dataset_id: str
    message:    str

class ChatResponse(BaseModel):
    intent: Intent
    # los demás campos (table, chart, text) se llenarán en pasos 3-B y 3-C
    answer: str | None = None

@router.post("", response_model=ChatResponse)
def chat(req: ChatRequest, user = Depends(get_current_user)):
    intent = route_message(req.message)

    # 🚧 por ahora sólo devolvemos la etiqueta; ejecución vendrá después
    return ChatResponse(intent=intent, answer=None)
