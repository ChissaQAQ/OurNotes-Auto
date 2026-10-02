"""命令行入口。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import sys
import threading
from pathlib import Path

from . import __version__
from .config import Config, apply_override, load_config, save_config
from .logfile import file_handler

logger = logging.getLogger("ournotes_auto")


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
    logger.debug(
        "OurNotes-Auto %s（Python %s，%s，%s）：%s",
        __version__,
        platform.python_version(),
        platform.platform(),
        _cpu_name(),
        " ".join(argv),
    )


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

    results = RecordStore("data").history(limit=None)
    try:
        _, catalog = _catalog(cfg)
    except Exception as e:  # 离线又没有缓存：只显示 ID
        logger.warning("读取曲目目录失败（%s），只显示曲目 ID", e)
        catalog = None

    def title(mid: int) -> str:
        song = catalog.songs.get(mid) if catalog else None
        return song.display_title() if song else str(mid)

    lines = report_lines(results, title, recent=args.recent, not_ap=args.not_ap)
    for line in lines:
        if args.json_log:  # 界面里只看得到日志
            logger.info("%s", line)
        else:
            print(line)
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
    sp.add_argument("--not-ap", type=int, default=10, help="「打过但还没 AP」最多列几张谱面（默认 10）")
    sp.set_defaults(func=cmd_records)

    from . import commands

    commands.register(sub)
    return p


def _watch_stdin(stop: threading.Event, src) -> None:
    for line in src:
        if line.strip() == "stop":
            break
    logger.info("收到停止请求")
    stop.set()


def start_stdin_watch(stop: threading.Event) -> None:
    """后台线程读标准输入，读到 stop 或输入关闭时置位 ``stop``。

    读的是标准输入的私有副本，0 号句柄改指向 NUL：Windows 上同步管道有挂起的读时，
    别处查询标准输入句柄也会一起卡住——加载 numpy 等扩展 DLL 时 CRT 初始化就会查询，
    结果子进程在 import 时就卡死。
    """
    fd = os.dup(sys.stdin.fileno())
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    src = os.fdopen(fd, encoding="utf-8", errors="replace")
    threading.Thread(target=_watch_stdin, args=(stop, src), name="stdin-stop", daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose, json_log=args.json_log)
    _log_environment(sys.argv[1:] if argv is None else argv)
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
    if args.stdin_stop:
        start_stdin_watch(args.stop)
    from .context import SetupError

    try:
        return int(args.func(cfg, args) or 0)
    except SetupError as e:
        logger.error("%s", e)
        return 2
    except KeyboardInterrupt:
        logger.warning("已中断")
        return 130
    except Exception:
        logger.exception("意外错误")
        return 1


if __name__ == "__main__":
    sys.exit(main())
