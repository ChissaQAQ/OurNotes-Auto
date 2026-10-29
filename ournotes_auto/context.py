"""打开设备并组装一次任务所需的全部对象（命令行和 MaaFramework Agent 共用）。"""

from __future__ import annotations

import logging
import subprocess
import threading
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .config import Config

if TYPE_CHECKING:
    from .charts.bdon import BdonClient
    from .charts.catalog import Catalog
    from .nav.navigator import GameNavigator
    from .player.session import PlaySession
    from .records import RecordStore

logger = logging.getLogger(__name__)
OCR_DIR = Path("resource") / "model" / "ocr"


class SetupError(RuntimeError):
    """配置或运行环境有问题（设备后端、OCR 模型等），需要用户处理。"""


@contextmanager
def open_device(cfg: Config):
    """打开设备，产出 (截图源, 触控)；目前只支持 MuMu IPC 截图，触控可选 minitouch 或 MuMu IPC
    （minitouch 启动失败时改用 MuMu IPC）。"""
    if cfg.device.backend != "mumu":
        raise SetupError(f"暂不支持的设备后端：{cfg.device.backend}")
    if cfg.device.touch not in ("mumu", "minitouch"):
        raise SetupError(f"不支持的触控方式：{cfg.device.touch}")
    from .device import adb
    from .device.mumu import MuMuFrameSource, MuMuTouch, open_mumu

    adb.resolve_package(cfg.device)  # 截图按包名找游戏的画面，日志里的游戏版本也按它读
    ipc = open_mumu(cfg.device)
    source = MuMuFrameSource(ipc)
    touch = None
    if cfg.device.touch == "minitouch":
        from .device.minitouch import MinitouchTouch

        mt = MinitouchTouch(cfg.device, source.size, display_id=lambda: ipc.display_id)
        try:
            mt.start()
            touch = mt
        except (OSError, RuntimeError, subprocess.SubprocessError) as e:
            mt.close()
            logger.warning("minitouch 启动失败（%s），改用 MuMu IPC 触控", e)
        except BaseException:
            mt.close()
            ipc.disconnect()
            raise
    if touch is None:
        touch = MuMuTouch(ipc)
    try:
        check_resolution(*source.size)
        _log_device(cfg, source, "MuMu IPC" if isinstance(touch, MuMuTouch) else "minitouch")
        yield source, touch
    finally:
        try:
            getattr(touch, "close", touch.release_all)()
        finally:
            ipc.disconnect()


def check_resolution(w: int, h: int) -> None:
    """坐标都是在 1280x720 下量的，按比例换算到其他 16:9 分辨率；不是 16:9 时界面排布不同，没法换算。
    游戏还没打开时截到的可能是竖着的桌面，所以不管方向。"""
    long, short = max(w, h), min(w, h)
    if not short or abs(long / short - 16 / 9) > 0.01:
        raise SetupError(f"模拟器分辨率是 {w}x{h}，不是 16:9，请在模拟器设置里改成 1280x720")
    if (long, short) != (1280, 720):
        logger.info("模拟器分辨率是 %dx%d，按比例换算坐标（推荐 1280x720，其他分辨率测试得少，点不准时请反馈）", w, h)


def _log_device(cfg: Config, source, touch_name: str) -> None:
    """设备信息记一行日志：反馈问题时附上日志就够了，不用另外说明设备。"""
    from .device import adb
    from .device.mumu import describe_mumu

    try:
        android, game = adb.versions(cfg.device)
    except (OSError, subprocess.SubprocessError):
        android = game = ""
    w, h = source.size
    parts = [f"实例 {cfg.device.instance}（{adb.adb_serial(cfg.device)}）", *describe_mumu(cfg.device), f"画面 {w}x{h}"]
    parts.append(f"触控 {touch_name}")
    if android:
        parts.append(f"Android {android}")
    if game:
        parts.append(f"游戏 {game}")
    logger.debug("设备：%s", "，".join(parts))


