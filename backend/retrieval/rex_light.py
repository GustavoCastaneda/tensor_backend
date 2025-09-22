from typing import List, Tuple, Dict, Any
import time

from qdrant_client import models

from backend.models import Document
from backend.retrieval.helpers import hash_content
from backend.retrieval.engine import calculate_confidence_score, calculate_coverage_score
from backend.retrieval.qdrant import execute_probe


def should_trigger_rex(
    kept_points: List[models.ScoredPoint], req: Any, trigger_hint: str | None = None
) -> Tuple[bool, str]:
    if trigger_hint:
        return True, trigger_hint
    if not kept_points:
        return True, "low_confidence_no_evidence"
    scores = [float(p.score or 0.0) for p in kept_points]
    if max(scores) < max(req.min_score, 0.5) or len(kept_points) == 1:
        return True, "low_confidence_borderline"
    doc_counts: Dict[str, int] = {}
    page_counts: Dict[tuple[str, int], int] = {}
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


def apply_rex_light(
    docs: List[Document], req: Any, base_points: List[models.ScoredPoint], trigger: str
) -> Tuple[List[models.ScoredPoint], Dict[str, Any]]:
    rex_max_rounds = 2
    rex_budget_ms = 400
    rex_probe_fanout = 2

    start = time.monotonic()
    rounds = 0
    variants_tried = 0

    best_points = list(base_points)
    best_cov = calculate_coverage_score(best_points)
    best_conf = calculate_confidence_score(best_points)
    remaining_ms = rex_budget_ms

    while rounds < rex_max_rounds and remaining_ms > 0:
        rounds += 1
        t0 = time.monotonic()
        if "defin" in trigger or "ambig" in trigger:
            seeds = [f"definición {req.message}", f"glosario {req.message}"]
        elif "low_diversity" in trigger:
            seeds = [f"metodología {req.message}", f"anexo {req.message}"]
        else:
            seeds = [f"tabla {req.message}", f"fórmula {req.message}"]
        step_points: List[models.ScoredPoint] = []
        for q in seeds[:rex_probe_fanout]:
            pts = execute_probe(q, docs, req.workspace_id, req.rerank, max(1, req.evidence_target * 3))
            variants_tried += 1
            step_points.extend(pts)
        # Deduplicate by hash
        merged = list(best_points) + step_points
        unique_by_hash: Dict[str, models.ScoredPoint] = {}
        for p in merged:
            pl = p.payload or {}
            h = pl.get("hash") or hash_content(pl.get("text", ""))
            if h not in unique_by_hash or float(p.score or 0.0) > float(unique_by_hash[h].score or 0.0):
                unique_by_hash[h] = p
        candidate_points = list(unique_by_hash.values())
        candidate_points.sort(key=lambda x: float(x.score or 0.0), reverse=True)
        # Apply global caps
        seen_per_page: Dict[tuple[str, int], int] = {}
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
        cov = calculate_coverage_score(kept)
        conf = calculate_confidence_score(kept)
        if (cov > best_cov + 1e-6) or (conf > best_conf + 1e-6):
            best_points, best_cov, best_conf = kept, cov, conf
        remaining_ms -= int((time.monotonic() - t0) * 1000)

    spent_ms = int((time.monotonic() - start) * 1000)
    return best_points, {
        "rex_applied": True,
        "rex_rounds": rounds,
        "rex_trigger": trigger,
        "rex_spent_ms": spent_ms,
        "rex_variants_tried": variants_tried,
    }


