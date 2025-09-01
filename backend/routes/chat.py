# backend/routes/chat.py
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from sqlmodel import Session, select

from backend.security import get_current_user
from backend.chat_router import route_message, Intent
from backend.sql_gen import generate_sql
from backend.sql_exec import run_sql_on_dataset
from backend.retriever import retrieve_relevant_columns
from backend.chart_suggest import suggest_chart
from backend.db import get_session
from backend.models import Dataset, Column

router = APIRouter(prefix="/chat", tags=["chat"])

ALLOWED_STATUSES = ("ready_for_embeddings", "ready_for_chat")


# ────────── Schemas ──────────
class ChatRequest(BaseModel):
    dataset_id: UUID
    message: str
    # Permite forzar columnas elegidas por el usuario (nombres exactos)
    force_columns: Optional[List[str]] = None


class TableData(BaseModel):
    columns: List[str]
    rows: List[dict]


class RetrievalItem(BaseModel):
    column_id: str
    score: float
    original_name: str
    detected_type: Optional[str] = None
    description: Optional[str] = None


class ChartSuggestion(BaseModel):
    type: str
    x: str
    y: List[str]


class ChatResponse(BaseModel):
    intent: Intent
    answer: Optional[str] = None
    table: Optional[TableData] = None
    sql: Optional[str] = None
    retrieval: Optional[List[RetrievalItem]] = None
    # Desambiguación
    needs_disambiguation: Optional[bool] = None
    candidates: Optional[List[str]] = None
    # Sugerencia de gráfica
    chart_suggestion: Optional[ChartSuggestion] = None


# ────────── Helpers ──────────
def _load_ds_and_columns(
    session: Session, dataset_id: UUID, user
) -> tuple[Dataset, list[dict], list[Column]]:
    ds = session.exec(select(Dataset).where(Dataset.id == dataset_id)).first()
    if not ds or ds.user_id != user["sub"]:
        raise HTTPException(404, "Dataset no encontrado")
    if not ds.parquet_url:
        raise HTTPException(400, "El dataset no tiene Parquet generado")
    if ds.status not in ALLOWED_STATUSES:
        # Permitimos consultar si ya hay Parquet; este mensaje solo informa
        pass

    cols = session.exec(select(Column).where(Column.dataset_id == dataset_id)).all()
    columns_hint = [
        {
            "original_name": c.original_name,
            "detected_type": c.detected_type,
            "sample_values": c.sample_values,
        }
        for c in cols
    ]
    return ds, columns_hint, cols


def _sql_flow(
    session: Session,
    dataset_id: UUID,
    question: str,
    user,
    relevant_col_names: Optional[List[str]] = None,
) -> tuple[str, str, TableData]:
    ds, columns_hint, _ = _load_ds_and_columns(session, dataset_id, user)
    sql, explanation = generate_sql(
        question, columns_hint, relevant_cols=relevant_col_names
    )
    out = run_sql_on_dataset(ds.parquet_url, sql, limit=300)
    # Evitamos pasar claves extra a TableData
    table = TableData(columns=out.get("columns", []), rows=out.get("rows", []))
    return sql, explanation, table


# ────────── Endpoint ──────────
@router.post("", response_model=ChatResponse)
def chat(
    req: ChatRequest,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    intent = route_message(req.message)

    # Camino 100% SQL
    if intent == Intent.sql:
        try:
            sql, explanation, table = _sql_flow(
                session, req.dataset_id, req.message, user, req.force_columns
            )
            chart_dict = suggest_chart({"columns": table.columns, "rows": table.rows})
            chart = ChartSuggestion(**chart_dict) if chart_dict else None
            return ChatResponse(
                intent=intent,
                answer=explanation,
                table=table,
                sql=sql,
                chart_suggestion=chart,
            )
        except Exception as e:
            raise HTTPException(400, f"Error al ejecutar SQL: {e}")

    # Camino Mixto: RAG columnas + SQL
    if intent == Intent.mixed:
        # Si el cliente ya forzó columnas, úsalo directo
        if req.force_columns:
            try:
                sql, explanation, table = _sql_flow(
                    session, req.dataset_id, req.message, user, req.force_columns
                )
                chart_dict = suggest_chart(
                    {"columns": table.columns, "rows": table.rows}
                )
                chart = ChartSuggestion(**chart_dict) if chart_dict else None
                return ChatResponse(
                    intent=intent,
                    answer=explanation,
                    table=table,
                    sql=sql,
                    chart_suggestion=chart,
                )
            except Exception as e:
                raise HTTPException(400, f"Error al ejecutar SQL (mixed/forced): {e}")

        # RAG en Qdrant (o fallback léxico del retriever)
        retr = retrieve_relevant_columns(str(req.dataset_id), req.message, top_k=5)
        relevant_names = [r["original_name"] for r in retr] if retr else []

        # Heurística de confianza: si top score < 0.55 o muy pegados los dos primeros → pedir confirmación
        needs_disamb = False
        candidates: Optional[List[str]] = None
        if retr:
            top = float(retr[0]["score"])
            second = float(retr[1]["score"]) if len(retr) > 1 else 0.0
            if top < 0.55 or (top - second) < 0.05:
                needs_disamb = True
                candidates = [r["original_name"] for r in retr][:5]

        if needs_disamb:
            return ChatResponse(
                intent=intent,
                answer="Necesito confirmar a qué columna(s) te refieres.",
                needs_disambiguation=True,
                candidates=candidates,
                retrieval=[RetrievalItem(**r) for r in retr] if retr else None,
            )

        # Suficiente confianza → ejecutar
        try:
            sql, explanation, table = _sql_flow(
                session,
                req.dataset_id,
                req.message,
                user,
                relevant_names or None,
            )
            chart_dict = suggest_chart({"columns": table.columns, "rows": table.rows})
            chart = ChartSuggestion(**chart_dict) if chart_dict else None
            return ChatResponse(
                intent=intent,
                answer=explanation,
                table=table,
                sql=sql,
                retrieval=[RetrievalItem(**r) for r in retr] if retr else None,
                chart_suggestion=chart,
            )
        except Exception as e:
            raise HTTPException(400, f"Error al ejecutar SQL (mixed): {e}")

    # Semántico: placeholder (sin RAG textual aún)
    if intent == Intent.semantic:
        _, columns_hint, _ = _load_ds_and_columns(session, req.dataset_id, user)
        col_names = [c["original_name"] for c in columns_hint]
        msg = "Aún no implementamos RAG textual. Columnas: " + ", ".join(col_names[:30]) + (
            "..." if len(col_names) > 30 else ""
        )
        return ChatResponse(intent=intent, answer=msg, table=None)

    return ChatResponse(intent=intent, answer=None, table=None)
