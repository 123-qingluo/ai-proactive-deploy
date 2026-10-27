"""主循环：感知 → 愿望 → Gate → 审议 → 时机 → 出口 → 反馈。"""
from __future__ import annotations

import re
import time
from pathlib import Path

from .audit import Tracer
from .config import load_config, resolve, validate_config
from .content import ContentComposer
from .deliberate import choose
from .desires import DesireService
from .dispatch import Dispatcher
from .feedback import FeedbackService
from .gate import GateService
from .host import FileSinkHost
from .llm import build_llm
from .models import (
    Candidate, Desire, DispatchResult, Intent, State, TimingResult, new_id, now_ts,
)
from .open_loops import OpenLoopService
from .perceive import Belief, PerceiveService
from .recall import RecallService
from .store import Database, Store
from .timeutil import date_key, day_start, get_tz, in_window


class Engine:
    def __init__(self, root: str | Path, host=None, clock=None, timing_llm=None, content_llm=None):
        self.root = Path(root).resolve()
        self.cfg = load_config(self.root)
        self.warnings = validate_config(self.cfg)
        self.tz = get_tz(self.cfg.get("timezone"))
        self.db = Database(resolve(self.root, self.cfg.get("storage", {}).get("db_path", "data/proactive.db")))
        self.db.init()
        self.store = Store(self.db)
        self.host = host if host is not None else FileSinkHost(self.root)
        self.clock = clock
        self.tracer = Tracer(
            resolve(self.root, self.cfg.get("audit", {}).get("trace_dir", "traces")),
            bool(self.cfg.get("audit", {}).get("jsonl", True)), self.host,
        )
        if self.store.control_get("mode") is None:
            self.store.set_mode(str(self.cfg.get("mode", "shadow")), self.now())
        self.recall = RecallService(self.root, self.cfg, self.tracer, self.host)
        self.open_loops = OpenLoopService(self.store, self.cfg, self.tracer, self.tz)
        self.perceive = PerceiveService(self.store, self.recall, self.cfg, self.tz)
        self.desires = DesireService(self.cfg)
        self.gate = GateService(self.cfg, self.store)
        self.feedback = FeedbackService(self.cfg, self.tracer, self.tz)
        self.timing = None  # set below
        from .timing import TimingService

        timing_client = (
            timing_llm if (timing_llm is not None and self.cfg.get("timing", {}).get("provider") == "http")
            else build_llm(self.cfg.get("timing", {}))
        )
        self.timing = TimingService(
            self.store, self.cfg, timing_client,
            self.tracer, self.tz, resolve(self.root, "prompts"),
        )
        content_client = (
            content_llm if (content_llm is not None and self.cfg.get("content", {}).get("provider") == "http")
            else build_llm(self.cfg.get("content", {}))
        )
        self.composer = ContentComposer(
            self.root, self.cfg, content_client, self.tracer,
        )
        self.dispatcher = Dispatcher(self.store, self.cfg, self.tracer, self.host)
        self.state = self.store.load_state(str(self.cfg.get("stream_id", "default")))

    # ------------------------------------------------------------- time / mode
    def now(self) -> float:
        if self.clock is not None:
            return float(self.clock())
        try:
            return float(self.host.now())
        except Exception:
            return now_ts()

    def mode(self) -> str:
        return self.store.get_mode(str(self.cfg.get("mode", "shadow")))

    def set_mode(self, mode: str) -> dict:
        now = self.now()
        self.store.set_mode(mode, now)
        self.tracer.log("mode", stream_id=self.state.stream_id, status=mode)
        return {"ok": True, "mode": mode}

    # ------------------------------------------------------------- observations
    def on_user_message(self, text: str, now: float | None = None) -> dict:
        now = now if now is not None else self.now()
        self.state.ensure_day(date_key(now, self.tz))
        self.state.trim_feedback(now)
        fb = self.feedback.observe_user_message(self.state, text, now)
        loops = self.open_loops.observe_user_text(self.state.stream_id, text, now)
        self.state.idle_anchor = now
        self._save(now)
        self.tracer.log(
            "user_message", stream_id=self.state.stream_id, status="ok",
            text=(text or "")[:120], feedback=fb, loops=loops,
        )
        return {"ok": True, "feedback": fb, "open_loops": loops}

    def on_bot_message(
        self, text: str, social: bool = True,
        expects_reply: bool | None = None, now: float | None = None,
    ) -> dict:
        now = now if now is not None else self.now()
        self.state.ensure_day(date_key(now, self.tz))
        self.state.last_bot_any_at = now
        if social:
            self.state.last_social_bot_at = now
            self.state.last_reply_at = now
            self.state.last_bot_text = (text or "").strip()
            self.state.owed_reply = False
            self.state.owed_since = 0.0
            if expects_reply is None:
                expects_reply = bool(re.search(r"[?？]\s*$", text or ""))
            self.state.expects_reply = bool(expects_reply)
            if expects_reply:
                self.state.expects_since = now
                self.state.follow_up_count = 0
        self._save(now)
        self.tracer.log(
            "bot_message", stream_id=self.state.stream_id, status="social" if social else "non_social",
            expects_reply=bool(expects_reply), text=(text or "")[:120],
        )
        return {"ok": True}

    def observe_background(self, event: dict, now: float | None = None) -> dict:
        now = now if now is not None else self.now()
        self.tracer.log("background", stream_id=self.state.stream_id, detail=event)
        return {"ok": True, "recorded": True, "ts": now}

    def mark_owed_reply(self, text: str | None = None, now: float | None = None) -> dict:
        now = now if now is not None else self.now()
        self.state.owed_reply = True
        self.state.owed_since = now
        if text:
            self.state.last_user_text = text
            if not self.state.last_user_at:
                self.state.last_user_at = now
        self._save(now)
        self.tracer.log("owed_reply", stream_id=self.state.stream_id, status="marked")
        return {"ok": True}

    def set_relation_state(self, anxiety: float, shame: float, now: float | None = None) -> dict:
        now = now if now is not None else self.now()
        self.state.relation_anxiety = float(anxiety)
        self.state.relation_shame = float(shame)
        self.state.relation_updated_at = now
        self.state.relation_explicit = True
        self._save(now)
        self.tracer.log(
            "relation", stream_id=self.state.stream_id, status="set",
            anxiety=self.state.relation_anxiety, shame=self.state.relation_shame,
        )
        return {"ok": True}

    # ------------------------------------------------------------- tick
    def tick(self, now: float | None = None) -> dict:
        now = now if now is not None else self.now()
        self.state.ensure_day(date_key(now, self.tz))
        self.state.trim_feedback(now)
        if self.state.shadow_until and self.state.shadow_until <= now:
            self.state.shadow_until = 0.0
            self.state.shadow_reason = ""
        self.store.expire_loops(self.state.stream_id, now)
        self.store.expire_intents(self.state.stream_id, now)
        feedback_timeout = self.feedback.check_timeouts(self.state, now)
        mode = self.mode()

        if mode == "off":
            payload = {"mode": "off", "desires": [], "gate": {"passed": 0, "blocked": {}},
                       "timing": {"action": "skip", "reason": "mode_off"},
                       "dispatch": {"action": "blocked", "reason": "mode_off"}, "feedback": feedback_timeout or {}}
            self._audit_tick(payload)
            self._save(now)
            return payload

        belief = self.perceive.build(self.state, now, mode)
        self.state.last_recall_score = belief.recall_score
        self.state.last_recall_title = belief.recall_title

        deferred = self._process_deferred(state=self.state, belief=belief, now=now, mode=mode)
        if deferred is not None and deferred.get("dispatch", {}).get("action") in ("sent", "shadowed"):
            deferred["feedback"] = feedback_timeout or {}
            self._audit_tick(deferred)
            self._save(now)
            return deferred

        desires = self.desires.generate(belief)
        candidates, blocked = self.gate.filter(desires, belief, self.state)
        candidate = choose(candidates)
        if candidate is None:
            payload = {
                "mode": mode,
                "desires": [d.kind for d in desires],
                "gate": {"passed": 0, "blocked": blocked},
                "timing": {"action": "skip", "reason": "no_candidate"},
                "dispatch": {"action": "blocked", "reason": "no_candidate"},
                "feedback": feedback_timeout or {},
            }
            self._audit_tick(payload)
            self._save(now)
            return payload

        decision = self.timing.decide(candidate, belief, self.state)
        intent = Intent.from_candidate(candidate)
        if decision.action == "defer":
            intent.status = "deferred"
            intent.deferred_until = now + max(1, decision.minutes) * 60
            intent.defer_count = 0
            self.store.insert_intent_for(self.state.stream_id, intent)
            payload = self._payload(mode, desires, candidates, blocked, decision, DispatchResult("blocked", "deferred"), feedback_timeout)
            self._audit_tick(payload, intent=intent.intent_id)
            self._save(now)
            return payload
        if decision.action == "skip":
            intent.status = "skipped"
            self.store.insert_intent_for(self.state.stream_id, intent)
            payload = self._payload(mode, desires, candidates, blocked, decision, DispatchResult("blocked", "timing_skip"), feedback_timeout)
            self._audit_tick(payload, intent=intent.intent_id)
            self._save(now)
            return payload

        persona = self._persona(belief)
        text, source = self.composer.compose(candidate, persona)
        if not text:
            intent.status = "blocked"
            self.store.insert_intent_for(self.state.stream_id, intent)
            payload = self._payload(mode, desires, candidates, blocked, decision, DispatchResult("blocked", "no_evidence"), feedback_timeout)
            self._audit_tick(payload, intent=intent.intent_id)
            self._save(now)
            return payload

        intent.content = text
        intent.meta["content_source"] = source
        self.store.insert_intent_for(self.state.stream_id, intent)
        result = self.dispatcher.dispatch(intent, text, belief, self.state)
        payload = self._payload(mode, desires, candidates, blocked, decision, result, feedback_timeout)
        self._audit_tick(payload, intent=intent.intent_id)
        self._save(now)
        return payload

    def _process_deferred(self, state: State, belief: Belief, now: float, mode: str) -> dict | None:
        rows = [
            row for row in self.store.list_intents(state.stream_id, ["deferred"], limit=20)
            if float(row.get("deferred_until", 0)) <= now
        ]
        if not rows:
            return None
        row = rows[0]
        if row.get("expires_at") and float(row["expires_at"]) <= now:
            self.store.update_intent(row["intent_id"], status="skipped")
            return None
        if state.closed_until > now or state.quiet_until > now or not belief.window_open:
            self.store.update_intent(row["intent_id"], status="skipped")
            self.tracer.log(
                "timing", stream_id=state.stream_id, status="deferred_blocked",
                reason="silence_or_window", intent=row["intent_id"],
            )
            return None
        desire = Desire(
            kind=row["kind"], evidence=row["evidence"], evidence_type=row["evidence_type"],
            priority=float(row["priority"]), face_risk=float(row["face_risk"]),
            confidence=float(row["confidence"]), class_=row["class_"],
            quota_class=row["quota_class"], created_at=float(row["created_at"]),
            expires_at=float(row["expires_at"]), meta=dict(row.get("meta") or {}),
        )
        candidate = Candidate(desire, float(row["score"]))
        decision = self.timing.decide(candidate, belief, state, use_llm=False)
        if decision.action != "send":
            self.store.update_intent(row["intent_id"], status="skipped")
            return None
        persona = self._persona(belief)
        text, source = self.composer.compose(candidate, persona)
        if not text:
            self.store.update_intent(row["intent_id"], status="blocked")
            return None
        meta = dict(row.get("meta") or {})
        meta["content_source"] = source
        intent = Intent(
            intent_id=row["intent_id"], kind=row["kind"], class_=row["class_"],
            quota_class=row["quota_class"], score=float(row["score"]),
            priority=float(row["priority"]), face_risk=float(row["face_risk"]),
            confidence=float(row["confidence"]), evidence=row["evidence"],
            evidence_type=row["evidence_type"], content=text, status="pending",
            created_at=float(row["created_at"]), expires_at=float(row["expires_at"]),
            deferred_until=0.0, defer_count=int(row.get("defer_count", 0)), meta=meta,
        )
        self.store.update_intent(intent.intent_id, content=text, meta=meta, status="pending")
        result = self.dispatcher.dispatch(intent, text, belief, state)
        return self._payload(mode, [], [], {}, decision, result, {}, intent_id=intent.intent_id)

    def _payload(self, mode, desires, candidates, blocked, decision: TimingResult, result: DispatchResult, feedback, intent_id: str = "") -> dict:
        return {
            "mode": mode,
            "desires": [d.kind for d in desires],
            "gate": {"passed": len(candidates), "blocked": blocked},
            "timing": {"action": decision.action, "minutes": decision.minutes,
                       "reason": decision.reason, "model_ok": decision.model_ok,
                       "fallback": decision.fallback},
            "dispatch": {"action": result.action, "reason": result.reason,
                         "strategy": result.strategy, "bubbles": result.bubbles},
            "feedback": feedback or {},
            "intent_id": intent_id,
        }

    def _audit_tick(self, payload: dict, intent: str = "") -> None:
        record = {
            "desires": payload.get("desires", []),
            "gate": payload.get("gate", {}),
            "timing": payload.get("timing", {}),
            "dispatch": payload.get("dispatch", {}),
            "feedback": payload.get("feedback", {}),
            "user_silence": round((self.now() - self.state.last_user_at) / 60, 2) if self.state.last_user_at else 0,
            "bot_silence": round((self.now() - self.state.last_social_bot_at) / 60, 2) if self.state.last_social_bot_at else 0,
            "mode": payload.get("mode", self.mode()),
        }
        if intent:
            record["intent"] = intent
        self.tracer.log("tick", stream_id=self.state.stream_id, **record)

    def _persona(self, belief: Belief) -> str | None:
        try:
            return self.host.persona({"kind": "", "user_silence": belief.user_silence})
        except Exception:
            return None

    # ------------------------------------------------------------- manual / probe
    def trigger_manual(
        self, text: str | None = None, kind: str = "manual", class_: str = "social",
        strategy: str | None = None, force: bool = False, now: float | None = None,
    ) -> dict:
        now = now if now is not None else self.now()
        self.state.ensure_day(date_key(now, self.tz))
        mode = self.mode()
        if mode == "off":
            return {"ok": False, "action": "blocked", "reason": "mode_off"}
        belief = self.perceive.build(self.state, now, mode)
        if text:
            content = text.strip()
            if not content:
                return {"ok": False, "action": "blocked", "reason": "empty_text"}
            intent = Intent(
                intent_id=new_id("intent"), kind=kind, class_=class_, quota_class=class_,
                score=1.0, priority=1.0, face_risk=0.0, confidence=1.0,
                evidence=content, evidence_type="manual", content=content,
                status="pending", created_at=now, expires_at=now + 3600, manual=True,
                meta={"manual": True},
            )
            self.store.insert_intent_for(self.state.stream_id, intent)
            result = self.dispatcher.dispatch(
                intent, content, belief, self.state, strategy=strategy, manual=True, force=force
            )
            self._audit_tick(self._payload(mode, [], [], {}, TimingResult("send", 0, "manual"), result, {}), intent=intent.intent_id)
            self._save(now)
            return {"ok": result.action in ("sent", "shadowed"), "action": result.action, "reason": result.reason}

        desires = self.desires.generate(belief)
        candidates, blocked = self.gate.filter(desires, belief, self.state, manual=True)
        candidate = choose(candidates)
        if candidate is None:
            return {"ok": False, "action": "blocked", "reason": "no_candidate", "blocked": blocked}
        content, source = self.composer.compose(candidate, self._persona(belief))
        if not content:
            return {"ok": False, "action": "blocked", "reason": "no_evidence"}
        intent = Intent.from_candidate(candidate, content=content, manual=True)
        intent.meta["content_source"] = source
        self.store.insert_intent_for(self.state.stream_id, intent)
        result = self.dispatcher.dispatch(
            intent, content, belief, self.state, strategy=strategy, manual=True, force=force
        )
        self._audit_tick(self._payload(mode, desires, candidates, blocked, TimingResult("send", 0, "manual"), result, {}), intent=intent.intent_id)
        self._save(now)
        return {"ok": result.action in ("sent", "shadowed"), "action": result.action, "reason": result.reason}

    def probe(self, allow_send: bool = False) -> dict:
        if not allow_send:
            return {"ok": False, "sent": False, "reason": "allow_send_required"}
        text = "【主动功能自检】这是一条探针消息，可以忽略。"
        try:
            sent = self.host.send_text(text, "direct") is True
        except Exception as exc:
            sent = False
            self.tracer.log("probe", stream_id=self.state.stream_id, status="error", error=str(exc)[:200])
        self.tracer.log("probe", stream_id=self.state.stream_id, status="sent" if sent else "failed")
        return {"ok": sent, "sent": sent, "text": text}

    # ------------------------------------------------------------- run / status
    def run_forever(self, interval: float | None = None, max_ticks: int | None = None) -> dict:
        interval = float(interval or self.cfg.get("tick", {}).get("seconds", 60))
        stop_file = resolve(self.root, self.cfg.get("daemon", {}).get("stop_file", "data/STOP"))
        stop_file.parent.mkdir(parents=True, exist_ok=True)
        ticks = 0
        while max_ticks is None or ticks < max_ticks:
            if stop_file.exists():
                try:
                    stop_file.unlink()
                except OSError:
                    pass
                break
            try:
                self.tick()
            except Exception as exc:
                self.tracer.log("error", stream_id=self.state.stream_id, error=str(exc)[:500])
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                break
            time.sleep(max(1.0, interval))
        return {"ok": True, "ticks": ticks}

    def status(self) -> dict:
        stats = self.store.stats(self.state.stream_id)
        return {
            "stream_id": self.state.stream_id,
            "mode": self.mode(),
            "shadow_until": self.state.shadow_until,
            "shadow_reason": self.state.shadow_reason,
            "last_user_at": self.state.last_user_at,
            "last_social_bot_at": self.state.last_social_bot_at,
            "last_proactive_at": self.state.last_proactive_at,
            "decay": self.state.decay,
            "pace": self.state.pace,
            "expects_reply": self.state.expects_reply,
            "owed_reply": self.state.owed_reply,
            "open_loops": stats["open_loops"],
            "pending_intents": stats["pending_intents"],
            "sent_total": stats["sent_total"],
            "due_loops": len(self.store.due_loops(self.state.stream_id, self.now())),
            "warnings": self.warnings,
        }

    def health(self) -> dict:
        probe_ok = False
        try:
            with self.db.tx() as conn:
                conn.execute("INSERT OR REPLACE INTO control(key, value, updated_at) VALUES ('__probe__','1',?)", (self.now(),))
                row = conn.execute("SELECT value FROM control WHERE key='__probe__'").fetchone()
                probe_ok = bool(row and row["value"] == "1")
                conn.execute("DELETE FROM control WHERE key='__probe__'")
        except Exception:
            probe_ok = False
        return {
            "ok": probe_ok,
            "mode": self.mode(),
            "db_path": self.db.path,
            "stream_id": self.state.stream_id,
            "host": type(self.host).__name__,
            "recall_mode": self.cfg.get("recall", {}).get("mode", "none"),
            "window": self.cfg.get("window", {}),
            "warnings": self.warnings,
        }

    # ------------------------------------------------------------- lifecycle
    def _save(self, now: float) -> None:
        self.store.save_state(self.state, now)

    def close(self) -> None:
        try:
            self.recall.close()
        finally:
            self.db.close()
