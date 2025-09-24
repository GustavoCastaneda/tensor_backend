# backend/routes/workspaces.py
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlmodel import Session, select

from backend.db import get_session
from backend.models import Document, Workspace
from backend.security import get_current_user


router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def _user_can_access(session: Session, user_id: str, workspace_id: str) -> bool:
    """Permite acceso si el usuario es owner o tiene documentos en el workspace."""
    existing = session.get(Workspace, workspace_id)
    if existing:
        return existing.owner_user_id == user_id

    doc = session.exec(
        select(Document.id)
        .where(Document.user_id == user_id, Document.workspace_id == workspace_id)  # type: ignore
        .limit(1)
    ).first()
    return doc is not None


def _summary_for_workspace(session: Session, user_id: str, workspace_id: str) -> Dict[str, Optional[object]]:
    """Pequeño resumen basado en los documentos del usuario en el workspace."""
    docs: List[Document] = session.exec(
        select(Document)
        .where(Document.user_id == user_id, Document.workspace_id == workspace_id)  # type: ignore
    ).all()

    counts: Dict[str, int] = {}
    pages_total = 0
    last_document_at: Optional[datetime] = None

    for doc in docs:
        counts[doc.status] = counts.get(doc.status, 0) + 1
        if doc.pages_count:
            pages_total += doc.pages_count
        if doc.created_at and (last_document_at is None or doc.created_at > last_document_at):
            last_document_at = doc.created_at

    return {
        "documents_count": len(docs),
        "counts_by_status": counts,
        "pages_total": pages_total,
        "last_document_at": last_document_at.isoformat() if last_document_at else None,
    }


@router.get("/me")
def my_workspaces(
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Lista workspaces del usuario con nombre, descripción y resumen."""
    workspace_ids: set[str] = set()

    doc_rows = session.exec(
        select(Document.workspace_id)
        .where(Document.user_id == user["sub"])  # type: ignore
        .distinct()
    ).all()
    for row in doc_rows:
        ws_id = row[0] if isinstance(row, tuple) else row
        if ws_id:
            workspace_ids.add(ws_id)

    own_records: List[Workspace] = session.exec(
        select(Workspace).where(Workspace.owner_user_id == user["sub"])
    ).all()
    names: Dict[str, str] = {}
    descriptions: Dict[str, Optional[str]] = {}
    for record in own_records:
        workspace_ids.add(record.id)
        names[record.id] = record.name
        descriptions[record.id] = record.description

    workspaces = []
    for ws_id in sorted(workspace_ids):
        workspaces.append(
            {
                "workspace_id": ws_id,
                "name": names.get(ws_id),
                "description": descriptions.get(ws_id),
                "summary": _summary_for_workspace(session, user["sub"], ws_id),
            }
        )

    return {"workspaces": workspaces}


@router.get("/{workspace_id}")
def get_workspace(
    workspace_id: str,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not _user_can_access(session, user["sub"], workspace_id):
        raise HTTPException(404, "Workspace no encontrado")

    workspace = session.get(Workspace, workspace_id)
    return {
        "workspace_id": workspace_id,
        "name": workspace.name if workspace else None,
        "description": workspace.description if workspace else None,
        "summary": _summary_for_workspace(session, user["sub"], workspace_id),
    }


@router.put("/{workspace_id}")
def upsert_workspace(
    workspace_id: str,
    payload: Dict[str, str] = Body(..., example={"name": "Mi workspace", "description": "Notas relevantes"}),
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    name = (payload.get("name") or "").strip()
    description = (payload.get("description") or "").strip() or None

    if not name:
        raise HTTPException(400, "name es requerido")

    existing = session.get(Workspace, workspace_id)
    if existing and existing.owner_user_id != user["sub"]:
        raise HTTPException(403, "No tienes permisos para modificar este workspace")

    now = datetime.utcnow()

    if existing is None:
        existing = Workspace(
            id=workspace_id,
            owner_user_id=user["sub"],
            name=name,
            description=description,
            created_at=now,
            updated_at=now,
        )
        session.add(existing)
    else:
        existing.name = name
        existing.description = description
        existing.updated_at = now

    session.commit()
    session.refresh(existing)

    return {
        "workspace_id": existing.id,
        "name": existing.name,
        "description": existing.description,
    }
