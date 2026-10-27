"""审计：每轮一行 JSONL，另可转发给宿主 audit()。"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path


class Tracer:
    def __init__(self, trace_dir: str | Path, enabled: bool = True, host=None):
        self.dir = Path(trace_dir)
        self.enabled = enabled
        self.host = host
        if self.enabled:
            self.dir.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, stream_id: str = "default", **fields) -> dict:
        now = time.time()
        record = {
            "ts": round(now, 3),
            "iso": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
            "event": event,
            "stream": stream_id,
        }
        record.update(fields)
        if self.enabled:
            day = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
            try:
                with (self.dir / f"{day}.jsonl").open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            except OSError:
                pass
        if self.host is not None:
            try:
                self.host.audit(record)
            except Exception:
                pass
        return record

    def tail(self, limit: int = 20) -> list[dict]:
        if not self.dir.exists():
            return []
        rows: list[dict] = []
        for path in sorted(self.dir.glob("*.jsonl"), reverse=True):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
                if len(rows) >= limit:
                    return rows
        return rows
