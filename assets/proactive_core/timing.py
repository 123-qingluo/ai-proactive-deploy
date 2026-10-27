"""时机：LLM 可 send/defer/skip；模型不可用或预算耗尽时规则 fallback。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .models import DESIRE_DEFAULTS, Candidate, State, TimingResult
from .perceive import Belief
from .timeutil import day_start


class TimingService:
    def __init__(self, store, cfg: dict, llm, tracer, tz, prompts_dir: str | Path):
        self.store = store
        self.cfg = cfg
        self.llm = llm
        self.tracer = tracer
        self.tz = tz
        self.prompts_dir = Path(prompts_dir)
        self.timing = cfg.get("timing", {})

    def budget_class(self, kind: str) -> str:
        return DESIRE_DEFAULTS.get(kind, {}).get("budget", "relationship")

    def _prompt(self) -> str:
        path = self.prompts_dir / "timing.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return "你是主动行为时机判定器。只输出 JSON：{\"action\":\"send|defer|skip\",\"reason\":\"...\",\"minutes\":0}。"

    def _rule(self, candidate: Candidate, belief: Belief, state: State) -> TimingResult:
        now = belief.now
        if candidate.expires_at and candidate.expires_at <= now:
            return TimingResult("skip", 0, "expired", model_ok=False, fallback=True)
        deadline = float(self.timing.get("deadline_send_minutes", 30)) * 60
        if candidate.expires_at and candidate.expires_at - now <= deadline:
            return TimingResult("send", 0, "deadline", model_ok=False, fallback=True)
        return TimingResult("send", 0, "rules", model_ok=False, fallback=True)

    def decide(
        self, candidate: Candidate, belief: Belief, state: State,
        manual: bool = False, use_llm: bool = True,
    ) -> TimingResult:
        if manual:
            return TimingResult("send", 0, "manual", model_ok=False, fallback=False)
        now = belief.now
        if candidate.expires_at and candidate.expires_at <= now:
            return TimingResult("skip", 0, "expired")
        deadline = float(self.timing.get("deadline_send_minutes", 30)) * 60
        if candidate.expires_at and candidate.expires_at - now <= deadline:
            return TimingResult("send", 0, "deadline", fallback=True)

        if not use_llm or not getattr(self.llm, "available", False):
            return self._rule(candidate, belief, state)
        if float(state.forced_rules_until or 0) > now:
            return self._rule(candidate, belief, state)

        budget_class = self.budget_class(candidate.kind)
        allowed, budget_reason = self.store.can_call_timing(
            state.stream_id, belief.day, belief.hour, budget_class, self.cfg
        )
        if not allowed:
            self.tracer.log(
                "timing", stream_id=state.stream_id, status="fallback",
                reason=budget_reason, kind=candidate.kind,
            )
            return self._rule(candidate, belief, state)

        payload = {
            "kind": candidate.kind,
            "priority": candidate.priority,
            "face_risk": candidate.face_risk,
            "confidence": candidate.confidence,
            "score": candidate.score,
            "evidence": candidate.evidence[:160],
            "user_silence_minutes": round(belief.user_silence / 60, 1),
            "bot_silence_minutes": round(belief.bot_silence / 60, 1),
            "window_open": belief.window_open,
            "quiet": belief.quiet_window,
            "mode": belief.mode,
            "expires_in_minutes": round((candidate.expires_at - now) / 60, 1) if candidate.expires_at else None,
        }
        self.store.record_timing_call(
            state.stream_id, belief.day, belief.hour, budget_class, now, model_ok=False
        )
        try:
            raw = self.llm.complete(self._prompt(), json.dumps(payload, ensure_ascii=False))
            decision = self._parse(raw)
            if decision is None:
                raise ValueError("invalid timing json")
            action, minutes, reason = decision
            state.fallback_streak = 0
            self.tracer.log(
                "timing", stream_id=state.stream_id, status="model",
                action=action, minutes=minutes, reason=reason, kind=candidate.kind,
            )
            return TimingResult(action, minutes, reason, model_ok=True, fallback=False)
        except Exception as exc:
            state.fallback_streak += 1
            if state.fallback_streak >= int(self.timing.get("fallback_after_streak", 3)):
                state.forced_rules_until = day_start(now, self.tz) + 86400
                state.fallback_streak = 0
            self.tracer.log(
                "timing", stream_id=state.stream_id, status="model_error",
                error=str(exc)[:200], kind=candidate.kind,
            )
            return self._rule(candidate, belief, state)

    def _parse(self, text: str):
        cleaned = (text or "").strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned).strip()
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end < start:
            return None
        try:
            data = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
        action = str(data.get("action", "")).lower()
        if action not in ("send", "defer", "skip"):
            return None
        try:
            minutes = int(data.get("minutes", 0) or 0)
        except (TypeError, ValueError):
            minutes = 0
        min_m = int(self.timing.get("defer_min_minutes", 5))
        max_m = int(self.timing.get("defer_max_minutes", 360))
        if action == "defer":
            minutes = max(min_m, min(max_m, minutes or min_m))
        return action, minutes, str(data.get("reason", ""))[:200]
