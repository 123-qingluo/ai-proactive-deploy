"""反馈：回复速度、连发、话题延续、情绪；拒绝降级 shadow，超时放慢。"""
from __future__ import annotations

import re

from .models import State

CLOSED_RE = re.compile(r"睡了|晚安|先睡|不聊了|明天再说|别发了|安静|勿扰|静音|退下")
REJECT_RE = re.compile(r"别说了|不想聊|闭嘴|好烦|烦死|滚|不用了|别烦|停|拒绝|没兴趣|不要")
CONFUSED_RE = re.compile(r"啊？|什么意思|没懂|看不懂|什么鬼|？？|不懂|啥")
POSITIVE_RE = re.compile(r"好|哈哈|开心|喜欢|谢谢|嗯嗯|想你|可爱|笑死|棒")


def _bigrams(text: str) -> set[str]:
    text = re.sub(r"\s+", "", text or "").lower()
    if len(text) < 2:
        return set(text)
    return {text[i : i + 2] for i in range(len(text) - 1)}


def _overlap(a: str, b: str) -> float:
    sa, sb = _bigrams(a), _bigrams(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa)


class FeedbackService:
    def __init__(self, cfg: dict, tracer, tz):
        self.cfg = cfg
        self.tracer = tracer
        self.tz = tz
        self.fb = cfg.get("feedback", {})

    def observe_user_message(self, state: State, text: str, now: float) -> dict:
        state.last_user_at = now
        state.last_user_text = (text or "").strip()
        state.expects_reply = False
        state.owed_reply = False
        state.follow_up_count = 0
        state.recent_user_ts.append(now)
        state.recent_user_ts = state.recent_user_ts[-20:]
        if CLOSED_RE.search(text or ""):
            hours = float(self.fb.get("closed_hours", 12))
            state.closed_until = max(state.closed_until, now + hours * 3600)
            self.tracer.log("feedback", stream_id=state.stream_id, status="closed_until", until=state.closed_until)

        pending = state.pending_feedback
        if not pending or pending.get("replied"):
            return {}
        if now - float(pending.get("sent_at", 0)) > float(self.fb.get("response_window_minutes", 120)) * 60:
            self._timeout(state, now, pending)
            return {"verdict": "timeout"}
        return self._evaluate(state, text, now, pending)

    def check_timeouts(self, state: State, now: float) -> dict | None:
        pending = state.pending_feedback
        if not pending or pending.get("replied"):
            return None
        if now - float(pending.get("sent_at", 0)) < float(self.fb.get("response_window_minutes", 120)) * 60:
            return None
        return self._timeout(state, now, pending)

    def _timeout(self, state: State, now: float, pending: dict) -> dict:
        state.pace = min(
            float(self.fb.get("pace_cap", 3.0)),
            state.pace * float(self.fb.get("timeout_pace_multiplier", 1.5)),
        )
        item = {
            "ts": now, "verdict": "timeout", "kind": pending.get("kind", ""),
            "behavior": 0.0, "sentiment": 0.5, "final": 0.15,
            "pace": round(state.pace, 3), "decay": round(state.decay, 3),
        }
        state.recent_feedback.append(item)
        state.last_feedback = item
        state.pending_feedback = None
        self.tracer.log("feedback", stream_id=state.stream_id, status="timeout", pace=state.pace)
        return item

    def _evaluate(self, state: State, text: str, now: float, pending: dict) -> dict:
        sent_at = float(pending.get("sent_at", now))
        delay_min = max(0.0, (now - sent_at) / 60)
        fast = float(self.fb.get("fast_reply_minutes", 5))
        window = float(self.fb.get("response_window_minutes", 120))
        speed = 1.0 if delay_min <= fast else max(0.0, 1.0 - (delay_min - fast) / max(1.0, window - fast))

        multi_window = float(self.fb.get("multi_reply_window_minutes", 5)) * 60
        recent = [ts for ts in state.recent_user_ts if now - ts <= multi_window]
        consecutive = 1.0 if len(recent) >= 2 else 0.0
        topic = 1.0 if _overlap(pending.get("content", ""), text) >= 0.2 else 0.0
        behavior = 0.40 * speed + 0.30 * consecutive + 0.30 * topic

        sentiment_window = float(self.fb.get("sentiment_window_minutes", 30)) * 60
        if now - sent_at <= sentiment_window:
            sentiment, verdict = self._sentiment(text)
        else:
            sentiment, verdict = 0.5, "neutral"
        weight = float(self.fb.get("behavior_weight", 0.7))
        final = weight * behavior + (1 - weight) * sentiment

        if verdict == "reject":
            state.decay = max(
                float(self.fb.get("decay_floor", 0.15)),
                state.decay * float(self.fb.get("reject_decay", 0.5)),
            )
            state.quiet_until = max(
                state.quiet_until,
                now + float(self.fb.get("reject_quiet_minutes", 120)) * 60,
            )
        elif verdict == "confused":
            pass
        elif final >= 0.6:
            state.decay = min(1.0, state.decay + float(self.fb.get("respond_decay", 0.3)))
        item = {
            "ts": now, "verdict": verdict, "kind": pending.get("kind", ""),
            "delay_minutes": round(delay_min, 2), "speed": round(speed, 3),
            "consecutive": consecutive, "topic": topic,
            "behavior": round(behavior, 3), "sentiment": sentiment,
            "final": round(final, 3), "decay": round(state.decay, 3), "pace": round(state.pace, 3),
        }
        state.recent_feedback.append(item)
        state.last_feedback = item
        state.pending_feedback = None

        self._maybe_shadow(state, now)
        self.tracer.log("feedback", stream_id=state.stream_id, status=verdict, **item)
        return item

    def _maybe_shadow(self, state: State, now: float) -> None:
        rejects = sum(
            1 for item in state.recent_feedback
            if isinstance(item, dict) and item.get("verdict") == "reject"
            and now - float(item.get("ts", 0)) <= 24 * 3600
        )
        confused = sum(
            1 for item in state.recent_feedback
            if isinstance(item, dict) and item.get("verdict") == "confused"
            and now - float(item.get("ts", 0)) <= 24 * 3600
        )
        if rejects >= int(self.fb.get("shadow_after_rejects", 1)):
            state.shadow_until = max(state.shadow_until, now + float(self.fb.get("shadow_hours", 24)) * 3600)
            state.shadow_reason = "reject"
        elif confused >= int(self.fb.get("shadow_after_confused", 2)):
            state.shadow_until = max(state.shadow_until, now + float(self.fb.get("shadow_hours", 24)) * 3600)
            state.shadow_reason = "confused"

    def _sentiment(self, text: str) -> tuple[float, str]:
        if REJECT_RE.search(text or ""):
            return 0.0, "reject"
        if CONFUSED_RE.search(text or ""):
            return 0.25, "confused"
        if POSITIVE_RE.search(text or ""):
            return 1.0, "positive"
        return 0.5, "neutral"
