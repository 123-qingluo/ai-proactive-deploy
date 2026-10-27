"""CLI：观察、tick、run、daemon、模式切换、开放话题、计划项、手动触发、自检。"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

VERSION = "1.0.0"


def _root(args) -> Path:
    return Path(args.root or os.environ.get("PROACTIVE_ROOT", ".")).resolve()


def _emit(obj, as_json: bool, human: str | None = None) -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        print(human if human is not None else json.dumps(obj, ensure_ascii=False, indent=2))


def _engine(root: Path, need_host: bool = False):
    from .config import load_config, validate_config
    from .engine import Engine
    from .host import FileSinkHost, load_host

    cfg = load_config(root)
    validate_config(cfg)
    host = load_host(root, cfg) if need_host else FileSinkHost(root)
    return Engine(root, host=host)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _terminate(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def _daemon_start(root: Path, interval: float | None) -> int:
    from .config import load_config, resolve, validate_config

    cfg = load_config(root)
    validate_config(cfg)
    pid_path = resolve(root, cfg.get("daemon", {}).get("pid_file", "data/proactive.pid"))
    stop_path = resolve(root, cfg.get("daemon", {}).get("stop_file", "data/STOP"))
    log_path = resolve(root, cfg.get("daemon", {}).get("log_file", "data/daemon.log"))
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().strip())
        except ValueError:
            pid = 0
        if _pid_alive(pid):
            print(f"daemon 已在运行：PID {pid}")
            return 1
        pid_path.unlink(missing_ok=True)
    if stop_path.exists():
        stop_path.unlink(missing_ok=True)
    interval = float(interval or cfg.get("daemon", {}).get("interval_seconds", 60))
    cmd = [sys.executable, str(root / "run.py"), "run", "--interval", str(interval)]
    log = log_path.open("a", encoding="utf-8")
    kwargs = {"cwd": str(root), "stdout": log, "stderr": subprocess.STDOUT}
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    pid_path.write_text(str(proc.pid), encoding="utf-8")
    print(f"daemon started PID={proc.pid} interval={interval}s log={log_path}")
    return 0


def _daemon_stop(root: Path) -> int:
    from .config import load_config, resolve, validate_config

    cfg = load_config(root)
    validate_config(cfg)
    pid_path = resolve(root, cfg.get("daemon", {}).get("pid_file", "data/proactive.pid"))
    stop_path = resolve(root, cfg.get("daemon", {}).get("stop_file", "data/STOP"))
    if not pid_path.exists():
        print("daemon 未运行（无 PID 文件）")
        return 1
    try:
        pid = int(pid_path.read_text().strip())
    except ValueError:
        pid = 0
    stop_path.parent.mkdir(parents=True, exist_ok=True)
    stop_path.write_text("stop", encoding="utf-8")
    for _ in range(20):
        if not _pid_alive(pid):
            break
        time.sleep(0.5)
    if _pid_alive(pid):
        _terminate(pid)
        time.sleep(0.5)
    stop_path.unlink(missing_ok=True)
    pid_path.unlink(missing_ok=True)
    print(f"daemon stopped PID={pid}")
    return 0


def _daemon_status(root: Path) -> int:
    from .config import load_config, resolve, validate_config

    cfg = load_config(root)
    validate_config(cfg)
    pid_path = resolve(root, cfg.get("daemon", {}).get("pid_file", "data/proactive.pid"))
    if not pid_path.exists():
        print("daemon 未运行")
        return 1
    try:
        pid = int(pid_path.read_text().strip())
    except ValueError:
        pid = 0
    alive = _pid_alive(pid)
    print(json.dumps({"running": alive, "pid": pid}, ensure_ascii=False))
    return 0 if alive else 1


def _uninstall(root: Path, purge: bool, yes: bool) -> int:
    code_items = ["proactive_core", "prompts", "templates", "examples", "config", "run.py", "adapter.py", "__pycache__"]
    data_items = ["data", "traces", "reports", "DEPLOY-REPORT.md"]
    code = [root / item for item in code_items if (root / item).exists()]
    data = [root / item for item in data_items if (root / item).exists()]
    print(f"部署根：{root}")
    print("将删除（代码/配置）：")
    for path in code:
        print(f"  - {path}")
    if purge:
        print("将删除（数据，不可恢复）：")
        for path in data:
            print(f"  - {path}")
        if not yes:
            if sys.stdin.isatty():
                try:
                    answer = input("确认删除全部数据？输入 DELETE：").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\n未收到确认，已取消。")
                    return 2
                if answer != "DELETE":
                    print("已取消。")
                    return 2
            else:
                print("非交互环境需要同时传 --yes 才能 --purge。已取消。")
                return 2
    else:
        print("保留数据：")
        for path in data:
            print(f"  - {path}")
    failures = []
    for path in code + (data if purge else []):
        try:
            shutil.rmtree(path) if path.is_dir() else path.unlink()
        except OSError as exc:
            failures.append(f"{path}: {exc}")
    if failures:
        print("以下路径未能删除：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("卸载完成。" + ("数据已删除。" if purge else "数据目录仍在。"))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proactivectl", description="AI 主动行为部署与运行")
    parser.add_argument("--root", default=None, help="部署根目录；默认 PROACTIVE_ROOT 或当前目录")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="建库并检查")
    sub.add_parser("health", help="健康检查")
    sub.add_parser("status", help="当前状态")

    p = sub.add_parser("mode", help="热切换 off/shadow/enforce")
    p.add_argument("mode", choices=["off", "shadow", "enforce"])

    sub.add_parser("tick", help="执行一轮")

    p = sub.add_parser("run", help="前台循环")
    p.add_argument("--interval", type=float, default=None)
    p.add_argument("--max-ticks", type=int, default=None)

    p = sub.add_parser("daemon", help="后台进程管理")
    p.add_argument("action", choices=["start", "stop", "status"])
    p.add_argument("--interval", type=float, default=None)

    p = sub.add_parser("observe-user", help="记录一条用户消息")
    p.add_argument("text")

    p = sub.add_parser("observe-bot", help="记录一条 bot 消息")
    p.add_argument("text")
    p.add_argument("--non-social", action="store_true", help="系统/计划类消息；不计入社交沉默")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--expects-reply", dest="expects", action="store_true")
    group.add_argument("--no-expects-reply", dest="expects", action="store_false")
    p.set_defaults(expects=None)

    p = sub.add_parser("mark-owed-reply", help="标记欠用户一句回应")
    p.add_argument("--text", default=None)

    p = sub.add_parser("set-relation", help="显式设置关系焦虑/羞耻")
    p.add_argument("--anxiety", type=float, required=True)
    p.add_argument("--shame", type=float, required=True)

    p = sub.add_parser("observe-background", help="记录后台检查；不写意图、不耗预算")
    p.add_argument("--event", default="{}")

    p = sub.add_parser("open-loops", help="开放话题")
    p.add_argument("action", choices=["list", "close"])
    p.add_argument("loop_id", nargs="?")

    p = sub.add_parser("schedule", help="计划项（D9）")
    p.add_argument("action", choices=["add", "list", "done"])
    p.add_argument("--title", default="")
    p.add_argument("--due-in-minutes", type=float, default=60)
    p.add_argument("--due-at", default="")
    p.add_argument("--priority", type=float, default=0.5)
    p.add_argument("--face-risk", type=float, default=0.2)
    p.add_argument("item_id", nargs="?")

    p = sub.add_parser("trigger", help="手动触发（跳过静默/窗口/配额，仍记账审计）")
    p.add_argument("--text", default=None)
    p.add_argument("--kind", default="manual")
    p.add_argument("--class", dest="class_", default="social", choices=["social", "scheduled", "system"])
    p.add_argument("--strategy", default=None)
    p.add_argument("--force", action="store_true", help="覆盖 shadow（不覆盖 off）")

    p = sub.add_parser("probe", help="发送探针；必须显式 --allow-send")
    p.add_argument("--allow-send", action="store_true")

    p = sub.add_parser("audit", help="查看最近审计")
    p.add_argument("--tail", type=int, default=20)

    p = sub.add_parser("config", help="配置")
    p.add_argument("action", choices=["show", "validate"])

    p = sub.add_parser("recall-test", help="测试记忆召回")
    p.add_argument("query")

    sub.add_parser("selfcheck", help="A1–A10 + S1–S6 自动验收")

    p = sub.add_parser("uninstall", help="卸载；默认保留数据")
    p.add_argument("--purge", action="store_true")
    p.add_argument("--yes", action="store_true")

    for subparser in sub.choices.values():
        subparser.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                               help="输出 JSON（也可写在子命令前）")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    root = _root(args)

    if args.command == "selfcheck":
        from .config import load_config
        from .selfcheck import run_selfcheck

        report = run_selfcheck(load_config(root), root=root)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            for item in report["checks"]:
                print(f"[{'PASS' if item['ok'] else 'FAIL'}] {item['name']:<22} {item.get('detail', '')}")
            print(f"\n结果：{report['passed']}/{report['total']} 通过，耗时 {report['duration_ms']}ms")
        return 0 if report["ok"] else 1

    if args.command == "uninstall":
        return _uninstall(root, purge=args.purge, yes=args.yes)

    if args.command == "daemon":
        if args.action == "start":
            return _daemon_start(root, args.interval)
        if args.action == "stop":
            return _daemon_stop(root)
        return _daemon_status(root)

    if args.command == "config":
        from .config import load_config, validate_config

        cfg = load_config(root)
        if args.action == "validate":
            try:
                warnings = validate_config(cfg)
                _emit({"ok": True, "warnings": warnings}, args.json, json.dumps({"ok": True, "warnings": warnings}, ensure_ascii=False, indent=2))
                return 0
            except Exception as exc:
                _emit({"ok": False, "error": str(exc)}, args.json, f"配置无效：{exc}")
                return 1
        _emit(cfg, args.json, json.dumps(cfg, ensure_ascii=False, indent=2))
        return 0

    need_host = args.command in ("tick", "run", "trigger", "probe")
    try:
        engine = _engine(root, need_host=need_host)
    except Exception as exc:
        print(f"初始化失败：{exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "init":
            health = engine.health()
            _emit(health, args.json, f"已初始化：{health['db_path']} mode={health['mode']}")
            return 0 if health["ok"] else 1
        if args.command == "health":
            health = engine.health()
            _emit(health, args.json, json.dumps(health, ensure_ascii=False, indent=2))
            return 0 if health["ok"] else 1
        if args.command == "status":
            status = engine.status()
            _emit(status, args.json, json.dumps(status, ensure_ascii=False, indent=2))
            return 0
        if args.command == "mode":
            result = engine.set_mode(args.mode)
            _emit(result, args.json, f"mode={args.mode}")
            return 0
        if args.command == "tick":
            result = engine.tick()
            _emit(result, args.json, json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "run":
            try:
                result = engine.run_forever(interval=args.interval, max_ticks=args.max_ticks)
            except KeyboardInterrupt:
                result = {"ok": True, "interrupted": True}
            _emit(result, args.json, json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "observe-user":
            result = engine.on_user_message(args.text)
            _emit(result, args.json, json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "observe-bot":
            result = engine.on_bot_message(args.text, social=not args.non_social, expects_reply=args.expects)
            _emit(result, args.json, json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "mark-owed-reply":
            result = engine.mark_owed_reply(args.text)
            _emit(result, args.json, json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "set-relation":
            result = engine.set_relation_state(args.anxiety, args.shame)
            _emit(result, args.json, json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "observe-background":
            try:
                event = json.loads(args.event)
            except json.JSONDecodeError as exc:
                print(f"--event 不是合法 JSON：{exc}", file=sys.stderr)
                return 2
            result = engine.observe_background(event)
            _emit(result, args.json, json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "open-loops":
            if args.action == "list":
                rows = engine.store.open_loops(engine.state.stream_id, status=None)
                _emit(rows, args.json, json.dumps(rows, ensure_ascii=False, indent=2))
                return 0
            if not args.loop_id:
                print("close 需要 loop_id", file=sys.stderr)
                return 2
            engine.store.mark_loop(args.loop_id, "done", engine.now())
            print(f"closed {args.loop_id}")
            return 0
        if args.command == "schedule":
            from .models import new_id

            if args.action == "add":
                if not args.title:
                    print("add 需要 --title", file=sys.stderr)
                    return 2
                now = engine.now()
                if args.due_at:
                    try:
                        due = datetime.fromisoformat(args.due_at).timestamp()
                    except ValueError:
                        print("--due-at 需要 ISO 时间，例如 2026-10-28T09:00", file=sys.stderr)
                        return 2
                else:
                    due = now + args.due_in_minutes * 60
                item_id = new_id("sched")
                engine.store.add_scheduled(
                    engine.state.stream_id, item_id, args.title, due, now,
                    priority=args.priority, face_risk=args.face_risk,
                )
                print(f"added {item_id}")
                return 0
            if args.action == "list":
                rows = engine.store.scheduled_items(engine.state.stream_id)
                _emit(rows, args.json, json.dumps(rows, ensure_ascii=False, indent=2))
                return 0
            if not args.item_id:
                print("done 需要 item_id", file=sys.stderr)
                return 2
            engine.store.mark_scheduled(args.item_id, "done")
            print(f"done {args.item_id}")
            return 0
        if args.command == "trigger":
            result = engine.trigger_manual(
                text=args.text, kind=args.kind, class_=args.class_,
                strategy=args.strategy, force=args.force,
            )
            _emit(result, args.json, json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("ok") else 2
        if args.command == "probe":
            result = engine.probe(allow_send=args.allow_send)
            _emit(result, args.json, json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("ok") else 2
        if args.command == "audit":
            rows = engine.tracer.tail(args.tail)
            _emit(rows, args.json, json.dumps(rows, ensure_ascii=False, indent=2))
            return 0
        if args.command == "recall-test":
            items = engine.recall.recall(args.query)
            _emit(items, args.json, json.dumps(items, ensure_ascii=False, indent=2))
            return 0
        parser.error("未知命令")
    finally:
        engine.close()
    return 0
