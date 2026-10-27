"""自检：A1–A10 + S1–S6，全部用假时钟/假宿主，不产生真实消息。"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

from .config import DEFAULT_CONFIG, ConfigError, deep_merge, save_config, validate_config
from .engine import Engine
from .models import Candidate, Desire, now_ts
from .open_loops import OpenLoopService


class FakeClock:
    def __init__(self, ts: float):
        self.value = float(ts)

    def now(self) -> float:
        return self.value

    def set(self, value: float) -> None:
        self.value = float(value)

    def advance(self, seconds: float) -> None:
        self.value += float(seconds)


class FakeHost:
    def __init__(self, clock: FakeClock):
        self.clock = clock
        self.sent: list[dict] = []
        self.audit_events: list[dict] = []
        self.recall_items: list[tuple[str, float, str]] = []
        self.fail = False
        self.persona_text = ""

    def now(self) -> float:
        return self.clock.now()

    def send_text(self, text: str, strategy: str):
        if self.fail:
            return None
        self.sent.append({"ts": self.clock.now(), "text": text, "strategy": strategy})
        return True

    def recall(self, query: str, top_k: int):
        return list(self.recall_items)[:top_k]

    def persona(self, context: dict):
        return self.persona_text

    def audit(self, event: dict):
        self.audit_events.append(event)


class StubLLM:
    available = True
    model = "stub"

    def __init__(self, responses=None, default='{"action":"send","reason":"ok","minutes":0}'):
        self.responses = list(responses or [])
        self.default = default
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        return self.responses.pop(0) if self.responses else self.default


def _ts(y=2026, mo=10, d=28, h=12, mi=0) -> float:
    return datetime(y, mo, d, h, mi).timestamp()


class Harness:
    def __init__(self, root: Path, clock: FakeClock, overrides: dict, timing_responses=None, fail_send=False):
        self.root = root
        self.clock = clock
        cfg = deep_merge(DEFAULT_CONFIG, overrides)
        save_config(root, cfg)
        self.host = FakeHost(clock)
        self.host.fail = fail_send
        self.timing_llm = StubLLM(timing_responses)
        self.content_llm = StubLLM(default="模板")
        self.engine = Engine(
            root, host=self.host, clock=clock.now,
            timing_llm=self.timing_llm, content_llm=self.content_llm,
        )
        self.engine.set_mode(cfg.get("mode", "shadow"))

    def close(self):
        self.engine.close()


def _copy_assets(source: Path, target: Path) -> None:
    for name in ("prompts", "templates"):
        if (source / name).exists():
            shutil.copytree(source / name, target / name)


def _add_loop(engine: Engine, due_offset: float = -60, ttl_offset: float = 3600, text: str = "明天一起看展览") -> str:
    from .models import new_id

    now = engine.now()
    loop_id = new_id("loop")
    engine.store.add_open_loop(
        engine.state.stream_id, loop_id, text, text,
        now + due_offset, now, now + ttl_offset,
    )
    return loop_id


def _set_last_user(engine: Engine, hours: float, text: str = "最近有点忙") -> None:
    engine.state.last_user_at = engine.now() - hours * 3600
    engine.state.last_user_text = text


def _set_last_social(engine: Engine, hours: float) -> None:
    engine.state.last_social_bot_at = engine.now() - hours * 3600


def _record(name: str, checks: list, fn) -> None:
    started = time.time()
    try:
        result = fn()
        if isinstance(result, tuple):
            ok, detail = result
        elif isinstance(result, dict):
            ok, detail = bool(result.get("ok")), result.get("detail", "")
        else:
            ok, detail = bool(result), ""
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=2)}"
    checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300], "ms": int((time.time() - started) * 1000)})


def run_selfcheck(base_cfg: dict | None = None, root: str | Path | None = None) -> dict:
    started = time.time()
    base_cfg = deep_merge(DEFAULT_CONFIG, base_cfg or {})
    temp_root = Path(tempfile.mkdtemp(prefix="proactive-selfcheck-"))
    source_root = Path(root or ".").resolve()
    _copy_assets(source_root, temp_root)
    checks: list[dict] = []
    clock = FakeClock(_ts())

    def harness(name: str, overrides: dict | None = None, timing_responses=None, fail_send=False) -> Harness:
        payload = {"stream_id": name, "mode": "enforce", "timezone": "local"}
        payload = deep_merge(payload, overrides or {})
        payload["stream_id"] = name
        return Harness(temp_root, clock, payload, timing_responses=timing_responses, fail_send=fail_send)

    try:
        # A1 凌晨社交候选被窗口拦截
        def a1():
            clock.set(_ts(h=2))
            h = harness("a1")
            _add_loop(h.engine)
            result = h.engine.tick()
            h.close()
            assert not h.host.sent, h.host.sent
            assert result["gate"]["blocked"].get("outside_window", 0) >= 1, result
            return True, f"02:00 blocked={result['gate']['blocked']}"

        # A2 “睡了”后全部拦截
        def a2():
            clock.set(_ts(h=12))
            h = harness("a2")
            h.engine.on_user_message("我睡了，晚安")
            _add_loop(h.engine)
            result = h.engine.tick()
            closed = h.engine.state.closed_until
            h.close()
            assert not h.host.sent, h.host.sent
            assert closed > clock.now(), closed
            assert result["gate"]["blocked"].get("silence", 0) >= 1, result
            return True, f"closed_until={closed} blocked={result['gate']['blocked']}"

        # A3 计划性发送不计入社交沉默
        def a3():
            clock.set(_ts(h=12))
            h = harness("a3")
            h.engine.trigger_manual("学习提醒", class_="scheduled")
            social_before = h.engine.state.last_social_bot_at
            clock.advance(90 * 60)
            h.engine.trigger_manual("学习提醒 2", class_="scheduled")
            assert h.engine.state.last_social_bot_at == social_before
            _set_last_user(h.engine, 3)
            _set_last_social(h.engine, 3)
            _add_loop(h.engine)
            result = h.engine.tick()
            h.close()
            assert result["dispatch"]["action"] == "sent", result
            return True, "scheduled 未更新 last_social；social 仍可发送"

        # A4 后台检查不写意图、不耗预算
        def a4():
            clock.set(_ts(h=12))
            h = harness("a4")
            intents_before = h.engine.store.count_intents_since("a4", 0)
            calls_before = h.timing_llm.calls
            for _ in range(10):
                h.engine.observe_background({"event": "bili_check"})
            intents_after = h.engine.store.count_intents_since("a4", 0)
            calls_after = h.timing_llm.calls
            h.close()
            assert intents_before == intents_after == 0
            assert calls_before == calls_after == 0
            return True, "10 次后台检查，intents=0，model_calls=0"

        # A5 defer 10 分钟后只重试一次
        def a5():
            clock.set(_ts(h=12))
            h = harness(
                "a5",
                {"timing": {"provider": "http", "base_url": "http://stub", "model": "stub",
                            "hourly_budget": 100}},
                timing_responses=['{"action":"defer","reason":"busy","minutes":10}'],
            )
            h.engine.cfg["timing"]["per_kind_budget"] = 100
            _set_last_user(h.engine, 3)
            _set_last_social(h.engine, 3)
            _add_loop(h.engine)
            first = h.engine.tick()
            assert first["timing"]["action"] == "defer", first
            assert not h.host.sent
            clock.advance(10 * 60)
            second = h.engine.tick()
            h.close()
            assert second["dispatch"]["action"] == "sent", second
            assert len(h.host.sent) == 1, h.host.sent
            return True, "第一次 defer 10m，第二次规则重试并发送"

        # A6 第 25 次时机判定 fallback，不再调用模型
        def a6():
            clock.set(_ts(h=12))
            h = harness(
                "a6",
                {"mode": "shadow", "timing": {"provider": "http", "base_url": "http://stub", "model": "stub",
                                              "hourly_budget": 100}},
            )
            h.engine.cfg["timing"]["per_kind_budget"] = 100
            belief = h.engine.perceive.build(h.engine.state, h.engine.now(), "shadow")
            desire = Desire("D2", "测试", "loop", 0.92, 0.34, 0.88,
                            class_="social", quota_class="social",
                            created_at=h.engine.now(), expires_at=h.engine.now() + 3600)
            candidate = Candidate(desire, 0.7)
            last = None
            for _ in range(25):
                last = h.engine.timing.decide(candidate, belief, h.engine.state)
            calls = h.timing_llm.calls
            h.close()
            assert calls == 24, calls
            assert last.fallback and last.reason == "rules", last
            return True, f"model_calls={calls}，第 25 次 fallback"

        # A7 配额 0 拦截；-1 不限
        def a7():
            clock.set(_ts(h=12))
            h0 = harness("a7-zero", {"dispatch": {"quotas": {"social": 0}}})
            _set_last_user(h0.engine, 3)
            _set_last_social(h0.engine, 3)
            _add_loop(h0.engine)
            r0 = h0.engine.tick()
            h0.close()
            assert not h0.host.sent and r0["gate"]["blocked"].get("quota", 0) >= 1, r0
            h1 = harness("a7-unlimited", {"dispatch": {"quotas": {"social": -1}}})
            _set_last_user(h1.engine, 3)
            _set_last_social(h1.engine, 3)
            _add_loop(h1.engine)
            r1 = h1.engine.tick()
            h1.close()
            assert r1["dispatch"]["action"] == "sent", r1
            return True, "quota=0 拦截；quota=-1 放行"

        # A8 勿扰时段手动触发仍发送、记账、审计
        def a8():
            clock.set(_ts(h=2))
            h = harness("a8", {"mode": "enforce"})
            result = h.engine.trigger_manual("手动测试")
            ledger = h.engine.store.ledger_rows("a8")
            audit = h.engine.tracer.tail(5)
            h.close()
            assert result["action"] == "sent", result
            assert len(h.host.sent) == 1
            assert len(ledger) == 1 and ledger[0]["status"] == "sent"
            assert any(item.get("event") == "dispatch" for item in audit)
            return True, "02:00 手动发送成功，ledger+1，audit 留痕"

        # A9 无依据的空闲分享不生成
        def a9():
            clock.set(_ts(h=12))
            h = harness("a9")
            h.engine.state.last_user_at = h.engine.now() - 2 * 3600
            h.engine.state.last_user_text = ""
            result = h.engine.tick()
            h.close()
            assert "D8" not in result["desires"], result
            assert not h.host.sent
            return True, "D8 无 evidence，未生成"

        # A10 两次拒绝后 shadow
        def a10():
            clock.set(_ts(h=12))
            h = harness("a10", {"feedback": {"shadow_after_rejects": 2}})
            h.engine.trigger_manual("第一次主动", class_="social")
            h.engine.on_user_message("别说了")
            h.engine.trigger_manual("第二次主动", class_="social")
            h.engine.on_user_message("不想聊")
            before = len(h.host.sent)
            _set_last_user(h.engine, 3)
            _set_last_social(h.engine, 3)
            _add_loop(h.engine)
            result = h.engine.tick()
            h.close()
            assert h.engine.state.shadow_until > clock.now()
            assert len(h.host.sent) == before
            return True, f"shadow_until={h.engine.state.shadow_until}，后续不发送"

        # S1 窗口重叠
        def s1():
            try:
                validate_config(deep_merge(DEFAULT_CONFIG, {"window": {"quiet_start": "08:00", "quiet_end": "10:00"}}))
            except ConfigError as exc:
                return True, str(exc)
            return False, "未拒绝重叠窗口"

        # S2 不可达评分
        def s2():
            try:
                validate_config(deep_merge(DEFAULT_CONFIG, {"gate": {"min_confidence": 0.99, "score_threshold": 0.99}}))
            except ConfigError as exc:
                return True, str(exc)
            return False, "未拒绝不可达配置"

        # S3 预算不足
        def s3():
            try:
                validate_config(deep_merge(DEFAULT_CONFIG, {"timing": {"daily_budget": 10, "per_kind_budget": 4}}))
            except ConfigError as exc:
                return True, str(exc)
            return False, "未拒绝预算不足"

        # S4 账本可写可读
        def s4():
            clock.set(_ts(h=12))
            h = harness("s4")
            ledger_id, reason = h.engine.store.reserve_ledger(
                "s4", "2026-10-28", "social", "social", "D2", "intent-test", 1, clock.now(), h.engine.cfg,
            )
            assert ledger_id and not reason
            h.engine.store.mark_ledger(ledger_id, "sent")
            rows = h.engine.store.ledger_rows("s4")
            h.close()
            assert rows and rows[0]["status"] == "sent"
            return True, f"ledger_id={ledger_id}"

        # S5 探针必须显式 allow-send
        def s5():
            clock.set(_ts(h=12))
            h = harness("s5")
            no = h.engine.probe(allow_send=False)
            yes = h.engine.probe(allow_send=True)
            h.close()
            assert not no["sent"] and yes["sent"] and len(h.host.sent) == 1
            return True, "未授权不发；--allow-send 发到假宿主"

        # S6 记忆不可用，主循环仍能跑
        def s6():
            clock.set(_ts(h=12))
            h = harness("s6", {"recall": {"mode": "memory_import", "memory_root": "../missing-memory"}})
            _set_last_user(h.engine, 3)
            _set_last_social(h.engine, 3)
            _add_loop(h.engine)
            result = h.engine.tick()
            items = h.engine.recall.recall("测试")
            h.close()
            assert items == []
            assert result["dispatch"]["action"] in ("sent", "shadowed", "blocked"), result
            return True, "recall 失败返回空，tick 未抛错"

        # 额外：D1 推断
        def d1_infer():
            clock.set(_ts(h=12))
            h = harness("d1")
            h.engine.state.last_user_at = h.engine.now() - 40 * 60
            h.engine.state.last_user_text = "刚才说到一半"
            h.engine.state.last_reply_at = h.engine.now() - 3 * 3600
            h.engine.state.last_social_bot_at = h.engine.now() - 3 * 3600
            result = h.engine.tick()
            h.close()
            assert "D1" in result["desires"], result
            return True, "40 分钟未回，D1 生成"

        # 额外：D7 从反馈推断
        def d7_infer():
            clock.set(_ts(h=12))
            h = harness("d7")
            h.engine.state.last_user_at = h.engine.now() - 10 * 60
            h.engine.state.last_user_text = "嗯"
            h.engine.state.recent_feedback = [
                {"ts": h.engine.now() - 3600, "verdict": "reject"},
                {"ts": h.engine.now() - 1800, "verdict": "reject"},
            ]
            result = h.engine.tick()
            h.close()
            assert "D7" in result["desires"], result
            return True, "两次 reject → D7 生成"

        # 额外：mode 热切换
        def mode_switch():
            clock.set(_ts(h=12))
            h = harness("mode")
            h.engine.set_mode("off")
            r0 = h.engine.tick()
            assert not h.host.sent and r0["mode"] == "off"
            h.engine.set_mode("shadow")
            _set_last_user(h.engine, 3)
            _set_last_social(h.engine, 3)
            _add_loop(h.engine)
            r1 = h.engine.tick()
            assert r1["dispatch"]["action"] in ("shadowed", "blocked"), r1
            assert not h.host.sent
            h.engine.set_mode("enforce")
            h.engine.tick()
            h.close()
            return True, "off→shadow→enforce 热切换"

        for name, fn in [
            ("A1_quiet_window", a1),
            ("A2_closed_until", a2),
            ("A3_scheduled_not_social", a3),
            ("A4_background_no_budget", a4),
            ("A5_defer_retry_once", a5),
            ("A6_model_budget", a6),
            ("A7_quota", a7),
            ("A8_manual_quiet", a8),
            ("A9_no_evidence_idle", a9),
            ("A10_reject_shadow", a10),
            ("S1_window_overlap", s1),
            ("S2_unreachable_score", s2),
            ("S3_budget_math", s3),
            ("S4_ledger_io", s4),
            ("S5_probe", s5),
            ("S6_recall_fail_open", s6),
            ("D1_inference", d1_infer),
            ("D7_inference", d7_infer),
            ("mode_hot_switch", mode_switch),
        ]:
            _record(name, checks, fn)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    ok = all(item["ok"] for item in checks)
    return {
        "ok": ok,
        "checks": checks,
        "passed": len([c for c in checks if c["ok"]]),
        "total": len(checks),
        "duration_ms": int((time.time() - started) * 1000),
    }
