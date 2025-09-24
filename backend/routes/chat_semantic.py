"""
Endpoint /chat/semantic para consultas sobre documentos PDF con RAG
Implementa estrategia de retrieval adaptativa, fusión de chunks y citas clicables
"""

from typing import List, Optional, Dict, Any, Tuple, Set
import os
import re
import json
import time
import hashlib
from uuid import UUID
from dataclasses import dataclass
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

# AI DataStream imports
try:
    from ai_datastream.api.fastapi import AiChatDataStreamAsyncResponse, FastApiDataStreamRequest
    from ai_datastream.agent.openai import OpenAIChatStreamer
    AI_DATASTREAM_AVAILABLE = True
except ImportError:
    AI_DATASTREAM_AVAILABLE = False
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from qdrant_client import QdrantClient, models

from backend.db import get_session
from backend.security import get_current_user
from backend.models import Document
from backend.query_embeddings import get_query_embedding
from backend.retrieval.helpers import light_rerank as _light_rerank
from backend.retrieval.helpers import normalize_snippet as _normalize_snippet
from backend.retrieval.helpers import confidence_badge as _confidence_badge
from backend.retrieval.helpers import hash_content as _hash_content
from backend.retrieval.engine import search_r1_loop as _search_r1_loop
from backend.retrieval.rex_light import should_trigger_rex as _should_trigger_rex
from backend.retrieval.rex_light import apply_rex_light as _apply_rex_light
from backend.memory.store import search_memory as _memory_search
from backend.memory.store import write_memory as _memory_write
from backend.indexers.llama_mvp import get_llama_mvp

# Router para endpoints de chat semántico
router = APIRouter(prefix="/chat", tags=["chat"])

# Configuración de servicios externos
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
EMB_MODEL = os.getenv("EMBEDDINGS_MODEL", "text-embedding-3-small")
LLM_MODEL = os.getenv("ANSWER_MODEL", "gpt-5")
REASONING_EFFORT = os.getenv("REASONING_EFFORT", "medium")
DOC_SEARCH_LIMIT = int(os.getenv("DOC_SEARCH_LIMIT", "24"))
QDRANT_TIMEOUT_SECONDS = float(os.getenv("QDRANT_TIMEOUT_SECONDS", "10.0"))  # Aumentado de 3 a 10 segundos

# Configuración de búsqueda híbrida
HYBRID_ALPHA = float(os.getenv("HYBRID_ALPHA", "0.7"))  # Peso para dense vs sparse (0.7 = 70% dense, 30% sparse)
HYBRID_ENABLED = os.getenv("HYBRID_SEARCH", "true").strip().lower() == "true"

# Feature flags (rollout)
REX_LIGHT_FLAG = os.getenv("REX_LIGHT", "false").strip().lower() == "true"
COMO_MEMORY_FLAG = os.getenv("COMO_MEMORY", "false").strip().lower() == "true"
USE_LLAMA_MVP = os.getenv("USE_LLAMA_MVP", "false").strip().lower() == "true"

# Lexical search tuning
SPANISH_STOPWORDS = {
    "como", "para", "cual", "cuales", "donde", "cuando", "dentro", "sobre", "este", "esta",
    "estos", "estas", "del", "con", "sus", "las", "los", "que", "archivos", "archivo",
    "cuales", "tiene", "cual", "cuales", "al", "una", "unos", "unas", "sera", "será",
    "cual", "cuales", "porque", "cual", "cuales", "esto", "esta", "ese", "esa", "aquel",
}
PREFERRED_SPARSE_TERMS = [
    "costo", "costos", "precio", "precios", "presupuesto", "inversion", "inversiones",
    "universidad", "virtual", "desarrollo", "implementacion", "implementación", "sistema",
    "montos", "importe", "importes",
]
MAX_SPARSE_TERMS = 4
COST_KEYWORDS = {
    "costo", "costos", "precio", "precios", "tarifa", "presupuesto", "importe", "importes",
    "mxn", "usd", "$", "desarrollo", "implementacion", "implementación", "sistema", "virtual"
}

# ───────────────────────── Search-R1-lite Classes ─────────────────────────

class StopSignal(Enum):
    """Señales para parar el loop de búsqueda"""
    CONFIDENCE_MET = "confidence_met"
    COVERAGE_MET = "coverage_met"
    MAX_STEPS = "max_steps"
    BUDGET_EXHAUSTED = "budget_exhausted"
    NO_NOVELTY = "no_novelty"

@dataclass
class ProbeResult:
    """Resultado de una probe de búsqueda"""
    query: str
    points: List[models.ScoredPoint]
    latency_ms: int
    
@dataclass
class SearchState:
    """Estado del loop de búsqueda Search-R1"""
    step: int
    queries_executed: List[str]
    all_points: List[models.ScoredPoint]
    seen_hashes: Set[str]
    confidence_score: float
    coverage_score: float
    novelty_score: float
    budget_remaining_ms: int
    stop_signal: Optional[StopSignal]
    
@dataclass
class SearchTelemetry:
    """Telemetría del Search-R1 loop"""
    executed_steps: int
    queries_per_probe: List[int]
    queries_executed: List[str]
    stop_signal: str
    latencies_ms: Dict[str, int]
    final_evidence_count: int
    exploration_badge: Optional[str]
    # REX-light telemetry
    rex_applied: Optional[bool] = None
    rex_rounds: Optional[int] = None
    rex_trigger: Optional[str] = None
    rex_spent_ms: Optional[int] = None
    rex_variants_tried: Optional[int] = None


# ───────────────────────── Schemas de Request/Response ─────────────────────────

class ChatSemanticFilters(BaseModel):
    """Filtros opcionales para la búsqueda en documentos"""
    doc_id: Optional[UUID] = None  # Filtrar por documento específico
    page_range: Optional[List[int]] = Field(
        default=None,
        description="[start, end] inclusive, base-1 pages",
    )
    lang: Optional[str] = None  # Filtrar por idioma


class ChatSemanticRequest(BaseModel):
    """Request para consultas semánticas sobre documentos"""
    workspace_id: str  # Scope de búsqueda (obligatorio)
    message: str  # Pregunta del usuario (obligatorio)
    intent: str = Field(default="semantic")  # Tipo de consulta (fijo en semantic)
    k: int = Field(default=8)  # Objetivo de evidencias tras filtrado
    filters: Optional[ChatSemanticFilters] = None  # Filtros opcionales
    rerank: bool = Field(default=True)  # Aplicar reordenamiento ligero
    min_score: float = Field(default=0.35)  # Umbral de similitud
    per_page_cap: int = Field(default=2)  # Máx. sub-chunks por misma página
    per_doc_cap: int = Field(default=4)  # Máx. evidencias por mismo documento
    include_debug: bool = Field(default=False)  # Incluir trazas/latencias
    include_thinking_summary: bool = Field(default=False)  # Resumen legible del razonamiento operativo
    conversation: Optional[str] = None  # ID o historial para desambiguar
    client_version: Optional[str] = None  # Telemetría
    stream: bool = Field(default=False)  # Habilitar streaming SSE
    
    # ───────── Search-R1-lite Parameters ─────────
    search_loop_max_steps: int = Field(default=2, ge=1, le=3)  # Máx. ciclos de búsqueda
    search_loop_budget_ms: int = Field(default=1500)  # Presupuesto de tiempo extra (aumentado de 600)
    probe_fanout: int = Field(default=2, ge=2, le=4)  # Reformulaciones por step (mínimo 2)
    evidence_target: int = Field(default=4, ge=3, le=5)  # Bloques de evidencia finales
    confidence_target: str = Field(default="medium")  # "low"|"medium"|"high"
    trace_level: str = Field(default="none")  # "none"|"basic"|"full"
    
    # ───────── Hybrid Search Parameters ─────────
    hybrid_enabled: bool = Field(default=True)  # Habilitar búsqueda híbrida
    hybrid_alpha: float = Field(default=0.7, ge=0.0, le=1.0)  # Peso dense vs sparse (0.7 = 70% dense, 30% sparse)


class CitationItem(BaseModel):
    """Elemento de cita con metadatos para navegación"""
    doc_id: str  # ID del documento
    title: str  # Nombre legible del archivo
    page: int  # Página (base-1)
    chunk_seq_range: str  # Rango de chunks (ej. "3" o "2–3")
    text_snippet: str  # Fragmento de texto (~200–400 chars)
    score: float  # Score de relevancia (0–1)
    confidence_badge: Optional[str] = None  # "alto"|"medio"|"bajo"
    viewer_link: str  # URL para abrir el visor en la página


