"""SQLite 存储：状态、开放话题、计划项、意图、账本、时机预算、控制表。"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .models import Intent, State


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=5000")

    def init(self) -> None:
        schema = (Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8")
        self.conn.executescript(schema)
        row = self.conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        if row is None:
            self.conn.execute("INSERT INTO schema_meta(key, value) VALUES('schema_version','1')")
        elif int(row["value"]) > 1:
            raise RuntimeError("数据库 schema 版本高于当前代码支持版本")

    @contextmanager
    def tx(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    def fetchone(self, sql: str, params=()):
        return self.conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params=()):
        return self.conn.execute(sql, params).fetchall()

    def execute(self, sql: str, params=()):
        return self.conn.execute(sql, params)


class Store:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------- state
    def load_state(self, stream_id: str) -> State:
        row = self.db.fetchone("SELECT json FROM state WHERE stream_id = ?", (stream_id,))
        if row is None:
            return State(stream_id=stream_id)
        try:
            return State.from_dict(json.loads(row["json"]), stream_id=stream_id)
        except (json.JSONDecodeError, TypeError):
            return State(stream_id=stream_id)

    def save_state(self, state: State, now: float) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO state(stream_id, json, updated_at) VALUES (?, ?, ?)",
            (state.stream_id, json.dumps(state.to_dict(), ensure_ascii=False), now),
        )

    # ------------------------------------------------------------- control
    def control_get(self, key: str, default=None):
        row = self.db.fetchone("SELECT value FROM control WHERE key = ?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return default

    def control_set(self, key: str, value, now: float) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO control(key, value, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(value, ensure_ascii=False), now),
        )

    def get_mode(self, default: str = "shadow") -> str:
        value = self.control_get("mode", default)
        return value if value in ("off", "shadow", "enforce") else default

    def set_mode(self, mode: str, now: float) -> None:
        if mode not in ("off", "shadow", "enforce"):
            raise ValueError("mode 只能是 off/shadow/enforce")
        self.control_set("mode", mode, now)

    # ------------------------------------------------------------- open loops
    def add_open_loop(
        self, stream_id: str, loop_id: str, text: str, evidence: str,
        due_at: float, created_at: float, expires_at: float, meta: dict | None = None,
    ) -> None:
        self.db.execute(
            "INSERT INTO open_loops(loop_id, stream_id, text, evidence, due_at, created_at, expires_at, meta) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (loop_id, stream_id, text, evidence, due_at, created_at, expires_at,
             json.dumps(meta or {}, ensure_ascii=False)),
        )

    def open_loops(self, stream_id: str, status: str | None = "open", limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM open_loops WHERE stream_id = ?"
        params: list[Any] = [stream_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY due_at ASC LIMIT ?"
        params.append(int(limit))
        return [self._loop_row(row) for row in self.db.fetchall(sql, tuple(params))]

    def due_loops(self, stream_id: str, now: float) -> list[dict]:
        rows = self.db.fetchall(
            "SELECT * FROM open_loops WHERE stream_id = ? AND status = 'open' AND due_at <= ? "
            "ORDER BY due_at ASC",
            (stream_id, now),
        )
        return [self._loop_row(row) for row in rows]

    @staticmethod
    def _loop_row(row) -> dict:
        data = dict(row)
        try:
            data["meta"] = json.loads(data.get("meta") or "{}")
        except json.JSONDecodeError:
            data["meta"] = {}
        return data

    def mark_loop(self, loop_id: str, status: str, now: float) -> None:
        fields = "status = ?"
        params: list[Any] = [status]
        if status == "notified":
            fields += ", notified_at = ?"
            params.append(now)
        params.append(loop_id)
        self.db.execute(f"UPDATE open_loops SET {fields} WHERE loop_id = ?", tuple(params))

    def expire_loops(self, stream_id: str, now: float) -> int:
        cur = self.db.execute(
            "UPDATE open_loops SET status = 'expired' WHERE stream_id = ? "
            "AND status IN ('open','notified') AND expires_at <= ?",
            (stream_id, now),
        )
        return cur.rowcount

    def count_open_loops(self, stream_id: str) -> int:
        row = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM open_loops WHERE stream_id = ? AND status IN ('open','notified')",
            (stream_id,),
        )
        return int(row["c"])

    def count_loops_since(self, stream_id: str, since: float) -> int:
        row = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM open_loops WHERE stream_id = ? AND created_at >= ?",
            (stream_id, since),
        )
        return int(row["c"])

    # ------------------------------------------------------------- scheduled
    def add_scheduled(
        self, stream_id: str, item_id: str, title: str, due_at: float, created_at: float,
        priority: float = 0.5, face_risk: float = 0.2, meta: dict | None = None,
    ) -> None:
        self.db.execute(
            "INSERT INTO scheduled_items(item_id, stream_id, title, due_at, created_at, priority, face_risk, meta) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (item_id, stream_id, title, due_at, created_at, priority, face_risk,
             json.dumps(meta or {}, ensure_ascii=False)),
        )

    def due_scheduled(self, stream_id: str, now: float) -> list[dict]:
        rows = self.db.fetchall(
            "SELECT * FROM scheduled_items WHERE stream_id = ? AND status = 'pending' AND due_at <= ? "
            "ORDER BY due_at ASC",
            (stream_id, now),
        )
        out = []
        for row in rows:
            data = dict(row)
            try:
                data["meta"] = json.loads(data.get("meta") or "{}")
            except json.JSONDecodeError:
                data["meta"] = {}
            out.append(data)
        return out

    def scheduled_items(self, stream_id: str, status: str | None = None) -> list[dict]:
        sql = "SELECT * FROM scheduled_items WHERE stream_id = ?"
        params: list[Any] = [stream_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY due_at ASC"
        return [dict(r) for r in self.db.fetchall(sql, tuple(params))]

    def mark_scheduled(self, item_id: str, status: str) -> None:
        self.db.execute("UPDATE scheduled_items SET status = ? WHERE item_id = ?", (status, item_id))

    # ------------------------------------------------------------- intents
    def insert_intent_for(self, stream_id: str, intent: Intent) -> None:
        self.db.execute(
            "INSERT INTO intents(intent_id, stream_id, kind, \"class\", quota_class, score, priority, "
            "face_risk, confidence, evidence, evidence_type, content, status, created_at, expires_at, "
            "deferred_until, defer_count, manual, meta) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                intent.intent_id, stream_id, intent.kind, intent.class_, intent.quota_class, intent.score,
                intent.priority, intent.face_risk, intent.confidence, intent.evidence, intent.evidence_type,
                intent.content, intent.status, intent.created_at, intent.expires_at,
                intent.deferred_until, intent.defer_count, 1 if intent.manual else 0,
                json.dumps(intent.meta, ensure_ascii=False),
            ),
        )

    def update_intent(self, intent_id: str, **fields) -> None:
        allowed = {
            "content", "status", "deferred_until", "defer_count", "meta",
        }
        cleaned = {k: v for k, v in fields.items() if k in allowed}
        if not cleaned:
            return
        if "meta" in cleaned:
            cleaned["meta"] = json.dumps(cleaned["meta"], ensure_ascii=False)
        sets = ", ".join(f"{k} = ?" for k in cleaned)
        self.db.execute(f"UPDATE intents SET {sets} WHERE intent_id = ?", (*cleaned.values(), intent_id))

    def get_intent(self, intent_id: str) -> dict | None:
        row = self.db.fetchone("SELECT * FROM intents WHERE intent_id = ?", (intent_id,))
        return self._intent_row(row) if row else None

    def list_intents(self, stream_id: str, statuses: Iterable[str] | None = None, limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM intents WHERE stream_id = ?"
        params: list[Any] = [stream_id]
        statuses = list(statuses or [])
        if statuses:
            sql += " AND status IN (" + ",".join("?" for _ in statuses) + ")"
            params.extend(statuses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(int(limit))
        return [self._intent_row(r) for r in self.db.fetchall(sql, tuple(params))]

    @staticmethod
    def _intent_row(row) -> dict:
        data = dict(row)
        data["class_"] = data.pop("class")
        data["manual"] = bool(data.get("manual"))
        try:
            data["meta"] = json.loads(data.get("meta") or "{}")
        except json.JSONDecodeError:
            data["meta"] = {}
        return data

    def expire_intents(self, stream_id: str, now: float) -> int:
        cur = self.db.execute(
            "UPDATE intents SET status = 'skipped' WHERE stream_id = ? "
            "AND status IN ('pending','deferred') AND expires_at > 0 AND expires_at <= ?",
            (stream_id, now),
        )
        return cur.rowcount

    def count_intents_since(self, stream_id: str, since: float) -> int:
        row = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM intents WHERE stream_id = ? AND created_at >= ?",
            (stream_id, since),
        )
        return int(row["c"])

    # ------------------------------------------------------------- ledger / quota
    def quota_count(self, stream_id: str, day: str, class_: str) -> tuple[int, int]:
        row = self.db.fetchone(
            "SELECT COUNT(*) AS c, COALESCE(SUM(bubbles),0) AS b FROM ledger "
            "WHERE stream_id = ? AND day = ? AND \"class\" = ? AND status IN ('reserved','sent')",
            (stream_id, day, class_),
        )
        return int(row["c"]), int(row["b"])

    def reserve_ledger(
        self, stream_id: str, day: str, class_: str, quota_class: str, kind: str,
        intent_id: str, bubbles: int, now: float, cfg: dict, bypass_quota: bool = False,
    ) -> tuple[int, str]:
        dispatch = cfg.get("dispatch", {})
        quotas = dispatch.get("quotas", {})
        bubble_quotas = dispatch.get("bubble_quotas", {})
        quota = int(quotas.get(class_, -1))
        bubble_quota = int(bubble_quotas.get(class_, -1))
        per_event = int(dispatch.get("per_event_bubbles", 2))
        if bubbles < 1 or bubbles > per_event:
            return 0, "bubbles"
        if not bypass_quota:
            count, bubble_count = self.quota_count(stream_id, day, class_)
            if quota == 0 or (quota > 0 and count >= quota):
                return 0, "quota"
            if bubble_quota == 0 or (bubble_quota > 0 and bubble_count + bubbles > bubble_quota):
                return 0, "bubble_quota"
        with self.db.tx() as conn:
            cur = conn.execute(
                "INSERT INTO ledger(stream_id, day, \"class\", quota_class, kind, intent_id, bubbles, status, ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'reserved', ?)",
                (stream_id, day, class_, quota_class, kind, intent_id, bubbles, now),
            )
            return int(cur.lastrowid), ""

    def mark_ledger(self, ledger_id: int, status: str) -> None:
        self.db.execute("UPDATE ledger SET status = ? WHERE id = ?", (status, ledger_id))

    def ledger_rows(self, stream_id: str, limit: int = 50) -> list[dict]:
        return [
            dict(r) for r in self.db.fetchall(
                "SELECT * FROM ledger WHERE stream_id = ? ORDER BY id DESC LIMIT ?",
                (stream_id, int(limit)),
            )
        ]

    def count_sent_kind(self, stream_id: str, day: str, kind: str) -> int:
        row = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM ledger WHERE stream_id = ? AND day = ? AND kind = ? AND status = 'sent'",
            (stream_id, day, kind),
        )
        return int(row["c"])

    # ------------------------------------------------------------- timing budget
    def timing_counts(self, stream_id: str, day: str, hour: str, budget_class: str) -> tuple[int, int, int]:
        daily = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM timing_calls WHERE stream_id = ? AND day = ?",
            (stream_id, day),
        )["c"]
        per_kind = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM timing_calls WHERE stream_id = ? AND day = ? AND budget_class = ?",
            (stream_id, day, budget_class),
        )["c"]
        hourly = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM timing_calls WHERE stream_id = ? AND day = ? AND hour = ?",
            (stream_id, day, hour),
        )["c"]
        return int(daily), int(per_kind), int(hourly)

    def can_call_timing(self, stream_id: str, day: str, hour: str, budget_class: str, cfg: dict) -> tuple[bool, str]:
        timing = cfg.get("timing", {})
        daily_budget = int(timing.get("daily_budget", 24))
        per_kind = int(timing.get("per_kind_budget", 4))
        hourly_budget = int(timing.get("hourly_budget", 2))
        daily, by_kind, hourly = self.timing_counts(stream_id, day, hour, budget_class)
        if daily >= daily_budget:
            return False, "daily_budget"
        if by_kind >= per_kind:
            return False, "per_kind_budget"
        if hourly >= hourly_budget:
            return False, "hourly_budget"
        return True, ""

    def record_timing_call(self, stream_id: str, day: str, hour: str, budget_class: str, ts: float, model_ok: bool) -> None:
        self.db.execute(
            "INSERT INTO timing_calls(stream_id, day, hour, budget_class, ts, model_ok) VALUES (?, ?, ?, ?, ?, ?)",
            (stream_id, day, hour, budget_class, ts, 1 if model_ok else 0),
        )

    # ------------------------------------------------------------- stats
    def stats(self, stream_id: str) -> dict:
        loops = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM open_loops WHERE stream_id = ? AND status IN ('open','notified')",
            (stream_id,),
        )["c"]
        pending = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM intents WHERE stream_id = ? AND status IN ('pending','deferred')",
            (stream_id,),
        )["c"]
        sent = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM ledger WHERE stream_id = ? AND status = 'sent'",
            (stream_id,),
        )["c"]
        return {"open_loops": int(loops), "pending_intents": int(pending), "sent_total": int(sent)}
