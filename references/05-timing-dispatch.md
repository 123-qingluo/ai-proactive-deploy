# 05 时机与出口

> 实现在 `timing.py` / `content.py` / `dispatch.py`。时机回答"现在说合适吗"，出口回答"怎么落地"。

## 1. 时机（T1–T6）

| ID | 规则 | 实现 |
| --- | --- | --- |
| T1 | LLM 只输出 `send / defer / skip` + 理由 + 分钟数 | `timing.md` + JSON 解析 |
| T2 | 日预算 24、预算类 4、每小时 2；耗尽 fallback | `timing_calls` 表 |
| T3 | `defer` 分钟夹 5–360；结果缓存 30 分钟 | 重试走规则，不二次调模型 |
| T4 | 距截止 <30 分钟 → 强制 send | `expires_at` 判断 |
| T5 | 后台检查不消耗预算 | `observe_background` 不进入时机 |
| T6 | 模型不可用/超时 → fallback；连续 3 次 → 当日规则直发 | `fallback_streak` / `forced_rules_until` |

### 预算类

时机预算按 5 个预算类统计，而不是 9 个愿望各一份：

| 预算类 | 愿望 |
| --- | --- |
| relationship | D1/D4/D5/D7 |
| open_loop | D2/D3 |
| recall | D6 |
| idle | D8 |
| scheduled | D9 |

`daily_budget=24`、`per_kind_budget=4`、`hourly_budget=2`；`24 >= 4 × 5` 通过 S3。

### 规则 fallback

LLM 关闭、预算耗尽、连续失败、`forced_rules_until` 未到，都走规则：

- 候选过期 → `skip`；
- 距截止 <30 分钟 → `send`；
- 其余 → `send`（Gate 已经过滤过）。

规则 fallback 不调用模型，也不消耗预算；`model_ok=false`、`fallback=true` 写审计。

### defer 只重试一次

- 第一次 LLM 返回 defer → `intents.status=deferred`，`deferred_until=now+minutes`。
- 到期后的 tick 用规则重试（不再调模型）；成功就发，失败/跳过就标 `skipped`。
- 重试时重新检查 `closed_until` / `quiet_until` / 窗口；用户中途说"睡了"会拦下。

## 2. 内容

默认使用 `templates/content_templates.json` 的 evidence 模板：

| 愿望 | 模板 |
| --- | --- |
| D1 | 刚才你说「{quote}」，我还没接住，现在说还来得及吗？ |
| D2 | 你之前提到「{loop}」，时间差不多了，还顺利吗？ |
| D3 | 「{loop}」我一直记着，等你有空再说。 |
| D4 | 今天还好吗？「{quote}」之后一直有点惦记。 |
| D5 | 上次问你的「{quote}」，后来有结果了吗？ |
| D6 | 突然想起「{memory_title}」。 |
| D7 | 刚刚你说「{quote}」，如果不想聊也没关系，我在。 |
| D8 | 刚看到「{evidence}」，想起你。 |
| D9 | 该做「{scheduled}」了。 |

- 只使用 evidence；没有 evidence 不生成愿望。
- `content.provider=http` 时可用 LLM 改写；prompt 规定"只使用 evidence、不编造、不超过 max_chars"；失败回退模板。
- `persona` 只影响 LLM 文案；模板默认中性、克制、短句。

## 3. 出口（E1–E8）

| ID | 规则 |
| --- | --- |
| E1 | 三类：`social` / `scheduled` / `system` |
| E2 | 窗口/静默只拦 social 与 scheduled；system 放行（可配 respect_window_for_system） |
| E3 | 活跃 09:00–24:00，勿扰 00:00–09:00；插件只能收窄 |
| E4 | 账本按 class + quota_class 记次数与气泡；`-1` 不限、`0` 禁止 |
| E5 | system 最小间隔 600s；提醒类可显式 0 |
| E6 | 手动触发跳过静默/窗口/配额，但**仍记账、仍审计** |
| E7 | 发送成功后才更新 `last_social_bot_at`（仅 social）与 `last_proactive_at` |
| E8 | 投递失败释放预占，写 `failed`，不静默重试 |

### 投递判定

```python
sent = host.send_text(text, strategy)
# True = 成功；False/None/异常 = 失败
```

默认 `FileSinkHost` 把消息写进 `data/outbox.jsonl` 并返回 True——它只用于部署验收，不是真实投递。

### shadow

`mode=shadow` 或 `shadow_until > now`：

- 完整判定 + 内容 + `intent`（status=shadowed）；
- **不预占账本、不调 `send_text`**；
- 审计 `dispatch.status=shadowed`。

### 失败

失败时：

1. `ledger.status=failed`（释放预占）；
2. `intent.status=failed`；
3. 写 `dispatch:failed` 审计；
4. 不重试；下一轮重新走完整漏斗。

## 4. 守护进程

- 库本身不藏线程；`Engine.run_forever()` 是唯一循环。
- `run.py run` 前台循环；`daemon start` 启动独立进程，PID 文件管理。
- `daemon stop` 先写 `data/STOP`，等待退出；超时才 `taskkill`/SIGTERM。
- 配置改动（窗口/阈值/预算）需要重启 daemon；`mode` 热切换，不需要。
