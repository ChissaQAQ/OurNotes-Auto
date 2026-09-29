"""谱面的标准化数据模型。

坐标约定：
- 时间 ``time_ms`` 为谱面时间（毫秒，浮点），0 为乐曲开头，不含任何音频/输入偏移。
- 横向位置用「单位」表示，整条判定带宽 24 个单位（6 条主轨 × 4），
  音符覆盖区间为 ``[left, right)``，例如 laneStart=6, laneEnd=11（闭区间）对应 left=6, right=12。
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from enum import Enum

LANE_UNITS = 24


class Ease(str, Enum):
    """长条相邻节点之间的横向插值曲线（取起点节点上的值）。

    已用谱面内 Combo 判定点的实际位置反推验证（误差 < 0.001）。
    注意游戏的命名与常见缓动库相反：EaseIn 是先快后慢。
    """

    LINEAR = "Linear"
    EASE_IN = "EaseIn"
    EASE_OUT = "EaseOut"

    def apply(self, t: float) -> float:
        if self is Ease.EASE_IN:
            return 1.0 - (1.0 - t) ** 2
        if self is Ease.EASE_OUT:
            return t * t
        return t

    @classmethod
    def parse(cls, value: str | None) -> "Ease":
        try:
            return cls(value) if value else cls.LINEAR
        except ValueError:
            return cls.LINEAR


class NoteKind(str, Enum):
    TAP = "tap"  # 点击
    FLICK = "flick"  # 滑动（方向见 direction）
    TRACE = "trace"  # 追踪：判定时刻手指按在该位置即可，无需按下动作


class FlickDirection(str, Enum):
    UP = "up"
    LEFT = "left"
    RIGHT = "right"


@dataclass(frozen=True)
class Span:
    """横向覆盖区间，单位见模块说明。"""

    left: float
    right: float

    @property
    def center(self) -> float:
        return (self.left + self.right) / 2

    @property
    def width(self) -> float:
        return self.right - self.left


@dataclass
class PointNote:
    """单点判定音符：点击、滑动、追踪，以及引导线的起点。"""

    id: int
    time_ms: float
    kind: NoteKind
    span: Span
    direction: FlickDirection = FlickDirection.UP
    critical: bool = False


@dataclass
class PathNode:
    """长条路径上的一个节点（含不可见的形状控制点）。"""

    time_ms: float
    span: Span
    ease_left: Ease = Ease.LINEAR
    ease_right: Ease = Ease.LINEAR


class SlideHead(str, Enum):
    TAP = "tap"  # 起点需要按下判定（SlideBegin）
    FLICK = "flick"  # 起点需要按下并滑动（SlideBeginFlick），随后继续按住沿路径移动
    TRACE = "trace"  # 起点只要按着经过即可
    NONE = "none"  # 起点无判定（引导线 GuideBegin），只需在中途判定点前按住


class SlideTail(str, Enum):
    RELEASE = "release"  # 终点松手（无需精确时机）
    FLICK = "flick"  # 终点需要滑动
    TRACE = "trace"  # 终点为追踪判定：该时刻按在位置上即可
    NONE = "none"  # 终点无判定


@dataclass
class Slide:
    """长条：从首节点按下，沿路径移动到末节点。"""

    id: int
    nodes: list[PathNode]
    head: SlideHead = SlideHead.TAP
    tail: SlideTail = SlideTail.RELEASE
    head_direction: FlickDirection = FlickDirection.UP
    tail_direction: FlickDirection = FlickDirection.UP
    critical: bool = False
    # 路径中途的判定点时刻（Combo / SlideConnection / SlideConnectionTrace）
    checkpoints: list[float] = field(default_factory=list)
    guide: bool = False  # 引导线（不可见路径，只有中途追踪判定点）

    def __post_init__(self) -> None:
        self.nodes.sort(key=lambda n: n.time_ms)
        self._times = [n.time_ms for n in self.nodes]
        self.checkpoints.sort()

    @property
    def first_judged_ms(self) -> float:
        """第一个需要判定的时刻（起点无判定时为第一个中途判定点）。"""
        if self.head is not SlideHead.NONE or not self.checkpoints:
            return self.start_ms
        return self.checkpoints[0]

    @property
    def start_ms(self) -> float:
        return self.nodes[0].time_ms

    @property
    def end_ms(self) -> float:
        return self.nodes[-1].time_ms

    def span_at(self, time_ms: float) -> Span:
        """时刻 ``time_ms`` 时长条在判定线上的横向区间。"""
        nodes = self.nodes
        if time_ms <= nodes[0].time_ms:
            return nodes[0].span
        if time_ms >= nodes[-1].time_ms:
            return nodes[-1].span
        i = bisect.bisect_right(self._times, time_ms) - 1
        a, b = nodes[i], nodes[i + 1]
        dt = b.time_ms - a.time_ms
        t = 0.0 if dt <= 0 else (time_ms - a.time_ms) / dt
        fl = a.ease_left.apply(t)
        fr = a.ease_right.apply(t)
        return Span(
            a.span.left + (b.span.left - a.span.left) * fl,
            a.span.right + (b.span.right - a.span.right) * fr,
        )


@dataclass
class Chart:
    music_id: int
    difficulty: str
    title: str = ""
    notes: list[PointNote] = field(default_factory=list)
    slides: list[Slide] = field(default_factory=list)
    # 谱面声明的判定音符总数（= 满连击数），用于校验解析是否完整
    judged_count: int = 0
    # 解析时实际统计到的判定数；与 judged_count 不一致说明解析有遗漏
    parsed_judged: int = 0
    duration_ms: float = 0.0

    def __post_init__(self) -> None:
        self.notes.sort(key=lambda n: n.time_ms)
        self.slides.sort(key=lambda s: s.start_ms)

    @property
    def key(self) -> str:
        return f"{self.music_id}_{self.difficulty}"

    def first_hits(self, window_ms: float = 1.0) -> tuple[float, list[Span]]:
        """最早需要操作的时刻及该时刻所有音符的横向区间（用于首音符同步）。"""
        events: list[tuple[float, Span]] = [(n.time_ms, n.span) for n in self.notes]
        events += [(s.first_judged_ms, s.span_at(s.first_judged_ms)) for s in self.slides]
        if not events:
            raise ValueError(f"谱面 {self.key} 没有任何音符")
        t0 = min(t for t, _ in events)
        return t0, [span for t, span in events if t - t0 <= window_ms]

    @property
    def last_ms(self) -> float:
        ends = [n.time_ms for n in self.notes] + [s.end_ms for s in self.slides]
        return max(ends, default=0.0)
