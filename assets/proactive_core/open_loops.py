"""开放话题：从用户原话里抽“承诺 + 时间词”，不猜没有时间锚点的内容。"""
from __future__ import annotations

import re

from .models import new_id
from .timeutil import day_start, parse_due_hint

CLOSED_RE = re.compile(r"睡了|晚安|先睡|不聊了|别发了|安静|闭嘴|退下|勿扰|静音|明天再说")
COMMIT_RE = re.compile(
    r"答应|约好|说好|回头|晚点|帮你|给我|查|提醒|记得|见面|发你|给你|带你|请你|"
    r"一起|安排|截止|报名|交|完成|搞定|准备|展览|考试|面试|复诊|出差|旅行|聚会"
)
TIME_RE = re.compile(
    r"今天|明天|后天|大后天|今晚|明晚|下周|下个?周|周末|这周末|本周末|月底|月末|"
    r"下个?月|(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]|(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})"
)
DONE_RE = re.compile(r"已经|完成了|搞定了|不用了|取消了|结束了|办完了")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _bigrams(text: str) -> set[str]:
    text = _normalize(text)
    if len(text) < 2:
        return set(text)
    return {text[i : i + 2] for i in range(len(text) - 1)}


def _overlap(a: str, b: str) -> float:
    sa, sb = _bigrams(a), _bigrams(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa)


class OpenLoopService:
    def __init__(self, store, cfg: dict, tracer, tz):
        self.store = store
        self.cfg = cfg
        self.tracer = tracer
        self.tz = tz
        self.loop_cfg = cfg.get("open_loop", {})

    def observe_user_text(self, stream_id: str, text: str, now: float) -> dict:
        text = (text or "").strip()
        if not text:
            return {"added": 0, "completed": 0, "skipped": "empty"}
        if CLOSED_RE.search(text):
            self.tracer.log("open_loop", stream_id=stream_id, status="closed_signal")
            return {"added": 0, "completed": 0, "skipped": "closed_signal"}

        completed = self._complete_matching(stream_id, text, now)
        added = 0
        max_add = int(self.loop_cfg.get("max_new_per_day", 3))
        if self.store.count_loops_since(stream_id, day_start(now, self.tz)) >= max_add:
            return {"added": 0, "completed": completed, "skipped": "daily_cap"}
        open_count = self.store.count_open_loops(stream_id)
        max_open = int(self.loop_cfg.get("max_open", 30))

        existing = (
            self.store.open_loops(stream_id, status="open", limit=200)
            + self.store.open_loops(stream_id, status="notified", limit=200)
        )
        known = {_normalize(item["text"]) for item in existing}
        sentences = re.split(r"[。！？!?\n]+", text)
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 4 or not TIME_RE.search(sentence) or not COMMIT_RE.search(sentence):
                continue
            due_at = parse_due_hint(sentence, now, self.tz)
            if due_at is None:
                continue
            norm = _normalize(sentence)
            if any(norm in k or k in norm for k in known if len(norm) >= 4):
                continue
            if open_count + added >= max_open:
                break
            loop_id = new_id("loop")
            ttl = float(self.loop_cfg.get("ttl_hours", 48)) * 3600
            expires_at = max(now, due_at) + ttl
            self.store.add_open_loop(
                stream_id, loop_id, sentence[:160], sentence,
                due_at, now, expires_at, meta={"time_hint": sentence},
            )
            known.add(norm)
            added += 1
            self.tracer.log(
                "open_loop", stream_id=stream_id, status="added",
                loop_id=loop_id, due_at=due_at, text=sentence[:80],
            )
        return {"added": added, "completed": completed, "skipped": ""}

    def _complete_matching(self, stream_id: str, text: str, now: float) -> int:
        if not DONE_RE.search(text):
            return 0
        completed = 0
        for item in self.store.open_loops(stream_id, status="open", limit=100):
            if _overlap(text, item["text"]) >= 0.35:
                self.store.mark_loop(item["loop_id"], "done", now)
                completed += 1
                self.tracer.log("open_loop", stream_id=stream_id, status="done", loop_id=item["loop_id"])
        return completed

    def expire(self, stream_id: str, now: float) -> int:
        return self.store.expire_loops(stream_id, now)
