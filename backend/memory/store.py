import json
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from sqlmodel import Session, select

from backend.models import MemoryUnit


def write_memory(
    session: Session,
    *,
    workspace_id: str,
    conversation_id: Optional[str],
    cue: str,
    evidence_refs: List[Dict[str, Any]],
    terms: List[str],
    doc_hashes: Optional[List[str]] = None,
    step_index: Optional[int] = 0,
) -> MemoryUnit:
    unit = MemoryUnit(
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        cue=cue[:200],
        evidence_refs_json=json.dumps(evidence_refs, ensure_ascii=False),
        terms_json=json.dumps([t.lower() for t in terms], ensure_ascii=False),
        doc_hashes_json=json.dumps(doc_hashes or []),
        step_index=step_index or 0,
        is_valid=True,
    )
    session.add(unit)
    session.commit()
    session.refresh(unit)
    return unit


def search_memory(
    session: Session,
    *,
    workspace_id: str,
    terms: List[str],
    limit: int = 5,
) -> List[MemoryUnit]:
    q = select(MemoryUnit).where(
        MemoryUnit.workspace_id == workspace_id, MemoryUnit.is_valid == True
    ).order_by(MemoryUnit.created_at.desc())
    units = session.exec(q).all()
    if not units:
        return []
    needle = {t.lower() for t in terms if t}
    scored: List[tuple[int, MemoryUnit]] = []
    for u in units:
        unit_terms = set(json.loads(u.terms_json or "[]"))
        overlap = len(needle & unit_terms)
        if overlap > 0:
            scored.append((overlap, u))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [u for _, u in scored[:limit]]


def invalidate_by_doc_hash(
    session: Session, *, workspace_id: str, doc_hash: str
) -> int:
    q = select(MemoryUnit).where(MemoryUnit.workspace_id == workspace_id)
    units = session.exec(q).all()
    count = 0
    for u in units:
        hashes = set(json.loads(u.doc_hashes_json or "[]"))
        if doc_hash in hashes and u.is_valid:
            u.is_valid = False
            count += 1
    if count:
        session.commit()
    return count


def gc_memory(
    session: Session, *, workspace_id: str, max_items: int = 50, ttl_days: int = 30
) -> int:
    q = select(MemoryUnit).where(MemoryUnit.workspace_id == workspace_id).order_by(
        MemoryUnit.created_at.desc()
    )
    units = session.exec(q).all()
    now = datetime.utcnow()
    ttl_threshold = now - timedelta(days=ttl_days)
    to_invalidate: List[MemoryUnit] = []
    for idx, u in enumerate(units):
        if u.created_at < ttl_threshold:
            to_invalidate.append(u)
        elif idx >= max_items:
            to_invalidate.append(u)
    for u in to_invalidate:
        if u.is_valid:
            u.is_valid = False
    if to_invalidate:
        session.commit()
    return len(to_invalidate)



