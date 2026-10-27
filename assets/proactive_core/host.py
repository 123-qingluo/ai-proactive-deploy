"""宿主接口：now / send_text / recall / persona / audit。

默认 FileSinkHost 只把消息写进 data/outbox.jsonl，用于部署验收；真实投递必须替换 adapter.py。
"""
from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Protocol


class HostError(RuntimeError):
    pass


class Host(Protocol):
    def now(self) -> float: ...
    def send_text(self, text: str, strategy: str) -> bool | None: ...
    def recall(self, query: str, top_k: int) -> list[tuple[str, float, str]]: ...
    def persona(self, context: dict) -> str | None: ...
    def audit(self, event: dict) -> None: ...


class FileSinkHost:
    """验收用宿主：不连接任何 IM，只把待发消息落盘。"""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.path = self.root / "data" / "outbox.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def now(self) -> float:
        return time.time()

    def send_text(self, text: str, strategy: str) -> bool | None:
        record = {"ts": time.time(), "strategy": strategy, "text": text, "delivered": True, "sink": "file"}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True

    def recall(self, query: str, top_k: int) -> list[tuple[str, float, str]]:
        return []

    def persona(self, context: dict) -> str | None:
        return None

    def audit(self, event: dict) -> None:
        return None


def load_host(root: str | Path, cfg: dict):
    root = Path(root).resolve()
    spec = str(cfg.get("host", {}).get("adapter", "adapter:create_host"))
    module_name, _, factory_name = spec.partition(":")
    if not module_name or not factory_name:
        raise HostError(f"host.adapter 格式应为 module:factory，当前 {spec!r}")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        module = importlib.import_module(module_name)
        factory = getattr(module, factory_name)
        return factory(str(root), cfg)
    except Exception as exc:
        raise HostError(f"无法加载宿主适配器 {spec}: {exc}") from exc