class ChatSemanticResponse(BaseModel):
    """Response con respuesta sintetizada y citas navegables"""
    answer: str  # Síntesis final del LLM
    reasoning: Optional[str] = None  # Razonamiento del modelo (si está disponible)
    citations: List[CitationItem]  # Citas clicables (2–5 ítems)
    meta: Dict[str, Any]  # Metadatos (workspace_id, intent, used_model, latency_ms)
    debug: Optional[Dict[str, Any]] = None  # Debug info si include_debug=true


# ───────────────────────── Funciones Helper ─────────────────────────

def _get_qdrant() -> QdrantClient:
    """Obtiene cliente de Qdrant para búsquedas vectoriales"""
    return QdrantClient(url=QDRANT_URL, timeout=QDRANT_TIMEOUT_SECONDS)

def _ensure_text_index(qdrant: QdrantClient, collection_name: str) -> bool:
    """
    Asegura que existe un índice full-text en el campo 'text' de la colección
    Retorna True si el índice existe o se creó exitosamente
    """
    try:
        # Verificar si ya existe el índice
        collection_info = qdrant.get_collection(collection_name=collection_name)
        payload_schema = collection_info.payload_schema or {}
        text_index_info = payload_schema.get("text")
        
        if text_index_info:
            print(f"  ✓ Full-text index already exists for collection {collection_name}")
            return True
        
        # Crear el índice full-text
        print(f"  Creating full-text index for collection {collection_name}...")
        qdrant.create_payload_index(
            collection_name=collection_name,
            field_name="text",
            field_schema=models.TextIndexParams(
                type=models.TextIndexType.TEXT,
                tokenizer=models.TokenizerType.WORD,
                min_token_len=2,
                max_token_len=20,
                lowercase=True
            )
        )
        print(f"  ✓ Full-text index created successfully")
        return True
        
    except Exception as e:
        print(f"  ✗ Error creating full-text index: {e}")
        return False


def _confidence_badge(score: float) -> str:
    """Convierte score numérico a badge de confianza legible"""
    if score >= 0.7:
        return "alto"
    if score >= 0.5:
        return "medio"
    return "bajo"


def _make_viewer_link(doc_id: str, page: int) -> str:
    """Genera URL para abrir el visor en una página específica"""
    return f"/viewer/{doc_id}?page={page}"


def _normalize_snippet(text: str, max_chars: int = 1000) -> str:
    """Trunca texto de forma inteligente sin cortar palabras"""
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    # Buscar último espacio antes del límite
    cutoff = text.rfind(" ", 0, max_chars)
    if cutoff == -1:
        cutoff = max_chars
    return text[:cutoff].rstrip() + "…"


def _light_rerank(query: str, items: List[models.ScoredPoint]) -> None:
    """
    Re-rank ligero: favorece block_type relevante según el tipo de consulta
    - Definiciones → prioriza "text"
    - Cálculos → prioriza "formula", "table"  
    - Datos → prioriza "table", "figure"
    """
    q = query.lower()
    if any(w in q for w in ["definición", "qué es", "significa", "concepto"]):
        prefer = {"text"}
    elif any(w in q for w in ["cálculo", "calcular", "fórmula", "ecuación"]):
        prefer = {"formula", "table"}
    elif any(w in q for w in ["tabla", "datos", "números", "estadísticas"]):
        prefer = {"table", "figure"}
    else:
        prefer = {"text", "table", "formula"}
    
    # Aplicar boost del 20% a tipos preferidos
    for it in items:
        bt = (it.payload or {}).get("block_type")
        if bt in prefer:
            it.score = float(it.score or 0.0) * 1.2


def _compute_lexical_score(text: str, terms: List[str]) -> float:
    """Calcula score lexical simple basado en términos coincidentes."""
    if not text or not terms:
        return 0.0
    text_norm = text.lower()
    hits = 0
    for term in terms:
        term_norm = term.lower().strip(".,;:¿?¡!()[]{}\"")
        if term_norm and term_norm in text_norm:
            hits += 1
    return hits / len(terms)


def _record_to_scored_point(record: Any) -> models.ScoredPoint:
    """Convierte un Record (scroll) en ScoredPoint con score inicial 0."""
    return models.ScoredPoint(
        id=getattr(record, "id", None),
        payload=getattr(record, "payload", {}) or {},
        score=float(getattr(record, "score", 0.0) or 0.0),
        vector=getattr(record, "vector", None),
        version=getattr(record, "version", 0),
    )


def _payload_contains_keywords(payload: Dict[str, Any], keywords: Set[str]) -> bool:
    text = (payload.get("text") or payload.get("text_preview") or "").lower()
    if not text:
        return False
    return any(kw in text for kw in keywords)


def _extract_relevant_snippet(text: str, max_chars: int = 600) -> str:
    """Devuelve un fragmento que prioriza la aparición de keywords de costo."""
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text

    lowered = text.lower()
    keyword_pool = list(COST_KEYWORDS) + ["$", "mxn", "usd"]
    for kw in keyword_pool:
        idx = lowered.find(kw)
        if idx != -1:
            half = max_chars // 2
            start = max(0, idx - half)
            end = min(len(text), start + max_chars)
            snippet = text[start:end].strip()
            prefix = "…" if start > 0 else ""
            suffix = "…" if end < len(text) else ""
            return f"{prefix}{snippet}{suffix}"

    return _normalize_snippet(text, max_chars)


def _extract_answer_and_reasoning(resp: Any) -> Tuple[str, str]:
    answer = ""
    reasoning_text = ""

    if hasattr(resp, "output") and resp.output:
        for item in resp.output:
            item_type = getattr(item, "type", None)
            if item_type == "reasoning":
                summaries = getattr(item, "summary", None) or []
                for summary_item in summaries:
                    text_val = getattr(summary_item, "text", None)
                    if text_val:
                        reasoning_text = text_val
                if not reasoning_text and getattr(item, "content", None):
                    for content_item in item.content or []:
                        text_val = getattr(content_item, "text", None)
                        if text_val:
                            reasoning_text = text_val
            elif item_type == "message" and getattr(item, "content", None):
                for content_item in item.content or []:
                    text_val = getattr(content_item, "text", None)
                    if text_val:
                        answer = text_val

    if not answer and hasattr(resp, "output_text") and resp.output_text:
        answer = resp.output_text

    if not answer and hasattr(resp, "output") and resp.output:
        for item in resp.output:
            for content_item in getattr(item, "content", []) or []:
                text_val = getattr(content_item, "text", None)
                if text_val:
                    answer = text_val
                    break
            if answer:
                break

    if not answer and reasoning_text:
        answer = reasoning_text

    return answer, reasoning_text


def _invoke_llama_index(
    req: "ChatSemanticRequest",
    docs: List[Document],
    reason: str,
    filters: Dict[str, Any],
) -> Tuple[Optional[Any], Dict[str, Any]]:
    print(f"=== LLAMAINDEX MVP ({reason}) ===")
    try:
        llama_mvp = get_llama_mvp()
        doc_ids = [str(d.id) for d in docs]

        if not getattr(llama_mvp, "vector_index", None):
            print("Building LlamaIndex for first time...")
            success = llama_mvp.build_indexes(req.workspace_id, doc_ids)
            if not success:
                print("Failed to build LlamaIndex; skipping fallback")
                return None, {}
        else:
            print("Using existing LlamaIndex indexes")

        response, metrics = llama_mvp.query(
            query=req.message,
            workspace_id=req.workspace_id,
            response_mode="compact",
            similarity_top_k=5,
            summary_top_k=3,
            filters={k: v for k, v in filters.items() if v is not None},
        )
        metrics["reason"] = reason
        if response:
            preview = response.response[:200] if getattr(response, "response", None) else ""
            print(f"LlamaIndex response preview: {preview}...")
            print(f"LlamaIndex metrics: {metrics}")
        return response, metrics

    except Exception as e:
        print(f"LlamaIndex MVP failed ({reason}): {e}")
        return None, {"error": str(e), "reason": reason}


def _sse_event(data: Dict[str, Any], event: Optional[str] = None) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    if event:
        return f"event: {event}\ndata: {payload}\n\n"
    return f"data: {payload}\n\n"


