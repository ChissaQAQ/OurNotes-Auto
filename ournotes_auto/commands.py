"""需要连接设备的命令（截图、校准、演奏）。"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from .config import Config, parse_daily_time
from .context import SetupError, open_context
from .context import open_device as _device
from .context import open_ocr as _ocr

logger = logging.getLogger("ournotes_auto")

# 拟人化选项的上限：时机偏移再加上同步误差也要留在 PERFECT（±50ms）以内
MAX_GREAT_RATIO = 0.2
MAX_JITTER_MS = 20.0


def _parse_point(text: str) -> tuple[int, int]:
    x, y = (int(v) for v in text.split(","))
    return x, y


def _tap_all(touch, points: list[tuple[int, int]], gap_s: float) -> None:
    for i, (x, y) in enumerate(points):
        if i:
            time.sleep(gap_s)
        touch.tap(x, y)


def _chart(cfg: Config, song_text: str, difficulty: str | None):
    from .cli import _catalog, resolve_song

    client, catalog = _catalog(cfg)
    song = resolve_song(catalog, song_text)
    return client.chart(song.music_id, difficulty or cfg.game.difficulty, song.display_title())


def cmd_screenshot(cfg: Config, args) -> int:
    import cv2

    from .calibrate import overlay_geometry
    from .geometry import Geometry

    with _device(cfg) as (source, _):
        frame, _ = source.grab()
    if args.overlay:
        h, w = frame.shape[:2]
        frame = overlay_geometry(frame, Geometry(w, h, cfg.geometry), cfg.play.sync)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), frame)
    print(out)
    return 0


def cmd_play(cfg: Config, args) -> int:
    """演奏当前画面即将开始的一首歌：先依次点击 --tap 给出的坐标（如 LIVE START），然后同步并执行。"""
    from .player.session import PlaySession

    chart = _chart(cfg, args.song, args.difficulty)
    if args.offset is not None:
        cfg.play.offset_ms = args.offset
    if args.record:
        cfg.play.sync.record_frames = max(cfg.play.sync.record_frames, 600)
    reader = _combo_reader(_ocr(cfg)) if args.watch_combo else None
    stop = args.stop
    with _device(cfg) as (source, touch):
        session = PlaySession(cfg, source, touch, combo_reader=reader)
        _tap_all(touch, [_parse_point(p) for p in args.tap], args.tap_gap)
        try:
            outcome = session.play(chart, stop, require_sync_ok=not args.force)
        except KeyboardInterrupt:
            stop.set()
            raise
    s = outcome.stats
    print(
        f"完成 {chart.key}：同步 τ={outcome.sync.tau_s:.3f}s 残差 {outcome.sync.rms_ms:.1f}ms，"
        f"offset {outcome.offset_ms:+.1f}ms，发送 {s.sent} 个事件，平均迟到 {s.mean_late_ms:.2f}ms（最大 {s.max_late_ms:.2f}ms）"
    )
    return 0


def cmd_calibrate_motion(cfg: Config, args) -> int:
    """跟踪首音符全程，测量音符逼近的时间常数 τ 与运动消失线。"""
    from .calibrate import measure_motion
    from .geometry import Geometry

    chart = _chart(cfg, args.song, args.difficulty)
    with _device(cfg) as (source, touch):
        w, h = source.size
        geo = Geometry(w, h, cfg.geometry)
        _tap_all(touch, [_parse_point(p) for p in args.tap], args.tap_gap)
        r = measure_motion(source, geo, chart, cfg.play.sync)
    print(
        f"τ = {r.tau_s:.4f}s（当前配置 {cfg.play.sync.tau_s:.4f}s），"
        f"运动消失线 {r.horizon_y / h:+.4f} 屏高（轨道消失线 {geo.horizon_y / h:+.4f}），"
        f"残差 {r.rms_ms:.2f}ms，{'可信' if r.ok else '不可信'}"
    )
    print("确认无误后把 play.sync.tau_s 写入配置文件。")
    return 0 if r.ok else 1


def _combo_reader(ocr):
    """演奏界面连击数小图 → 数字（给 PlaySession 的断连报告用）。"""
    from .player.monitor import combo_reader

    return combo_reader(ocr.read_text)


def cmd_look(cfg: Config, args) -> int:
    """识别当前画面（调试导航用）。"""
    from .cli import _catalog
    from .nav.jacket import JacketMatcher
    from .nav.navigator import GameNavigator
    from .nav.screens import Screen, note_speed
    from .runner import NavigationError, identify_song

    ocr = _ocr(cfg)
    client, catalog = _catalog(cfg)
    with _device(cfg) as (source, touch):
        nav = GameNavigator(cfg, source, touch, ocr, jackets=JacketMatcher.load(client, catalog))
        screen, items = nav.look(blocking_ok=True)
        if args.save:
            print(nav.save_debug("look"))
        label = nav.selected_song() if screen in (Screen.BAND_CONFIRM, Screen.CHALLENGE_BAND_CONFIRM) else None
    print(f"画面：{screen}（{screen.name}）")
    if label is not None:
        print(f"曲名：{label.title!r}，难度：{label.difficulty}，等级：{label.level}，封面：{label.jacket}")
        try:
            song, basis = identify_song(catalog, label, label.difficulty or cfg.game.difficulty)
            print(f"识别为：{song.display_title()}（{song.music_id}，{basis}）")
        except NavigationError as e:
            print(f"识别为：无（{e}）")
    elif screen is Screen.LIVE_OPTIONS:
        print(f"节奏图示速度：{note_speed(items)}")
    if args.verbose_ocr:
        for it in items:
            print(f"  ({it.x:4.0f},{it.y:4.0f},{it.w:4.0f},{it.h:3.0f}) {it.text}")
    return 0


def check_run_config(cfg: Config) -> None:
    """连续演奏前检查配置（--set 或界面传来的值可能不合法）。"""
    from .sources import CHALLENGE_COSTS, CHALLENGE_SONG_MODES, DIFFICULTIES, SONG_MODES, parse_difficulties

    if cfg.game.difficulty not in DIFFICULTIES:
        raise SetupError(f"难度应为 {'/'.join(DIFFICULTIES)}：{cfg.game.difficulty}")
    if cfg.loop.song_mode not in SONG_MODES:
        raise SetupError(f"选曲方式应为 {'/'.join(SONG_MODES)}：{cfg.loop.song_mode}")
    if cfg.loop.song_mode == "ap":
        try:
            diffs = parse_difficulties(cfg.loop.ap_difficulties)
        except ValueError as e:
            raise SetupError(f"AP 补完的难度：{e}") from None
        if not diffs:
            raise SetupError("AP 补完至少要选一个难度")
    if cfg.loop.song_mode == "ap_first":
        try:
            parse_difficulties(cfg.loop.ap_first_difficulties)
        except ValueError as e:
            raise SetupError(f"优先没 AP 的歌的难度：{e}") from None
    if cfg.loop.song_mode in ("ap", "ap_first"):
        if cfg.loop.ap_max_attempts < 1:
            raise SetupError(f"AP 补完每首最多尝试次数应至少为 1：{cfg.loop.ap_max_attempts}")
    if cfg.loop.song_mode == "list" and not cfg.loop.song_list.strip():
        raise SetupError("选曲方式为歌单时要填写歌单（--songs 或 loop.song_list）")
    if cfg.loop.challenge:
        if cfg.loop.song_mode not in CHALLENGE_SONG_MODES:
            raise SetupError(f"挑战演出的选曲方式只能是 {'/'.join(CHALLENGE_SONG_MODES)}：{cfg.loop.song_mode}")
        if cfg.loop.until_lb_empty or cfg.loop.wait_lb:
            raise SetupError("挑战演出不消耗 LB，不能和打到 LB 用完、挂机一起用（挑战演出会打到 CP 不够一局为止）")
        if cfg.game.lb_refill:
            raise SetupError("挑战演出不消耗 LB，不能用道具补充 LB")
    elif cfg.loop.song_mode == "rotate":
        raise SetupError("rotate（轮流打挑战演出的歌）只用于挑战演出（--challenge 或 loop.challenge）")
    if cfg.game.challenge_cost is not None and cfg.game.challenge_cost not in CHALLENGE_COSTS:
        raise SetupError(
            f"挑战演出每局消耗的挑战pt应为 {'/'.join(map(str, CHALLENGE_COSTS))}：{cfg.game.challenge_cost}"
        )
    if cfg.game.lb_cost is not None and cfg.game.lb_cost not in (0, 1, 2, 3):
        raise SetupError(f"每局 LB 消耗应为 0~3：{cfg.game.lb_cost}")
    if cfg.loop.until_lb_empty and not cfg.game.lb_cost:
        raise SetupError("打到 LB 用完需要把每局 LB 消耗设为 1~3（--lb-cost 或 game.lb_cost）")
    if cfg.loop.wait_lb and cfg.game.lb_cost is None:
        raise SetupError("挂机需要把每局 LB 消耗设为 0~3（--lb-cost 或 game.lb_cost；0 为不等 LB：有就每局消耗 1 个，用完消耗 0 接着打）")
    if cfg.game.lb_refill and not cfg.game.lb_cost:
        raise SetupError("LB 不足时用道具补充需要把每局 LB 消耗设为 1~3（--lb-cost 或 game.lb_cost）")
    if cfg.game.lb_refill_limit < 0:
        raise SetupError(f"用道具补充 LB 的上限应为 0（不限）或正整数：{cfg.game.lb_refill_limit}")
    if cfg.loop.studio_claim_hours < 0:
        raise SetupError(f"领取录音室练习的间隔应为 0（不领）或正数（小时）：{cfg.loop.studio_claim_hours:g}")
    claim = cfg.loop.daily_claim_time
    if not isinstance(claim, str):  # 配置文件里不加引号的 22:30 会被 YAML 读成六十进制的整数
        raise SetupError(f'每天领取日常的时间要加引号，如 daily_claim_time: "22:30"：{claim!r}')
    if claim.strip():
        try:
            parse_daily_time(claim)
        except ValueError as e:
            raise SetupError(f"每天领取日常的{e}") from None
    touch = cfg.play.touch
    if not 0 <= touch.great_ratio <= MAX_GREAT_RATIO:
        raise SetupError(f"故意打 GREAT 的比例应在 0~{MAX_GREAT_RATIO}：{touch.great_ratio}")
    if not 0 <= touch.jitter_ms <= MAX_JITTER_MS:
        raise SetupError(f"时机随机偏移应在 0~{MAX_JITTER_MS:g}ms（PERFECT 只有 ±50ms）：{touch.jitter_ms}")
    if not 0 <= touch.position_jitter <= 1:
        raise SetupError(f"触控位置随机应在 0~1：{touch.position_jitter}")


def drop_great_for_ap(cfg: Config) -> None:
    """以 AP 为目标选曲（AP 补完 / 优先没 AP 的歌）时不故意打 GREAT。"""
    if cfg.play.touch.great_ratio > 0 and cfg.loop.song_mode in ("ap", "ap_first"):
        logger.info("选曲以 AP 为目标，本次不故意打 GREAT")
        cfg.play.touch.great_ratio = 0.0


def cmd_run(cfg: Config, args) -> int:
    """全自动循环：从当前画面导航到自由演出（或挑战演出），识别曲目并连续演奏。"""
    from .runner import Runner
    from .sources import make_source

    if args.difficulty:
        cfg.game.difficulty = args.difficulty
    if args.mode:
        cfg.loop.song_mode = args.mode
    if args.songs is not None:
        cfg.loop.song_list = args.songs
    if args.max_plays is not None:
        cfg.loop.max_plays = args.max_plays
    if args.lb_cost is not None:
        cfg.game.lb_cost = args.lb_cost
    if args.lb_refill is not None:
        cfg.game.lb_refill = True
        cfg.game.lb_refill_limit = args.lb_refill
    if args.until_lb_empty:
        cfg.loop.until_lb_empty = True
    if args.wait_lb:
        cfg.loop.wait_lb = True
    if args.claim_studio is not None:
        cfg.loop.studio_claim_hours = args.claim_studio
    if args.claim_daily is not None:
        cfg.loop.daily_claim_time = args.claim_daily
    if args.ap_difficulties:
        cfg.loop.ap_difficulties = args.ap_difficulties
    if args.ap_attempts is not None:
        cfg.loop.ap_max_attempts = args.ap_attempts
    if args.challenge:
        cfg.loop.challenge = True
    if args.challenge_cost is not None:
        cfg.game.challenge_cost = args.challenge_cost
    check_run_config(cfg)
    drop_great_for_ap(cfg)
    stop = args.stop
    with open_context(cfg, stop, watch_combo=args.watch_combo, record=args.record) as ctx:
        try:
            source = make_source(cfg, ctx.catalog)
        except ValueError as e:
            raise SetupError(str(e)) from None
        runner = Runner(cfg, ctx.nav, ctx.session, ctx.client, ctx.catalog, ctx.store, stop, source)
        try:
            stats = runner.run()
        except KeyboardInterrupt:
            stop.set()
            logger.info("已中断（共演奏 %d 局）", runner.stats.plays)
            return 130
    # 服务器维护时任务算失败（打过几局也一样，没打完）
    return 0 if (stats.plays or not stats.failures) and not stats.maintenance else 1


def _launch_game(cfg: Config) -> None:
    from .device import adb

    if adb.start_app(cfg.device):
        time.sleep(3)  # 等游戏窗口出来，截图才会取到游戏的画面
    else:
        logger.info("游戏已在运行")


def _game_navigator(cfg: Config, args, source, touch):
    """能在闪退、标题画面卡住时重启游戏的导航器。"""
    from .device import adb
    from .nav.navigator import GameNavigator

    def restart_app() -> None:
        adb.restart_app(cfg.device)
        source.ipc.refresh_display_id()

    source.ipc.refresh_display_id()
    return GameNavigator(
        cfg,
        source,
        touch,
        _ocr(cfg),
        args.stop,
        restart_app=restart_app,
        app_running=lambda: adb.app_running(cfg.device),
    )


def cmd_start(cfg: Config, args) -> int:
    """游戏没在运行时启动它，然后等到进入游戏（标题画面、登录奖励、公告都会处理掉）。"""
    from .runner import NavigationError

    _launch_game(cfg)
    with _device(cfg) as (source, touch):
        nav = _game_navigator(cfg, args, source, touch)
        try:
            screen = nav.ensure_in_game(args.timeout)
        except NavigationError as e:
            if args.stop.is_set():
                logger.info("已停止")
                return 130
            logger.error("%s", e)
            return 1
    logger.info("已进入游戏（%s）", screen)
    return 0


def _daily_jobs(text: str) -> list[str]:
    import argparse

    from .nav.daily import DAILY_JOBS

    jobs = [j.strip() for j in text.split(",") if j.strip()]
    bad = [j for j in jobs if j not in DAILY_JOBS]
    if bad or not jobs:
        raise argparse.ArgumentTypeError(f"应为 {','.join(DAILY_JOBS)} 中的若干项（逗号分隔）")
    return jobs


def cmd_daily(cfg: Config, args) -> int:
    """领取日常奖励（游戏没在运行时先启动），最后停在主界面。"""
    from .nav.daily import DAILY_JOBS, OPT_IN_JOBS
    from .runner import NavigationError

    jobs = args.jobs or [j for j in DAILY_JOBS if j not in OPT_IN_JOBS]
    _launch_game(cfg)
    with _device(cfg) as (source, touch):
        nav = _game_navigator(cfg, args, source, touch)
        try:
            # 不用 ensure_in_game：游戏可能停在日常页面或领取后的弹窗上，go_home 都认得（也会处理重新登录）
            nav.go_home(timeout_s=180)
            failed = nav.run_daily(jobs)
        except NavigationError as e:
            if args.stop.is_set():
                logger.info("已停止")
                return 130
            logger.error("%s", e)
            return 1
    if failed:
        logger.error("日常领取有项目出错：%s", "、".join(failed))
        return 1
    logger.info("日常领取完成")
    return 0


WATCH_COMBO_HELP = "演奏时监视连击数，结束后在日志里列出断连处附近的音符（调试漏判用）"


def cmd_select(cfg: Config, args) -> int:
    """在乐曲选择页依次选中指定的曲目（调试按曲目选歌）。先把「游玩状况」筛选改回「不指定」，不然有的歌不在列表里。"""
    from .cli import resolve_song
    from .runner import NavigationError

    with open_context(cfg, args.stop) as ctx:
        songs = [resolve_song(ctx.catalog, text) for text in args.songs]
        nav = ctx.nav
        try:
            nav.clear_status_filter()
            if args.category:
                nav.set_song_category(args.category)
            for song in songs:
                t0 = time.monotonic()
                pick = nav.select_song(song.music_id)
                took = time.monotonic() - t0
                name = f"{song.display_title()}（{song.music_id}）"
                if pick is None:
                    print(f"列表里没有 {name}（{took:.1f}s）")
                else:
                    print(f"选中 {name}{'，未解锁' if pick.locked else ''}（{took:.1f}s）")
        except NavigationError as e:
            if args.stop.is_set():
                return 130
            logger.error("%s", e)
            return 1
    return 0


def register(sub) -> None:
    from .sources import SONG_MODES

    sp = sub.add_parser("screenshot", help="截图（可叠加判定线/轨道线核对几何参数）")
    sp.add_argument("-o", "--output", default="debug/screenshot.png")
    sp.add_argument("--overlay", action="store_true", help="叠加几何参数示意")
    sp.set_defaults(func=cmd_screenshot)

    sp = sub.add_parser("look", help="识别当前画面（调试导航）")
    sp.add_argument("--save", action="store_true", help="保存截图到 debug/nav")
    sp.add_argument("--ocr", dest="verbose_ocr", action="store_true", help="列出全部 OCR 结果")
    sp.set_defaults(func=cmd_look)

    sp = sub.add_parser("start", help="启动游戏并等到进入游戏")
    sp.add_argument("--timeout", type=float, default=180.0, help="多久没进入游戏就报错（秒，重新登录时重新计时）")
    sp.set_defaults(func=cmd_start)

    sp = sub.add_parser(
        "daily", help="领取日常奖励（录音室练习、任务、通行证、限定/新手任务、T.G.W CARD、礼物盒），可选看故事"
    )
    sp.add_argument(
        "--jobs",
        type=_daily_jobs,
        help="只做这几项，逗号分隔：studio 录音室练习、story 看故事（跳过没看过的乐队 / 视角 / 羁绊故事）、missions 任务、"
        "pass 通行证、limited 限定任务、beginner 新手任务、tgw T.G.W CARD（每日积分、每日奖励、商店里的免费商品）、"
        "gifts 礼物盒（默认除 story 外全部，总是按这个顺序）",
    )
    sp.set_defaults(func=cmd_daily)

    sp = sub.add_parser("select", help="在乐曲选择页依次选中指定曲目（调试按曲目选歌）")
    sp.add_argument("songs", nargs="+", metavar="SONG", help="曲目 ID 或曲名")
    sp.add_argument("--category", choices=("原创", "翻唱", "全部"), help="先切换到这个分类")
    sp.set_defaults(func=cmd_select)

    sp = sub.add_parser("run", help="全自动连续演奏（自由演出，--challenge 时为挑战演出）")
    sp.add_argument("-d", "--difficulty", choices=("easy", "normal", "hard", "expert"), help="覆盖 game.difficulty")
    sp.add_argument(
        "--mode",
        choices=SONG_MODES,
        help="覆盖 loop.song_mode（ap：全曲 AP 补完；ap_first：当前难度（或 loop.ap_first_difficulties）"
        "优先打没 AP 的歌，没有了再随机（挑战演出时轮流打）；list：按歌单打；rotate：挑战演出的几首歌轮流打）",
    )
    sp.add_argument("--songs", metavar="LIST", help="覆盖 loop.song_list（歌单：曲目 ID 或曲名，逗号分隔，可加 @难度）")
    sp.add_argument("-n", "--max-plays", type=int, help="覆盖 loop.max_plays（0 为不限）")
    sp.add_argument("--lb-cost", type=int, choices=(0, 1, 2, 3), help="覆盖 game.lb_cost（每局消耗的 LB）")
    sp.add_argument(
        "--lb-refill",
        type=int,
        metavar="N",
        help="LB 不足时用道具里的 LIVE BOOST饮料补充，本次最多补充 N 个 LB（0 为直到饮料用完）；不用星钻、不看广告",
    )
    sp.add_argument("--until-lb-empty", action="store_true", help="打到 LB 用完为止（清体力）")
    sp.add_argument(
        "--wait-lb",
        action="store_true",
        help="挂机：LB 用完后在乐队确认页等它恢复到每局消耗数再继续，一直运行（每局消耗 0 时不等：有 LB 就每局消耗 1 个，用完消耗 0 接着打）",
    )
    sp.add_argument(
        "--claim-studio",
        type=float,
        metavar="H",
        help="覆盖 loop.studio_claim_hours：每隔 H 小时回主界面领一次录音室练习（收获），开始时先领一次；0 为不领",
    )
    sp.add_argument(
        "--claim-daily",
        metavar="HH:MM",
        help="覆盖 loop.daily_claim_time：每天到这个时间（电脑的本地时间）回主界面领一次日常（任务、礼物盒等）；"
        "空字符串为不领",
    )
    sp.add_argument("--ap-difficulties", metavar="D,D", help="覆盖 loop.ap_difficulties（如 expert,hard）")
    sp.add_argument("--ap-attempts", type=int, help="覆盖 loop.ap_max_attempts（AP 补完每首最多打几次）")
    sp.add_argument(
        "--challenge",
        action="store_true",
        help="打挑战演出（部分活动期间开放，消耗挑战pt），打到 CP 不够一局为止；选曲方式只能是 current / rotate / ap_first",
    )
    sp.add_argument(
        "--challenge-cost",
        type=int,
        choices=(200, 400, 800, 1600),
        help="覆盖 game.challenge_cost（挑战演出每局消耗的挑战pt，默认 200）",
    )
    sp.add_argument("--watch-combo", action="store_true", help=WATCH_COMBO_HELP)
    sp.add_argument("--record", action="store_true", help="同步失败时保存跟踪区截图到 debug/sync")
    sp.set_defaults(func=cmd_run)

    def add_start_args(p) -> None:
        p.add_argument("song", help="曲目 ID 或曲名")
        p.add_argument("-d", "--difficulty", choices=("easy", "normal", "hard", "expert"))
        p.add_argument("--tap", action="append", default=[], metavar="X,Y", help="开始前依次点击的坐标，可重复")
        p.add_argument("--tap-gap", type=float, default=0.8, help="多次点击之间的间隔（秒）")

    sp = sub.add_parser("play", help="演奏即将开始的一首歌（需已在开始前的画面）")
    add_start_args(sp)
    sp.add_argument("--offset", type=float, help="覆盖 play.offset_ms")
    sp.add_argument("--record", action="store_true", help="保存同步用的跟踪区截图到 debug/sync")
    sp.add_argument("--force", action="store_true", help="同步不可信时也继续演奏")
    sp.add_argument("--watch-combo", action="store_true", help=WATCH_COMBO_HELP)
    sp.set_defaults(func=cmd_play)

    cal = sub.add_parser("calibrate", help="校准").add_subparsers(dest="calibrate_cmd", required=True)
    sp = cal.add_parser("motion", help="测量音符运动参数（修改游戏流速后需要重新测量）")
    add_start_args(sp)
    sp.set_defaults(func=cmd_calibrate_motion)
