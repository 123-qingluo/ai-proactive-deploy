"""许可（Gate）：硬性方向修正与软评分。"""
from __future__ import annotations

from .models import NEW_TOPIC_KINDS, Candidate, Desire, State
from .perceive import Belief


class GateService:
    def __init__(self, cfg: dict, store):
        self.cfg = cfg
        self.store = store
        self.gate = cfg.get("gate", {})

    def score(self, desire: Desire) -> float:
        rel = float(desire.confidence)
        urg = float(desire.priority)
        exp = max(0.2, 1.0 - 0.5 * float(desire.face_risk))
        cost = float(desire.face_risk)
        raw = 0.30 * rel + 0.25 * urg + 0.20 * exp + 0.25 - 0.20 * cost
        return max(0.0, min(1.0, raw))

    def filter(self, desires: list[Desire], belief: Belief, state: State, manual: bool = False):
        self.gate = self.cfg.get("gate", {})
        threshold = float(self.gate.get("score_threshold", 0.5))
        min_conf = float(self.gate.get("min_confidence", 0.55))
        max_face = float(self.gate.get("max_face_risk", 0.65))
        min_silence = float(self.gate.get("new_topic_user_silence_minutes", 60)) * 60
        min_interval = float(self.gate.get("min_interval_minutes", 120)) * 60
        overrides = self.gate.get("min_interval_by_kind", {}) or {}
        kind_caps = self.gate.get("daily_kind_caps", {}) or {}
        day = belief.day
        candidates: list[Candidate] = []
        blocked: dict[str, int] = {}

        def block(reason: str) -> None:
            blocked[reason] = blocked.get(reason, 0) + 1

        for desire in desires:
            if desire.confidence < min_conf:
                block("low_confidence")
                continue
            if desire.face_risk > max_face:
                block("face_risk")
                continue
            if desire.expires_at and desire.expires_at <= belief.now:
                block("expired")
                continue

            if not manual and desire.class_ != "system":
                if state.closed_until > belief.now:
                    block("silence")
                    continue
                if state.quiet_until > belief.now:
                    block("silence")
                    continue
                if not belief.window_open:
                    block("outside_window")
                    continue
                if desire.kind in NEW_TOPIC_KINDS and belief.user_silence < min_silence:
                    block("new_topic_too_soon")
                    continue
                if desire.class_ == "social" and state.last_social_bot_at:
                    interval = float(overrides.get(desire.kind, min_interval))
                    interval *= max(1.0, float(state.pace))
                    if belief.now - state.last_social_bot_at < interval:
                        block("min_interval")
                        continue
                quota = int(self.cfg.get("dispatch", {}).get("quotas", {}).get(desire.class_, -1))
                if quota == 0:
                    block("quota")
                    continue
                if quota > 0:
                    count, _bubbles = self.store.quota_count(state.stream_id, day, desire.class_)
                    if count >= quota:
                        block("quota")
                        continue
                cap = int(kind_caps.get(desire.kind, -1))
                if cap == 0 or (cap > 0 and self.store.count_sent_kind(state.stream_id, day, desire.kind) >= cap):
                    block("kind_cap")
                    continue

            raw = self.score(desire)
            score = raw * max(0.0, min(1.0, float(state.decay)))
            if score < threshold:
                block("score")
                continue
            candidates.append(Candidate(desire=desire, score=round(score, 4)))

        return candidates, blocked