def _stream_openai_response(
    client: "OpenAI",
    request_kwargs: Dict[str, Any],
    citations: List[CitationItem],
    finalize_cb,
    req: "ChatSemanticRequest",
) -> StreamingResponse:
    def event_stream():
        info_payload = {
            "type": "info",
            "citations": [c.model_dump() for c in citations],
            "stream": True,
        }
        yield _sse_event(info_payload)

        try:
            # Usar responses.create con stream=True en lugar de responses.stream
            stream = client.responses.create(**request_kwargs, stream=True)
            
            answer_text = ""
            reasoning_text = ""

            for event in stream:
                event_type = getattr(event, "type", "")

                if event_type == "response.reasoning.delta":
                    delta = getattr(event, "delta", None)
                    text_val = None
                    if isinstance(delta, str):
                        text_val = delta
                    elif hasattr(delta, "text"):
                        text_val = delta.text
                    if text_val:
                        reasoning_text += text_val
                        yield _sse_event({"type": "reasoning", "text": text_val})
                        
                elif event_type == "response.reasoning_summary_text.delta":
                    # Manejar reasoning summary
                    delta = getattr(event, "delta", None)
                    text_val = None
                    if isinstance(delta, str):
                        text_val = delta
                    elif hasattr(delta, "text") and delta.text:
                        text_val = delta.text
                    if text_val:
                        reasoning_text += text_val
                        yield _sse_event({"type": "reasoning", "text": text_val})
                        
                elif event_type == "response.output_text.delta":
                    delta = getattr(event, "delta", None)
                    if isinstance(delta, str) and delta:
                        answer_text += delta
                        yield _sse_event({"type": "token", "text": delta})
                    elif hasattr(delta, "text") and delta.text:
                        answer_text += delta.text
                        yield _sse_event({"type": "token", "text": delta.text})
                        
                elif event_type in ["response.completed", "response.done"]:
                    # El stream ha terminado
                    break

            # Usar el texto acumulado para generar la respuesta final
            response_model = finalize_cb(answer_text, reasoning_text)
            yield _sse_event({"type": "final", "response": response_model.model_dump()})
            yield "event: done\n\n"

        except Exception as e:
            yield _sse_event({"type": "error", "message": str(e)})
            yield "event: done\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ───────────────────────── Search-R1-lite Engine ─────────────────────────

def _get_confidence_threshold(target: str) -> float:
    """Mapeo de confidence_target a umbral numérico"""
    thresholds = {"low": 0.4, "medium": 0.6, "high": 0.75}
    return thresholds.get(target, 0.6)

def _generate_query_variants(original_query: str, fanout: int) -> List[str]:
    """
    Genera variantes de la consulta usando LLM para reformulación
    Estrategias: cues semánticas, expansión de acrónimos, block_type hints
    """
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        prompt = f"""Genera {fanout} reformulaciones de esta consulta para mejorar la búsqueda de documentos:

Consulta original: "{original_query}"

Estrategias:
- Agregar cues semánticas: "definición", "metodología", "alcance", "anexo"
- Especificar tipo de contenido: "tabla", "fórmula", "ejemplo práctico"
- Expandir acrónimos comunes
- Usar sinónimos técnicos

Devuelve solo las {fanout} consultas reformuladas, una por línea, sin numeración."""

        # Usar chat.completions con response_format para structured outputs
        try:
            resp = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=200,
                response_format={"type": "text"}
            )
            content = resp.choices[0].message.content
            if content:
                variants = [line.strip() for line in content.strip().split("\n") if line.strip()]
                return variants[:fanout]
        except Exception as e:
            print(f"Error en chat.completions: {e}")
            # Fallback: variantes simples
            return [
                f"definición de {original_query}",
                f"metodología {original_query}",
            ][:fanout]

    except Exception as e:
        print(f"Error generando variantes: {e}")
        # Fallback: variantes simples
        return [
            f"definición de {original_query}",
            f"metodología {original_query}",
        ][:fanout]

def _execute_hybrid_search(
    query: str,
    doc_id: str,
    qdrant: QdrantClient,
    query_filter: models.Filter,
    k_raw: int
) -> Tuple[List[models.ScoredPoint], List[models.ScoredPoint]]:
    """
    Ejecuta búsqueda híbrida: dense (vectorial) + sparse (lexical)
    Retorna: (dense_results, sparse_results)
    """
    dense_results = []
    sparse_results = []
    
    try:
        # 1. BÚSQUEDA DENSA (Vectorial) - Actual
        query_vec = get_query_embedding(query)
        dense_res = qdrant.search(
            collection_name=doc_id,
            query_vector=query_vec,
            query_filter=query_filter,
            limit=k_raw,
            with_payload=True,
        )
        dense_results = dense_res or []
        
        # Debug: mostrar qué está encontrando la búsqueda dense
        print(f"Debug Dense Search for doc {doc_id}:")
        print(f"  Query: '{query}'")
        print(f"  Results count: {len(dense_results)}")
        for i, p in enumerate(dense_results[:3]):
            payload = p.payload or {}
            text_source = payload.get('text_preview') or payload.get('text', '')
            text_preview = (text_source[:100]).replace('\n', ' ')
            print(f"    Dense {i+1}: score={float(p.score or 0.0):.3f}, text='{text_preview}...'")
        
        # 2. BÚSQUEDA SPARSE (Lexical) - Real usando MatchText para búsqueda de subcadenas
        # Buscar por términos exactos usando MatchText en Qdrant
        key_terms: List[str] = []
        try:
            normalized_terms = re.findall(r"\w+", query.lower())
            candidate_terms: List[str] = []
            for term in normalized_terms:
                if len(term) <= 3:
                    continue
                if term in SPANISH_STOPWORDS:
                    continue
                if term in candidate_terms:
                    continue
                candidate_terms.append(term)

            if not candidate_terms:
                candidate_terms = [term for term in normalized_terms if len(term) > 3]

            def _term_priority(t: str) -> Tuple[int, int]:
                preferred_index = PREFERRED_SPARSE_TERMS.index(t) if t in PREFERRED_SPARSE_TERMS else len(PREFERRED_SPARSE_TERMS)
                original_index = candidate_terms.index(t)
                return preferred_index, original_index

            ordered_terms = sorted(candidate_terms, key=_term_priority)
            key_terms = ordered_terms[:MAX_SPARSE_TERMS]
            
            # Crear filtros de texto para búsqueda de subcadenas usando MatchText
            text_filters = []
            for term in key_terms[:MAX_SPARSE_TERMS]:
                text_filters.append(
                    models.FieldCondition(
                        key="text",
                        match=models.MatchText(text=term)
                    )
                )
            
            # Combinar filtros de texto con filtros existentes usando SHOULD (OR)
            if text_filters:
                # Verificar índice full-text SIN crearlo automáticamente (para evitar timeouts)
                try:
                    collection_info = qdrant.get_collection(collection_name=doc_id)
                    payload_schema = collection_info.payload_schema or {}
                    text_index_info = payload_schema.get("text")
                    has_text_index = bool(text_index_info)
                    print(f"  Text index available: {has_text_index}")
                except Exception as e:
                    print(f"  Error checking text index: {e}")
                    has_text_index = False
                
                if has_text_index:
                    # Usar should para que encuentre documentos que contengan CUALQUIERA de los términos
                    sparse_filter = models.Filter(
                        must=query_filter.must,  # Mantener filtros existentes (workspace_id, etc.)
                        should=text_filters      # Agregar términos de texto como OR
                    )
                    
                    # Búsqueda con filtro de texto usando search (más rápido que scroll)
                    try:
                        scroll_points, _ = qdrant.scroll(
                            collection_name=doc_id,
                            scroll_filter=sparse_filter,
                            limit=k_raw,
                            with_payload=True,
                            with_vectors=False,
                        )
                        sparse_results = [
                            _record_to_scored_point(rec) for rec in (scroll_points or [])
                        ]
                    except Exception as search_error:
                        print(f"  Search with text filters failed: {search_error}")
                        sparse_results = []
                else:
                    print(f"  No text index available, attempting to create it...")
                    # Intentar crear el índice full-text de forma asíncrona
                    try:
                        qdrant.create_payload_index(
                            collection_name=doc_id,
                            field_name="text",
                            field_schema=models.TextIndexParams(
                                type=models.TextIndexType.TEXT,
                                tokenizer=models.TokenizerType.WORD,
                                min_token_len=2,
                                max_token_len=20,
                                lowercase=True
                            )
                        )
                        print(f"  ✓ Text index created successfully")
                        
                        # Ahora intentar la búsqueda sparse
                        sparse_filter = models.Filter(
                            must=query_filter.must,
                            should=text_filters
                        )
                        scroll_points, _ = qdrant.scroll(
                            collection_name=doc_id,
                            scroll_filter=sparse_filter,
                            limit=k_raw,
                            with_payload=True,
                            with_vectors=False,
                        )
                        sparse_results = [
                            _record_to_scored_point(rec) for rec in (scroll_points or [])
                        ]
                        print(f"  Sparse search after index creation: {len(sparse_results)} results")
                    except Exception as create_error:
                        print(f"  Failed to create text index: {create_error}")
                        sparse_results = []
            else:
                sparse_results = []
            
            # Debug: mostrar qué está encontrando la búsqueda sparse
            print(f"Debug Sparse Search for doc {doc_id}:")
            print(f"  Query: '{query}'")
            print(f"  Key terms (original case): {key_terms}")
            print(f"  Text filters count: {len(text_filters)}")
            print(f"  Results count: {len(sparse_results)}")
            
            # Verificar si existe índice full-text en la colección
            try:
                collection_info = qdrant.get_collection(collection_name=doc_id)
                payload_schema = collection_info.payload_schema or {}
                text_index_info = payload_schema.get("text")
                if text_index_info:
                    print(f"  ✓ Full-text index exists for 'text' field: {text_index_info}")
                else:
                    print(f"  ✗ No full-text index found for 'text' field")
                    print(f"  Available payload schema: {list(payload_schema.keys())}")
            except Exception as e:
                print(f"  Error checking collection info: {e}")
            
            # Debug: mostrar campos disponibles en un documento de ejemplo
            if dense_results:
                sample_payload = dense_results[0].payload or {}
                print(f"  Sample payload keys: {list(sample_payload.keys())}")
                sample_text = sample_payload.get('text_preview') or sample_payload.get('text', 'NO_TEXT')
                print(f"  Sample text field: '{sample_text[:100]}...'")
                for term in key_terms[:3]:
                    if term.lower() in sample_text.lower():
                        print(f"    ✓ Found term '{term}' in sample text")
                    else:
                        print(f"    ✗ Term '{term}' NOT found in sample text")

            for i, p in enumerate(sparse_results[:3]):
                payload = p.payload or {}
                text_source = payload.get('text_preview') or payload.get('text', '')
                text_preview = (text_source[:100]).replace('\n', ' ')
                print(f"    Sparse {i+1}: score={float(p.score or 0.0):.3f}, text='{text_preview}...'")

        except Exception as e:
            print(f"Error en búsqueda sparse real: {e}")
            # Fallback a búsqueda vectorial con query expandida
            try:
                expanded_query = " ".join(key_terms) if 'key_terms' in locals() else query
                sparse_query_vec = get_query_embedding(expanded_query)
                sparse_res = qdrant.search(
                    collection_name=doc_id,
                    query_vector=sparse_query_vec,
                    query_filter=query_filter,
                    limit=k_raw,
                    with_payload=True,
                )
                sparse_results = sparse_res or []
                print(f"  Fallback sparse results: {len(sparse_results)}")
            except Exception as e2:
                print(f"  Fallback also failed: {e2}")
                sparse_results = []
        
    except Exception as e:
        print(f"Error en búsqueda híbrida para doc {doc_id}: {e}")
        return [], []

    # Ajustar scores lexicals si existen resultados sparse
    if sparse_results:
        for point in sparse_results:
            payload = point.payload or {}
            text_payload = payload.get("text") or payload.get("text_preview", "")
            score = _compute_lexical_score(text_payload, key_terms if 'key_terms' in locals() else [])
            point.score = score
        # Debug: mostrar scores ajustados
        print("  Adjusted sparse scores:")
        for i, p in enumerate(sparse_results[:3]):
            payload = p.payload or {}
            text_source = payload.get('text_preview') or payload.get('text', '')
            text_preview = (text_source[:100]).replace('\n', ' ')
            print(f"    Sparse {i+1}: score={float(p.score or 0.0):.3f}, text='{text_preview}...'")

    return dense_results, sparse_results

