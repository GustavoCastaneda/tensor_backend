# backend/document_router.py
import os
from typing import Tuple
from redis import Redis
from rq import Queue

from backend.parsers.formula_detector import detect_formulas_in_document

# Configurar conexión Redis
redis_conn = Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))

# Colas separadas para servicios light y heavy
q_light = Queue("doc_parse_light", connection=redis_conn)
q_heavy = Queue("doc_parse_heavy", connection=redis_conn)

def route_document_processing(doc_id: str, buffer: bytes, ext: str) -> Tuple[str, str, list]:
    """
    Enruta el documento al servicio apropiado basado en la detección de fórmulas.
    
    Ambos servicios usan Docling, pero:
    - Light: Docling SIN formula enrichment (más rápido)
    - Heavy: Docling CON formula enrichment (más completo)
    
    Args:
        doc_id: ID del documento
        buffer: Contenido del documento en bytes
        ext: Extensión del archivo (pdf, docx)
        
    Returns:
        Tuple[str, str, list]: (queue_name, service_type, detected_patterns)
    """
    print(f"[router] Analyzing document {doc_id} ({ext}) for formula detection...")
    
    try:
        # Detectar fórmulas en el documento
        has_formulas, detected_patterns = detect_formulas_in_document(buffer, ext)
        
        if has_formulas:
            print(f"[router] Document {doc_id} has formulas: {detected_patterns[:3]}")
            print(f"[router] Routing to HEAVY service (Docling WITH formula enrichment)")
            
            # Encolar en servicio pesado
            job = q_heavy.enqueue(
                "backend.ingest_document_heavy.process_document_heavy",
                doc_id,
                job_timeout="45m",  # Más tiempo para procesamiento pesado
                result_ttl=500,
            )
            
            return "doc_parse_heavy", "heavy", detected_patterns
            
        else:
            print(f"[router] Document {doc_id} has no formulas detected")
            print(f"[router] Routing to LIGHT service (Docling WITHOUT formula enrichment)")
            
            # Encolar en servicio ligero
            job = q_light.enqueue(
                "backend.ingest_document_light.process_document_light",
                doc_id,
                job_timeout="10m",   # Menos tiempo para procesamiento ligero
                result_ttl=500,
            )
            
            return "doc_parse_light", "light", detected_patterns
            
    except Exception as e:
        print(f"[router] Error routing document {doc_id}: {e}")
        print(f"[router] Defaulting to HEAVY service for safety")
        
        # En caso de error, usar servicio pesado (más seguro)
        job = q_heavy.enqueue(
            "backend.ingest_document_heavy.process_document_heavy",
            doc_id,
            job_timeout="45m",
            result_ttl=500,
        )
        
        return "doc_parse_heavy", "heavy", [f"Routing error: {str(e)}"]

def get_queue_stats() -> dict:
    """
    Obtiene estadísticas de las colas para monitoreo.
    
    Returns:
        dict: Estadísticas de ambas colas
    """
    try:
        light_count = len(q_light)
        heavy_count = len(q_heavy)
        
        return {
            "light_queue": {
                "name": "doc_parse_light",
                "pending_jobs": light_count,
                "service_type": "light (Docling without formula enrichment)"
            },
            "heavy_queue": {
                "name": "doc_parse_heavy", 
                "pending_jobs": heavy_count,
                "service_type": "heavy (Docling with formula enrichment)"
            },
            "total_pending": light_count + heavy_count
        }
    except Exception as e:
        print(f"[router] Error getting queue stats: {e}")
        return {"error": str(e)}
