# 03 感知与愿望

> 实现在 `perceive.py` / `desires.py` / `open_loops.py`。核心：没有 evidence 的愿望不生成。

## 1. 感知（R1–R5）

| ID | 规则 | 实现 |
| --- | --- | --- |
| R1 | `user_silence = now - last_user_at`，无记录为 0 | `Belief.user_silence` |
| R2 | `bot_silence = now - last_social_bot_at`，环境消息不计 | 只更新 `last_social_bot_at` 于 social |
| R3 | 收尾语/静音指令 → `closed_until`（默认 +12h） | `feedback.CLOSED_RE` |
| R4 | 每 tick 采一次 recall：有开放话题用话题，否则用户最后一句 | `PerceiveService` |
| R5 | `window_open` / `quiet` | `timeutil.in_window` |

## 2. 愿望表（D1–D9）

| ID | 愿望 | 触发 | 优先级 | 面子风险 | evidence |
| --- | --- | --- | --- | --- | --- |
| D1 | 补偿 | 欠一句回应 | 0.90 | 0.42 | 用户原话 |
| D2 | 开放话题（到期） | `due_at <= now` | 0.92 | 0.34 | 话题句 |
| D3 | 开放话题（未到期） | 存在未到期话题 | 0.78 | 0.34 | 话题句 |
| D4 | 关心 | `user_silence >= 3h` | 0.72 | 0.30 | 用户最后一句 |
| D5 | 追问 | `expects_reply` 且 45–360 分钟 | 0.68 | 0.28 | bot 的问题 |
| D6 | 回忆 | `recall_score >= 0.75` 且无开放话题 | 0.62 | 0.24 | 记忆标题 |
| D7 | 情绪 | 关系焦虑/羞耻 ≥2 | 0.55 | 0.45 | 用户最后一句 |
| D8 | 空闲分享 | `user_silence >= 1h` **且有依据** | 0.35 | 0.16 | 用户原话或记忆 |
| D9 | 计划项 | 计划项到期 | 0.32–0.65 | 0.10–0.25 | 计划标题 |

## 3. D1 / D7 的显式优先与保守推断

### D1 补偿

- 宿主 `mark_owed_reply(text)` 显式标记 → 直接生成。
- 否则若 `last_user_at > last_reply_at` 且静默 ≥ **30 分钟** → 生成（`owed_reply_infer_enabled` 可关）。
- 只有社交/actor 回复会更新 `last_reply_at`；system/scheduled 不清除欠回应。
- evidence 必须是用户原话；没有原文不生成。

### D7 情绪

- 宿主 `set_relation_state(anxiety, shame)` 显式设置 → 优先使用，不再推断。
- 否则 24h 内 `reject >= 2` 或 `confused >= 2` → 推断焦虑/羞耻 ≥2（`relation_infer_enabled` 可关）。
- evidence 用用户最后一句；没有原文不生成。

## 4. 愿望去重（R7）

- 普通愿望：`kind + stream + 日期`，同日同 kind 只生成一次。
- D9：`D9 + item_id`，不同计划项可分别生成（否则学习/提醒类每 90 分钟一次会被误杀）。
- D4 每日最多 1 次；D5 最多 2 次追问（`follow_up_count`）。

## 5. 开放话题（R8）

从用户原话抽取，必须同时命中：

- **承诺/计划词**：答应、约好、说好、回头、晚点、帮你、查、提醒、记得、见面、一起、安排、截止、报名、考试、面试、复诊、出差、旅行、聚会……
- **时间词**：今天/明天/后天/大后天、今晚/明晚、下周/下周末、周末、月底、下个月初/中/底、M月D日、YYYY-MM-DD。

解析出绝对到期时间；解析失败就不建（不猜）。

约束：

- TTL 48h，**从 `max(now, due_at)` 起算**，保证"下周"的承诺不会创建 48h 后先过期；
- 同时最多 30 条 open；
- 每天最多新增 3 条；
- 文本归一化后去重；
- 用户说"已经…了/完成了/不用了/取消了"，且与某条话题字符重叠 ≥0.35，标记 done。

## 6. 无依据的空闲分享（A9 / X4）

D8 的 evidence 只能来自：

- 用户最后一句原话；或
- 召回命中的记忆标题（`recall_score >= 0.3`）。

两者都没有 → **不生成 D8**，而不是生成后被 Gate 拦。这样不会浪费一次时机模型调用。

## 7. 计划项（D9）

本 skill 不内置学习提醒、唱歌、空间动态、B 站回复。宿主可以：

```bash
python proactive/run.py schedule add --title "复习" --due-in-minutes 90
```

D9 是通用计划愿望；到期后走同一条 Gate/Timing/Dispatch。计划项发送属于 `scheduled`，不更新 `last_social_bot_at`。