def _combine_and_rerank_hybrid(
    dense_results: List[models.ScoredPoint],
    sparse_results: List[models.ScoredPoint],
    query: str,
    alpha: float = 0.7  # Peso para dense (0.7) vs sparse (0.3)
) -> List[models.ScoredPoint]:
    """
    Combina resultados dense y sparse con reranking híbrido
    alpha: peso para dense (0.7) vs sparse (0.3)
    """
    # Crear diccionario para combinar por ID único
    combined_scores: Dict[str, Dict[str, Any]] = {}
    
    # Procesar resultados dense
    for point in dense_results:
        payload = point.payload or {}
        content_hash = payload.get("hash") or _hash_content(payload.get("text", ""))
        combined_scores[content_hash] = {
            "point": point,
            "dense_score": float(point.score or 0.0),
            "sparse_score": 0.0,
            "final_score": 0.0
        }
    
    # Procesar resultados sparse
    for point in sparse_results:
        payload = point.payload or {}
        content_hash = payload.get("hash") or _hash_content(payload.get("text", ""))
        
        if content_hash in combined_scores:
            # Ya existe, actualizar sparse score
            combined_scores[content_hash]["sparse_score"] = float(point.score or 0.0)
        else:
            # Nuevo, agregar
            combined_scores[content_hash] = {
                "point": point,
                "dense_score": 0.0,
                "sparse_score": float(point.score or 0.0),
                "final_score": 0.0
            }
    
    # Calcular scores híbridos
    for data in combined_scores.values():
        # Normalizar scores a [0,1] si es necesario
        dense_norm = data["dense_score"]
        sparse_norm = data["sparse_score"]
        
        # Score híbrido: alpha * dense + (1-alpha) * sparse
        data["final_score"] = alpha * dense_norm + (1 - alpha) * sparse_norm
    
    # Crear puntos con scores híbridos
    hybrid_points = []
    for data in combined_scores.values():
        point = data["point"]
        # Crear nuevo punto con score híbrido
        hybrid_point = models.ScoredPoint(
            id=point.id,
            score=data["final_score"],
            payload=point.payload,
            vector=point.vector,
            version=point.version if hasattr(point, 'version') else 0
        )
        hybrid_points.append(hybrid_point)
    
    # Ordenar por score híbrido descendente
    hybrid_points.sort(key=lambda x: float(x.score or 0.0), reverse=True)
    
    return hybrid_points

def _execute_probe(
    query: str, 
    docs: List[Document], 
    req: ChatSemanticRequest, 
    qdrant: QdrantClient
) -> ProbeResult:
    """Ejecuta una probe de búsqueda híbrida individual"""
    t0 = time.monotonic()
    
    # Reducir fanout por probe para evitar timeouts bajo carga
    k_raw = max(1, min(6, req.evidence_target * 2))
    
    all_points: List[models.ScoredPoint] = []
    
    # Buscar en cada documento con búsqueda híbrida
    for d in docs:
        # Construir filtros
        must: List[models.FieldCondition] = [
            models.FieldCondition(
                key="workspace_id", match=models.MatchValue(value=req.workspace_id)
            )
        ]
        
        if req.filters and req.filters.lang:
            must.append(
                models.FieldCondition(
                    key="lang", match=models.MatchValue(value=req.filters.lang)
                )
            )
        
        if req.filters and req.filters.page_range and len(req.filters.page_range) == 2:
            pr = req.filters.page_range
            must.append(
                models.FieldCondition(
                    key="page", range=models.Range(gte=int(pr[0]), lte=int(pr[1]))
                )
            )

        qf = models.Filter(must=must)

        # Ejecutar búsqueda híbrida o solo dense según configuración
        hybrid_results = []
        try:
            if req.hybrid_enabled and HYBRID_ENABLED:
                dense_results, sparse_results = _execute_hybrid_search(
                    query, str(d.id), qdrant, qf, k_raw
                )
                
                # Combinar y rerank resultados híbridos
                hybrid_results = _combine_and_rerank_hybrid(
                    dense_results, sparse_results, query, alpha=req.hybrid_alpha
                )
                
                # Debug: mostrar resultados híbridos finales
                print(f"Debug Hybrid Results for doc {str(d.id)}:")
                print(f"  Dense results: {len(dense_results)}")
                print(f"  Sparse results: {len(sparse_results)}")
                print(f"  Hybrid results: {len(hybrid_results)}")
                print(f"  Alpha: {req.hybrid_alpha}")
                for i, p in enumerate(hybrid_results[:3]):
                    payload = p.payload or {}
                    text_preview = (payload.get('text', '')[:100]).replace('\n', ' ')
                    print(f"    Hybrid {i+1}: score={float(p.score or 0.0):.3f}, text='{text_preview}...'")
            else:
                # Fallback a búsqueda solo dense (comportamiento original)
                query_vec = get_query_embedding(query)
                hybrid_results = qdrant.search(
                    collection_name=str(d.id),
                    query_vector=query_vec,
                    query_filter=qf,
                    limit=k_raw,
                    with_payload=True,
                ) or []
        except Exception as hybrid_error:
            print(f"Hybrid search failed for doc {str(d.id)}: {hybrid_error}")
            # Fallback robusto a búsqueda solo dense
            try:
                query_vec = get_query_embedding(query)
                hybrid_results = qdrant.search(
                    collection_name=str(d.id),
                    query_vector=query_vec,
                    query_filter=qf,
                    limit=k_raw,
                    with_payload=True,
                ) or []
                print(f"  Fallback to dense-only search successful: {len(hybrid_results)} results")
            except Exception as fallback_error:
                print(f"  Fallback also failed: {fallback_error}")
                hybrid_results = []
        
        # Aplicar rerank ligero adicional si está habilitado
        if hybrid_results and req.rerank:
            _light_rerank(query, hybrid_results)
        
        all_points.extend(hybrid_results)
    
    latency_ms = int((time.monotonic() - t0) * 1000)
    return ProbeResult(query=query, points=all_points, latency_ms=latency_ms)

