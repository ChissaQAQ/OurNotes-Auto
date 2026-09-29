"""几何与同步参数的校准工具（与具体设备无关的部分）。"""

from __future__ import annotations

import dataclasses
import logging
import math
import statistics
import threading

import cv2
import numpy as np

from .charts.model import LANE_UNITS, Chart
from .config import SyncParams
from .device.base import FrameSource
from .geometry import Geometry
from .player.sync import NoteTracker, SyncResult

logger = logging.getLogger(__name__)


def overlay_geometry(frame: np.ndarray, geo: Geometry, sync: SyncParams | None = None) -> np.ndarray:
    """在截图上画出判定线、单位分隔线和跟踪区，用于人工核对几何参数。

    - 绿色：判定线；黄色：触控高度（与判定线不同时）
    - 青色：每 4 个单位一条的轨道线，按透视延伸到画面顶端，应与游戏轨道边沿重合
    - 洋红：首音符跟踪区上下沿
    """
    img = frame.copy()
    h, w = img.shape[:2]
    jy = int(round(geo.judge_y))
    for u in range(0, LANE_UNITS + 1, 4):
        p0 = (int(round(geo.x_at(u, 0))), 0)
        p1 = (int(round(geo.x_at(u))), jy)
        thick = 2 if u in (0, LANE_UNITS) else 1
        cv2.line(img, p0, p1, (255, 255, 0), thick, cv2.LINE_AA)
        cv2.putText(img, str(u), (p1[0] - 6, min(jy + 18, h - 2)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 0), 1)
    cv2.line(img, (0, jy), (w - 1, jy), (0, 255, 0), 1, cv2.LINE_AA)
    ty = int(round(geo.touch_y))
    if ty != jy:
        cv2.line(img, (0, ty), (w - 1, ty), (0, 255, 255), 1, cv2.LINE_AA)
    if sync is not None:
        for frac in (sync.track_top, sync.track_bottom):
            y = int(frac * h)
            cv2.line(img, (0, y), (w - 1, y), (255, 0, 255), 1)
    return img


def motion_params(base: SyncParams) -> SyncParams:
    """完整跟踪首音符并拟合运动模型用的参数：不提前结束、跟踪到接近判定线、不用 τ 先验。"""
    return dataclasses.replace(
        base, fit_horizon=True, solve_lead_s=-1.0, track_bottom=0.75, tau_rel_sigma=math.inf, tau_ratio_max=math.inf
    )


def measure_motion(
    source: FrameSource,
    geo: Geometry,
    chart: Chart,
    base: SyncParams,
    stop: threading.Event | None = None,
) -> SyncResult:
    """在演奏开始前调用；跟踪首音符全程，返回结果中的 ``tau_s`` 与 ``horizon_y`` 即运动参数。"""
    first_ms, spans = chart.first_hits()
    tracker = NoteTracker(geo, motion_params(base), first_ms, spans)
    result = tracker.wait(source, stop)
    logger.info(
        "τ=%.4fs，运动消失线 %.4f 屏高（轨道消失线 %.4f）；%d 个样本，残差 %.2fms",
        result.tau_s,
        result.horizon_y / geo.height,
        geo.horizon_y / geo.height,
        len(result.samples),
        result.rms_ms,
    )
    return result


def summarize_motion(results: list[SyncResult], height: int) -> tuple[float, float, float]:
    """多次测量的 τ 中位数、τ 离散度与运动消失线中位数（屏高比例）。拟合不可信的结果会被忽略。"""
    good = [r for r in results if r.ok]
    if not good:
        raise ValueError("没有可用的测量结果")
    taus = [r.tau_s for r in good]
    spread = statistics.pstdev(taus) if len(taus) > 1 else float("nan")
    return statistics.median(taus), spread, statistics.median(r.horizon_y / height for r in good)
