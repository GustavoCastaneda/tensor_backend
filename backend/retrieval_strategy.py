# backend/retrieval_strategy.py
"""
Estrategia de Retrieval Inteligente con Anti-Colapso y Fusión
Implementa la estrategia completa de retrieval para evitar inundación de resultados
"""

from typing import List, Dict, Any, Tuple
from dataclasses import dataclass
from collections import defaultdict, Counter
import re
from backend.query_embeddings import get_query_embedding

@dataclass
class RetrievedChunk:
    """Representa un chunk recuperado con metadatos completos"""
    id: str
    workspace_id: str
    doc_id: str
    title: str
    page: int
    chunk_seq: int
    block_type: str
    text: str
    hash: str
    score: float = 0.0

@dataclass
class EvidenceBlock:
    """Bloque de evidencia fusionado para el LLM"""
    doc_id: str
    title: str
    page: int
    chunk_range: str  # "2-3" o "2" si es un solo chunk
    text: str
    citation: str  # "Documento.pdf · p.12 · c.2-3"
    char_count: int

class AdaptiveRetrievalStrategy:
    """Implementa estrategia de retrieval adaptativa basada en el tipo de consulta"""
    
    def __init__(self, max_evidence_chars: int = 2000):
        self.max_evidence_chars = max_evidence_chars
        
        # Límites base (más conservadores)
        self.base_limits = {
            "top_k_initial": 12,
            "max_chunks_per_page": 3,
            "max_chunks_per_doc": 6,
        }
        
        # Límites adaptativos por tipo de consulta
        self.query_limits = {
            "resumen": {
                "top_k_initial": 20,
                "max_chunks_per_page": 5,
                "max_chunks_per_doc": 10,
                "description": "Consultas amplias que requieren cobertura completa"
            },
            "análisis": {
                "top_k_initial": 16,
                "max_chunks_per_page": 4,
                "max_chunks_per_doc": 8,
                "description": "Análisis detallados que necesitan múltiples perspectivas"
            },
            "específico": {
                "top_k_initial": 8,
                "max_chunks_per_page": 2,
                "max_chunks_per_doc": 4,
                "description": "Preguntas puntuales que requieren precisión"
            },
            "búsqueda": {
                "top_k_initial": 15,
                "max_chunks_per_page": 3,
                "max_chunks_per_doc": 6,
                "description": "Búsquedas generales con balance entre cobertura y precisión"
            }
        }
    
    def classify_query(self, query: str) -> str:
        """
        Clasifica el tipo de consulta para aplicar límites adaptativos
        
        Args:
            query: Consulta del usuario
            
        Returns:
            Tipo de consulta: 'resumen', 'análisis', 'específico', 'búsqueda'
        """
        query_lower = query.lower()
        
        # Patrones para consultas de resumen
        resumen_patterns = [
            "resumen", "resumir", "resume", "sumario", "síntesis",
            "puntos principales", "ideas clave", "conclusiones",
            "todo el documento", "documento completo", "información general"
        ]
        
        # Patrones para consultas de análisis
        analisis_patterns = [
            "análisis", "analizar", "analiza", "evaluar", "evaluación",
            "comparar", "comparación", "diferencias", "similitudes",
            "tendencias", "patrones", "correlación", "relación"
        ]
        
        # Patrones para consultas específicas
        especifico_patterns = [
            "qué es", "definición", "significa", "cuánto", "cuándo",
            "dónde", "cómo", "por qué", "quién", "cuál",
            "valor específico", "número exacto", "fecha", "precio"
        ]
        
        # Clasificación por patrones
        if any(pattern in query_lower for pattern in resumen_patterns):
            return "resumen"
        elif any(pattern in query_lower for pattern in analisis_patterns):
            return "análisis"
        elif any(pattern in query_lower for pattern in especifico_patterns):
            return "específico"
        else:
            return "búsqueda"
    
    def get_adaptive_limits(self, query: str) -> Dict[str, int]:
        """
        Obtiene límites adaptativos basados en el tipo de consulta
        
        Args:
            query: Consulta del usuario
            
        Returns:
            Diccionario con límites adaptativos
        """
        query_type = self.classify_query(query)
        limits = self.query_limits[query_type].copy()
        del limits["description"]  # Remover descripción
        return limits
    
    def retrieve_and_merge(self, 
                          query: str, 
                          workspace_id: str,
                          qdrant_client,
                          collection_name: str) -> List[EvidenceBlock]:
        """
        Estrategia adaptativa de retrieval con límites dinámicos
        
        Args:
            query: Consulta del usuario
            workspace_id: ID del workspace
            qdrant_client: Cliente de Qdrant
            collection_name: Nombre de la colección
            
        Returns:
            Lista de bloques de evidencia fusionados
        """
        # 0. Obtener límites adaptativos basados en el tipo de consulta
        limits = self.get_adaptive_limits(query)
        query_type = self.classify_query(query)
        
        print(f"[Retrieval] Tipo de consulta: {query_type}")
        print(f"[Retrieval] Límites: {limits}")
        
        # 1. Retrieval inicial con filtro obligatorio y límites adaptativos
        initial_chunks = self._initial_retrieval(
            query, workspace_id, qdrant_client, collection_name, limits
        )
        
        # 2. Anti-colapso adaptativo
        anti_collapsed = self._apply_anti_collapse(initial_chunks, limits)
        
        # 3. Balance por documento adaptativo
        balanced = self._apply_per_doc_cap(anti_collapsed, limits)
        
        # 4. Re-rank ligero por block_type
        reranked = self._light_rerank(query, balanced)
        
        # 5. Fusión de chunks contiguos
        evidence_blocks = self._merge_contiguous_chunks(reranked)
        
        print(f"[Retrieval] Chunks finales: {len(evidence_blocks)}")
        return evidence_blocks
    
    def _initial_retrieval(self, 
                          query: str, 
                          workspace_id: str,
                          qdrant_client,
                          collection_name: str,
                          limits: Dict[str, int]) -> List[RetrievedChunk]:
        """Retrieval inicial con filtro obligatorio de workspace_id"""
        try:
            # Búsqueda vectorial con filtro de workspace
            search_result = qdrant_client.search(
                collection_name=collection_name,
                query_vector=self._get_query_embedding(query),  # Implementar
                query_filter={
                    "must": [
                        {"key": "workspace_id", "match": {"value": workspace_id}}
                    ]
                },
                limit=limits["top_k_initial"],
                with_payload=True
            )
            
            chunks = []
            for result in search_result:
                payload = result.payload
                chunks.append(RetrievedChunk(
                    id=result.id,
                    workspace_id=payload["workspace_id"],
                    doc_id=payload["doc_id"],
                    title=payload["title"],
                    page=payload["page"],
                    chunk_seq=payload["chunk_seq"],
                    block_type=payload["block_type"],
                    text=payload["text"],
                    hash=payload["hash"],
                    score=result.score
                ))
            
            return chunks
            
        except Exception as e:
            print(f"Error en retrieval inicial: {e}")
            return []
    
    def _apply_anti_collapse(self, chunks: List[RetrievedChunk], limits: Dict[str, int]) -> List[RetrievedChunk]:
        """Anti-colapso adaptativo: límite dinámico de chunks por página"""
        page_groups = defaultdict(list)
        
        # Agrupar por página
        for chunk in chunks:
            page_groups[chunk.page].append(chunk)
        
        # Seleccionar chunks por página usando límite adaptativo
        selected = []
        for page, page_chunks in page_groups.items():
            # Ordenar por score descendente
            page_chunks.sort(key=lambda x: x.score, reverse=True)
            # Tomar máximo según límite adaptativo
            selected.extend(page_chunks[:limits["max_chunks_per_page"]])
        
        return selected
    
    def _apply_per_doc_cap(self, chunks: List[RetrievedChunk], limits: Dict[str, int]) -> List[RetrievedChunk]:
        """Balance por documento adaptativo: límite dinámico de chunks por documento"""
        doc_groups = defaultdict(list)
        
        # Agrupar por documento
        for chunk in chunks:
            doc_groups[chunk.doc_id].append(chunk)
        
        # Seleccionar chunks por documento usando límite adaptativo
        selected = []
        for doc_id, doc_chunks in doc_groups.items():
            # Ordenar por score descendente
            doc_chunks.sort(key=lambda x: x.score, reverse=True)
            # Tomar máximo según límite adaptativo
            selected.extend(doc_chunks[:limits["max_chunks_per_doc"]])
        
        return selected
    
    def _light_rerank(self, query: str, chunks: List[RetrievedChunk]) -> List[RetrievedChunk]:
        """Re-rank ligero favoreciendo block_type relevante a la consulta"""
        query_lower = query.lower()
        
        # Detectar tipo de consulta
        if any(word in query_lower for word in ["definición", "qué es", "significa", "concepto"]):
            preferred_types = ["text"]
        elif any(word in query_lower for word in ["cálculo", "calcular", "fórmula", "ecuación"]):
            preferred_types = ["formula", "table"]
        elif any(word in query_lower for word in ["tabla", "datos", "números", "estadísticas"]):
            preferred_types = ["table", "figure"]
        else:
            preferred_types = ["text", "table", "formula"]
        
        # Aplicar boost a tipos preferidos
        for chunk in chunks:
            if chunk.block_type in preferred_types:
                chunk.score *= 1.2  # 20% boost
        
        # Re-ordenar por score
        chunks.sort(key=lambda x: x.score, reverse=True)
        return chunks
    
    def _merge_contiguous_chunks(self, chunks: List[RetrievedChunk]) -> List[EvidenceBlock]:
        """Fusiona chunks contiguos en bloques de evidencia"""
        # Agrupar por documento y página
        groups = defaultdict(lambda: defaultdict(list))
        for chunk in chunks:
            groups[chunk.doc_id][chunk.page].append(chunk)
        
        evidence_blocks = []
        
        for doc_id, pages in groups.items():
            for page, page_chunks in pages.items():
                # Ordenar por chunk_seq
                page_chunks.sort(key=lambda x: x.chunk_seq)
                
                # Fusionar chunks contiguos
                merged_groups = self._group_contiguous_chunks(page_chunks)
                
                for group in merged_groups:
                    if group:
                        evidence_block = self._create_evidence_block(group)
                        if evidence_block:
                            evidence_blocks.append(evidence_block)
        
        return evidence_blocks
    
    def _group_contiguous_chunks(self, chunks: List[RetrievedChunk]) -> List[List[RetrievedChunk]]:
        """Agrupa chunks contiguos por chunk_seq"""
        if not chunks:
            return []
        
        groups = []
        current_group = [chunks[0]]
        
        for i in range(1, len(chunks)):
            if chunks[i].chunk_seq == chunks[i-1].chunk_seq + 1:
                # Chunk contiguo, agregar al grupo actual
                current_group.append(chunks[i])
            else:
                # No contiguo, finalizar grupo actual y empezar nuevo
                groups.append(current_group)
                current_group = [chunks[i]]
        
        groups.append(current_group)
        return groups
    
    def _create_evidence_block(self, chunks: List[RetrievedChunk]) -> EvidenceBlock:
        """Crea un bloque de evidencia fusionado"""
        if not chunks:
            return None
        
        # Fusionar texto
        merged_text = " ".join(chunk.text for chunk in chunks)
        
        # Crear rango de chunks
        if len(chunks) == 1:
            chunk_range = str(chunks[0].chunk_seq)
        else:
            chunk_range = f"{chunks[0].chunk_seq}-{chunks[-1].chunk_seq}"
        
        # Crear cita
        citation = f"{chunks[0].title} · p.{chunks[0].page} · c.{chunk_range}"
        
        return EvidenceBlock(
            doc_id=chunks[0].doc_id,
            title=chunks[0].title,
            page=chunks[0].page,
            chunk_range=chunk_range,
            text=merged_text,
            citation=citation,
            char_count=len(merged_text)
        )
    
    def _get_query_embedding(self, query: str) -> List[float]:
        """Obtiene el embedding de la consulta"""
        return get_query_embedding(query)
