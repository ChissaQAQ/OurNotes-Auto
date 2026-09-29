"""判定带的屏幕几何：谱面横向单位 → 屏幕像素。

默认值由游戏 LiveScene 的 screenPlane（16:9 基准）推导：
- 屏幕平面上下边 y=±5.4，上边半宽 0.48、下边半宽 9.6，轨道在透视下线性收拢；
- 判定线位于平面 y=-3.16，即自顶向下屏高的 (5.4+3.16)/10.8 = 79.26%；
- 该高度处 24 个单位占屏宽 9.85% ~ 90.15%；
- 轨道边沿延长后交于屏幕顶端之上 5.26% 屏高处（透视消失线）。
这些值需在实机上用 calibrate 命令核对，可在配置里覆盖。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .charts.model import LANE_UNITS, Span


@dataclass(frozen=True)
class GeometryParams:
    judge_y: float = 0.7926  # 判定线高度 / 屏高（自顶向下）
    lane_left: float = 0.0985  # 判定线处单位 0 左沿 / 屏宽
    lane_right: float = 0.9015  # 判定线处单位 24 右沿 / 屏宽
    horizon_y: float = -0.0526  # 轨道透视消失线高度 / 屏高
    # 音符运动的消失线（y - y_h 随时间指数增长）；None 表示与轨道消失线相同（实机验证吻合）
    motion_horizon_y: float | None = None
    touch_y_offset: float = 0.0  # 触控点相对判定线的纵向偏移 / 屏高（正值向下）


class Geometry:
    def __init__(self, width: int, height: int, params: GeometryParams | None = None):
        self.width = width
        self.height = height
        self.params = params or GeometryParams()
        p = self.params
        self.judge_y = p.judge_y * height
        self.touch_y = (p.judge_y + p.touch_y_offset) * height
        self.horizon_y = p.horizon_y * height
        mh = p.horizon_y if p.motion_horizon_y is None else p.motion_horizon_y
        self.motion_horizon_y = mh * height
        self._left = p.lane_left * width
        self._unit = (p.lane_right - p.lane_left) * width / LANE_UNITS
        self.center_x = (p.lane_left + p.lane_right) / 2 * width

    def _scale(self, y: float) -> float:
        """高度 y 处的横向缩放（判定线处为 1，越往上越窄）。"""
        return (y - self.horizon_y) / (self.judge_y - self.horizon_y)

    def x_at(self, unit: float, y: float | None = None) -> float:
        """横向单位 ``unit``（0~24，可为小数）在高度 y 处的屏幕 x。"""
        x = self._left + unit * self._unit
        if y is None:
            return x
        return self.center_x + (x - self.center_x) * self._scale(y)

    def row_range(self, span: Span, y: float, shrink: float = 0.0) -> tuple[int, int]:
        """区间在高度 y 处覆盖的像素列范围 [x0, x1)；shrink 为两侧各收缩的比例。"""
        pad = span.width * shrink
        x0 = self.x_at(span.left + pad, y)
        x1 = self.x_at(span.right - pad, y)
        return int(round(x0)), max(int(round(x1)), int(round(x0)) + 1)

    def note_y(self, dt_s: float, tau_s: float) -> float:
        """距到达判定线还有 ``dt_s`` 秒的音符所在高度（运动模型见 player/sync.py）。"""
        return self.motion_horizon_y + (self.judge_y - self.motion_horizon_y) * math.exp(-dt_s / tau_s)

    @property
    def unit_px(self) -> float:
        """判定线处一个单位的像素宽度。"""
        return self._unit
