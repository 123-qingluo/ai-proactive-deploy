---
name: ai-proactive-deploy
description: 给 AI 角色部署一套可验收的主动行为系统：愿望→许可→时机→出口四层漏斗，含 A1–A10/S1–S6 自检。用于 QQ/Discord/微信 bot、桌面 Agent 需要"主动开口"时；不是定时器，不是随机发消息。
disable-model-invocation: true
---

# AI 主动行为部署

> **主动不是定时器，是"愿望 → 许可 → 时机 → 出口"四层漏斗。**
> 定时任务只回答"到点了吗"；主动要回答"为什么说、该不该说、现在说合不合适、说出去怎么落地"。

## 何时用

- 长期陪伴型 AI 角色需要主动关心、追问、兑现约定、分享，而不是只等用户开口。
- 已有随机心跳/定时推送，但行为不可复现、半夜发消息、把工具消息算成社交、预算被后台检查吃光。
- 要把一套已经验证过的主动漏斗复用到新 bot。

## 何时不用

| 想要什么 | 去哪 |
| --- | --- |
| 每天固定时间发提醒 | 用系统计划任务/提醒器，不要主动漏斗 |
| 随机概率发消息 | 这正是反模式 X1；先想清楚愿望与证据 |
| 人格/情绪状态机 | 记忆/人格系统负责"怎么说"；本 skill 决定"该不该说" |
| 文档问答/RAG | 不是本 skill |

## 交付物

部署后在目标项目新增：

```
<target>/proactive/
├─ proactive_core/          # 可运行核心（Python ≥3.11，标准库）
├─ prompts/                 # timing / content 提示词（LLM 可选）
├─ templates/               # 内容模板、adapter 模板
├─ config/proactive.config.json
├─ adapter.py               # 宿主适配器：默认只写 outbox，生产必须替换
├─ data/proactive.db        # SQLite：状态/开放话题/计划项/意图/账本/预算
├─ traces/                  # 每轮一行 JSONL
├─ reports/                 # 部署与运维报告
├─ run.py
└─ DEPLOY-REPORT.md
```

## 执行流程

### Step 0 五个部署判断

| # | 问题 | 默认 |
| --- | --- | --- |
| 1 | 目标聊天流、时区、活跃/勿扰窗口？ | 单流；本机时区；09:00–24:00 活跃，00:00–09:00 勿扰 |
| 2 | 宿主如何提供事件：用户消息、bot 社交/系统消息、欠回复、关系状态？ | `observe-user` / `observe-bot` / `mark-owed-reply` / `set-relation` |
| 3 | 是否接记忆召回？ | 不接；要接则 `recall.mode=memory_import` 指向 `../memory` |
| 4 | 首次上线模式与观察期？ | `shadow` 至少 24h |
| 5 | 默认内容语气/是否有 persona？ | 中性、克制、短句；persona 仅影响 LLM 文案 |

用户回答"按默认"才用默认值；把回答写进 `decisions.md`，部署时传给 `--decisions-file`。

### Step 1 预检

- Python ≥3.11；目标目录存在且可写。
- 目标下是否已有 `proactive/`：默认保留配置与数据；`--force` 刷新代码（会覆盖 prompts/templates），`--force-config` 才重写配置。
- **默认 `adapter.py` 只是把消息写进 `data/outbox.jsonl`，不会真的发到 QQ/Discord。** 不替换它，`enforce` 也不会产生真实消息。

### Step 2 部署

以下命令相对于本 skill 目录；`--target` 用绝对路径：

```bash
python scripts/deploy.py \
  --target <目标项目根> \
  --profile offline \
  --decisions-file decisions.md
```

- `--profile offline`：规则 + 模板，任何机器可跑。
- `--profile llm`：预置 OpenAI 兼容配置，需填 `timing` / `content` 的 `base_url` / `model` / `api_key`。
- 重复执行幂等；`--dry-run` 只打印计划。

### Step 3 读报告，判定"部署完成"

打开 `<target>/proactive/DEPLOY-REPORT.md`：

- 健康检查 `ok=true`；
- 自检 **19/19**（A1–A10、S1–S6、D1/D7 推断、mode 热切换）；
- 有任何 FAIL：按 `references/08-troubleshooting.md` 修，修完重跑：

```bash
python proactive/run.py selfcheck --json
```

**不要把未通过自检的系统切到 enforce。**

### Step 4 接宿主：替换 adapter.py

最小契约：

