# 09 验收

> "部署完成" = 自检 19/19 + 宿主 adapter 已替换 + shadow 观察 24h。不是文件复制完。

## 1. 验收门

| 门 | 标准 | 命令 |
| --- | --- | --- |
| 建库/可写 | `health.ok=true` | `python proactive/run.py health --json` |
| 行为自检 | 19/19 | `python proactive/run.py selfcheck --json` |
| 宿主接入 | `send_text` 返回 True 才算成功 | 替换 `adapter.py` + `probe --allow-send` |
| shadow 观察 | 24h 内无"半夜/重复/明显错发" | `audit --tail`、`data/outbox.jsonl` |
| enforce | 显式切模式后才真实发送 | `mode enforce` |

## 2. 本次实测记录（2026-10-27）

本机实测（Python 3.11+ / SQLite）：

```
初始化：OK
自检：19/19 通过，耗时约 0.2 s
```

| 用例 | 结果 | 实测说明 |
| --- | --- | --- |
| A1 凌晨社交候选 | PASS | 02:00 `blocked={"outside_window":1}`，无发送 |
| A2 "睡了"后 | PASS | `closed_until` 生效，`blocked={"silence":1}` |
| A3 计划消息与社交 | PASS | scheduled 不更新 `last_social_bot_at`，social 仍可发 |
| A4 后台检查 | PASS | 10 次 `observe_background`，intents=0、model_calls=0 |
| A5 defer | PASS | 第一次 defer 10m，第二次规则重试并发送，仅一次 |
| A6 模型预算 | PASS | model_calls=24，第 25 次 fallback 不再调模型 |
| A7 配额 | PASS | quota=0 拦截；quota=-1 放行 |
| A8 手动勿扰 | PASS | 02:00 手动发送成功，ledger +1，audit 留痕 |
| A9 无依据空闲 | PASS | D8 未生成（不是生成后被拦） |
| A10 连续拒绝 | PASS | 达到阈值后 shadow，后续不发送 |
| S1 窗口重叠 | PASS | 拒绝 active/quiet 重叠 |
| S2 不可达评分 | PASS | 拒绝永远无候选的阈值组合 |
| S3 预算数学 | PASS | 拒绝 `daily < per_kind × 5` |
| S4 账本 | PASS | 预占/标记可写可读 |
| S5 探针 | PASS | 未授权不发；`--allow-send` 调假宿主 |
| S6 记忆不可用 | PASS | recall 返回空，tick 不抛错 |
| D1 推断 | PASS | 40 分钟未回，D1 生成 |
| D7 推断 | PASS | 24h 内 2 次 reject，D7 生成 |
| mode 热切换 | PASS | off → shadow → enforce，无需重启 |

CLI 冒烟：

- `observe-user` 能从"明天下午一起看展览"抽出 1 条开放话题；
- `trigger --text` 在 shadow 下返回 `shadowed`，不写 outbox；
- `mode enforce` 后 `trigger` 写入 outbox；
- `probe --allow-send` 写入探针；
- `daemon start --interval 1` → `status running` → `daemon stop` 正常；
- 默认 `adapter.py` 是 FileSinkHost，真实消息没有发出；
- 与 `ai-memory-deploy` 部署在同一目标目录，`recall.mode=memory_import` 能正常返回示例记忆（ID / score / title）。

## 3. 自检没有覆盖的

- **真实投递**：S5 用假宿主；只有 `probe --allow-send` 才调真实 `send_text`。
- **真实 LLM 质量**：timing/content LLM 默认关闭；自检用 StubLLM，只验证协议与 fallback。
- **真实聊天节奏**：A1–A10 是场景断言，不证明"这个角色说得好不好"。
- **多 chat 并发**：单流部署；多实例需要宿主自己调度。
- **平台适配器**：没有内置 QQ/Discord/微信适配器。

## 4. shadow 期人工检查（建议 24h）

1. 看 `audit --tail 100`：`timing.action` 是否多为 send；`fallback` 是否异常高。
2. 看 `gate.blocked`：`outside_window` / `min_interval` / `score` 是否合理。
3. 看 `open-loops list`：抽取的话题是否真的来自用户原话，时间是否合理。
4. 看 `data/outbox.jsonl`：文案是否只用了 evidence，有没有编造。
5. 抽查 `recent_feedback`：D1/D7 推断是否误报。

## 5. 切 enforce 前清单

- [ ] 自检 19/19
- [ ] `adapter.py` 已替换，`send_text` 返回 True
- [ ] `probe --allow-send` 成功（真实平台收到探针）
- [ ] shadow 至少 24h，无半夜/重复/明显错发
- [ ] 开放话题抽查 ≥10 条，时间解析正确
- [ ] 系统/计划消息确认用 `--non-social` 记录
- [ ] 配额/窗口/时区符合预期
- [ ] 回滚命令已知：`mode off`、`daemon stop`、`uninstall --keep-data`

## 6. 撤销

```bash
python proactive/run.py mode off
python proactive/run.py daemon stop
python proactive/run.py uninstall              # 保留 data/
python proactive/run.py uninstall --purge --yes  # 连数据删除
```
