# Security

## 仓库内容边界

- 本仓库不包含真实 API key、token、cookie、密码、私钥或聊天数据。
- `assets/config/proactive.config.json` 是模板，`api_key` 字段保持为空。
- 部署后的真实配置位于目标项目的 `proactive/config/`，`data/`、`traces/`、`reports/` 与根目录 `adapter.py` 均被 `.gitignore` 排除。
- `adapter.py` 默认只写 `data/outbox.jsonl`，不会把消息发到任何外部平台。

## 提交前检查

```bash
python -m compileall -q assets/proactive_core scripts
python proactive/run.py selfcheck --json
```

不要在 Issue、PR、示例配置或测试数据中粘贴真实密钥、手机号、QQ 号、地址或第三方隐私。

## 报告问题

请使用 GitHub 的私密漏洞报告 / Security Advisory 功能，不要在公开 Issue 中披露利用细节或真实凭据。
