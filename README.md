# AI Proactive Deploy

> 给长期陪伴型 AI 角色部署一套**可验收**的主动行为系统。
> 主动不是定时器，是 **愿望 → 许可 → 时机 → 出口** 四层漏斗。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## 它是什么

一个 agent skill + 一套可直接运行的 Python 部署包：

- **统一 tick**：感知、愿望、Gate、审议、时机、出口、反馈都在同一个循环里，禁止各自注册定时器；
- **四层漏斗**：D1–D9 愿望 → G1–G10 许可 → 审议 → T1–T6 时机 → E1–E8 出口；
- **可回滚**：默认 `shadow`（完整判定但不发送），`mode enforce` 才真正调用 `send_text`；
- **可验收**：A1–A10 场景、S1–S6 启动自检、D1/D7 推断、mode 热切换，共 19 项自动检查；
- **与记忆解耦**：`recall` 可选，失败返回空，不阻塞主循环；可直接接 `ai-memory-deploy`。

## 它不是什么

- 不是固定间隔的定时推送；那会退化成定时任务（反模式 X1）。
- 不是随机概率发消息；没有愿望与证据就不该开口。
- 不是人格/情绪状态机；它只决定"该不该说、什么时候说"，不决定"角色此刻什么态度"。
- 不是聊天归档或知识库。

## 快速开始

### 方式一：作为 agent skill

把整个目录放进 skills 目录（例如 `~/.zcode/skills/ai-proactive-deploy/`），然后显式调用：

```
/ai-proactive-deploy
```

### 方式二：直接部署

```bash
python scripts/deploy.py \
  --target /path/to/project \
  --profile offline \
  --decisions-file decisions.md
```

部署后：

```bash
python proactive/run.py status
python proactive/run.py observe-user "明天下午一起看展览吧，我答应帮你查路线"
python proactive/run.py tick            # shadow 下只判定，不发送
python proactive/run.py mode enforce    # 观察后再切
python proactive/run.py daemon start
```

**默认 `adapter.py` 只把消息写进 `data/outbox.jsonl`，不会真的发到 QQ/Discord。** 生产必须替换 `create_host`，让 `send_text` 返回 True 才算投递成功。

## 目录结构

```
ai-proactive-deploy/
├─ SKILL.md                  # agent 执行手册：五问 → 部署 → 验收 → 接入
├─ references/               # 01–10：原则、状态、愿望、Gate、时机、反馈、配置、排障、验收、接入
├─ assets/
│  ├─ proactive_core/        # 核心（状态/存储/感知/愿望/Gate/审议/时机/内容/出口/反馈）
│  ├─ prompts/               # timing / content 提示词（LLM 可选）
│  ├─ templates/             # 内容模板、adapter 模板
│  └─ config/                # 默认配置
└─ scripts/deploy.py         # 部署器：复制、建库、自检、生成报告
```

部署后在目标项目生成：

```
<target>/proactive/
├─ proactive_core/  prompts/  templates/
├─ config/proactive.config.json
├─ adapter.py               # 宿主适配器（默认 FileSink）
├─ data/proactive.db        # 状态/开放话题/计划项/意图/账本/预算
├─ traces/                  # 每轮 JSONL
├─ reports/
├─ run.py
└─ DEPLOY-REPORT.md
```

## 设计要点

### 四层漏斗

| 层 | 回答的问题 | 关键约束 |
| --- | --- | --- |
| Desire（D1–D9） | 为什么说？ | 必须有 evidence；无依据不生成 |
| Gate（G1–G10） | 该不该说？ | 置信度、面子风险、窗口、静默、间隔、配额、软评分 |
| Deliberate（R10–R12） | 说哪一件？ | 人际优先于计划；并列按优先级/时间 |
| Timing（T1–T6） | 现在说合适吗？ | LLM 可 send/defer/skip；预算耗尽/失败回退规则 |
| Dispatch（E1–E8） | 怎么落地？ | 账本预占、真实投递、失败释放、不静默重试 |

### 三条最容易写错的分界

1. **`last_social_bot_at` 只记录社交发送**：工具/环境/系统消息不算，否则社交会永久饿死。
2. **后台检查不消耗时机预算**：否则半天耗尽，全天 fallback。
3. **安静窗口不拦系统告警**：余额/审批/提醒必须能发出去。

### 反馈

拒绝 → `decay *= 0.5`（下限 0.15）+ 静默 120 分钟；24h 内达到阈值直接降级 shadow。
超时未回 → `pace *= 1.5`（上限 3.0）。
回复越快、越连续、越延续话题，`decay` 回升。

## 本次实测记录

2026-10-27，本次实测：

- 全新部署：初始化 OK，自检 **19/19**，耗时约 0.2 s；
- 覆盖 A1–A10、S1–S6、D1/D7 推断、mode 热切换；
- CLI 验证：observe-user 抽取开放话题、shadow 不发送、enforce 投递到 FileSink、probe 手动探针、daemon start/status/stop；
- 与 `ai-memory-deploy` 同目录部署，`memory_import` 召回成功；
- 默认 FileSink 不产生真实消息；`probe --allow-send` 才调用 `send_text`。

验收细节与失败样例见 [`references/09-acceptance.md`](references/09-acceptance.md)。

## 环境要求

- Python ≥3.11；
- SQLite（标准库）；
- 无必装第三方依赖；
- LLM 可选：OpenAI 兼容 `/chat/completions`；不配置时全规则/模板运行。

## v1 边界

- 只发文本；不含学习提醒、唱歌、空间动态、B 站回复等领域活动。
- 单流部署；一个 `proactive/` 对应一条聊天流。
- 不内置真实 QQ/Discord 适配器；`adapter.py` 默认 FileSink。
- 默认 `shadow`；`mode enforce` 前先观察 24h。
- 自动验收 19 项；S5 探针只手动执行。

## License

MIT。见 [LICENSE](LICENSE)。
