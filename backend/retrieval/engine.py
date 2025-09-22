from typing import List, Tuple, Dict, Any, Set
import time

from qdrant_client import models

from backend.models import Document
from backend.retrieval.qdrant import execute_probe


def get_confidence_threshold(target: str) -> float:
    thresholds = {"low": 0.4, "medium": 0.6, "high": 0.75}
    return thresholds.get(target, 0.6)


def calculate_confidence_score(points: List[models.ScoredPoint]) -> float:
    if not points:
        return 0.0
    return max(float(p.score or 0.0) for p in points)


def calculate_coverage_score(points: List[models.ScoredPoint]) -> float:
    if not points:
        return 0.0
    unique_docs = set()
    unique_pages = set()
    unique_types = set()
    for p in points:
        pl = p.payload or {}
        unique_docs.add(pl.get("doc_id"))
        unique_pages.add((pl.get("doc_id"), pl.get("page")))
        unique_types.add(pl.get("block_type"))
    doc_div = min(1.0, len(unique_docs) / 2.0)
    page_div = min(1.0, len(unique_pages) / 3.0)
    type_div = min(1.0, len(unique_types) / 2.0)
    return (doc_div + page_div + type_div) / 3.0


def calculate_novelty_score(
    new_points: List[models.ScoredPoint], seen_hashes: Set[str]
) -> Tuple[float, Set[str]]:
    from backend.retrieval.helpers import hash_content

    if not new_points:
        return 0.0, seen_hashes
    new_hashes = set()
    novel = 0
    for p in new_points:
        pl = p.payload or {}
        h = pl.get("hash") or hash_content(pl.get("text", ""))
        new_hashes.add(h)
        if h not in seen_hashes:
            novel += 1
    return (novel / len(new_points)), (seen_hashes | new_hashes)


def search_r1_loop(
    docs: List[Document], req: Any
) -> Tuple[List[models.ScoredPoint], Dict[str, Any]]:
    t_loop = time.monotonic()

    steps = 0
    queries_per_probe: List[int] = []
    probe_latencies: List[int] = []
    all_points: List[models.ScoredPoint] = []
    seen_hashes: Set[str] = set()
    budget = req.search_loop_budget_ms

    while steps < req.search_loop_max_steps and budget > 0:
        steps += 1
        queries = [req.message] if steps == 1 else [req.message]  # simple reuse
        queries_per_probe.append(len(queries))

        step_pts: List[models.ScoredPoint] = []
        for q in queries:
            t0 = time.monotonic()
            pts = execute_probe(q, docs, req.workspace_id, req.rerank, max(1, req.evidence_target * 3))
            probe_latencies.append(int((time.monotonic() - t0) * 1000))
            step_pts.extend(pts)
            budget -= probe_latencies[-1]
            if budget <= 0:
                break

        all_points.extend(step_pts)
        # Early stop if confidence met
        if calculate_confidence_score(all_points) >= get_confidence_threshold(req.confidence_target):
            break

    telemetry = {
        "executed_steps": steps,
        "queries_per_probe": queries_per_probe,
        "stop_signal": "completed",
        "latencies_ms": {"total_loop": int((time.monotonic() - t_loop) * 1000), "probes": probe_latencies},
        "final_evidence_count": len(all_points),
        "exploration_badge": f"Exploration applied ({steps})" if steps > 1 else None,
    }
    return all_points, telemetry


