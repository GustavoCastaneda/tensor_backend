from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select
from typing import Optional, List, Dict

from backend.security import get_current_user
from backend.db import get_session          # ← sesión sync
from backend.models import User, Document   # ← modelos SQLModel

router = APIRouter()

@router.get("/me")
def me(
    user = Depends(get_current_user),       # JWT verificado
    session: Session = Depends(get_session) # conexión Postgres
):
    # 1) ¿Ya existe en la tabla?
    db_user = session.get(User, user["sub"])

    # 2) Si no existe, lo creamos
    if db_user is None:
        db_user = User(
            id    = user["sub"],
            email = user.get("email_address") or user.get("email"),
        )
        session.add(db_user)
        session.commit()        # ← importante guardar
        session.refresh(db_user)

    # 3) Respondemos con los datos almacenados
    return {
        "id": db_user.id,
        "email": db_user.email,
    }


@router.get("/me/documents")
def my_documents(
    workspace_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="Filtrar por status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Lista documentos del usuario (opcional por workspace y status) con resumen de estados."""
    base_q = select(Document).where(Document.user_id == user["sub"])  # type: ignore
    if workspace_id:
        base_q = base_q.where(Document.workspace_id == workspace_id)
    if status:
        base_q = base_q.where(Document.status == status)

    # Obtener lista paginada
    docs: List[Document] = session.exec(
        base_q.order_by(Document.created_at.desc()).offset(offset).limit(limit)
    ).all()

    # Resumen de estados (sin paginar)
    sum_q = select(Document).where(Document.user_id == user["sub"])  # type: ignore
    if workspace_id:
        sum_q = sum_q.where(Document.workspace_id == workspace_id)
    all_for_summary: List[Document] = session.exec(sum_q).all()
    counts: Dict[str, int] = {}
    for d in all_for_summary:
        counts[d.status] = counts.get(d.status, 0) + 1

    return {
        "user_id": user["sub"],
        "workspace_id": workspace_id,
        "counts_by_status": counts,
        "documents": [
            {
                "id": str(d.id),
                "filename": d.filename,
                "workspace_id": getattr(d, "workspace_id", None),
                "status": d.status,
                "pages_count": d.pages_count,
                "text_chars": d.text_chars,
                "formulas_count": d.formulas_count or 0,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in docs
        ],
    }


@router.get("/me/overview")
def my_overview(
    user = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Resumen por workspace: totales y estados de documentos del usuario."""
    all_docs: List[Document] = session.exec(
        select(Document).where(Document.user_id == user["sub"])  # type: ignore
    ).all()

    workspaces: Dict[str, Dict[str, int]] = {}
    for d in all_docs:
        ws = getattr(d, "workspace_id", None) or "default"
        if ws not in workspaces:
            workspaces[ws] = {}
        workspaces[ws][d.status] = workspaces[ws].get(d.status, 0) + 1

    # Armar respuesta con una muestra de documentos recientes por workspace
    by_ws_docs: Dict[str, List[Dict]] = {}
    for d in sorted(all_docs, key=lambda x: x.created_at or 0, reverse=True):
        ws = getattr(d, "workspace_id", None) or "default"
        by_ws_docs.setdefault(ws, [])
        if len(by_ws_docs[ws]) < 10:
            by_ws_docs[ws].append(
                {
                    "id": str(d.id),
                    "filename": d.filename,
                    "status": d.status,
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                }
            )

    return {
        "user_id": user["sub"],
        "workspaces": [
            {
                "workspace_id": ws,
                "counts_by_status": workspaces[ws],
                "recent_documents": by_ws_docs.get(ws, []),
            }
            for ws in sorted(workspaces.keys())
        ],
    }
