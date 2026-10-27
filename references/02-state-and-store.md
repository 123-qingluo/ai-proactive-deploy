# 02 状态与存储

> 状态字段在 `proactive_core/models.py`，表结构在 `proactive_core/schema.sql`。

## 1. State 字段

| 字段 | 含义 |
| --- | --- |
| `last_user_at` / `last_user_text` | 用户最后一次开口 |
| `last_social_bot_at` | 最后一次**社交**发送；系统/计划消息不更新 |
| `last_bot_any_at` | 任何 bot 消息；用于"欠回应"判断 |
| `last_reply_at` | 最后一次社交回复；D1 推断用 |
| `last_proactive_at` | 最后一次主动成功发送 |
| `last_system_bot_at` / `last_scheduled_bot_at` | 系统/计划类最近发送 |
| `expects_reply` / `expects_since` | bot 发了问题，等待回复 |
| `follow_up_count` | D5 已追问次数 |
| `owed_reply` / `owed_since` | 宿主显式标记"欠一句回应" |
| `closed_until` | 用户说"睡了/晚安/别发了"后的静音截止 |
| `quiet_until` | 拒绝反馈触发的静默 |
| `decay` | 反馈衰减系数（0.15–1.0） |
| `pace` | 节奏倍数（1.0–3.0），乘到最小间隔 |
| `relation_anxiety` / `relation_shame` / `relation_explicit` | 关系状态，宿主可显式设置 |
| `shadow_until` / `shadow_reason` | 反馈触发的 24h shadow |
| `daily` | 当日计数：发送、愿望、时机调用、小时桶 |
| `pending_feedback` | 刚发出的社交消息等待首条回复 |
| `recent_feedback` | 48h 内反馈记录（最多 30 条） |
| `recent_user_ts` | 最近用户消息时间，判定连发 |
| `fallback_streak` / `forced_rules_until` | 时机模型连续失败后当日降级规则 |

`daily` 在跨天后重置；`pending_feedback` / `recent_feedback` 跨天保留，直到消费或过期。

## 2. SQLite 表

| 表 | 作用 |
| --- | --- |
| `state` | 每 stream 一份 JSON 状态 |
| `open_loops` | 开放话题：text/evidence/due/expires/status |
| `scheduled_items` | 通用计划项（D9）：title/due/priority/face |
| `intents` | 候选与结果：score/BDI 输入/status/deferred |
| `ledger` | 发送账本：class/quota_class/bubbles/status |
| `timing_calls` | 时机模型调用计数：day/hour/budget_class |
| `control` | 运行控制：`mode`（off/shadow/enforce） |
| `schema_meta` | schema 版本 |

## 3. 账本与预算不是一回事

- **时机预算**（`[timing] daily_budget=24 / per_kind_budget=4 / hourly_budget=2`）限制的是**模型调用次数**。耗尽 → 当天该候选走规则 fallback。预算类共 5 个：relationship、open_loop、recall、idle、scheduled。
- **发送配额**（`[dispatch] quotas`）限制的是**实际发送条数**。`-1` 不限，`0` 禁止。默认 social 24、scheduled 24、system -1。
- **bubble 配额**限制消息气泡总数；文本模式每条 1 个气泡。

混用会导致两类 bug：后台检查吃光时机预算（X3），或配额 0 的科目仍然被发送（A7）。

## 4. 预占与释放

发送前先在 `ledger` 插 `reserved`，成功改 `sent`，失败改 `failed`。

- 预占同时检查 quota 与 bubble quota；
- 失败释放（不计入配额），写 `failed` 审计；
- 手动触发（`--manual`）跳过配额检查，但**仍写账本**（E6/A8）。

## 5. mode 热切换

`mode` 存在 `control` 表，daemon 每 tick 读取：

```bash
python proactive/run.py mode off
python proactive/run.py mode shadow
python proactive/run.py mode enforce
```

- `off`：tick 直接返回，不判定、不发送。
- `shadow`：完整判定 + 内容 + 审计，不调 `send_text`。
- `enforce`：通过后真正投递。

其他配置（窗口、阈值、预算）改动后需要重启 daemon；不要把阈值散在代码里（X9）。

## 6. 备份

```bash
# 先停 daemon
python proactive/run.py daemon stop
copy proactive\data\proactive.db backup\proactive-YYYYMMDD.db
```

WAL 模式下同时备份 `-wal` 文件，或使用 SQLite 在线备份 API。`traces/` 是可再生的审计，不入库也可。
