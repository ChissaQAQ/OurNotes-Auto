"""命令行入口。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import re
import sys
import threading
import time
from pathlib import Path

from . import __version__, sysmem
from .config import Config, apply_override, load_config, save_config
from .logfile import file_handler

logger = logging.getLogger("ournotes_auto")
SECRET_SETTING = re.compile(r"(notify\.target\s*=).*", re.S)  # 推送地址等于密码，日志里不记（键前后有空格也认）


class JsonLineFormatter(logging.Formatter):
    """每条日志一行 JSON（ASCII 转义），供 MaaFramework Agent 等上层程序解析后转发。"""

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)
        return json.dumps({"level": record.levelname, "name": record.name, "msg": msg})


def _setup_logging(verbose: int, log_file: str | None = "data/ournotes.log", json_log: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt = logging.Formatter("%(asctime)s %(levelname).1s %(name)s: %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    console = logging.StreamHandler(sys.stdout if json_log else sys.stderr)
    console.setLevel(level)
    console.setFormatter(JsonLineFormatter() if json_log else fmt)
    root.addHandler(console)
    if log_file:
        root.addHandler(file_handler(log_file))
    for noisy in ("urllib3", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _cpu_name() -> str:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
    except (ImportError, OSError):
        return platform.processor()


def _log_environment(argv: list[str]) -> None:
    """版本和运行环境记一行日志（模拟器设备的信息在连接时另记一行，见 context.open_device）。"""
    mem = sysmem.describe()
    logger.debug(
        "OurNotes-Auto %s（Python %s，%s，%s%s）：%s",
        __version__,
        platform.python_version(),
        platform.platform(),
        _cpu_name(),
        f"，{mem}" if mem else "",
        " ".join(_masked(argv)),
    )


def _masked(argv: list[str]) -> list[str]:
    return [SECRET_SETTING.sub(r"\1***", a) for a in argv]


# ---------------------------------------------------------------- 谱面


def _catalog(cfg: Config, refresh: bool = False):
    from .context import load_catalog

    return load_catalog(cfg, refresh)


def resolve_song(catalog, text: str):
    """曲目 ID 或曲名（任意语言，模糊匹配）→ Song。"""
    found = catalog.lookup(text)  # 人输入的曲名：取最像的，匹配结果会打印出来
    if found is None:
        raise SystemExit(f"找不到曲目：{text}")
    song, score = found
    if score < 100:
        logger.info("「%s」匹配到 %d %s（相似度 %.0f）", text, song.music_id, song.display_title(), score)
    return song


def cmd_charts_search(cfg: Config, args) -> int:
    _, catalog = _catalog(cfg, args.refresh)
    if args.text:
        found = catalog.match(args.text, min_score=0, min_margin=0)
        songs = [found[0]] if found else []
    else:
        songs = sorted(catalog.songs.values(), key=lambda s: s.music_id)
    for s in songs:
        abbr = {"easy": "EZ", "normal": "NM", "hard": "HD", "expert": "EX"}
        diffs = " ".join(f"{abbr.get(d, d)}{s.difficulties[d].get('level', '?')}" for d in s.difficulties)
        print(f"{s.music_id:>7}  {s.display_title():<30}  {diffs}")
    return 0


def cmd_charts_show(cfg: Config, args) -> int:
    from .geometry import Geometry
    from .planner import Planner

    client, catalog = _catalog(cfg)
    song = resolve_song(catalog, args.song)
    diff = args.difficulty or cfg.game.difficulty
    chart = client.chart(song.music_id, diff, song.display_title())
    plan = Planner(Geometry(1280, 720, cfg.geometry), cfg.play.touch).plan(chart)
    first_ms, spans = chart.first_hits()
    print(f"{chart.key} {chart.title}")
    print(f"  判定音符 {chart.parsed_judged}/{chart.judged_count}，单点 {len(chart.notes)}，长条/引导 {len(chart.slides)}")
    print(f"  时长 {chart.last_ms / 1000:.1f}s，首音符 {first_ms:.0f}ms，区间 {[f'{s.left:g}-{s.right:g}' for s in spans]}")
    print(f"  手势 {len(plan.gestures)}，触控事件 {len(plan.events)}，丢弃 {len(plan.dropped)}")
    return 0


def cmd_charts_prefetch(cfg: Config, args) -> int:
    from .charts.bdon import ChartNotFound

    client, catalog = _catalog(cfg, refresh=True)
    diffs = [args.difficulty] if args.difficulty else list(("easy", "normal", "hard", "expert"))
    ok = fail = 0
    for song in sorted(catalog.songs.values(), key=lambda s: s.music_id):
        for d in diffs:
            if d not in song.difficulties:
                continue
            try:
                client.chart(song.music_id, d)
                ok += 1
            except (ChartNotFound, ValueError) as e:
                fail += 1
                logger.warning("%d_%s：%s", song.music_id, d, e)
    print(f"已缓存 {ok} 张谱面，失败 {fail}")
    return 0 if fail == 0 else 1


# ---------------------------------------------------------------- 记录


def cmd_records(cfg: Config, args) -> int:
    from .records import RecordStore
    from .report import report_lines

    store = RecordStore("data")
    if args.clear_chart_offsets:
        count = store.clear_chart_offsets()
        logger.info("已清除 %d 张谱面的 offset（全局学习值 %+.1fms 不变）", count, store.learned_offset_ms)
        return 0
    results = store.history(limit=None)
    library = _library(cfg)
    lines = report_lines(results, library.title, recent=args.recent, not_ap=args.not_ap, chart_offsets=store.chart_offsets())
    show = (lambda line: logger.info("%s", line)) if args.json_log else print  # 界面里只看得到日志
    for line in lines:
        show(line)
    _show_stats(library, show, open_page=args.json_log)
    return 0


def _library(cfg: Config):
    """曲名、乐队、等级（:class:`.stats.Library`）。"""
    from .stats import Library

    try:
        _, catalog = _catalog(cfg)
    except Exception as e:  # 离线又没有缓存：只显示 ID
        logger.warning("读取曲目目录失败（%s），只显示曲目 ID", e)
        catalog = None
    return Library.from_catalog(catalog)


OPENED = {"focused": "统计窗口已经开着，已切到前面", "window": "已打开统计窗口（打完一局自动刷新）"}


def _show_stats(library, show, open_page: bool) -> None:
    """更新统计页、显示累计时长和成就；从界面运行时打开「统计与成就」窗口。出错只记警告。"""
    from . import stats

    try:
        view = stats.refresh("data", library=library)
        for line in stats.summary_lines(view):
            show(line)
        page = Path("data", stats.PAGE_HTML).resolve()
        if open_page:
            show(OPENED.get(stats.open_page(page), f"已在浏览器里打开统计页：{page}"))
        else:
            show(f"完整的统计页：{page}")
    except Exception as e:  # noqa: BLE001 - 统计页只是给人看的
        logger.warning("更新统计页失败：%s", e)


def cmd_backup(cfg: Config, args) -> int:
    """导出备份，或从备份、旧的包导入（见 backup.py）。"""
    from . import backup

    data, root = Path("data"), Path.cwd()
    try:
        if args.backup_cmd == "export":
            path, b = backup.export_bundle(data, root, Path(args.out) if args.out else None)
            logger.info("已导出 %s 局演奏记录、%s 次运行记录、%d 个成就：%s", f"{len(b.records):,}", f"{len(b.sessions):,}", len(b.unlocked), path)
            logger.info("换了新的包以后，用 backup import 导入就能恢复（招募截图不在备份里）")
            return 0
        src = Path(args.source) if args.source else backup.latest(root)
        if src is None:
            logger.error("没找到备份：%s 里没有导出的备份文件，请指定备份文件或旧的包目录", backup.backup_dirs(root)[0])
            return 1
        b = backup.read_source(src, data)
        result = backup.import_bundle(data, b)
    except (backup.BackupError, OSError) as e:
        logger.error("%s", e)
        return 1
    if result.bad:
        logger.warning("%s 里有 %d 行演奏记录读不了，跳过了", b.name, result.bad)
    if result.backup is None:
        logger.info("%s 里的记录这边都有了，没有改动", b.name)
        return 0
    parts = [f"{result.records:,} 局演奏记录"] if result.records else []
    parts += [f"{result.arena:,} 局激奏舞台记录"] if result.arena else []
    parts += [f"{result.sessions:,} 次运行记录"] if result.sessions else []
    parts += [f"{result.achievements} 个成就"] if result.achievements else []
    parts += ["学到的 offset"] if result.offsets else []
    known = f"（另有 {result.known:,} 局这边已经有了）" if result.known else ""
    logger.info("已从 %s 导入%s%s；导入前的记录复制到了 %s", b.name, "、".join(parts) or "成就的解锁时间", known, result.backup)
    _show_stats(_library(cfg), print, open_page=False)
    return 0


def cmd_stats_serve(cfg: Config, args) -> int:
    """「统计与成就」窗口背后的本机服务（见 stats_server.py），窗口关掉后退出。"""
    from . import stats, stats_server

    stats_server.serve(Path("data"), Path(args.ready), refresh=lambda: stats.refresh("data", library=_library(cfg)))
    return 0


def cmd_stats_window(cfg: Config, args) -> int:
    """双击包目录里的「统计与成就」快捷方式：打开统计窗口（已经开着时切到前面），再重新统计一遍
    （窗口看到页面变了会自己刷新）。没有控制台，不输出。"""
    from . import stats

    page = Path("data", stats.PAGE_HTML).resolve()
    existed = page.exists()
    if existed:
        stats.open_page(page)
    _show_stats(_library(cfg), lambda line: None, open_page=not existed)
    return 0


def cmd_notify_test(cfg: Config, args) -> int:
    """按 notify 的设置发一条测试推送。"""
    from . import notify

    if not cfg.notify.channel.strip():
        logger.error("没有设置推送方式：在设置里选「推送到手机」并填写推送地址（命令行运行时在 config.yaml 的 notify 里设置）")
        return 1
    try:
        notify.send(cfg.notify, "test", "推送测试", "收到这条消息，说明推送设置好了")
    except notify.NotifyError as e:
        logger.error("推送失败：%s", e)
        return 1
    logger.info("已发出测试推送（%s），看看手机收到没有", cfg.notify.channel.strip())
    return 0


# ---------------------------------------------------------------- 配置


def cmd_init_config(cfg: Config, args) -> int:
    path = Path(args.config)
    if path.exists() and not args.force:
        print(f"{path} 已存在（加 --force 覆盖）")
        return 1
    save_config(cfg, path)
    print(f"已写入 {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ournotes-auto", description="BanG Dream! Our Notes 自动演奏工具")
    p.add_argument("-c", "--config", default="config.yaml", help="配置文件（默认 config.yaml）")
    p.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="覆盖一项配置，如 --set game.difficulty=hard（可重复）",
    )
    p.add_argument("-v", "--verbose", action="count", default=0, help="输出调试日志")
    p.add_argument("--json-log", action="store_true", help="控制台日志改为每行一条 JSON，输出到标准输出")
    p.add_argument("--stdin-stop", action="store_true", help="从标准输入读到 stop 或输入关闭时停止任务")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("init-config", help="生成默认配置文件")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_init_config)

    charts = sub.add_parser("charts", help="谱面相关").add_subparsers(dest="charts_cmd", required=True)
    sp = charts.add_parser("search", help="列出或搜索曲目")
    sp.add_argument("text", nargs="?", default="")
    sp.add_argument("--refresh", action="store_true", help="强制刷新索引")
    sp.set_defaults(func=cmd_charts_search)
    sp = charts.add_parser("show", help="下载并解析谱面，显示统计")
    sp.add_argument("song", help="曲目 ID 或曲名")
    sp.add_argument("-d", "--difficulty", choices=("easy", "normal", "hard", "expert"))
    sp.set_defaults(func=cmd_charts_show)
    sp = charts.add_parser("prefetch", help="缓存全部谱面")
    sp.add_argument("-d", "--difficulty", choices=("easy", "normal", "hard", "expert"))
    sp.set_defaults(func=cmd_charts_prefetch)

    sp = sub.add_parser("records", help="汇总本地演奏记录（data/records.jsonl）")
    sp.add_argument("--recent", type=int, default=10, help="列出最近几局（默认 10）")
    sp.add_argument("--not-ap", type=int, default=10, help="「还没 AP」「按谱面修正的 offset」最多列几张（默认 10）")
    sp.add_argument("--clear-chart-offsets", action="store_true", help="清除按谱面学到的 offset（全局的不变）")
    sp.set_defaults(func=cmd_records)
    sp = sub.add_parser("stats-window", help="打开「统计与成就」窗口（包目录里「统计与成就」快捷方式运行的就是这个）")
    sp.set_defaults(func=cmd_stats_window)
    sp = sub.add_parser("stats-serve", help="「统计与成就」窗口背后的本机服务（打开窗口时自动启动，窗口关掉后退出）")
    sp.add_argument("--ready", required=True, help="起来后把端口和密钥写进这个文件")
    sp.set_defaults(func=cmd_stats_serve)
    backup = sub.add_parser("backup", help="备份与恢复：导出演奏记录、运行记录、成就和学到的 offset，或者导入")
    backup = backup.add_subparsers(dest="backup_cmd", required=True)
    sp = backup.add_parser("export", help="导出备份（默认放在「文档\\OurNotes-Auto」）")
    sp.add_argument("--out", help="放到这个文件夹")
    sp.set_defaults(func=cmd_backup)
    sp = backup.add_parser("import", help="从备份文件、旧的包目录或它的 data 文件夹导入（合并，已有的记录不变）")
    sp.add_argument("source", nargs="?", default="", help="不填时用默认位置里最新的备份")
    sp.set_defaults(func=cmd_backup)
    sp = sub.add_parser("notify-test", help="按配置的 notify（推送到手机）发一条测试推送")
    sp.set_defaults(func=cmd_notify_test)

    from . import commands

    commands.register(sub)
    return p


def _watch_stdin(stop: threading.Event, src, update: threading.Event | None = None) -> None:
    for line in src:
        cmd = line.strip()
        if cmd == "stop":
            break
        if cmd == "update" and update is not None:
            logger.debug("收到更新请求")
            update.set()
    logger.info("收到停止请求")
    stop.set()


def start_stdin_watch(stop: threading.Event, update: threading.Event | None = None) -> None:
    """后台线程读标准输入，读到 stop 或输入关闭时置位 ``stop``，读到 update 时置位 ``update``（新版本已下载，
    挂机在安全的时机退出、由 Agent 重新启动来换上新代码）。

    读的是标准输入的私有副本，0 号句柄改指向 NUL：Windows 上同步管道有挂起的读时，
    别处查询标准输入句柄也会一起卡住——加载 numpy 等扩展 DLL 时 CRT 初始化就会查询，
    结果子进程在 import 时就卡死。
    """
    fd = os.dup(sys.stdin.fileno())
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    src = os.fdopen(fd, encoding="utf-8", errors="replace")
    threading.Thread(target=_watch_stdin, args=(stop, src, update), name="stdin-stop", daemon=True).start()


def _ensure_shortcut() -> None:
    """从界面运行任务时，发布包目录里还没有「统计与成就」快捷方式就建一个（后台做，不耽误任务）。"""
    from . import stats_window

    def run() -> None:
        try:
            if stats_window.ensure_shortcut(Path.cwd()):
                logger.debug("已在包目录里建快捷方式「%s」", stats_window.SHORTCUT)
        except Exception as e:  # noqa: BLE001 - 快捷方式只是方便打开统计窗口
            logger.debug("建快捷方式失败：%s", e)

    threading.Thread(target=run, name="stats-shortcut", daemon=True).start()


def _agent_run(rest: list[str]) -> int:
    """``agent-run <JSON>``：Agent 交来的任务（任务名、界面选项、控制器信息）换成命令行参数执行，
    开始和完成各在界面上显示一行。"""
    from .agent.params import TASKS, ParamError, agent_argv

    try:
        task, argv = agent_argv(json.loads(rest[0]) if rest else None)
    except ValueError as e:
        _setup_logging(0, json_log=True)
        logger.error("%s", e if isinstance(e, ParamError) else f"任务参数不是有效的 JSON：{e}")
        return 2
    return _main(argv, label=TASKS[task])


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["agent-run"]:
        return _agent_run(argv[1:])
    return _main(argv)


def _main(argv: list[str], label: str | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.label = label  # 界面上的任务名（记进统计页的运行记录）
    _setup_logging(args.verbose, json_log=args.json_log)
    _log_environment(argv)
    if label:
        logger.info("开始%s", label)
        _ensure_shortcut()
    cfg = load_config(args.config)
    for item in args.set:
        key, sep, value = item.partition("=")
        try:
            if not sep:
                raise ValueError(f"--set 需要 KEY=VALUE 格式：{item}")
            apply_override(cfg, key.strip(), value)
        except ValueError as e:
            logger.error("%s", e)
            return 2
    args.stop = threading.Event()  # 需要中途停止的命令用它；Ctrl+C 另外处理
    args.update = threading.Event()  # 挂机收到它后在安全的时机退出（退出码 EXIT_UPDATE），换上新版本再继续
    if args.stdin_stop:
        start_stdin_watch(args.stop, args.update)
    from . import notify
    from .context import SetupError
    from .release import EXIT_UPDATE

    errors = notify.LastError()
    logging.getLogger().addHandler(errors)
    started = time.monotonic()
    args.summary = ""  # 连续演奏的局数、FC、AP，完成时推送
    try:
        code = int(args.func(cfg, args) or 0)
    except SetupError as e:
        logger.error("%s", e)
        code = 2
    except KeyboardInterrupt:
        logger.warning("已中断")
        return 130
    except Exception:
        logger.exception("意外错误")
        code = 1
    finally:
        logging.getLogger().removeHandler(errors)
    if label and code == 0 and not args.stop.is_set():
        logger.info("%s完成", label)
    if not args.stop.is_set() and code != EXIT_UPDATE:  # 手动停止的不推送；为更新退出的马上接着运行
        notify.task_end(cfg.notify, args.command, label, code, args.summary, errors.last, time.monotonic() - started)
    return code


if __name__ == "__main__":
    sys.exit(main())