def _evaluate_search_state(state: SearchState, req: ChatSemanticRequest) -> StopSignal:
    """
    Evalúa el estado actual y determina si parar el loop
    Señales: Confidence, Coverage, Novelty, Budget, Max Steps
    """
    # 1. Verificar presupuesto
    if state.budget_remaining_ms <= 0:
        return StopSignal.BUDGET_EXHAUSTED
    
    # 2. Verificar max steps
    if state.step >= req.search_loop_max_steps:
        return StopSignal.MAX_STEPS
    
    # 3. Verificar confidence
    confidence_threshold = _get_confidence_threshold(req.confidence_target)
    if state.confidence_score >= confidence_threshold:
        return StopSignal.CONFIDENCE_MET
    
    # 4. Verificar coverage (diversidad)
    if state.coverage_score >= 0.8:  # 80% coverage considerado suficiente
        return StopSignal.COVERAGE_MET
    
    # 5. Verificar novelty (si el último probe no añadió evidencia nueva)
    if state.step > 1 and state.novelty_score < 0.1:
        return StopSignal.NO_NOVELTY
    
    return None  # Continuar

def _calculate_confidence_score(points: List[models.ScoredPoint]) -> float:
    """Calcula score de confianza basado en el mejor resultado"""
    if not points:
        return 0.0
    return max(float(p.score or 0.0) for p in points)

def _calculate_coverage_score(points: List[models.ScoredPoint]) -> float:
    """Calcula score de cobertura basado en diversidad de documentos/páginas"""
    if not points:
        return 0.0
    
    unique_docs = set()
    unique_pages = set()
    unique_types = set()
    
    for p in points:
        payload = p.payload or {}
        unique_docs.add(payload.get("doc_id"))
        unique_pages.add((payload.get("doc_id"), payload.get("page")))
        unique_types.add(payload.get("block_type"))
    
    # Score basado en diversidad
    doc_diversity = min(1.0, len(unique_docs) / 2.0)  # Ideal: 2+ documentos
    page_diversity = min(1.0, len(unique_pages) / 3.0)  # Ideal: 3+ páginas
    type_diversity = min(1.0, len(unique_types) / 2.0)  # Ideal: 2+ tipos
    
    return (doc_diversity + page_diversity + type_diversity) / 3.0

def _calculate_novelty_score(
    new_points: List[models.ScoredPoint], 
    seen_hashes: Set[str]
) -> Tuple[float, Set[str]]:
    """
    Calcula score de novelty y actualiza set de hashes vistos
    Novelty = fracción de chunks nuevos vs. total
    """
    if not new_points:
        return 0.0, seen_hashes
    
    new_hashes = set()
    novel_count = 0
    
    for p in new_points:
        payload = p.payload or {}
        content_hash = payload.get("hash") or _hash_content(payload.get("text", ""))
        new_hashes.add(content_hash)
        
        if content_hash not in seen_hashes:
            novel_count += 1
    
    novelty_score = novel_count / len(new_points) if new_points else 0.0
    updated_hashes = seen_hashes | new_hashes
    
    return novelty_score, updated_hashes

def _hash_content(content: str) -> str:
    """Genera hash de contenido para deduplicación"""
    return hashlib.md5(content.encode('utf-8')).hexdigest()

def _search_r1_loop(
    docs: List[Document], 
    req: ChatSemanticRequest, 
    qdrant: QdrantClient
) -> Tuple[List[models.ScoredPoint], SearchTelemetry]:
    """
    Ejecuta el loop principal de Search-R1-lite
    INIT → PROBE[i] → EVALUATE → (STOP | REFORMULATE)
    """
    t_loop_start = time.monotonic()
    
    # Inicializar estado
    state = SearchState(
        step=0,
        queries_executed=[],
        all_points=[],
        seen_hashes=set(),
        confidence_score=0.0,
        coverage_score=0.0,
        novelty_score=1.0,  # Primer probe siempre tiene novelty
        budget_remaining_ms=req.search_loop_budget_ms,
        stop_signal=None
    )
    
    probe_latencies = []
    queries_per_probe = []
    
    # ────────── Loop principal ──────────
    while state.step < req.search_loop_max_steps and state.budget_remaining_ms > 0:
        step_start = time.monotonic()
        state.step += 1
        
        # Generar consultas para este probe
        if state.step == 1:
            # INIT: consulta original
            queries = [req.message]
        else:
            # REFORMULATE: generar variantes
            queries = _generate_query_variants(req.message, req.probe_fanout)
        
        state.queries_executed.extend(queries)
        queries_per_probe.append(len(queries))
        
        # Ejecutar probes
        step_points: List[models.ScoredPoint] = []
        for query in queries:
            if state.budget_remaining_ms <= 0:
                break
                
            probe_result = _execute_probe(query, docs, req, qdrant)
            step_points.extend(probe_result.points)
            probe_latencies.append(probe_result.latency_ms)
            
            # Actualizar budget
            state.budget_remaining_ms -= probe_result.latency_ms
        
        # Actualizar estado con resultados del step
        state.all_points.extend(step_points)
        
        # Calcular métricas
        state.confidence_score = _calculate_confidence_score(state.all_points)
        state.coverage_score = _calculate_coverage_score(state.all_points)
        state.novelty_score, state.seen_hashes = _calculate_novelty_score(
            step_points, state.seen_hashes
        )
        
        step_duration = int((time.monotonic() - step_start) * 1000)
        state.budget_remaining_ms -= step_duration
        
        # EVALUATE: decidir si parar
        stop_signal = _evaluate_search_state(state, req)
        if stop_signal:
            state.stop_signal = stop_signal
            break
    
    # Construir telemetría
    total_latency = int((time.monotonic() - t_loop_start) * 1000)
    
    exploration_badge = None
    if req.trace_level in ["basic", "full"]:
        exploration_badge = f"Exploration applied ({state.step})"
    
    telemetry = SearchTelemetry(
        executed_steps=state.step,
        queries_per_probe=queries_per_probe,
        queries_executed=state.queries_executed,
        stop_signal=state.stop_signal.value if state.stop_signal else "completed",
        latencies_ms={
            "total_loop": total_latency,
            "probes": probe_latencies,
        },
        final_evidence_count=len(state.all_points),
        exploration_badge=exploration_badge
    )
    
    return state.all_points, telemetry


# ───────────────────────── REX-light Orchestration ─────────────────────────

def _should_trigger_rex(
    kept_points: List[models.ScoredPoint],
    req: ChatSemanticRequest,
    trigger_hint: Optional[str] = None,
) -> Tuple[bool, str]:
    """Evalúa triggers para activar REX-light."""
    if trigger_hint:
        return True, trigger_hint

    if not kept_points:
        return True, "low_confidence_no_evidence"

    # Confidence: solo 1 evidencia borderline
    scores = [float(p.score or 0.0) for p in kept_points]
    if max(scores) < max(req.min_score, 0.5) or len(kept_points) == 1:
        return True, "low_confidence_borderline"

    # Diversidad: >70% del mismo doc o >2 de la misma página
    doc_counts: Dict[str, int] = {}
    page_counts: Dict[Tuple[str, int], int] = {}
    for p in kept_points:
        pl = p.payload or {}
        doc_id = str(pl.get("doc_id"))
        page = int(pl.get("page", 0))
        doc_counts[doc_id] = doc_counts.get(doc_id, 0) + 1
        page_counts[(doc_id, page)] = page_counts.get((doc_id, page), 0) + 1
    total = len(kept_points)
    if total > 0 and max(doc_counts.values()) / total > 0.7:
        return True, "low_diversity_same_doc"
    if any(c > 2 for c in page_counts.values()):
        return True, "low_diversity_same_page"

    return False, "none"


