"""配置：全部阈值集中在 JSON，代码不得硬编码。"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .models import BUDGET_CLASSES, DESIRE_DEFAULTS
from .timeutil import TimeError, get_tz, parse_hhmm, windows_overlap

CONFIG_VERSION = 1

DEFAULT_CONFIG: dict[str, Any] = {
    "config_version": CONFIG_VERSION,
    "stream_id": "default",
    "timezone": "local",
    "mode": "shadow",  # off | shadow | enforce
    "tick": {"seconds": 60},
    "window": {
        "active_start": "09:00",
        "active_end": "24:00",
        "quiet_start": "00:00",
        "quiet_end": "09:00",
    },
    "gate": {
        "score_threshold": 0.50,
        "min_confidence": 0.55,
        "max_face_risk": 0.65,
        "new_topic_user_silence_minutes": 60,
        "min_interval_minutes": 120,
        "min_interval_by_kind": {"D8": 60, "D7": 120},
        "daily_kind_caps": {},
    },
    "desires": {
        "reconnect_after_hours": 3,
        "idle_share_after_minutes": 60,
        "follow_up_delay_minutes": 45,
        "follow_up_max_age_minutes": 360,
        "owed_reply_infer_minutes": 30,
        "owed_reply_infer_enabled": True,
        "relation_infer_enabled": True,
        "relation_threshold": 2,
        "d4_daily_cap": 1,
        "d5_max_follow_ups": 2,
    },
    "timing": {
        "provider": "none",  # none | http
        "base_url": "",
        "api_key": "",
        "model": "",
        "timeout_s": 20,
        "daily_budget": 24,
        "per_kind_budget": 4,
        "hourly_budget": 2,
        "cache_minutes": 30,
        "defer_min_minutes": 5,
        "defer_max_minutes": 360,
        "fallback_after_streak": 3,
        "deadline_send_minutes": 30,
    },
    "dispatch": {
        "system_min_gap_seconds": 600,
        "respect_window_for_system": False,
        "quotas": {"social": 24, "scheduled": 24, "system": -1},
        "bubble_quotas": {"social": 60, "scheduled": 60, "system": -1},
        "per_event_bubbles": 2,
        "strategies": {"social": "direct", "scheduled": "wait_idle", "system": "direct"},
    },
    "open_loop": {
        "ttl_hours": 48,
        "max_open": 30,
        "max_new_per_day": 3,
        "use_llm_commitment": False,
    },
    "feedback": {
        "response_window_minutes": 120,
        "sentiment_window_minutes": 30,
        "decay_floor": 0.15,
        "pace_cap": 3.0,
        "shadow_after_rejects": 1,
        "shadow_after_confused": 2,
        "behavior_weight": 0.7,
        "fast_reply_minutes": 5,
        "multi_reply_window_minutes": 5,
        "timeout_pace_multiplier": 1.5,
        "shadow_hours": 24,
        "closed_hours": 12,
        "reject_decay": 0.5,
        "respond_decay": 0.3,
        "reject_quiet_minutes": 120,
    },
    "content": {
        "provider": "none",  # none | http
        "base_url": "",
        "api_key": "",
        "model": "",
        "timeout_s": 20,
        "max_chars": 120,
        "templates_file": "templates/content_templates.json",
    },
    "recall": {
        "mode": "none",  # none | host | memory_import | memory_cli
        "memory_root": "../memory",
        "folder_id": "default",
        "top_k": 3,
        "min_score": 0.3,
        "fail_open": True,
        "timeout_s": 8,
    },
    "audit": {"trace_dir": "traces", "jsonl": True},
    "storage": {"db_path": "data/proactive.db"},
    "host": {"adapter": "adapter:create_host"},
    "daemon": {
        "pid_file": "data/proactive.pid",
        "stop_file": "data/STOP",
        "log_file": "data/daemon.log",
        "interval_seconds": 60,
    },
}


class ConfigError(ValueError):
    pass


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_config(root: str | Path, overrides: dict | None = None) -> dict:
    root = Path(root)
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    path = root / "config" / "proactive.config.json"
    if not path.exists():
        path = root / "proactive.config.json"
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            cfg = deep_merge(cfg, json.load(fh))
    if overrides:
        cfg = deep_merge(cfg, overrides)
    return cfg


def resolve(root: str | Path, relative: str) -> Path:
    p = Path(relative)
    return p if p.is_absolute() else Path(root) / p


def save_config(root: str | Path, cfg: dict) -> Path:
    path = Path(root) / "config" / "proactive.config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return path


def validate_config(cfg: dict) -> list[str]:
    """返回 warnings；无法运行或永远无候选的配置抛 ConfigError。"""
    warnings: list[str] = []
    if cfg.get("config_version") != CONFIG_VERSION:
        raise ConfigError(f"config_version 必须是 {CONFIG_VERSION}")
    if not str(cfg.get("stream_id") or "").strip():
        raise ConfigError("stream_id 不能为空")
    if cfg.get("mode") not in ("off", "shadow", "enforce"):
        raise ConfigError("mode 只能是 off/shadow/enforce")

    try:
        get_tz(cfg.get("timezone"))
    except TimeError as exc:
        raise ConfigError(str(exc)) from exc

    window = cfg.get("window", {})
    try:
        for key in ("active_start", "active_end", "quiet_start", "quiet_end"):
            parse_hhmm(window.get(key, ""))
    except TimeError as exc:
        raise ConfigError(str(exc)) from exc
    if windows_overlap(
        window["active_start"], window["active_end"],
        window["quiet_start"], window["quiet_end"],
    ):
        raise ConfigError("S1: active 与 quiet 窗口重叠")

    gate = cfg.get("gate", {})
    threshold = float(gate.get("score_threshold", 0.5))
    min_conf = float(gate.get("min_confidence", 0.55))
    max_face = float(gate.get("max_face_risk", 0.65))
    if not (0 <= threshold <= 1 and 0 <= min_conf <= 1 and 0 <= max_face <= 1):
        raise ConfigError("gate 阈值必须在 0~1")
    if min_conf > max_face:
        warnings.append("min_confidence 高于 max_face_risk：允许，但候选池会很小")
    max_priority = max(v["priority"] for v in DESIRE_DEFAULTS.values())
    exp_term = max(0.2, 1 - 0.5 * max_face)
    best = 0.30 * min_conf + 0.25 * max_priority + 0.20 * exp_term + 0.25 - 0.20 * max_face
    if best < threshold:
        raise ConfigError(
            f"S2: 在 min_confidence={min_conf}、max_face_risk={max_face} 下最高分 {best:.3f} "
            f"低于 score_threshold={threshold}，永远不会产生候选"
        )
    if min_conf >= threshold:
        warnings.append("S2: min_confidence >= score_threshold；可运行，但会先被置信度截断")

    timing = cfg.get("timing", {})
    daily = int(timing.get("daily_budget", 24))
    per_kind = int(timing.get("per_kind_budget", 4))
    hourly = int(timing.get("hourly_budget", 2))
    if daily < per_kind * len(BUDGET_CLASSES):
        raise ConfigError(
            f"S3: daily_budget={daily} < per_kind_budget={per_kind} × {len(BUDGET_CLASSES)} 个预算类"
        )
    if hourly < 1:
        raise ConfigError("timing.hourly_budget 必须 ≥1")
    if not (0 <= int(timing.get("defer_min_minutes", 5)) <= int(timing.get("defer_max_minutes", 360))):
        raise ConfigError("defer_min_minutes 不能大于 defer_max_minutes")

    dispatch = cfg.get("dispatch", {})
    for name in ("quotas", "bubble_quotas"):
        for key, value in (dispatch.get(name) or {}).items():
            if int(value) < -1:
                raise ConfigError(f"dispatch.{name}.{key} 必须 ≥ -1")
    if int(dispatch.get("per_event_bubbles", 1)) < 1:
        raise ConfigError("dispatch.per_event_bubbles 必须 ≥1")

    if float(cfg.get("content", {}).get("max_chars", 120)) < 10:
        raise ConfigError("content.max_chars 过小")
    if cfg.get("recall", {}).get("mode") not in ("none", "host", "memory_import", "memory_cli"):
        raise ConfigError("recall.mode 不合法")
    return warnings
