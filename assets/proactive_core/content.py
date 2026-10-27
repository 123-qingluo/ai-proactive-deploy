"""内容生成：默认 evidence 模板；可选 LLM 只能使用 evidence。"""
from __future__ import annotations

import json
from pathlib import Path

from .config import resolve


DEFAULT_TEMPLATES = {
    "D1": "刚才你说「{quote}」，我还没接住，现在说还来得及吗？",
    "D2": "你之前提到「{loop}」，时间差不多了，还顺利吗？",
    "D3": "「{loop}」我一直记着，等你有空再说。",
    "D4": "今天还好吗？「{quote}」之后一直有点惦记。",
    "D5": "上次问你的「{quote}」，后来有结果了吗？",
    "D6": "突然想起「{memory_title}」。",
    "D7": "刚刚你说「{quote}」，如果不想聊也没关系，我在。",
    "D8": "刚看到「{evidence}」，想起你。",
    "D9": "该做「{scheduled}」了。",
}
FALLBACK = "突然想起「{evidence}」。"


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


class ContentComposer:
    def __init__(self, root: str | Path, cfg: dict, llm, tracer):
        self.root = Path(root)
        self.cfg = cfg
        self.llm = llm
        self.tracer = tracer
        self.content_cfg = cfg.get("content", {})
        self.max_chars = int(self.content_cfg.get("max_chars", 120))
        self.templates = dict(DEFAULT_TEMPLATES)
        path = resolve(self.root, self.content_cfg.get("templates_file", "templates/content_templates.json"))
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self.templates.update({str(k): str(v) for k, v in data.items()})
            except (OSError, json.JSONDecodeError):
                pass

    def _prompt(self) -> str:
        path = self.root / "prompts" / "content.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return (
            "你是主动消息文案器。只使用给定 evidence 中的事实，不得编造。"
            "输出一条中文短消息，不要解释，不要引号。"
        )

    def compose(self, candidate, persona: str | None = None) -> tuple[str, str]:
        evidence = (candidate.evidence or "").strip()
        if not evidence:
            return "", "no_evidence"
        values = _SafeDict({
            "evidence": evidence[:80],
            "quote": evidence[:60],
            "loop": evidence[:80],
            "memory_title": evidence[:60],
            "scheduled": evidence[:80],
        })
        values.update({k: v for k, v in (candidate.meta or {}).items() if isinstance(v, (str, int, float))})

        template = self.templates.get(candidate.kind, FALLBACK)
        try:
            text = template.format_map(values)
        except (KeyError, ValueError):
            text = FALLBACK.format_map(values)
        text = " ".join(text.split()).strip()
        source = "template"

        if (self.content_cfg.get("provider") or "none").lower() == "http" and getattr(self.llm, "available", False):
            payload = {
                "kind": candidate.kind,
                "evidence": evidence,
                "persona": persona or "",
                "max_chars": self.max_chars,
            }
            try:
                raw = self.llm.complete(self._prompt(), json.dumps(payload, ensure_ascii=False))
                candidate_text = " ".join((raw or "").strip().strip("\"'“”").split())
                if candidate_text and len(candidate_text) <= self.max_chars:
                    text = candidate_text
                    source = "llm"
            except Exception as exc:
                self.tracer.log("content", "llm_error", error=str(exc)[:200], kind=candidate.kind)

        if not text:
            text = FALLBACK.format_map(values)
            source = "fallback"
        if len(text) > self.max_chars:
            text = text[: self.max_chars - 1] + "…"
        return text, source