def _apply_rex_light(
    docs: List[Document],
    req: ChatSemanticRequest,
    qdrant: QdrantClient,
    base_points: List[models.ScoredPoint],
    trigger: str,
) -> Tuple[List[models.ScoredPoint], Dict[str, Any]]:
    """Ejecuta 1–2 rondas REX-light dentro de presupuesto y devuelve mejor set."""
    rex_max_rounds = 2
    rex_budget_ms = 400
    rex_probe_fanout = 2

    start = time.monotonic()
    rounds = 0
    variants_tried = 0

    best_points = list(base_points)
    best_cov = _calculate_coverage_score(best_points)
    best_conf = _calculate_confidence_score(best_points)

    remaining_ms = rex_budget_ms

    while rounds < rex_max_rounds and remaining_ms > 0:
        rounds += 1
        t0 = time.monotonic()

        # Variantes según trigger
        if "defin" in trigger or "ambig" in trigger:
            seeds = [f"definición {req.message}", f"glosario {req.message}"]
        elif "low_diversity" in trigger:
            seeds = [f"metodología {req.message}", f"anexo {req.message}"]
        else:
            seeds = [f"tabla {req.message}", f"fórmula {req.message}"]

        # Ejecutar probes
        step_points: List[models.ScoredPoint] = []
        for q in seeds[:rex_probe_fanout]:
            pr = _execute_probe(q, docs, req, qdrant)
            variants_tried += 1
            step_points.extend(pr.points)
            remaining_ms -= pr.latency_ms
            if remaining_ms <= 0:
                break

        # Mezclar + dedupe por hash y rango
        merged = list(best_points) + step_points
        unique_by_hash: Dict[str, models.ScoredPoint] = {}
        for p in merged:
            pl = p.payload or {}
            h = pl.get("hash") or _hash_content(pl.get("text", ""))
            if h not in unique_by_hash or float(p.score or 0.0) > float(unique_by_hash[h].score or 0.0):
                unique_by_hash[h] = p

        candidate_points = list(unique_by_hash.values())
        candidate_points.sort(key=lambda x: float(x.score or 0.0), reverse=True)

        # Aplicar caps globales (per-page, per-doc)
        seen_per_page: Dict[Tuple[str, int], int] = {}
        seen_per_doc: Dict[str, int] = {}
        kept: List[models.ScoredPoint] = []
        for p in candidate_points:
            pl = p.payload or {}
            doc_id = str(pl.get("doc_id"))
            page = int(pl.get("page", 0))
            key = (doc_id, page)
            if seen_per_page.get(key, 0) >= req.per_page_cap:
                continue
            if seen_per_doc.get(doc_id, 0) >= req.per_doc_cap:
                continue
            kept.append(p)
            seen_per_page[key] = seen_per_page.get(key, 0) + 1
            seen_per_doc[doc_id] = seen_per_doc.get(doc_id, 0) + 1
            if len(kept) >= req.evidence_target:
                break

        cov = _calculate_coverage_score(kept)
        conf = _calculate_confidence_score(kept)

        # Si mejora cobertura o confianza, actualizar trayectoria
        improved = (cov > best_cov + 1e-6) or (conf > best_conf + 1e-6)
        if improved:
            best_points, best_cov, best_conf = kept, cov, conf

        step_spent = int((time.monotonic() - t0) * 1000)
        remaining_ms -= step_spent

    spent_ms = int((time.monotonic() - start) * 1000)
    return best_points, {
        "rex_applied": True,
        "rex_rounds": rounds,
        "rex_trigger": trigger,
        "rex_spent_ms": spent_ms,
        "rex_variants_tried": variants_tried,
        "novelty": None,
    }
# ───────────────────────── Endpoint Principal ─────────────────────────

