"""感知：把状态、窗口、开放话题、召回结果汇总成一轮的 belief。"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import State
from .timeutil import date_key, hour_key, in_window


@dataclass
class Belief:
    now: float
    mode: str
    user_silence: float
    bot_silence: float
    window_open: bool
    quiet_window: bool
    closed: bool
    quiet_user: bool
    last_user_text: str
    last_bot_text: str
    expects_reply: bool
    expects_age: float
    owed_reply: bool
    owed_age: float
    open_loops: list
    due_loops: list
    scheduled_due: list
    relation_anxiety: float
    relation_shame: float
    recall_items: list
    recall_score: float
    recall_title: str
    day: str
    hour: str
    raw_state: State = field(repr=False)


class PerceiveService:
    def __init__(self, store, recall, cfg: dict, tz):
        self.store = store
        self.recall = recall
        self.cfg = cfg
        self.tz = tz
        self.window = cfg.get("window", {})
        self.desire_cfg = cfg.get("desires", {})

    def build(self, state: State, now: float, mode: str) -> Belief:
        open_loops = self.store.open_loops(state.stream_id, status="open")
        due_loops = self.store.due_loops(state.stream_id, now)
        scheduled_due = self.store.due_scheduled(state.stream_id, now)
        query = ""
        if due_loops:
            query = due_loops[0]["text"]
        elif open_loops:
            query = open_loops[0]["text"]
        elif state.last_user_text:
            query = state.last_user_text
        recall_items = self.recall.recall(query, int(self.cfg.get("recall", {}).get("top_k", 3))) if query else []
        recall_score = max((item[1] for item in recall_items), default=0.0)
        recall_title = recall_items[0][2] if recall_items else ""

        anxiety, shame = self._relation(state, now)
        return Belief(
            now=now,
            mode=mode,
            user_silence=(now - state.last_user_at) if state.last_user_at else 0.0,
            bot_silence=(now - state.last_social_bot_at) if state.last_social_bot_at else 0.0,
            window_open=in_window(now, self.window.get("active_start", "09:00"), self.window.get("active_end", "24:00"), self.tz),
            quiet_window=in_window(now, self.window.get("quiet_start", "00:00"), self.window.get("quiet_end", "09:00"), self.tz),
            closed=state.closed_until > now,
            quiet_user=state.quiet_until > now,
            last_user_text=state.last_user_text,
            last_bot_text=state.last_bot_text,
            expects_reply=state.expects_reply,
            expects_age=(now - state.expects_since) if state.expects_since else 0.0,
            owed_reply=state.owed_reply,
            owed_age=(now - state.owed_since) if state.owed_since else 0.0,
            open_loops=open_loops,
            due_loops=due_loops,
            scheduled_due=scheduled_due,
            relation_anxiety=anxiety,
            relation_shame=shame,
            recall_items=recall_items,
            recall_score=recall_score,
            recall_title=recall_title,
            day=date_key(now, self.tz),
            hour=hour_key(now, self.tz),
            raw_state=state,
        )

    def _relation(self, state: State, now: float) -> tuple[float, float]:
        if state.relation_explicit:
            return float(state.relation_anxiety), float(state.relation_shame)
        if not self.desire_cfg.get("relation_infer_enabled", True):
            return 0.0, 0.0
        rejects = confused = 0
        for item in state.recent_feedback:
            if not isinstance(item, dict) or now - float(item.get("ts", 0)) > 24 * 3600:
                continue
            if item.get("verdict") == "reject":
                rejects += 1
            elif item.get("verdict") == "confused":
                confused += 1
        return float(rejects), float(confused)