def open_ocr(cfg: Config):
    from .nav.ocr import MaaOcr, OcrUnavailable
    from .ocr_models import EXTRA, FetchError, fetch, missing

    model = cfg.loop.ocr_model
    # 其他语言的模型不随发布包分发，第一次用到时下载
    if model in EXTRA and missing(OCR_DIR, model):
        logger.info("第一次使用该游戏语言，正在下载文字识别模型（约 10 MB）……")
        try:
            fetch(OCR_DIR, model, log=logger.debug)
        except FetchError as e:
            raise SetupError(f"{e}；可以稍后重试，或运行 python tools/fetch_ocr.py --model {model}") from None
    try:
        return MaaOcr("resource", model)
    except OcrUnavailable as e:
        raise SetupError(str(e)) from None


def load_catalog(cfg: Config, refresh: bool = False) -> tuple[BdonClient, Catalog]:
    from .charts.bdon import BdonClient
    from .charts.catalog import Catalog

    client = BdonClient(cfg.charts)
    return client, Catalog.load(client, refresh=refresh)


def load_jackets_async(cfg: Config, catalog: Catalog) -> Future:
    """在后台线程里加载封面（第一次运行要下载，约半分钟），同时去连模拟器、进乐曲选择页；
    导航第一次用到封面时再等它（见 :attr:`GameNavigator.jackets`）。用自己的连接，仍是逐个下载。"""
    from .charts.bdon import BdonClient
    from .nav.jacket import JacketMatcher

    future: Future = Future()

    def work() -> None:
        try:
            future.set_result(JacketMatcher.load(BdonClient(cfg.charts), catalog))
        except BaseException as e:  # 在用到封面的地方抛出
            future.set_exception(e)

    threading.Thread(target=work, name="jackets", daemon=True).start()
    return future


@dataclass
class TaskContext:
    cfg: Config
    nav: GameNavigator
    session: PlaySession
    client: BdonClient
    catalog: Catalog
    store: RecordStore
    stop: threading.Event


@contextmanager
def open_context(
    cfg: Config,
    stop: threading.Event | None = None,
    *,
    watch_combo: bool = False,
    record: bool = False,
    data_dir: str = "data",
):
    """连接模拟器、加载曲库（封面在后台加载），产出 :class:`TaskContext`；``stop`` 置位后导航与演奏会尽快退出。"""
    from .nav.navigator import GameNavigator
    from .player.monitor import combo_reader
    from .player.session import PlaySession
    from .records import RecordStore

    ocr = open_ocr(cfg)
    client, catalog = load_catalog(cfg)
    jackets = load_jackets_async(cfg, catalog)
    stop = stop or threading.Event()
    with open_device(cfg) as (source, touch):
        from .device import adb

        def restart_app() -> None:
            # 重启后不能立刻 refresh_display_id()：游戏进程还没建好新的 display 时它会退到
            # "default"，可能返回 0（模拟器桌面）并被一直用下去，之后对着桌面 OCR 永远认不出画面（#58）。
            before = source.ipc.display_id
            adb.restart_app(cfg.device)
            source.ipc.wait_for_game_display(stop=stop)
            logger.debug("重启游戏后重新解析 display_id：%s → %s", before, source.ipc.display_id)

        store = RecordStore(data_dir)
        nav = GameNavigator(
            cfg,
            source,
            touch,
            ocr,
            stop,
            jackets=jackets,
            restart_app=restart_app,
            app_running=lambda: adb.app_running(cfg.device),
            store=store,
            app_in_front=lambda: adb.app_in_front(cfg.device),
        )
        # 演奏界面连击数小图 → 数字（结束后在日志里列出断连处附近的音符，调试漏判用）
        reader = combo_reader(ocr.read_text) if watch_combo else None
        session = PlaySession(cfg, source, touch, combo_reader=reader)
        if record:
            cfg.play.sync.record_frames = max(cfg.play.sync.record_frames, 600)
            session.dump_success = False
        yield TaskContext(cfg, nav, session, client, catalog, store, stop)
