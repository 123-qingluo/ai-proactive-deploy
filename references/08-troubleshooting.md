# 08 故障排查

> 先定位是哪一层：愿望没生成、Gate 拦了、时机不发了、出口失败了，还是反馈把它打进了 shadow。

## 1. 症状 → 原因 → 处置

| 症状 | 先查 | 可能原因 | 处置 |
| --- | --- | --- | --- |
| 半夜发消息 | `traces` 的 `dispatch`；`window` | 窗口配错；system 类放行；手动触发 | 修窗口；确认消息 class；检查 `trigger` 调用 |
| 从不主动 | `mode`、`status`、`traces` | `off/shadow`；没有 evidence；Gate 全拦；配额 0；adapter 失败 | `mode enforce`；补 evidence；看 `gate.blocked`；查 adapter/ledger |
| 社交永久饿死 | `last_social_bot_at` | 系统/计划消息被当 social | 宿主用 `observe-bot --non-social`；检查 dispatch class |
| 时机预算半天耗尽 | `timing_calls`、`audit` | 后台检查走了 `tick`/时机 | 后台检查必须用 `observe_background`（A4） |
| 重复消息 | `intents`、`ledger`、`audit` | 失败后重试；多个调度器；同候选未去重 | 不重试；只留一个 tick；检查 `mode`/daemon 双开 |
| 一直 shadow | `status.shadow_until` | 被拒绝/困惑后降级；`mode` 仍是 shadow | 看 `recent_feedback`；确认用户是否真的拒绝；到期自动恢复 |
| 配额明明 -1 还是不发 | `gate.blocked` | 被 `min_interval`/窗口/评分拦，不是配额 | 看具体 blocked 原因 |
| daemon 起不来 | `data/daemon.log`、`status` | adapter 导入失败；端口/PID；配置无效 | `python proactive/run.py health`；修 adapter；删陈旧 PID |
| 消息只进了 outbox | `adapter.py` | 默认 FileSinkHost | 替换 `create_host` 为真实投递 |
| D1 误报 | `owed_reply`、`last_reply_at` | 宿主没有 `observe-bot` 记录回复 | 保证社交回复调用 `on_bot_message(social=True)`；调大 `owed_reply_infer_minutes` 或关闭推断 |
| D7 误报 | `recent_feedback` | 规则词表把玩笑当 reject/confused | 调词表/关闭 `relation_infer_enabled`；用显式 `set-relation` |
| 开放话题抽错 | `open_loops` | 时间词/承诺词误命中 | 收紧正则；解析失败本来就不建 |
| recall 报错 | `recall-test`、traces | 路径不对/依赖缺失 | `memory_root` 指向部署目录；失败会自动 fail-open |

## 2. 五个排查命令

```bash
python proactive/run.py status --json          # mode/静默/开放话题/队列
python proactive/run.py config validate        # S1/S2/S3
python proactive/run.py audit --tail 50        # 最近 tick
python proactive/run.py open-loops list        # 开放话题
python proactive/run.py health --json          # DB/host/mode/recall
```

## 3. trace 字段

每轮一行 JSONL：

```json
{"ts":..., "event":"tick", "mode":"shadow", "user_silence":..., "bot_silence":...,
 "desires":["D2"], "gate":{"passed":1,"blocked":{}},
 "timing":{"action":"send","reason":"rules","fallback":true,"model_ok":false},
 "dispatch":{"action":"shadowed","reason":"shadow"},
 "feedback":{}}
```

常见 `dispatch.action`：

| 值 | 含义 |
| --- | --- |
| `sent` | 真实投递成功 |
| `shadowed` | shadow 下只记录 |
| `blocked` | 配额/系统间隔/mode off |
| `failed` | `send_text` 返回 False/None/异常 |

## 4. A1–A10 失败时的定位

| 用例 | 先看 |
| --- | --- |
| A1/A2 | `window` / `closed_until` / Gate blocked |
| A3 | `last_social_bot_at` 是否被 scheduled 更新 |
| A4 | `observe_background` 是否误走 `tick` |
| A5 | `intents.deferred_until` / 第二次 tick 是否重新 Gate 拦掉 |
| A6 | `timing_calls` 是否按 stream 计数；预算配置 |
| A7 | `dispatch.quotas` 与 Gate 顺序 |
| A8 | `trigger --manual` 是否被 shadow/off 拦 |
| A9 | D8 是否在没有 evidence 时仍生成 |
| A10 | `recent_feedback` / `shadow_after_rejects` |

## 5. 不要做的事

- 不要为了"多发点"把 `min_interval` 调到 0；先确认愿望与证据是否成立。
- 不要用重试掩盖 `send_text` 失败；失败要释放账本并审计（X8）。
- 不要把后台检查塞进 `tick`；它会把时机预算吃光（X3）。
- 不要在群聊/系统消息上更新 `last_social_bot_at`（X2）。