```python
def create_host(root, config):
    return MyHost()  # 必须有 now() 和 send_text(text, strategy)
```

- `send_text` 必须返回 **True 才算成功**；`None`/`False`/异常都按失败处理。
- 失败会释放账本预占、写 `failed` 审计，**不会静默重试**（X8）。
- 用户消息：`engine.on_user_message(text)`；bot 消息：`engine.on_bot_message(text, social=True/False)`。
- 系统/计划消息传 `social=False`，否则会毒化 `last_social_bot_at`（X2）。
- 后台检查：`engine.observe_background(event)`，不写意图、不耗时机预算（X3/A4）。
- 欠回复/关系状态由宿主显式标记；引擎的 30 分钟/2 次拒绝推断只是兜底。

### Step 5 shadow 跑 24h

- 保持 `mode=shadow`：完整判定、生成内容、写审计，但 `send_text` 不被调用。
- 检查 `traces/*.jsonl`：`gate.blocked` 原因、`timing.action`、`dispatch.shadowed`。
- 手动探针（会真的调用 `send_text`，默认 FileSink 只是写 outbox）：

```bash
python proactive/run.py probe --allow-send
```

- 观察开放话题是否抽得准：`python proactive/run.py open-loops list`。

### Step 6 切 enforce

```bash
python proactive/run.py mode enforce
python proactive/run.py daemon start --interval 60
python proactive/run.py daemon status
```

- 观察前 24h：`status`、`audit --tail`、`data/outbox.jsonl` 或真实消息。
- 出现连续拒绝/困惑，反馈会自动降级 shadow；也可以立刻 `mode off`。

### Step 7 回滚

```bash
python proactive/run.py mode off
python proactive/run.py daemon stop
python proactive/run.py uninstall              # 删代码/配置，保留 data/
python proactive/run.py uninstall --purge --yes  # 连数据一起删
```

## 硬规则

1. **一个调度器**：库不藏线程；daemon 是独立进程循环 `run.py run`，仍只调 `Engine.tick`。
2. **不用固定间隔代替漏斗**（X1）：所有发送必须经过 Gate → 审议 → 时机。
3. **社交沉默只算社交**（X2）：`system`/`scheduled` 消息不更新 `last_social_bot_at`。
4. **后台检查不耗预算**（X3）：`observe_background` 不写意图、不调时机模型。
5. **无 evidence 不生成愿望**（X4/R6）：D8 没有依据就不生成，而不是生成后被拦。
6. **确认事项不要注入即标记已问**（X5）：只有真正 send 成功才记账。
7. **安静窗口不拦系统告警**（X7）：`system` 类绕过 quiet/window，只受最小间隔。
8. **失败不静默重试**（X8）：释放预占，写 failed；下次重新走漏斗。
9. **阈值全在配置**（X9）：代码不硬编码；改配置后 daemon 需重启（`mode` 除外）。
10. **单流部署**：一个 `proactive/` 对应一条聊天流；多聊天多实例。

## v1 边界

- 只发文本；不内置学习提醒、唱歌、空间动态、B 站回复等多模态/领域活动。
- 不接入真实 QQ/Discord；`adapter.py` 默认 FileSink，生产必须替换。
- 默认不启用 LLM；timing/content 可选，失败回退规则/模板。
- 单流；多实例部署。
- 自动验收 19 项；S5 探针只手动执行，部署自检不真发。
- 无 GitHub 发布动作（如需另行说明）。

## 参考索引（按需读）

| 文件 | 什么时候读 |
| --- | --- |
| `references/01-principles.md` | 解释四层漏斗、X1–X10 反模式 |
| `references/02-state-and-store.md` | 状态字段、SQLite 表、账本与预算 |
| `references/03-perceive-desires.md` | R1–R9、D1–D9、开放话题、D1/D7 推断 |
| `references/04-gate-deliberate.md` | G1–G10 公式、审议与并列排序 |
| `references/05-timing-dispatch.md` | T1–T6、预算类、E1–E8、shadow/daemon |
| `references/06-feedback-openloops.md` | F1–F6、拒绝降级、pace/decay |
| `references/07-config.md` | 参数默认值、S1–S3 校验 |
| `references/08-troubleshooting.md` | 症状 → 排查命令 → 处置 |
| `references/09-acceptance.md` | A1–A10/S1–S6 实测记录与未覆盖项 |
| `references/10-integration.md` | 宿主事件、adapter、记忆召回、daemon |
