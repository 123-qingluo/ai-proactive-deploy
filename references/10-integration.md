# 10 宿主接入

> 核心与平台解耦：宿主提供"现在几点、把这句话发出去"，其余由引擎决定。

## 1. 最小宿主契约

```python
class MyHost:
    def now(self) -> float: ...
    def send_text(self, text: str, strategy: str) -> bool | None: ...
    def recall(self, query: str, top_k: int) -> list[tuple[str, float, str]]: ...  # 可选
    def persona(self, context: dict) -> str | None: ...                          # 可选
    def audit(self, event: dict) -> None: ...                                     # 可选
```

- `send_text` 必须返回 **True 才算成功**；`None`/`False`/异常都按失败。
- `strategy` 原样透传（direct / raw / wait_idle / resume_chatter）。
- `recall` 失败不能抛错；引擎也会包一层 fail-open。

部署后编辑 `proactive/adapter.py`：

```python
def create_host(root, config):
    return MyHost(...)
```

## 2. 事件接入

| 事件 | 调用 | 注意 |
| --- | --- | --- |
| 用户消息 | `engine.on_user_message(text)` | 更新 `last_user_at`、抽开放话题、清 expects |
| bot 社交消息 | `engine.on_bot_message(text, social=True)` | 更新 `last_social_bot_at`；自动判断问号 |
| bot 系统/计划消息 | `on_bot_message(text, social=False)` | **不**更新社交沉默（A3/X2） |
| 欠一句回应 | `mark_owed_reply(text)` | 显式优先；否则 30 分钟推断 |
| 关系状态 | `set_relation_state(anxiety, shame)` | 显式优先；否则 24h 反馈推断 |
| 后台检查 | `observe_background(event)` | 不写 intent、不耗时机预算（A4/X3） |

CLI 等价：

```bash
python proactive/run.py observe-user "原话"
python proactive/run.py observe-bot "回复" --non-social
python proactive/run.py mark-owed-reply --text "原话"
python proactive/run.py set-relation --anxiety 2 --shame 1
python proactive/run.py observe-background --event '{"event":"bili_check"}'
```

## 3. tick 与守护

- 核心只有 `Engine.tick(now)` 一个入口，不藏线程。
- 前台：`run.py run --interval 60`。
- 后台：`run.py daemon start/stop/status`，PID 文件 + STOP 文件。
- 一个部署只允许一个 daemon；多开会导致重复发送。

## 4. 模式

```bash
python proactive/run.py mode off      # 立即停
python proactive/run.py mode shadow   # 判定但不发
python proactive/run.py mode enforce  # 真发
```

- `mode` 存 SQLite `control`，daemon 每 tick 读取，无需重启。
- 其他配置改动需要重启 daemon。
- `shadow_until`（反馈降级）独立于全局 mode；两处任一为 shadow 都不发送。

## 5. 记忆召回

```json
"recall": {"mode": "memory_import", "memory_root": "../memory", "folder_id": "default"}
```

| 模式 | 行为 |
| --- | --- |
| `memory_import` | 直接 import `memory_core`，低延迟（推荐同机） |
| `memory_cli` | 子进程 `memory/run.py search --json`，兜底 |
| `host` | 调用宿主 `recall()` |
| `none` | 不接 |

失败返回空；D6/D8 会少一路证据，但主循环继续（S6）。
不要在 Gate 里调用记忆检索；只在感知阶段取一次（R4）。

## 6. 内容与 persona

- 默认模板只使用 evidence；`persona` 只影响 LLM 文案。
- 接 LLM 内容：`content.provider=http`，填 `base_url/model/api_key`；失败回退模板。
- 自定义文案：编辑 `templates/content_templates.json`；不要引入 evidence 之外的事实。

## 7. 手动触发与探针

```bash
python proactive/run.py trigger --text "现在说"       # 跳过静默/窗口/配额，仍记账
python proactive/run.py trigger --text "现在说" --force  # 覆盖 shadow（不覆盖 off）
python proactive/run.py probe --allow-send           # 真调 send_text，验证投递
```

`probe` 不写账本、不走 Gate；它只验证 `send_text` 是否可用。

## 8. 单流与多实例

v1 一个 `proactive/` 对应一条聊天流（`stream_id`）。
多聊天部署多份实例，各自一套 DB/配置/daemon；不要用一个实例硬塞多个 chat，否则预算、静默、账本会互相干扰。

## 9. 失败策略

| 失败 | 行为 |
| --- | --- |
| `send_text` False/None/异常 | 释放账本，写 failed，不重试 |
| 时机 LLM 超时 | 规则 fallback；连续 3 次当日规则直发 |
| 内容 LLM 失败 | 回退模板 |
| recall 失败 | 返回空，主循环继续 |
| daemon 崩溃 | 日志在 `data/daemon.log`；`daemon start` 重启 |

## 10. 上线顺序

1. `shadow` + FileSink，跑 24h；
2. 替换 `adapter.py`，`probe --allow-send` 验证真实投递；
3. 继续 shadow 观察真实 outbox；
4. `mode enforce`；
5. 观察反馈与 `status`，出现拒绝/困惑自动降级 shadow；
6. 异常时 `mode off` → `daemon stop`。
