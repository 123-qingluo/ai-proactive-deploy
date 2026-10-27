"""出口：账本预占、策略、投递、失败释放；system 只受最小间隔约束。"""
from __future__ import annotations

from .models import DispatchResult, Intent, State
from .perceive import Belief


class Dispatcher:
    def __init__(self, store, cfg: dict, tracer, host):
        self.store = store
        self.cfg = cfg
        self.tracer = tracer
        self.host = host
        self.dispatch_cfg = cfg.get("dispatch", {})

    def dispatch(
        self, intent: Intent, text: str, belief: Belief, state: State,
        strategy: str | None = None, manual: bool = False, force: bool = False,
    ) -> DispatchResult:
        mode = self.store.get_mode(self.cfg.get("mode", "shadow"))
        if mode == "off":
            self.store.update_intent(intent.intent_id, status="blocked")
            return DispatchResult("blocked", "mode_off")
        shadow = (mode == "shadow" or state.shadow_until > belief.now) and not force
        if shadow:
            self.store.update_intent(intent.intent_id, content=text, status="shadowed")
            self.tracer.log(
                "dispatch", stream_id=state.stream_id, status="shadowed",
                kind=intent.kind, class_=intent.class_, reason="shadow" if mode == "shadow" else state.shadow_reason,
            )
            return DispatchResult("shadowed", "shadow")

        if intent.class_ == "system":
            gap = float(self.dispatch_cfg.get("system_min_gap_seconds", 600))
            if state.last_system_bot_at and belief.now - state.last_system_bot_at < gap:
                self.store.update_intent(intent.intent_id, status="blocked")
                return DispatchResult("blocked", "system_gap")

        strategy = strategy or self.dispatch_cfg.get("strategies", {}).get(intent.class_, "direct")
        bubbles = 1
        ledger_id, reason = self.store.reserve_ledger(
            state.stream_id, belief.day, intent.class_, intent.quota_class,
            intent.kind, intent.intent_id, bubbles, belief.now, self.cfg, bypass_quota=manual,
        )
        if not ledger_id:
            self.store.update_intent(intent.intent_id, status="blocked")
            self.tracer.log(
                "dispatch", stream_id=state.stream_id, status="blocked",
                kind=intent.kind, class_=intent.class_, reason=reason,
            )
            return DispatchResult("blocked", reason)

        ok = False
        try:
            ok = self.host.send_text(text, strategy) is True
        except Exception as exc:
            self.tracer.log(
                "dispatch", stream_id=state.stream_id, status="error",
                kind=intent.kind, error=str(exc)[:200],
            )
            ok = False

        if not ok:
            self.store.mark_ledger(ledger_id, "failed")
            self.store.update_intent(intent.intent_id, status="failed", content=text)
            self.tracer.log(
                "dispatch", stream_id=state.stream_id, status="failed",
                kind=intent.kind, class_=intent.class_, ledger_id=ledger_id,
            )
            return DispatchResult("failed", "send_failed", strategy, bubbles, ledger_id)

        self.store.mark_ledger(ledger_id, "sent")
        self.store.update_intent(intent.intent_id, status="sent", content=text)
        state.last_proactive_at = belief.now
        state.last_bot_text = text
        state.last_bot_any_at = belief.now
        if intent.class_ == "social":
            state.last_social_bot_at = belief.now
            state.last_reply_at = belief.now
            state.pending_feedback = {
                "sent_at": belief.now,
                "content": text,
                "kind": intent.kind,
                "intent_id": intent.intent_id,
                "quota_class": intent.quota_class,
                "replied": False,
            }
            if intent.kind == "D5":
                state.follow_up_count += 1
        elif intent.class_ == "scheduled":
            state.last_scheduled_bot_at = belief.now
        else:
            state.last_system_bot_at = belief.now

        if intent.kind == "D2" and intent.meta.get("loop_id"):
            self.store.mark_loop(intent.meta["loop_id"], "notified", belief.now)
        if intent.kind == "D9" and intent.meta.get("item_id"):
            self.store.mark_scheduled(intent.meta["item_id"], "done")

        self.tracer.log(
            "dispatch", stream_id=state.stream_id, status="sent",
            kind=intent.kind, class_=intent.class_, strategy=strategy,
            bubbles=bubbles, ledger_id=ledger_id, manual=manual,
        )
        return DispatchResult("sent", "ok", strategy, bubbles, ledger_id)