@router.post("/semantic", response_model=ChatSemanticResponse)
def chat_semantic(
    req: ChatSemanticRequest,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """
    Endpoint para consultas semánticas sobre documentos PDF con RAG
    
    Flujo:
    1. Validación de request y carga de documentos del workspace
    2. Generación de embedding de la consulta
    3. Búsqueda vectorial en Qdrant con filtros
    4. Aplicación de caps anti-colapso (por página y documento)
    5. Fusión de chunks contiguos en bloques de evidencia
    6. Re-rank ligero por block_type relevante
    7. Síntesis con LLM y construcción de citas navegables
    """
    t0 = time.monotonic()

    # ────────── 1. Validación y carga de documentos ──────────
    if not req.workspace_id or not req.message:
        raise HTTPException(400, "workspace_id y message son requeridos")

    # Cargar documentos del workspace (solo los listos para chat)
    docs_query = (
        select(Document)
        .where(Document.workspace_id == req.workspace_id)
        .where(Document.status == "ready_for_chat")
        .order_by(Document.created_at.desc())
    )
    if req.filters and req.filters.doc_id:
        docs_query = docs_query.where(Document.id == req.filters.doc_id)
    docs = session.exec(docs_query.limit(DOC_SEARCH_LIMIT)).all()

    # Caso: workspace sin documentos
    if not docs:
        return ChatSemanticResponse(
            answer="No hay documentos en este workspace aún.",
            reasoning=None,
            citations=[],
            meta={
                "workspace_id": req.workspace_id,
                "intent": "semantic",
                "used_model": {
                    "embeddings": EMB_MODEL,
                    "answer": LLM_MODEL,
                    "reasoning_effort": REASONING_EFFORT,
                },
                "latency_ms": int((time.monotonic() - t0) * 1000),
            },
            debug={"reason": "workspace_sin_docs"} if req.include_debug else None,
        )

    # ────────── 2. Search-R1-lite Orchestration ──────────
    qdrant = _get_qdrant()

    # 2a. Como-memory (lectura): usar memoria como seeds suaves
    memory_hits = 0
    memory_items = []
    if COMO_MEMORY_FLAG:
        # Term extraction simple desde el mensaje (tokens >2 chars)
        terms = [t for t in req.message.lower().replace("/", " ").split() if len(t) > 2]
        mem_units = _memory_search(session, workspace_id=req.workspace_id, terms=terms, limit=5)
        if mem_units:
            memory_hits = len(mem_units)
            memory_items = mem_units
    t_search_start = time.monotonic()
    
    # Ejecutar loop de búsqueda inteligente
    all_points, search_telemetry = _search_r1_loop(docs, req, qdrant)
    
    t_search_total = int((time.monotonic() - t_search_start) * 1000)
    
    # ────────── 2b. LlamaIndex MVP Fallback ──────────
    llama_response = None
    llama_metrics: Dict[str, Any] = {}
    llama_trigger_reason: Optional[str] = None
    llama_filters = {
        "doc_id": str(req.filters.doc_id) if req.filters and req.filters.doc_id else None,
        "page_range": req.filters.page_range if req.filters and req.filters.page_range else None,
        "lang": req.filters.lang if req.filters and req.filters.lang else None,
    }
    if USE_LLAMA_MVP and len(all_points) < max(1, req.evidence_target):
        llama_trigger_reason = "initial_low_points"
        llama_response, llama_metrics = _invoke_llama_index(
            req, docs, llama_trigger_reason, llama_filters
        )
    elif USE_LLAMA_MVP:
        print("LlamaIndex fallback not triggered at initial stage (sufficient native points)")
    
    # ────────── DEBUG: Log de Search-R1 ──────────
    print("=== DEBUG SEARCH-R1 RESULTS ===")
    print(f"Total points found: {len(all_points)}")
    print(f"Documents scanned: {[str(d.id) for d in docs]}")
    native_doc_ids = [str((p.payload or {}).get('doc_id')) for p in all_points]
    print(f"Doc IDs in native results: {native_doc_ids[:10]}")
    print(f"Hybrid search enabled: {req.hybrid_enabled and HYBRID_ENABLED}")
    print(f"Hybrid alpha: {req.hybrid_alpha}")
    print(f"Search telemetry: {search_telemetry.__dict__}")
    print(f"Queries executed: {search_telemetry.queries_executed}")
    print(f"Stop signal: {search_telemetry.stop_signal}")
    print("=== END DEBUG SEARCH-R1 ===")

    native_points_available = bool(all_points)
    llama_has_answer = bool(llama_response and getattr(llama_response, "response", None))

    # ────────── 3. Validación de candidatos ──────────
    if not native_points_available and not llama_has_answer:
        return ChatSemanticResponse(
            answer="No encontré evidencia suficiente en tus documentos para responder con confianza.",
            reasoning=None,
            citations=[],
            meta={
                "workspace_id": req.workspace_id,
                "intent": "semantic",
                "used_model": {
                    "embeddings": EMB_MODEL,
                    "answer": LLM_MODEL,
                    "reasoning_effort": REASONING_EFFORT,
                },
                "latency_ms": int((time.monotonic() - t0) * 1000),
                "search_r1": {
                    "executed_steps": search_telemetry.executed_steps,
                    "stop_signal": search_telemetry.stop_signal,
                    "exploration_badge": search_telemetry.exploration_badge,
                }
            },
            debug={
                "reason": "sin_candidatos",
                "search_telemetry": search_telemetry.__dict__,
            }
            if req.include_debug
            else None,
        )

    # ────────── 4. Filtrado duro con deduplicación Search-R1 ──────────
    filtered: List[models.ScoredPoint] = []
    kept: List[models.ScoredPoint] = []
    merged_blocks: List[Dict[str, Any]] = []
    final_blocks: List[Dict[str, Any]] = []
    citations: List[CitationItem] = []
    rex_meta: Dict[str, Any] = {}

    if native_points_available:
        # Deduplicar por hash (de diferentes probes)
        unique_points = {}
        for p in all_points:
            payload = p.payload or {}
            content_hash = payload.get("hash") or _hash_content(payload.get("text", ""))

            # Mantener el mejor score por hash
            if content_hash not in unique_points or float(p.score or 0.0) > float(unique_points[content_hash].score or 0.0):
                unique_points[content_hash] = p

        # Filtro por score mínimo
        filtered = [p for p in unique_points.values() if float(p.score or 0.0) >= req.min_score]

        # Ordenar por score descendente
        filtered.sort(key=lambda p: float(p.score or 0.0), reverse=True)
        # ────────── DEBUG: Log de Qdrant Results ──────────
        print("=== DEBUG QDRANT RESULTS ===")
        print(f"Filtered points count: {len(filtered)}")
        print(f"Top 5 points details:")
        for i, p in enumerate(filtered[:5]):
            payload = p.payload or {}
            print(f"  Point {i+1}:")
            print(f"    Score: {float(p.score or 0.0):.4f}")
            print(f"    Doc ID: {payload.get('doc_id', 'N/A')}")
            print(f"    Page: {payload.get('page', 'N/A')}")
            print(f"    Chunk Seq: {payload.get('chunk_seq', 'N/A')}")
            print(f"    Block Type: {payload.get('block_type', 'N/A')}")
            text_source = payload.get('text_preview') or payload.get('text', '')
            print(f"    Text Preview: {(text_source[:100])}...")
            print()
        print("=== END DEBUG QDRANT RESULTS ===")

        cost_hits = [
            p for p in filtered if _payload_contains_keywords(p.payload or {}, COST_KEYWORDS)
        ]
        print(f"Cost keyword hits in native results: {len(cost_hits)}")
        if not cost_hits:
            print("No cost keywords detected in native evidence")

        if USE_LLAMA_MVP and not llama_response and not cost_hits:
            llama_trigger_reason = llama_trigger_reason or "no_cost_keywords"
            additional_response, additional_metrics = _invoke_llama_index(
                req, docs, llama_trigger_reason, llama_filters
            )
            if additional_response:
                llama_response = additional_response
                llama_metrics = additional_metrics
                print("LlamaIndex fallback delivered response after cost keyword check")
            else:
                print("LlamaIndex fallback did not return response (cost keyword check)")

        # Aplicar caps anti-colapso globales (combinando todos los probes)
        seen_per_page: Dict[Tuple[str, int], int] = {}
        seen_per_doc: Dict[str, int] = {}

        for p in filtered:
            pl = p.payload or {}
            doc_id = str(pl.get("doc_id"))
            page = int(pl.get("page", 0))
            key = (doc_id, page)

            if seen_per_page.get(key, 0) >= req.per_page_cap:
                continue
            if seen_per_doc.get(doc_id, 0) >= req.per_doc_cap:
                continue

            kept.append(p)
            seen_per_page[key] = seen_per_page.get(key, 0) + 1
            seen_per_doc[doc_id] = seen_per_doc.get(doc_id, 0) + 1

            if len(kept) >= req.evidence_target:
                break

        if REX_LIGHT_FLAG and kept:
            trigger_on, trigger_reason = _should_trigger_rex(kept, req)
            if trigger_on:
                kept, rex_meta = _apply_rex_light(docs, req, qdrant, kept, trigger_reason)

        groups: Dict[Tuple[str, int], List[models.ScoredPoint]] = {}
        for p in kept:
            pl = p.payload or {}
            key = (str(pl.get("doc_id")), int(pl.get("page", 0)))
            groups.setdefault(key, []).append(p)

        def group_contiguous(points: List[models.ScoredPoint]) -> List[List[models.ScoredPoint]]:
            points.sort(key=lambda x: int((x.payload or {}).get("chunk_seq", 0)))
            out: List[List[models.ScoredPoint]] = []
            cur: List[models.ScoredPoint] = []
            prev_seq = None

            for it in points:
                seq = int((it.payload or {}).get("chunk_seq", 0))
                if prev_seq is None or seq == prev_seq + 1:
                    cur.append(it)
                else:
                    out.append(cur)
                    cur = [it]
                prev_seq = seq

            if cur:
                out.append(cur)
            return out

        for (doc_id, page), pts in groups.items():
            for chain in group_contiguous(pts):
                title = (chain[0].payload or {}).get("title", "")
                texts = [(c.payload or {}).get("text", "") for c in chain]
                seqs = [int((c.payload or {}).get("chunk_seq", 0)) for c in chain]

                acc = []
                total = 0
                for t in texts:
                    if total + len(t) > 8000:  # Aumentado de 2000 a 8000 caracteres
                        break
                    acc.append(t)
                    total += len(t)

                merged_text = " ".join(acc)
                if not merged_text:
                    continue

                seq_min = min(seqs)
                seq_max = seq_min + len(acc) - 1
                chunk_seq_range = str(seq_min) if seq_min == seq_max else f"{seq_min}–{seq_max}"

                block_score = max(float(c.score or 0.0) for c in chain)

                merged_blocks.append(
                    {
                        "doc_id": doc_id,
                        "title": title,
                        "page": page,
                        "chunk_seq_range": chunk_seq_range,
                        "text": merged_text,
                        "score": block_score,
                    }
                )

        merged_blocks.sort(key=lambda b: float(b["score"]), reverse=True)

        seen_doc_page: set[Tuple[str, int]] = set()
        per_doc_final: Dict[str, int] = {}
        target = min(5, max(3, min(6, len(merged_blocks))))

        unique_docs_in_blocks = set(b["doc_id"] for b in merged_blocks)
        min_docs_target = min(2, len(unique_docs_in_blocks))

        for b in merged_blocks:
            key = (b["doc_id"], b["page"])

            if key in seen_doc_page:
                continue
            if per_doc_final.get(b["doc_id"], 0) >= req.per_doc_cap:
                continue

            final_blocks.append(b)
            seen_doc_page.add(key)
            per_doc_final[b["doc_id"]] = per_doc_final.get(b["doc_id"], 0) + 1

            if len(final_blocks) >= target:
                break

        for b in final_blocks:
            citations.append(
                CitationItem(
                    doc_id=b["doc_id"],
                    title=b["title"],
                    page=b["page"],
                    chunk_seq_range=b["chunk_seq_range"],
                    text_snippet=_extract_relevant_snippet(b["text"], 800),
                    score=float(b["score"]),
                    confidence_badge=_confidence_badge(float(b["score"])),
                    viewer_link=_make_viewer_link(b["doc_id"], b["page"]),
                )
            )

    # ────────── 9. Síntesis con LLM ──────────
    def finalize_response(answer_text: str, reasoning_text: str) -> ChatSemanticResponse:
        nonlocal memory_hits, memory_items, citations, rex_meta, llama_response, llama_metrics, llama_trigger_reason

        answer_clean = answer_text or ""
        reasoning_clean = reasoning_text or ""

        if COMO_MEMORY_FLAG and citations:
            lower_ans = answer_clean.lower()
            if any(k in lower_ans for k in ["definición", "se define", "equivale", "= (", "= "]):
                try:
                    top = citations[0]
                    cue = f"{req.message[:60]} → {answer_clean[:120]}"[:200]
                    evidence_refs = [
                        {
                            "doc_id": top.doc_id,
                            "page": top.page,
                            "chunk_seq_range": top.chunk_seq_range,
                        }
                    ]
                    terms = [t for t in req.message.lower().split() if len(t) > 2][:6]
                    _memory_write(
                        session,
                        workspace_id=req.workspace_id,
                        conversation_id=req.conversation or None,
                        cue=cue,
                        evidence_refs=evidence_refs,
                        terms=terms,
                        doc_hashes=None,
                        step_index=0,
                    )
                except Exception:
                    pass

        meta = {
            "workspace_id": req.workspace_id,
            "intent": "semantic",
            "used_model": {
                "embeddings": EMB_MODEL,
                "answer": LLM_MODEL,
                "reasoning_effort": REASONING_EFFORT,
            },
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "search_r1": {
                "executed_steps": search_telemetry.executed_steps,
                "stop_signal": search_telemetry.stop_signal,
                "exploration_badge": search_telemetry.exploration_badge,
                "queries_executed": len(search_telemetry.queries_per_probe) if search_telemetry.queries_per_probe else 0,
            },
            "hybrid_search": {
                "enabled": req.hybrid_enabled and HYBRID_ENABLED,
                "alpha": req.hybrid_alpha,
                "dense_weight": req.hybrid_alpha,
                "sparse_weight": 1.0 - req.hybrid_alpha,
            },
            "llama_mvp": {
                "enabled": USE_LLAMA_MVP,
                "used": llama_response is not None,
                "metrics": llama_metrics if llama_response else None,
                "trigger_reason": llama_trigger_reason or ("not_used" if USE_LLAMA_MVP else None),
            },
            "rex": rex_meta or {"rex_applied": False},
            "memory": {
                "applied": bool(memory_hits),
                "hits": memory_hits,
            } if COMO_MEMORY_FLAG else {"applied": False},
            "stream": req.stream,
        }

        debug = None
        if req.include_debug:
            debug = {
                "evidence_target": req.evidence_target,
                "min_score": req.min_score,
                "per_page_cap": req.per_page_cap,
                "per_doc_cap": req.per_doc_cap,
                "search_r1_full": search_telemetry.__dict__,
                "timings_ms": {
                    "search_total": t_search_total,
                    "loop_breakdown": search_telemetry.latencies_ms,
                },
                "candidates": [
                    {
                        "doc_id": str((p.payload or {}).get("doc_id")),
                        "page": int((p.payload or {}).get("page", 0)),
                        "chunk_seq": int((p.payload or {}).get("chunk_seq", 0)),
                        "score": float(p.score or 0.0),
                    }
                    for p in filtered[: min(50, len(filtered))]
                ],
            }
            if reasoning_clean:
                debug["gpt5_reasoning"] = reasoning_clean

            if req.include_thinking_summary:
                try:
                    qlist = search_telemetry.queries_executed if getattr(search_telemetry, "queries_executed", None) else []
                    qshow = " | ".join(qlist[:3]) + (" …" if len(qlist) > 3 else "")
                    rex_applied = (rex_meta or {}).get("rex_applied", False)
                    rex_brief = (
                        f"REX:{'on' if rex_applied else 'off'}"
                        + (
                            f" r={ (rex_meta or {}).get('rex_rounds', 0) } trig={(rex_meta or {}).get('rex_trigger','')}"
                            if rex_applied else ""
                        )
                    )
                    mem_brief = f"Memory:{'on' if COMO_MEMORY_FLAG and memory_hits else 'off'}"
                    sources_docs = len({c.doc_id for c in citations})
                    debug["thinking_summary"] = (
                        f"Steps:{search_telemetry.executed_steps}; Stop:{search_telemetry.stop_signal}; "
                        f"Queries:{qshow}; {rex_brief}; {mem_brief}; "
                        f"Evidence:{len(citations)} blocks from {sources_docs} docs"
                    )
                except Exception:
                    pass

        return ChatSemanticResponse(
            answer=answer_clean,
            reasoning=reasoning_clean or None,
            citations=citations,
            meta=meta,
            debug=debug,
        )

    answer = ""
    reasoning_text = ""

    if llama_response and llama_response.response:
        print("Using LlamaIndex response as primary answer")
        answer = llama_response.response
        reasoning_text = f"Generated by LlamaIndex MVP (latency: {llama_metrics.get('latency_ms', 0)}ms)"

        if hasattr(llama_response, 'source_nodes') and llama_response.source_nodes:
            citations = []
            for node in llama_response.source_nodes[:3]:
                metadata = node.metadata or {}
                citations.append(
                    CitationItem(
                        doc_id=metadata.get("doc_id", "unknown"),
                        title=metadata.get("title", "Document"),
                        page=metadata.get("page", 0),
                        chunk_seq_range=str(metadata.get("chunk_seq", 0)),
                        text_snippet=_extract_relevant_snippet(node.text, 800),
                        score=getattr(node, 'score', 0.0),
                        confidence_badge=_confidence_badge(getattr(node, 'score', 0.0)),
                        viewer_link=_make_viewer_link(metadata.get("doc_id", ""), metadata.get("page", 0)),
                    )
                )

        response_model = finalize_response(answer, reasoning_text)

        if req.stream:
            def single_event_stream():
                yield _sse_event({"type": "final", "response": response_model.model_dump()})
                yield "event: done\n\n"

            return StreamingResponse(single_event_stream(), media_type="text/event-stream")

        return response_model

    print("Using native engine for synthesis")
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    except Exception as e:
        print(f"Error preparendo cliente OpenAI: {e}")
        answer = "Evidencia encontrada; no se pudo sintetizar ahora. Revisa las citas para más detalle."
        reasoning_text = ""
        response_model = finalize_response(answer, reasoning_text)
        if req.stream:
            def error_stream():
                yield _sse_event({"type": "error", "message": str(e)})
                yield _sse_event({"type": "final", "response": response_model.model_dump()})
                yield "event: done\n\n"

            return StreamingResponse(error_stream(), media_type="text/event-stream")
        return response_model

    context = "\n\n".join(
        [
            f"[{i+1}] {c.title} · p.{c.page} · c.{c.chunk_seq_range}:\n{c.text_snippet}"
            for i, c in enumerate(citations)
        ]
    )

    prompt = (
        "Responde la pregunta del usuario de forma precisa, basándote en la evidencia que se te proporciona + tu base de conocimiento. "
        "Incluye números o definiciones exactas solo si están en la evidencia. "
        "Si falta evidencia para alguna parte, indícalo.\n\n"
        f"Pregunta: {req.message}\n\nEvidencia:\n{context}"
    )

    print("=== DEBUG FINAL PROMPT TO MODEL ===")
    print(f"Citations count: {len(citations)}")
    print(f"Context length: {len(context)} characters")
    print(f"Full prompt length: {len(prompt)} characters")

    if req.stream:
        request_kwargs = {
            "model": LLM_MODEL,
            "input": prompt,
            "reasoning": {"effort": REASONING_EFFORT, "summary": "auto"},
            "max_output_tokens": 25000,
        }
        return _stream_openai_response(client, request_kwargs, citations, finalize_response, req)

    try:
        resp = client.responses.create(
            model=LLM_MODEL,
            input=prompt,
            reasoning={"effort": REASONING_EFFORT, "summary": "auto"},
            max_output_tokens=25000,
        )
        answer, reasoning_text = _extract_answer_and_reasoning(resp)
    except Exception as e:
        print(f"Error en responses.create con reasoning: {e}")
        try:
            resp = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=500,
            )
            answer = resp.choices[0].message.content.strip()
            reasoning_text = ""
        except Exception as inner_e:
            print(f"Error general en síntesis LLM: {inner_e}")
            answer = "Evidencia encontrada; no se pudo sintetizar ahora. Revisa las citas para más detalle."
            reasoning_text = ""

    response_model = finalize_response(answer, reasoning_text)
    return response_model


