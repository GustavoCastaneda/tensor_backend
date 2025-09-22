"""LlamaIndex MVP integration for Qdrant fallback."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from llama_index.core import Document, Settings, StorageContext, SummaryIndex, VectorStoreIndex
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.response import Response
from llama_index.core.retrievers import SummaryIndexRetriever, VectorIndexRetriever
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.llms.openai import OpenAI
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.http import models


@dataclass
class LlamaIndexConfig:
    qdrant_url: str = os.getenv("QDRANT_URL", "http://qdrant:6333")
    qdrant_timeout: float = float(os.getenv("QDRANT_TIMEOUT_SECONDS", "10.0"))
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    embedding_model: str = os.getenv("EMBEDDINGS_MODEL", "text-embedding-3-small")
    llm_model: str = os.getenv("ANSWER_MODEL", "gpt-5")
    max_tokens: int = int(os.getenv("LLAMA_MAX_TOKENS", "500"))
    similarity_top_k: int = int(os.getenv("LLAMA_SIMILARITY_TOP_K", "5"))
    summary_top_k: int = int(os.getenv("LLAMA_SUMMARY_TOP_K", "3"))


class LlamaIndexMVP:
    def __init__(self, config: Optional[LlamaIndexConfig] = None) -> None:
        self.config = config or LlamaIndexConfig()
        self.qdrant_client: Optional[QdrantClient] = None
        self.vector_store: Optional[QdrantVectorStore] = None
        self.vector_index: Optional[VectorStoreIndex] = None
        self.summary_index: Optional[SummaryIndex] = None
        self._setup_llama_index()

    def _setup_llama_index(self) -> None:
        self.qdrant_client = QdrantClient(
            url=self.config.qdrant_url,
            timeout=self.config.qdrant_timeout,
        )
        Settings.embed_model = OpenAIEmbedding(
            model=self.config.embedding_model,
            api_key=self.config.openai_api_key,
        )
        Settings.llm = OpenAI(
            model=self.config.llm_model,
            api_key=self.config.openai_api_key,
            max_tokens=self.config.max_tokens,
        )

    def _create_documents_from_qdrant(
        self, workspace_id: str, doc_ids: Optional[List[str]] = None
    ) -> List[Document]:
        if self.qdrant_client is None:
            raise RuntimeError("Qdrant client not configured")
        documents: List[Document] = []
        collections = self.qdrant_client.get_collections()
        for collection in collections.collections:
            if doc_ids and collection.name not in doc_ids:
                continue
            points, _ = self.qdrant_client.scroll(
                collection_name=collection.name,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="workspace_id",
                            match=models.MatchValue(value=workspace_id),
                        )
                    ]
                ),
                limit=1000,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                text = payload.get("text", "")
                if not text or len(text.strip()) < 10:
                    continue
                documents.append(
                    Document(
                        text=text,
                        metadata={
                            "doc_id": collection.name,
                            "workspace_id": workspace_id,
                            "page": payload.get("page", 0),
                            "chunk_seq": payload.get("chunk_seq", 0),
                            "block_type": payload.get("block_type", "text"),
                            "title": payload.get("title", ""),
                            "hash": payload.get("hash", ""),
                            "lang": payload.get("lang", "es"),
                            "qdrant_id": str(point.id),
                        },
                    )
                )
        return documents

    def build_indexes(self, workspace_id: str, doc_ids: Optional[List[str]] = None) -> bool:
        documents = self._create_documents_from_qdrant(workspace_id, doc_ids)
        if not documents:
            return False
        self.vector_store = QdrantVectorStore(
            client=self.qdrant_client,
            collection_name=f"llama_index_{workspace_id}",
            embed_dim=1536,
        )
        storage_context = StorageContext.from_defaults(vector_store=self.vector_store)
        self.vector_index = VectorStoreIndex.from_documents(
            documents,
            storage_context=storage_context,
            show_progress=True,
        )
        self.summary_index = SummaryIndex.from_documents(
            documents,
            show_progress=True,
        )
        return True

    def _create_qdrant_filter(
        self, filters: Dict[str, Any], workspace_id: str
    ) -> Optional[models.Filter]:
        must_conditions = [
            models.FieldCondition(
                key="workspace_id",
                match=models.MatchValue(value=workspace_id),
            )
        ]
        doc_id = filters.get("doc_id")
        if doc_id:
            must_conditions.append(
                models.FieldCondition(
                    key="doc_id", match=models.MatchValue(value=doc_id)
                )
            )
        page_range = filters.get("page_range")
        if page_range and len(page_range) == 2:
            must_conditions.append(
                models.FieldCondition(
                    key="page",
                    range=models.Range(gte=int(page_range[0]), lte=int(page_range[1])),
                )
            )
        lang = filters.get("lang")
        if lang:
            must_conditions.append(
                models.FieldCondition(
                    key="lang", match=models.MatchValue(value=lang)
                )
            )
        return models.Filter(must=must_conditions)

    def query(
        self,
        query: str,
        workspace_id: str,
        response_mode: str = "compact",
        similarity_top_k: Optional[int] = None,
        summary_top_k: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Optional[Response], Dict[str, Any]]:
        if not self.vector_index or not self.summary_index:
            raise RuntimeError("Indexes not built. Call build_indexes() first.")
        start = time.monotonic()
        vector_kwargs = {}
        if filters:
            qdrant_filter = self._create_qdrant_filter(filters, workspace_id)
            if qdrant_filter:
                vector_kwargs["filter"] = qdrant_filter
        vector_retriever = VectorIndexRetriever(
            index=self.vector_index,
            similarity_top_k=similarity_top_k or self.config.similarity_top_k,
            vector_store_kwargs=vector_kwargs if vector_kwargs else None,
        )
        summary_retriever = SummaryIndexRetriever(
            index=self.summary_index,
            summary_top_k=summary_top_k or self.config.summary_top_k,
        )
        query_engine = RetrieverQueryEngine(
            retriever=vector_retriever,
            response_mode=response_mode,
            node_postprocessors=[],
        )
        response = query_engine.query(query)
        latency_ms = int((time.monotonic() - start) * 1000)
        source_nodes = []
        if hasattr(response, "source_nodes"):
            source_nodes = [
                {
                    "text": node.text[:200] + "..." if len(node.text) > 200 else node.text,
                    "score": getattr(node, "score", 0.0),
                    "metadata": node.metadata,
                }
                for node in response.source_nodes
            ]
        metrics = {
            "latency_ms": latency_ms,
            "retrieved_nodes_count": len(source_nodes),
            "response_length": len(response.response) if response and response.response else 0,
            "model_used": self.config.llm_model,
            "embedding_model": self.config.embedding_model,
            "similarity_top_k": similarity_top_k or self.config.similarity_top_k,
            "summary_top_k": summary_top_k or self.config.summary_top_k,
        }
        return response, metrics


_LLAMA_INSTANCE: Optional[LlamaIndexMVP] = None


def get_llama_mvp() -> LlamaIndexMVP:
    global _LLAMA_INSTANCE
    if _LLAMA_INSTANCE is None:
        _LLAMA_INSTANCE = LlamaIndexMVP()
    return _LLAMA_INSTANCE
