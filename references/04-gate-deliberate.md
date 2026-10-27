# 04 许可与审议

> 实现在 `gate.py` / `deliberate.py`。Gate 决定"该不该说"，审议决定"说哪一件"。

## 1. Gate 顺序（G1–G10）

| ID | 规则 | 不通过原因 |
| --- | --- | --- |
| G1 | `confidence < 0.55` | `low_confidence` |
| G2 | `face_risk > 0.65` | `face_risk` |
| G3 | 已过期 | `expired` |
| G4 | 用户静音 `quiet_until` 或 `closed_until` | `silence` |
| G5 | 非 system 且不在活跃窗口 | `outside_window` |
| G6 | 新话题类且用户静默 < 60 分钟 | `new_topic_too_soon` |
| G7 | 距上次社交主动 < `min_interval * pace` | `min_interval` |
| G8 | 当日配额用尽 / kind 上限 | `quota` / `kind_cap` |
| G9 | 软评分 < 0.50 | `score` |

顺序很重要：先硬约束（置信/面子/过期/静默/窗口），再间隔/配额，最后评分。这样审计里的 `blocked` 原因才可解释。

## 2. 软评分公式

```
rel  = confidence
urg  = priority
exp  = max(0.2, 1 - 0.5 * face_risk)
cost = face_risk
raw  = 0.30*rel + 0.25*urg + 0.20*exp + 0.25 - 0.20*cost
score = raw * decay
```

- `decay` 是反馈衰减；拒绝一次乘 0.5（下限 0.15）。低 decay 会让候选直接过不了阈值。
- 新话题类：D2/D3/D6/D8/D9。D1/D4/D5/D7 是修复/关系类，不受 60 分钟新话题门槛限制。
- 最小间隔默认 120 分钟；D8 60 分钟、D7 120 分钟可单独覆盖；`pace` 会整体放慢。

## 3. 手动触发（E6/A8）

`trigger --manual`：

- 跳过静默、窗口、配额；
- 不跳过置信度/面子/过期/软评分（除 `--text` 直接发送）；
- 仍写 `intent`、`ledger`、`audit`；
- 仍受 `mode=off` 限制；`shadow` 下仍不发送，除非显式 `--force`。

## 4. 审议（R10–R12）

```
bdi = score * (0.60 + 0.40*priority) * (1 - min(0.35, face_risk*0.35))
```

- 若本轮存在关系性候选（D1/D4/D5/D7），计划性候选（D9）的 `bdi *= 0.5`。
- 取最高；并列按 `priority` 降序，再按 `created_at` 升序。
- 审议只选一个；本 tick 不会同时发两条。

## 5. 为什么先 Gate 再审议

如果先审议，最高分候选可能被窗口/静默拦掉，次高分候选本来可发却没机会。先过滤再排序，能保证"在可发的候选里选最好"。

## 6. 审计

每个被丢弃的候选只记录原因计数，不写完整正文：

```json
{"gate": {"passed": 1, "blocked": {"outside_window": 2, "min_interval": 1}}}
```

排查时结合 `intents` 表与 `traces/` 看具体 kind/score。不要记录用户原话全文到审计，除非宿主明确允许。