# ───────────────────────── AI SDK Compatible Endpoint ─────────────────────────

@router.post("/ai-chat")
async def ai_chat(
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """
    Endpoint compatible con AI SDK frontend usando ai-datastream
    """
    if not AI_DATASTREAM_AVAILABLE:
        raise HTTPException(500, "AI DataStream not available. Install ai-datastream package.")
    
    try:
        body = await request.json()
        messages = body.get("messages", [])
        
        if not messages:
            raise HTTPException(400, "Messages are required")
        
        # Extraer la última pregunta del usuario
        last_message = messages[-1]
        if last_message.get("role") != "user":
            raise HTTPException(400, "Last message must be from user")
        
        question = last_message.get("content", "")
        workspace_id = body.get("workspace_id")
        
        if not workspace_id:
            raise HTTPException(400, "workspace_id is required")
        
        # Usar la lógica existente para obtener citas y contexto
        # TODO: Implementar búsqueda híbrida o usar función existente
        citations = []
        
        if not citations:
            # Si no hay citas, responder directamente
            context = "No se encontró información relevante en los documentos."
        else:
            context = "\n\n".join([
                f"[{i+1}] {c.title} · p.{c.page} · c.{c.chunk_seq_range}:\n{c.text_snippet}"
                for i, c in enumerate(citations)
            ])
        
        # Crear el prompt con el contexto
        system_prompt = (
            "Responde la pregunta del usuario de forma precisa, basándote en la evidencia que se te proporciona + tu base de conocimiento. "
            "Incluye números o definiciones exactas solo si están en la evidencia. "
            "Si falta evidencia para alguna parte, indícalo.\n\n"
            f"Evidencia:\n{context}"
        )
        
        # Configurar el streamer de OpenAI
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
        # Crear el streamer
        streamer = OpenAIChatStreamer(
            client=client,
            model=os.getenv("LLM_MODEL", "gpt-4o"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question}
            ],
            temperature=0.1,
            max_tokens=4000
        )
        
        # Retornar la respuesta compatible con AI SDK
        return AiChatDataStreamAsyncResponse(
            streamer, 
            system_prompt, 
            messages
        )
        
    except Exception as e:
        print(f"Error in AI chat endpoint: {e}")
        raise HTTPException(500, f"Internal server error: {str(e)}")
