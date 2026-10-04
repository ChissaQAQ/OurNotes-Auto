"""演奏一首歌：谱面 → 触控计划 → 首音符同步 → 按时执行。"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..charts.model import Chart
from ..config import Config
from ..device.base import FrameSource, TouchBackend
from ..geometry import Geometry
from ..planner import Plan, Planner
from .clock import now
from .executor import ExecStats, Executor
from .guard import TEMPLATE_PATH, LifeDepleted, PlayGuard, PlayInterrupted, load_template
from .monitor import ComboWatcher, find_breaks, report_breaks, save_samples
from .sync import NoteTracker, SyncResult, SyncTimeout

logger = logging.getLogger(__name__)


class SyncFailed(RuntimeError):
    def __init__(self, message: str, sync: SyncResult | None = None):
        super().__init__(message)
        self.sync = sync


@dataclass
class PlayOutcome:
    chart: Chart
    plan: Plan
    sync: SyncResult
    stats: ExecStats
    offset_ms: float  # 本次使用的总 offset（配置 + 学习值）
    stalls: list[tuple[float, float]] = field(default_factory=list)  # 截图卡住的时段（谱面 ms）


STALL_S = 0.1  # 演奏时单次截图超过这个时间算卡住（正常 ≤ 50ms，见 MuMuFrameSource）


class TimedSource:
    """演奏时包在截图源外面，记下卡住的截图。

    模拟器卡顿时截图接口会跟着卡住，这段时间发出的触控多半失效（实测卡 0.6s，连续 3 个 MISS）。
    只有演奏画面检查（每 0.5s）和连击监视会在演奏时截图，所以短的卡顿不一定能发现。
    """

    def __init__(self, source: FrameSource, slow_s: float = STALL_S):
        self.source = source
        self.slow_s = slow_s
        self._slow: list[tuple[float, float]] = []  # (开始, 结束) perf_counter

    @property
    def size(self) -> tuple[int, int]:
        return self.source.size

    def grab(self) -> tuple[np.ndarray, float]:
        start = now()
        frame, t = self.source.grab()
        end = now()
        if end - start >= self.slow_s:
            self._slow.append((start, end))
        return frame, t

    def stalls(self) -> list[tuple[float, float]]:
        """卡住的 (开始, 结束)，合并重叠的（另一个线程在排队等同一次卡顿）。"""
        merged: list[tuple[float, float]] = []
        for a, b in sorted(self._slow):
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        return merged


class PlaySession:
    def __init__(
        self,
        config: Config,
        source: FrameSource,
        touch: TouchBackend,
        learned_offset_ms: float = 0.0,
        combo_reader: Callable[[np.ndarray], int | None] | None = None,
    ):
        """``combo_reader``：把连击数小图读成数字；给出时演奏期间监视连击数，结束后报告断连的音符。"""
        self.cfg = config
        self.source = source
        self.touch = touch
        self.learned_offset_ms = learned_offset_ms
        self.combo_reader = combo_reader
        # 开启 sync.record_frames 时，同步成功后是否也保存跟踪区截图（每份几十 MB；连续演奏时只留失败的）
        self.dump_success = True
        w, h = source.size
        self.geometry = Geometry(w, h, config.geometry)
        self.planner = Planner(self.geometry, config.play.touch)
        self.executor = Executor(touch)
        self.guard_template = load_template() if config.play.guard_lost_s > 0 else None
        if config.play.guard_lost_s > 0 and self.guard_template is None:
            logger.warning("找不到 %s，演奏中不检查是否离开了演奏画面", TEMPLATE_PATH)
        # 上一次同步时画面静止取的跟踪区基线（谱面, 基线）：暂停重试后歌曲立即开始，等不到静止，沿用它
        self._baseline: tuple[str, np.ndarray] | None = None
        self.last_fps: float | None = None  # 最近一次 play 同步时测到的游戏出帧率（没同步出结果时为 None）

    @property
    def offset_ms(self) -> float:
        return self.cfg.play.offset_ms + self.learned_offset_ms

    def prepare(self, chart: Chart, retry: bool = False) -> tuple[Plan, NoteTracker]:
        plan = self.planner.plan(chart)
        if plan.dropped:
            logger.warning("%d 个手势因触点不足被丢弃", len(plan.dropped))
        first_ms, spans = chart.first_hits()
        sp = self.cfg.play.sync
        baseline = None
        if retry and self._baseline is not None and self._baseline[0] == chart.key:
            baseline = self._baseline[1]
        tracker = NoteTracker(
            self.geometry,
            sp,
            first_ms,
            spans,
            record_frames=sp.record_frames,
            baseline=baseline,
            max_start_delay_s=sp.retry_start_delay_s if retry else None,
        )
        logger.debug(
            "谱面 %s：%d 个手势 / %d 个触控事件，首音符 %.0fms（%d 个）",
            chart.key,
            len(plan.gestures),
            len(plan.events),
            first_ms,
            len(spans),
        )
        return plan, tracker

    def play(
        self,
        chart: Chart,
        stop: threading.Event | None = None,
        require_sync_ok: bool = True,
        retry: bool = False,
    ) -> PlayOutcome:
        """需在进入演奏画面、首音符出现之前调用（通常在点击开始后立即调用）。

        ``retry``：刚在暂停菜单点了重试（歌曲立即从头开始，沿用上一次的跟踪区基线，见 player/sync.py）。"""
        plan, tracker = self.prepare(chart, retry)
        self.last_fps = None
        try:
            sync = tracker.wait(self.source, stop)
        except SyncTimeout:
            if self.cfg.play.sync.record_frames:
                self._dump(tracker, chart, None)
            raise
        finally:
            if tracker.clean_baseline is not None:
                self._baseline = (chart.key, tracker.clean_baseline)
        self.last_fps = sync.fps or None
        if not sync.ok and require_sync_ok:
            if self.cfg.play.sync.record_frames:
                self._dump(tracker, chart, sync)
            raise SyncFailed("首音符同步失败：" + "；".join(sync.notes), sync)
        offset = self.offset_ms
        lead_ms = (sync.t0 + (plan.first_ms + offset) / 1000 - now()) * 1000
        if lead_ms < 0:
            logger.warning("同步完成时第一个操作已过去 %.0fms，开头音符可能丢失", -lead_ms)
        timed = TimedSource(self.source)
        watcher = ComboWatcher(timed) if self.combo_reader is not None else None
        if watcher is not None:
            watcher.start()
        guard = None
        if self.guard_template is not None:
            pc = self.cfg.play
            guard = PlayGuard(timed, self.guard_template, pc.guard_lost_s, life_zero_s=pc.guard_life_zero_s)
        try:
            stats = self.executor.run(plan.events, sync.t0, offset, guard.start(stop) if guard else stop)
        finally:
            if guard is not None:
                guard.stop()
            samples = watcher.stop() if watcher is not None else []
        stalls = [((a - sync.t0) * 1000 - offset, (b - sync.t0) * 1000 - offset) for a, b in timed.stalls()]
        if stalls:
            logger.warning(
                "演奏中截图卡住 %d 次（模拟器卡顿，这段时间的触控可能失效）：%s",
                len(stalls),
                "，".join(f"谱面 {a:.0f}~{b:.0f}ms" for a, b in stalls),
            )
        if guard is not None and guard.lost:
            raise PlayInterrupted("演奏中途离开了演奏画面")
        if guard is not None and guard.life_zero:
            raise LifeDepleted("演奏中生命值降到 0（整体对不上了）")
        if self.cfg.play.sync.record_frames and self.dump_success:
            self._dump(tracker, chart, sync)
        if watcher is not None:
            self._report_combo(chart, samples, sync.t0, offset)
        return PlayOutcome(chart, plan, sync, stats, offset, stalls)

    def _report_combo(self, chart: Chart, samples, t0: float, offset_ms: float) -> None:
        breaks = find_breaks(samples, self.combo_reader)
        logger.info("连击数监视：%d 张截图，断连 %d 次", len(samples), len(breaks))
        for line in report_breaks(chart, breaks, t0, offset_ms):
            logger.info("%s", line)
        # 有断连时总是保存，方便离线核对是真断连还是读错（误报）；自动保存的只留最新 20 个
        record = self.cfg.play.sync.record_frames
        if record or breaks:
            folder = Path("debug/combo") if record else Path("debug/combo/breaks")
            path = folder / f"{chart.key}_{time.strftime('%Y%m%d_%H%M%S')}.npz"
            try:
                saved = save_samples(path, samples, t0, offset_ms, keep=None if record else 20)
                logger.info("连击数截图已保存到 %s", saved)
            except (OSError, ValueError) as e:
                logger.warning("保存连击数截图失败：%s", e)

    @staticmethod
    def _dump(tracker: NoteTracker, chart: Chart, sync: SyncResult | None) -> None:
        path = Path("debug/sync") / f"{chart.key}_{time.strftime('%Y%m%d_%H%M%S')}.npz"
        try:
            logger.info("同步调试数据已保存到 %s", tracker.dump(path, sync))
        except (OSError, ValueError) as e:
            logger.warning("保存同步调试数据失败：%s", e)
