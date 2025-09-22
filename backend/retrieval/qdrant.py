import os
from typing import List

from qdrant_client import QdrantClient, models

from backend.models import Document
from backend.query_embeddings import get_query_embedding
from backend.retrieval.helpers import light_rerank


def get_qdrant() -> QdrantClient:
    return QdrantClient(url=os.getenv("QDRANT_URL", "http://qdrant:6333"))


def execute_probe(
    query: str,
    docs: List[Document],
    workspace_id: str,
    rerank: bool,
    k_raw: int,
) -> List[models.ScoredPoint]:
    query_vec = get_query_embedding(query)
    qc = get_qdrant()
    all_points: List[models.ScoredPoint] = []
    for d in docs:
        must = [
            models.FieldCondition(
                key="workspace_id", match=models.MatchValue(value=workspace_id)
            )
        ]
        qf = models.Filter(must=must)
        try:
            res = qc.search(
                collection_name=str(d.id),
                query_vector=query_vec,
                query_filter=qf,
                limit=k_raw,
                with_payload=True,
            )
        except Exception:
            res = []
        if res and rerank:
            light_rerank(query, res)
        all_points.extend(res or [])
    return all_points


