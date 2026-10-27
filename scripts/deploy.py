#!/usr/bin/env python3
"""把 ai-proactive-deploy 部署到目标目录，并跑 A1–A10/S1–S6 自检。"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
ASSETS = SKILL_ROOT / "assets"

RUN_PY = '''"""部署入口：把 proactive 目录设为根，再转给 CLI。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ["PROACTIVE_ROOT"] = str(ROOT)
sys.path.insert(0, str(ROOT))

from proactive_core.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
'''


def copy_tree(src: Path, dst: Path, force: bool, log: list[str]) -> None:
    if dst.exists() and force:
        shutil.rmtree(dst)
        log.append(f"替换 {dst}")
    if not dst.exists():
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        log.append(f"复制 {src.name} → {dst}")
    else:
        log.append(f"保留已存在的 {dst}")


def copy_file(src: Path, dst: Path, force: bool, log: list[str]) -> None:
    if dst.exists() and not force:
        log.append(f"保留已存在的 {dst}")
        return
    shutil.copy2(src, dst)
    log.append(f"写入 {dst}")


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return subprocess.run(cmd, cwd=str(cwd), text=True, encoding="utf-8", capture_output=True, env=env)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="部署 AI 主动行为系统")
    parser.add_argument("--target", required=True, help="目标项目根目录")
    parser.add_argument("--profile", choices=["offline", "llm"], default="offline",
                        help="offline=规则/模板；llm=预置 OpenAI 兼容配置（需填 key）")
    parser.add_argument("--force", action="store_true", help="覆盖代码目录（含 prompts/templates；不动 data/ 与 adapter.py）")
    parser.add_argument("--force-config", action="store_true", help="覆盖 config/proactive.config.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-selfcheck", action="store_true")
    parser.add_argument("--decisions-file", default=None)
    args = parser.parse_args(argv)
    if sys.version_info < (3, 11):
        print(f"需要 Python ≥3.11，当前 {sys.version.split()[0]}", file=sys.stderr)
        return 2

    target = Path(args.target).expanduser().resolve()
    root = target / "proactive"
    if args.dry_run:
        print(f"目标项目：{target}\n部署目录：{root}\nprofile：{args.profile}\ndry-run：不写文件。")
        return 0
    if not target.exists() or not target.is_dir():
        print(f"目标不是已存在目录：{target}", file=sys.stderr)
        return 2
    root.mkdir(parents=True, exist_ok=True)

    log: list[str] = []
    copy_tree(ASSETS / "proactive_core", root / "proactive_core", args.force, log)
    copy_tree(ASSETS / "prompts", root / "prompts", args.force, log)
    copy_tree(ASSETS / "templates", root / "templates", args.force, log)
    (root / "config").mkdir(exist_ok=True)

    config_path = root / "config" / "proactive.config.json"
    if args.force_config or not config_path.exists():
        cfg = json.loads((ASSETS / "config" / "proactive.config.json").read_text(encoding="utf-8"))
        if args.profile == "llm":
            cfg["timing"]["provider"] = "http"
            cfg["content"]["provider"] = "http"
        config_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log.append(f"写入 {config_path}（profile={args.profile}）")
    else:
        log.append(f"保留已存在的 {config_path}")

    adapter = root / "adapter.py"
    if not adapter.exists():
        shutil.copy2(ASSETS / "templates" / "adapter_stub.py", adapter)
        log.append(f"写入 {adapter}（默认 FileSinkHost，生产前替换）")
    else:
        log.append(f"保留宿主适配器 {adapter}")

    (root / "run.py").write_text(RUN_PY, encoding="utf-8")
    log.append(f"写入 {root / 'run.py'}")
    for sub in ("data", "traces", "reports"):
        (root / sub).mkdir(exist_ok=True)

    started = time.time()
    init = run([sys.executable, "run.py", "init", "--json"], root)
    init_ok = init.returncode == 0
    try:
        health = json.loads(init.stdout)
    except json.JSONDecodeError:
        health = {"raw": (init.stdout or init.stderr)[-2000:]}

    selfcheck = {}
    selfcheck_ok = True
    if not args.skip_selfcheck:
        check = run([sys.executable, "run.py", "selfcheck", "--json"], root)
        selfcheck_ok = check.returncode == 0
        try:
            selfcheck = json.loads(check.stdout)
        except json.JSONDecodeError:
            selfcheck = {"ok": False, "raw": (check.stdout or check.stderr)[-3000:]}

    decisions = ""
    if args.decisions_file:
        p = Path(args.decisions_file)
        if p.exists():
            decisions = p.read_text(encoding="utf-8")

    effective_mode = "shadow"
    try:
        effective_mode = json.loads(config_path.read_text(encoding="utf-8")).get("mode", "shadow")
    except (OSError, json.JSONDecodeError):
        pass

    report = [
        "# 主动行为部署报告",
        "",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 目标项目：`{target}`",
        f"- 部署目录：`{root}`",
        f"- profile：`{args.profile}`" + ("（LLM 连通性未验证，填好配置后先跑 eval/手动 tick）" if args.profile == "llm" else ""),
        f"- Python：{sys.version.split()[0]}",
        f"- 当前 mode：`{effective_mode}`（shadow = 只判定与记账，不发送）",
        "",
        "## 文件操作",
        "",
        *[f"- {line}" for line in log],
        "",
        "## 健康检查",
        "",
        "```json",
        json.dumps(health, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    if selfcheck:
        report += [
            "## 自检结果",
            "",
            f"- 结果：{'通过' if selfcheck.get('ok') else '未通过'}（{selfcheck.get('passed')}/{selfcheck.get('total')}）",
            f"- 耗时：{selfcheck.get('duration_ms')} ms",
            "",
            "| 用例 | 结果 | 说明 |",
            "| --- | --- | --- |",
        ]
        for item in selfcheck.get("checks", []):
            report.append(f"| {item['name']} | {'PASS' if item['ok'] else 'FAIL'} | {item.get('detail', '')} |")
        report.append("")
        if not selfcheck.get("ok"):
            report += ["**自检未通过：先修问题，不要切 enforce。**", ""]

    if decisions:
        report += ["## 部署前判断（用户回答）", "", decisions, ""]

    report += [
        "## 接入（宿主侧）",
        "",
        "1. 每条用户消息：`python run.py observe-user \"原话\"` 或直接调用 `engine.on_user_message`。",
        "2. 每条 bot 消息：`observe-bot`；系统/计划消息加 `--non-social`（不计入社交沉默）。",
        "3. 欠回复由宿主显式标记：`mark-owed-reply`；关系状态用 `set-relation`。",
        "4. 后台检查用 `observe-background`：不写意图、不耗时机预算。",
        "5. 真实投递：替换 `proactive/adapter.py` 的 `create_host`，`send_text` 返回 True 才算成功。",
        "",
        "## 运行",
        "",
        "```bash",
        "python proactive/run.py tick                 # 手动一轮（shadow 下不发送）",
        "python proactive/run.py run --interval 60    # 前台循环",
        "python proactive/run.py daemon start         # 后台进程",
        "python proactive/run.py status",
        "python proactive/run.py mode enforce         # 观察期后再切",
        "python proactive/run.py mode off             # 立即停",
        "python proactive/run.py probe --allow-send   # 手动探针（会真调用 send_text）",
        "```",
        "",
        "## 开放话题 / 计划项 / 手动触发",
        "",
        "```bash",
        "python proactive/run.py open-loops list",
        "python proactive/run.py schedule add --title \"复习\" --due-in-minutes 90",
        "python proactive/run.py trigger --text \"现在说话\"    # 跳过静默/窗口/配额，仍记账",
        "```",
        "",
        "## 配置",
        "",
        "- `config/proactive.config.json`：窗口、阈值、预算、配额、recall、LLM。",
        "- 默认 `mode=shadow`、`timing.provider=none`、`content.provider=none`；LLM 是增强，不是前置条件。",
        "- 接记忆：`recall.mode=memory_import`，`memory_root` 指向 `../memory`。",
        "- 换配置后 daemon 需重启；`mode` 通过 CLI 热切换。",
        "",
        "## 回滚",
        "",
        "```bash",
        "python proactive/run.py mode off",
        "python proactive/run.py daemon stop",
        "python proactive/run.py uninstall              # 只删代码/配置，保留数据",
        "python proactive/run.py uninstall --purge --yes  # 连数据一起删",
        "```",
        "",
    ]
    (root / "DEPLOY-REPORT.md").write_text("\n".join(report), encoding="utf-8")

    print(f"部署目录：{root}")
    print(f"初始化：{'OK' if init_ok else 'FAIL'}；自检：{'未跑' if args.skip_selfcheck else ('OK' if selfcheck_ok else 'FAIL')}")
    print(f"报告：{root / 'DEPLOY-REPORT.md'}")
    if not init_ok:
        print((init.stderr or init.stdout)[-2000:], file=sys.stderr)
    if not selfcheck_ok and selfcheck:
        for item in selfcheck.get("checks", []):
            if not item["ok"]:
                print(f"[FAIL] {item['name']}: {item['detail']}", file=sys.stderr)
    print(f"耗时：{int((time.time() - started) * 1000)} ms")
    return 0 if init_ok and selfcheck_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
