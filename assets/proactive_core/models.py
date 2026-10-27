"""领域模型：状态、愿望、候选、意图、决策结果。"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

DESIRE_DEFAULTS = {
    "D1": {"priority": 0.90, "face": 0.42, "class": "social", "budget": "relationship", "new_topic": False},
    "D2": {"priority": 0.92, "face": 0.34, "class": "social", "budget": "open_loop", "new_topic": True},
    "D3": {"priority": 0.78, "face": 0.34, "class": "social", "budget": "open_loop", "new_topic": True},
    "D4": {"priority": 0.72, "face": 0.30, "class": "social", "budget": "relationship", "new_topic": False},
    "D5": {"priority": 0.68, "face": 0.28, "class": "social", "budget": "relationship", "new_topic": False},
    "D6": {"priority": 0.62, "face": 0.24, "class": "social", "budget": "recall", "new_topic": True},
    "D7": {"priority": 0.55, "face": 0.45, "class": "social", "budget": "relationship", "new_topic": False},
    "D8": {"priority": 0.35, "face": 0.16, "class": "social", "budget": "idle", "new_topic": True},
    "D9": {"priority": 0.50, "face": 0.20, "class": "scheduled", "budget": "scheduled", "new_topic": True},
}
NEW_TOPIC_KINDS = {"D2", "D3", "D6", "D8", "D9"}
REPAIR_KINDS = {"D1", "D4", "D5", "D7"}
PLANNED_KINDS = {"D9"}
BUDGET_CLASSES = ("relationship", "open_loop", "recall", "idle", "scheduled")


def now_ts() -> float:
    return time.time()


def new_id(prefix: str) -> str:
    return f"{prefix}-" + uuid.uuid4().hex[:12]


def empty_daily() -> dict:
    return {
        "date": "",
        "sent": {},
        "desire_counts": {},
        "timing_calls": 0,
        "timing_by_class": {},
        "hourly": {},
    }


@dataclass
class State:
    stream_id: str = "default"
    last_user_at: float = 0.0
    last_social_bot_at: float = 0.0
    last_bot_any_at: float = 0.0
    last_reply_at: float = 0.0
    last_proactive_at: float = 0.0
    last_system_bot_at: float = 0.0
    last_scheduled_bot_at: float = 0.0
    last_user_text: str = ""
    last_bot_text: str = ""
    expects_reply: bool = False
    expects_since: float = 0.0
    follow_up_count: int = 0
    owed_reply: bool = False
    owed_since: float = 0.0
    closed_until: float = 0.0
    quiet_until: float = 0.0
    decay: float = 1.0
    pace: float = 1.0
    idle_anchor: float = 0.0
    relation_anxiety: float = 0.0
    relation_shame: float = 0.0
    relation_updated_at: float = 0.0
    relation_explicit: bool = False
    shadow_until: float = 0.0
    shadow_reason: str = ""
    daily: dict = field(default_factory=empty_daily)
    pending_feedback: dict | None = None
    recent_feedback: list = field(default_factory=list)
    recent_user_ts: list = field(default_factory=list)
    last_feedback: dict = field(default_factory=dict)
    last_tick_at: float = 0.0
    last_recall_score: float = 0.0
    last_recall_title: str = ""
    fallback_streak: int = 0
    forced_rules_until: float = 0.0

    def ensure_day(self, day: str) -> None:
        daily = self.daily or empty_daily()
        if daily.get("date") != day:
            self.daily = empty_daily()
            self.daily["date"] = day
        else:
            self.daily = daily
        for key, default in empty_daily().items():
            self.daily.setdefault(key, default)

    def trim_feedback(self, now: float) -> None:
        self.recent_feedback = [
            item for item in self.recent_feedback
            if isinstance(item, dict) and now - float(item.get("ts", 0)) <= 48 * 3600
        ][-30:]
        self.recent_user_ts = [
            float(ts) for ts in self.recent_user_ts
            if now - float(ts) <= 3600
        ][-20:]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict, stream_id: str = "default") -> "State":
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        payload = {k: v for k, v in (data or {}).items() if k in allowed}
        payload["stream_id"] = payload.get("stream_id") or stream_id
        state = cls(**payload)
        if not state.daily:
            state.daily = empty_daily()
        return state


@dataclass
class Desire:
    kind: str
    evidence: str
    evidence_type: str
    priority: float
    face_risk: float
    confidence: float
    reason: str = ""
    class_: str = "social"
    quota_class: str = "social"
    created_at: float = 0.0
    expires_at: float = 0.0
    due_at: float = 0.0
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Candidate:
    desire: Desire
    score: float
    bdi: float = 0.0

    @property
    def kind(self) -> str:
        return self.desire.kind

    def __getattr__(self, item):
        return getattr(self.desire, item)


@dataclass
class Intent:
    intent_id: str
    kind: str
    class_: str
    quota_class: str
    score: float
    priority: float
    face_risk: float
    confidence: float
    evidence: str
    evidence_type: str
    content: str = ""
    status: str = "pending"
    created_at: float = 0.0
    expires_at: float = 0.0
    deferred_until: float = 0.0
    defer_count: int = 0
    manual: bool = False
    meta: dict = field(default_factory=dict)

    @classmethod
    def from_candidate(cls, candidate: Candidate, content: str = "", manual: bool = False) -> "Intent":
        d = candidate.desire
        return cls(
            intent_id=new_id("intent"),
            kind=d.kind,
            class_=d.class_,
            quota_class=d.quota_class,
            score=candidate.score,
            priority=d.priority,
            face_risk=d.face_risk,
            confidence=d.confidence,
            evidence=d.evidence,
            evidence_type=d.evidence_type,
            content=content,
            created_at=d.created_at,
            expires_at=d.expires_at,
            manual=manual,
            meta=dict(d.meta),
        )


@dataclass
class TimingResult:
    action: str          # send | defer | skip
    minutes: int = 0
    reason: str = ""
    model_ok: bool = False
    fallback: bool = False


@dataclass
class DispatchResult:
    action: str          # sent | shadowed | blocked | failed
    reason: str = ""
    strategy: str = "direct"
    bubbles: int = 1
    ledger_id: int = 0
