"""愿望生成：D1–D9。没有 evidence 的愿望不生成。"""
from __future__ import annotations

from .models import DESIRE_DEFAULTS, Desire
from .perceive import Belief


class DesireService:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.desire_cfg = cfg.get("desires", {})

    def generate(self, belief: Belief) -> list[Desire]:
        state = belief.raw_state
        now = belief.now
        out: list[Desire] = []
        counts = state.daily.setdefault("desire_counts", {})

        def add(desire: Desire, dedupe_key: str) -> None:
            if counts.get(dedupe_key, 0) >= 1:
                return
            counts[dedupe_key] = counts.get(dedupe_key, 0) + 1
            out.append(desire)

        # D1 补偿：显式 owed_reply 优先；否则保守推断
        owed = belief.owed_reply
        if not owed and self.desire_cfg.get("owed_reply_infer_enabled", True):
            infer_minutes = float(self.desire_cfg.get("owed_reply_infer_minutes", 30))
            if state.last_user_at > state.last_reply_at and now - state.last_user_at >= infer_minutes * 60:
                owed = True
        if owed:
            quote = belief.last_user_text
            if quote:
                defaults = DESIRE_DEFAULTS["D1"]
                add(Desire(
                    kind="D1", evidence=quote, evidence_type="quote",
                    priority=defaults["priority"], face_risk=defaults["face"],
                    confidence=0.82, reason="欠一句回应", class_="social", quota_class="social",
                    created_at=now, expires_at=now + 2 * 3600,
                    meta={"quote": quote[:60]},
                ), "D1")

        # D2 到期开放话题
        for loop in belief.due_loops:
            defaults = DESIRE_DEFAULTS["D2"]
            add(Desire(
                kind="D2", evidence=loop["text"], evidence_type="loop",
                priority=defaults["priority"], face_risk=defaults["face"],
                confidence=0.88, reason="开放话题到期", class_="social", quota_class="social",
                created_at=now, expires_at=float(loop["expires_at"]), due_at=float(loop["due_at"]),
                meta={"loop": loop["text"][:80], "loop_id": loop["loop_id"]},
            ), "D2")

        # D3 未到期开放话题
        if not belief.due_loops:
            for loop in belief.open_loops:
                defaults = DESIRE_DEFAULTS["D3"]
                add(Desire(
                    kind="D3", evidence=loop["text"], evidence_type="loop",
                    priority=defaults["priority"], face_risk=defaults["face"],
                    confidence=0.75, reason="记着的开放话题", class_="social", quota_class="social",
                    created_at=now, expires_at=float(loop["expires_at"]), due_at=float(loop["due_at"]),
                    meta={"loop": loop["text"][:80], "loop_id": loop["loop_id"]},
                ), "D3")

        # D4 关心
        reconnect = float(self.desire_cfg.get("reconnect_after_hours", 3)) * 3600
        if belief.user_silence >= reconnect and belief.last_user_text:
            defaults = DESIRE_DEFAULTS["D4"]
            add(Desire(
                kind="D4", evidence=belief.last_user_text, evidence_type="quote",
                priority=defaults["priority"], face_risk=defaults["face"],
                confidence=0.70, reason="用户长时间沉默", class_="social", quota_class="social",
                created_at=now, expires_at=now + 4 * 3600,
                meta={"quote": belief.last_user_text[:60]},
            ), "D4")

        # D5 追问：只在 bot 发问后的 45–360 分钟内
        delay = float(self.desire_cfg.get("follow_up_delay_minutes", 45)) * 60
        max_age = float(self.desire_cfg.get("follow_up_max_age_minutes", 360)) * 60
        max_follow = int(self.desire_cfg.get("d5_max_follow_ups", 2))
        if (
            belief.expects_reply and delay <= belief.expects_age <= max_age
            and state.follow_up_count < max_follow and belief.last_bot_text
        ):
            defaults = DESIRE_DEFAULTS["D5"]
            add(Desire(
                kind="D5", evidence=belief.last_bot_text, evidence_type="quote",
                priority=defaults["priority"], face_risk=defaults["face"],
                confidence=0.72, reason="等回复超时", class_="social", quota_class="social",
                created_at=now, expires_at=state.expects_since + max_age,
                meta={"quote": belief.last_bot_text[:60]},
            ), "D5")

        # D6 回忆
        if belief.recall_score >= 0.75 and not belief.open_loops and belief.recall_title:
            defaults = DESIRE_DEFAULTS["D6"]
            add(Desire(
                kind="D6", evidence=belief.recall_title, evidence_type="memory",
                priority=defaults["priority"], face_risk=defaults["face"],
                confidence=0.70, reason="高相关记忆", class_="social", quota_class="social",
                created_at=now, expires_at=now + 6 * 3600,
                meta={"memory_title": belief.recall_title[:60], "recall_score": round(belief.recall_score, 4)},
            ), "D6")

        # D7 情绪
        threshold = float(self.desire_cfg.get("relation_threshold", 2))
        if (belief.relation_anxiety >= threshold or belief.relation_shame >= threshold) and belief.last_user_text:
            defaults = DESIRE_DEFAULTS["D7"]
            add(Desire(
                kind="D7", evidence=belief.last_user_text, evidence_type="quote",
                priority=defaults["priority"], face_risk=defaults["face"],
                confidence=0.65, reason="关系状态需要修复", class_="social", quota_class="social",
                created_at=now, expires_at=now + 3 * 3600,
                meta={
                    "quote": belief.last_user_text[:60],
                    "anxiety": belief.relation_anxiety,
                    "shame": belief.relation_shame,
                },
            ), "D7")

        # D8 空闲分享：必须有依据，否则不生成（A9）
        idle_after = float(self.desire_cfg.get("idle_share_after_minutes", 60)) * 60
        if belief.user_silence >= idle_after:
            evidence = belief.last_user_text or belief.recall_title
            evidence_type = "quote" if belief.last_user_text else "memory"
            if evidence:
                defaults = DESIRE_DEFAULTS["D8"]
                meta = {"evidence": evidence[:80]}
                if evidence_type == "quote":
                    meta["quote"] = evidence[:60]
                else:
                    meta["memory_title"] = evidence[:60]
                add(Desire(
                    kind="D8", evidence=evidence, evidence_type=evidence_type,
                    priority=defaults["priority"], face_risk=defaults["face"],
                    confidence=0.60, reason="空闲且有依据", class_="social", quota_class="social",
                    created_at=now, expires_at=now + 6 * 3600, meta=meta,
                ), "D8")

        # D9 通用计划项（不内置学习/唱歌等业务）
        for item in belief.scheduled_due:
            add(Desire(
                kind="D9", evidence=item["title"], evidence_type="scheduled",
                priority=float(item.get("priority", 0.5)),
                face_risk=float(item.get("face_risk", 0.2)),
                confidence=0.85, reason="计划项到期", class_="scheduled", quota_class="scheduled",
                created_at=now, expires_at=float(item["due_at"]) + 6 * 3600,
                due_at=float(item["due_at"]),
                meta={"scheduled": item["title"][:80], "item_id": item["item_id"]},
            ), f"D9:{item['item_id']}")

        return out
