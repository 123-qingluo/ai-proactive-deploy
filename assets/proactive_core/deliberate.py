"""审议：在通过 Gate 的候选里选一个意图；人际优先于计划性。"""
from __future__ import annotations

from .models import PLANNED_KINDS, REPAIR_KINDS, Candidate


def choose(candidates: list[Candidate]) -> Candidate | None:
    if not candidates:
        return None
    has_relationship = any(c.kind in REPAIR_KINDS for c in candidates)
    for candidate in candidates:
        bdi = candidate.score * (0.60 + 0.40 * candidate.priority)
        bdi *= 1 - min(0.35, candidate.face_risk * 0.35)
        if has_relationship and candidate.kind in PLANNED_KINDS:
            bdi *= 0.5
        candidate.bdi = round(bdi, 6)
    candidates.sort(key=lambda c: (-c.bdi, -c.priority, c.desire.created_at))
    return candidates[0]
