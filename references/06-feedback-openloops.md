# 06 反馈与开放话题

> 实现在 `feedback.py` / `open_loops.py`。反馈决定"下次还敢不敢说"，开放话题决定"下次能说什么"。

## 1. 反馈（F1–F6）

### 行为分

```
speed       = 1 - clamp((回复分钟 - 5) / (120 - 5), 0, 1)
consecutive = 5 分钟内是否连发（>=2 条用户消息）
topic       = 主动内容与用户回复的字符二元组重叠 >= 0.2
behavior    = 0.4*speed + 0.3*consecutive + 0.3*topic
```

### 情绪分（规则词表）

| verdict | 分数 | 触发词（示例） |
| --- | --- | --- |
| positive | 1.0 | 好、哈哈、开心、喜欢、谢谢、想你 |
| neutral | 0.5 | 默认 |
| confused | 0.25 | 啊？、什么意思、没懂、？？、什么鬼 |
| reject | 0.0 | 别说了、不想聊、闭嘴、好烦、滚、不用了、没兴趣 |

规则词表是默认；需要更细可接 LLM（v1 未内置 sentiment LLM 调用，保持可预测）。

### 综合

```
final = 0.7 * behavior + 0.3 * sentiment
```

### 衰减与放慢

- reject：`decay *= 0.5`，下限 0.15；`quiet_until = now + 120m`。
- 24h 内 reject 达到 `shadow_after_rejects`（默认 1）→ `shadow_until = now + 24h`。
- 24h 内 confused 达到 `shadow_after_confused`（默认 2）→ 同样 shadow。
- 超时未回（`response_window_minutes=120`）：`pace *= 1.5`，上限 3.0。
- 正向回复（final ≥0.6）：`decay += 0.3`，上限 1.0。

`decay` 乘进 Gate 软评分；`pace` 乘进社交最小间隔。两者共同实现"被拒绝就少说、被无视就慢一点"。

### 与 A10 的关系

规格 A10 说"连续 2 次被拒 → shadow"，参考配置默认 `shadow_after_rejects=1`（更保守）。
本实现默认 1；自检 A10 用配置覆盖为 2 来验证"2 次"场景。两者都满足 A10：达到 2 次一定已经 shadow。

## 2. 开放话题

### 抽取

必须同时命中承诺词与时间词，且能解析出绝对时间；解析失败不建（不猜）。

- 承诺词：答应、约好、说好、回头、晚点、帮你、查、提醒、记得、见面、一起、安排、截止、报名、考试、面试、复诊、出差、旅行、聚会……
- 时间词：今天/明天/后天/大后天、今晚/明晚、下周X/下周末、周末、月底、下个月初/中/底、M月D日、YYYY-MM-DD。

### 约束

| 约束 | 值 |
| --- | --- |
| TTL | 48h，从 `max(now, due_at)` 起算 |
| 最大 open | 30 |
| 日新增 | 3 |
| 去重 | 归一化文本包含关系 |
| 完成 | "已经…了/完成了/不用了/取消了" 且字符重叠 ≥0.35 |

### 生命周期

```
open → notified（D2 已发提醒）→ done / expired
```

- D2 发送成功后 `status=notified`，不会同一天反复提醒。
- 到期后 `expired`；用户完成则 `done`。
- 人工：

```bash
python proactive/run.py open-loops list
python proactive/run.py open-loops close <loop_id>
```

## 3. 为什么 TTL 从 due_at 起算

规格原文只写 TTL 48h。如果从创建起算，"下周六一起吃饭"会在周三创建、周五过期，永远等不到周六。本实现从 `max(now, due_at)` 起算，并在 `01-principles` 标为显式取舍。

## 4. 与记忆系统的关系

开放话题来自用户原话；`recall` 只用于 D6/D8 的证据，不把记忆检索写进 Gate。
记忆不可用时，开放话题与规则愿望照常运行（S6）。
