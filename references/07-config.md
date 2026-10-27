# 07 配置

> 唯一配置源：`config/proactive.config.json`。代码不硬编码阈值（X9）。

## 1. 窗口与模式

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `mode` | `shadow` | 初始模式；实际以 `control` 表为准，CLI `mode` 热切换 |
| `timezone` | `local` | IANA 时区或 `local`；Windows 无 tzdata 时用 local |
| `window.active_start/end` | 09:00 / 24:00 | 非 system 类允许时间 |
| `window.quiet_start/end` | 00:00 / 09:00 | 勿扰；与 active 不得重叠（S1） |
| `tick.seconds` | 60 | 前台/后台循环间隔 |
| `stream_id` | `default` | 单流部署；多聊多实例 |

## 2. Gate

| 参数 | 默认 | 调大的影响 |
| --- | --- | --- |
| `gate.score_threshold` | 0.50 | 更少候选 |
| `gate.min_confidence` | 0.55 | 更少候选 |
| `gate.max_face_risk` | 0.65 | 允许更冒犯的愿望 |
| `gate.new_topic_user_silence_minutes` | 60 | 新话题要等更久 |
| `gate.min_interval_minutes` | 120 | 社交主动更稀疏 |
| `gate.min_interval_by_kind` | `{"D8":60,"D7":120}` | 按 kind 覆盖 |
| `gate.daily_kind_caps` | `{}` | 按 kind 限制发送数 |

**S2**：配置校验会计算 `min_confidence`、`max_face_risk` 下可达到的最高软评分；低于阈值直接报错。默认 `min_confidence=0.55 > score_threshold=0.50`，会给出 warning（可运行，但置信度先截断）。

## 3. 愿望

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `reconnect_after_hours` | 3 | D4 关心 |
| `idle_share_after_minutes` | 60 | D8 空闲 |
| `follow_up_delay_minutes` | 45 | D5 最早追问 |
| `follow_up_max_age_minutes` | 360 | D5 最晚追问 |
| `owed_reply_infer_minutes` | 30 | D1 推断静默 |
| `owed_reply_infer_enabled` | true | 关闭后只认显式标记 |
| `relation_infer_enabled` | true | D7 从反馈推断 |
| `relation_threshold` | 2 | 焦虑/羞耻阈值 |
| `d4_daily_cap` | 1 | D4 每日上限 |
| `d5_max_follow_ups` | 2 | D5 最多追问 |

## 4. 时机

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `provider` | `none` | `http` 才调用 LLM |
| `daily_budget` | 24 | 模型调用日预算 |
| `per_kind_budget` | 4 | 每个预算类日预算 |
| `hourly_budget` | 2 | 每小时模型调用 |
| `cache_minutes` | 30 | defer 缓存 |
| `defer_min_minutes/max` | 5 / 360 | defer 夹取 |
| `fallback_after_streak` | 3 | 连续失败后当日规则直发 |
| `deadline_send_minutes` | 30 | 距截止该分钟数内强制 send |

预算类 5 个：relationship / open_loop / recall / idle / scheduled。
**S3**：`daily_budget >= per_kind_budget × 5`，默认 24 ≥ 20。

## 5. 出口

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `dispatch.quotas` | social 24 / scheduled 24 / system -1 | `-1` 不限，`0` 禁止 |
| `dispatch.bubble_quotas` | social 60 / scheduled 60 / system -1 | 气泡总量 |
| `dispatch.per_event_bubbles` | 2 | 单条最多气泡 |
| `dispatch.system_min_gap_seconds` | 600 | 系统消息最小间隔 |
| `dispatch.respect_window_for_system` | false | 系统消息默认不受窗口限制 |
| `dispatch.strategies` | social direct / scheduled wait_idle / system direct | 透传给 `send_text` |

## 6. 反馈与开放话题

| 参数 | 默认 |
| --- | --- |
| `response_window_minutes` | 120 |
| `sentiment_window_minutes` | 30 |
| `decay_floor` | 0.15 |
| `pace_cap` | 3.0 |
| `shadow_after_rejects` | 1 |
| `shadow_after_confused` | 2 |
| `shadow_hours` | 24 |
| `behavior_weight` | 0.7 |
| `fast_reply_minutes` | 5 |
| `multi_reply_window_minutes` | 5 |
| `timeout_pace_multiplier` | 1.5 |
| `reject_decay` | 0.5 |
| `respond_decay` | 0.3 |
| `reject_quiet_minutes` | 120 |
| `open_loop.ttl_hours` | 48 |
| `open_loop.max_open` | 30 |
| `open_loop.max_new_per_day` | 3 |

## 7. 内容与 LLM

```json
"content": {
  "provider": "none",
  "base_url": "",
  "model": "",
  "api_key": "",
  "max_chars": 120,
  "templates_file": "templates/content_templates.json"
}
```

- `provider=http` 时走 OpenAI 兼容 `/chat/completions`；失败回退模板。
- prompt 规定只使用 evidence；`persona` 只影响语气。
- `timing.provider=http` 同理；`api_key` 为空时多数服务会失败，请填。

## 8. 记忆召回

```json
"recall": {
  "mode": "none",
  "memory_root": "../memory",
  "folder_id": "default",
  "top_k": 3,
  "min_score": 0.3,
  "fail_open": true,
  "timeout_s": 8
}
```

| mode | 行为 |
| --- | --- |
| `none` | 不召回 |
| `host` | 调用宿主 `recall()` |
| `memory_import` | 进程内 import `memory_core`（推荐同机） |
| `memory_cli` | 子进程 `memory/run.py search --json`（兜底） |

任何失败返回空；主循环不因回忆服务挂掉而停（S6）。

## 9. 调参顺序

1. 先看写入/愿望：D1–D9 是否该生成的没生成（看 `desires` 与 evidence）。
2. 再看 Gate：`gate.blocked` 哪个原因最多，调对应门槛。
3. 再看时机：`timing.action` 与 `fallback`；规则 fallback 过多说明 LLM/预算有问题。
4. 最后看反馈：`decay`/`pace`/`shadow`；不要用调宽 Gate 来对抗用户拒绝。

## 10. 配置校验

```bash
python proactive/run.py config validate
```

必须全过：

- S1：active 与 quiet 不重叠；
- S2：当前置信度/面子风险下至少有一个候选能达到阈值；
- S3：日预算 ≥ 预算类预算 × 5。
