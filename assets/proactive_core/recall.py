"""记忆召回适配：none / host / memory_import / memory_cli。

任何失败都返回空列表，不抛错；主动主循环不能因为记忆服务挂了而停。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


class RecallService:
    def __init__(self, root: str | Path, cfg: dict, tracer, host=None):
        self.root = Path(root).resolve()
        self.cfg = cfg
        self.tracer = tracer
        self.host = host
        self.recall_cfg = cfg.get("recall", {})
        self._memory_system = None
        self._memory_scope_cls = None

    def _items(self, raw) -> list[tuple[str, float, str]]:
        out = []
        min_score = float(self.recall_cfg.get("min_score", 0.3))
        for row in raw or []:
            try:
                if isinstance(row, dict):
                    rid, score, title = row.get("id"), float(row.get("score", 0)), row.get("title", "")
                else:
                    rid, score, title = row[0], float(row[1]), row[2]
            except (TypeError, ValueError, IndexError):
                continue
            if score >= min_score:
                out.append((str(rid), score, str(title)))
        return out

    def recall(self, query: str, top_k: int | None = None) -> list[tuple[str, float, str]]:
        mode = str(self.recall_cfg.get("mode", "none"))
        if not query or mode == "none":
            return []
        top_k = int(top_k or self.recall_cfg.get("top_k", 3))
        try:
            if mode == "host":
                if self.host is None:
                    return []
                return self._items(self.host.recall(query, top_k))
            if mode == "memory_import":
                return self._memory_import(query, top_k)
            if mode == "memory_cli":
                return self._memory_cli(query, top_k)
        except Exception as exc:
            self.tracer.log("recall", "error", mode=mode, error=str(exc)[:300])
            return []
        return []

    def _memory_root(self) -> Path:
        path = Path(str(self.recall_cfg.get("memory_root", "../memory")))
        return path if path.is_absolute() else (self.root / path).resolve()

    def _memory_import(self, query: str, top_k: int) -> list[tuple[str, float, str]]:
        memory_root = self._memory_root()
        if not (memory_root / "memory_core").exists():
            self.tracer.log("recall", "unavailable", mode="memory_import", reason="memory_core_not_found")
            return []
        if self._memory_system is None:
            if str(memory_root) not in sys.path:
                sys.path.insert(0, str(memory_root))
            from memory_core import MemorySystem  # type: ignore
            from memory_core.models import Scope  # type: ignore

            self._memory_scope_cls = Scope
            self._memory_system = MemorySystem.open(memory_root)
        scope = self._memory_scope_cls(folder_id=str(self.recall_cfg.get("folder_id", "default")))
        rows = self._memory_system.search(scope, query, top_k=top_k, include_knowledge=True)
        items = self._items(rows)
        self.tracer.log("recall", "ok" if items else "empty", mode="memory_import", count=len(items))
        return items

    def _memory_cli(self, query: str, top_k: int) -> list[tuple[str, float, str]]:
        memory_root = self._memory_root()
        run_py = memory_root / "run.py"
        if not run_py.exists():
            self.tracer.log("recall", "unavailable", mode="memory_cli", reason="run_py_not_found")
            return []
        cmd = [
            sys.executable, str(run_py), "search", query, "--json",
            "--folder", str(self.recall_cfg.get("folder_id", "default")),
            "-k", str(top_k),
        ]
        proc = subprocess.run(
            cmd, cwd=str(memory_root), text=True, encoding="utf-8",
            capture_output=True, timeout=float(self.recall_cfg.get("timeout_s", 8)),
        )
        if proc.returncode != 0:
            self.tracer.log("recall", "error", mode="memory_cli", code=proc.returncode, stderr=proc.stderr[:200])
            return []
        try:
            raw = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.tracer.log("recall", "error", mode="memory_cli", reason="bad_json")
            return []
        items = self._items(raw)
        self.tracer.log("recall", "ok" if items else "empty", mode="memory_cli", count=len(items))
        return items

    def close(self) -> None:
        if self._memory_system is not None:
            try:
                self._memory_system.close()
            except Exception:
                pass
            self._memory_system = None
