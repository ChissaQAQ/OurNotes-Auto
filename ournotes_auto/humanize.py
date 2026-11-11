"""一次运行共享的自然操作习惯与状态。幅度均有界，不主动制造断连判定。"""

from __future__ import annotations

import bisect
import math
import random
import time

from .charts.model import Chart, NoteKind, PointNote, Slide
from .config import HumanizeConfig
from .records import game_day

# 点击最长按到基准的这个倍数（35ms → 50ms）。只加不减：游戏掉帧时按得太短，按下和松开可能落在同一帧被漏看
# （docs/开发计划.md 的 issue #10 排查）
TAP_HOLD_MAX = 50 / 35
# 疲劳（累计演奏的谱面时长）随休息按这个时间常数消退：短休息消掉一小部分，长休息基本清零
FATIGUE_RECOVERY_S = 600.0
FATIGUE_FULL_S = 3600.0  # 累计这么久疲劳到顶
REST_EVERY_SPREAD = (0.6, 1.4)  # 每轮短休息前的局数：rest_every_plays 乘这个范围里的随机数
LONG_REST_SPREAD = (0.8, 1.2)  # 每轮长休息前的演奏时长：long_rest_minutes 乘这个范围里的随机数
RARE_REST_SCALE = (2.0, 6.0)  # 偶尔特别久的短休息：rest_max_s 的这么多倍
RARE_REST_MAX_S = 3600.0
DAILY_CAP_SCALE = (0.85, 1.0)  # 每日演奏上限（loop.daily_hours）每天打的折扣


class Humanizer:
    """``habit_key`` 给出时（见 :meth:`.records.RecordStore.habit_key`），操作习惯按「key + 游戏日」生成：
    同一个游戏日里每次运行都是同一套，换日后换一套；没给时每个进程随机一套。休息、按住时长等每次的变化另用随机数。"""

    def __init__(self, config: HumanizeConfig, seed: int | None = None, habit_key: str | None = None):
        config.validate()
        self.config = config
        self.rng = random.Random(seed)
        self.clock = time.monotonic
        self.habit_key = habit_key
        self._habit_day: str | None = None
        self.refresh_habits()
        self.plays = self.since_rest = 0
        self.active_s = self.work_s = 0.0  # 上次长休息后累计的谱面时长；疲劳
        self.last_end: float | None = None
        self.familiarity: dict[str, int] = {}
        self.loads: dict[int, float] = {}
        self.spread = 1.0
        # 这一轮短休息前的局数、长休息前的演奏时长：用到时才按当时的配置抽，休息后重新抽
        self._rest_after: int | None = None
        self._long_after_s: float | None = None

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def refresh_habits(self) -> None:
        """生成（换日后重新生成）这一天的操作习惯。"""
        day = game_day() if self.habit_key else ""
        if day == self._habit_day:
            return
        self._habit_day = day
        rng = random.Random(f"{self.habit_key}:{day}") if self.habit_key else self.rng
        self.timing_bias = rng.uniform(-0.15, 0.15)
        self.hand_bias = rng.uniform(0.25, 0.65) * rng.choice((-1, 1))
        self.hold_scale = rng.uniform(1.08, 1.3)
        self.position_bias = (rng.uniform(-0.25, 0.25), rng.uniform(-0.25, 0.25))
        self.cap_scale = rng.uniform(*DAILY_CAP_SCALE)

    def daily_scale(self) -> float:
        """每日演奏上限今天打的折扣；没开自然操作时为 1。"""
        if not self.enabled:
            return 1.0
        self.refresh_habits()
        return self.cap_scale

    def _rest_after_plays(self) -> int:
        if self._rest_after is None:
            n = self.config.rest_every_plays
            self._rest_after = max(1, round(n * self.rng.uniform(*REST_EVERY_SPREAD)))
        return self._rest_after

    def _long_rest_after_s(self) -> float:
        if self._long_after_s is None:
            self._long_after_s = self.config.long_rest_minutes * 60 * self.rng.uniform(*LONG_REST_SPREAD)
        return self._long_after_s

    def begin_chart(self, chart: Chart) -> None:
        if not self.enabled:
            return
        self.refresh_habits()
        if self.last_end is not None:
            idle = max(0.0, self.clock() - self.last_end)
            if idle >= 60:
                self.recover(idle)
        warmup = 0.15 * max(0.0, 1 - self.plays / 3)
        fatigue = 0.3 * min(1.0, self.work_s / FATIGUE_FULL_S)
        familiar = 0.15 * min(1.0, self.familiarity.get(chart.key, 0) / 5)
        self.spread = 0.85 + warmup + fatigue - familiar
        owners = [*chart.notes, *chart.slides]
        times = sorted(
            [n.time_ms for n in chart.notes]
            + [t for s in chart.slides for t in {s.start_ms, *s.checkpoints, s.end_ms}]
        )
        starts = sorted(s.start_ms for s in chart.slides)
        ends = sorted(s.end_ms for s in chart.slides)
        previous: dict[int, tuple[float, float]] = {}
        self.loads = {}
        for owner in sorted(owners, key=lambda o: o.time_ms if isinstance(o, PointNote) else o.start_ms):
            t = owner.time_ms if isinstance(owner, PointNote) else owner.start_ms
            x = owner.span.center if isinstance(owner, PointNote) else owner.nodes[0].span.center
            hand = int(x >= 12)
            old_t, old_x = previous.get(hand, (t - 1000, x))
            density = max(0, bisect.bisect_right(times, t + 250) - bisect.bisect_left(times, t - 250) - 1)
            jump = abs(x - old_x) / 12 * max(0.0, 1 - (t - old_t) / 500)
            held = bisect.bisect_right(starts, t) - bisect.bisect_right(ends, t)
            complex_gesture = isinstance(owner, Slide) or owner.kind is NoteKind.FLICK
            self.loads[id(owner)] = min(1.0, density / 8 + jump / 2 + 0.1 * held + 0.15 * complex_gesture)
            previous[hand] = (t, x)

    def load(self, owner: PointNote | Slide) -> float:
        return self.loads.get(id(owner), 0.0)

    def tap_hold(self, base_ms: float, owner: PointNote) -> float:
        # 密集处松得快一些、更接近基准，也少占触点（触点按抬起时刻加 finger_gap_ms 分配）
        factor = self.rng.gauss(self.hold_scale - 0.15 * self.load(owner), 0.06)
        return base_ms * min(TAP_HOLD_MAX, max(1.0, factor))

    def flick_shape(self) -> tuple[float, float, float]:
        # 时长、距离只在基准以内变化，保留相邻横滑的避让范围。
        return self.rng.uniform(0.88, 1.0), self.rng.uniform(0.96, 1.0), self.rng.uniform(0.85, 1.15)

    def position(self, k: float) -> tuple[float, float]:
        return tuple(k * min(1.0, max(-1.0, self.rng.gauss(bias, 0.45))) for bias in self.position_bias)

    def noise(self, owner: PointNote | Slide, limit: float) -> float:
        x = owner.span.center if isinstance(owner, PointNote) else owner.nodes[0].span.center
        bias = self.hand_bias * (1 if x >= 12 else -1)
        sigma = (0.2 + 0.35 * self.load(owner)) * self.spread
        return limit * min(1.0, max(-1.0, self.rng.gauss(bias, sigma)))

    def completed(self, chart: Chart) -> None:
        if not self.enabled:
            return
        duration = max(chart.duration_ms, chart.last_ms) / 1000
        self.active_s += duration
        self.work_s += duration
        self.plays += 1
        self.since_rest += 1
        self.familiarity[chart.key] = min(5, self.familiarity.pop(chart.key, 0) + 1)
        if len(self.familiarity) > 128:
            del self.familiarity[next(iter(self.familiarity))]
        self.last_end = self.clock()

    def recover(self, seconds: float) -> None:
        """休息（或等 LB 等空闲）了 ``seconds`` 秒：重新数短休息；够长的算长休息，重新累计演奏时长。"""
        c = self.config
        self.work_s *= math.exp(-seconds / FATIGUE_RECOVERY_S)
        self.since_rest = 0
        self._rest_after = None
        if seconds >= c.long_rest_min_s:
            self.active_s = 0.0
            self._long_after_s = None
        self.last_end = self.clock()

    def rest_seconds(self) -> tuple[float, str]:
        """下一局前该休息多久和休息的说法；不用休息时为 ``(0, "")``。等 LB、维护等已经空闲够久的，算作休息过了。"""
        c = self.config
        if not self.enabled:
            return 0.0, ""
        idle = 0.0 if self.last_end is None else max(0.0, self.clock() - self.last_end)
        if c.long_rest_minutes > 0 and self.active_s >= self._long_rest_after_s():
            if idle >= c.long_rest_min_s:
                self.recover(idle)
                return 0.0, ""
            seconds, what = self.rng.uniform(c.long_rest_min_s, c.long_rest_max_s), "长休息"
        elif c.rest_every_plays > 0 and self.since_rest >= self._rest_after_plays():
            if idle >= max(60.0, c.rest_max_s):
                self.recover(idle)
                return 0.0, ""
            if self.rng.random() < c.rare_rest_chance:
                lo, hi = (min(RARE_REST_MAX_S, c.rest_max_s * k) for k in RARE_REST_SCALE)
                seconds, what = self.rng.uniform(lo, hi), "局间休息（久一点）"
            else:
                seconds, what = self.rng.uniform(c.rest_min_s, c.rest_max_s), "局间休息"
        else:
            return 0.0, ""
        if seconds <= 0:  # 休息时长设为 0：不等，照样重新计数
            self.recover(0.0)
            return 0.0, ""
        return seconds, what

    def menu_delay(self, result: bool = False) -> float:
        return self.rng.uniform(1.0, 2.5) if result else self.rng.uniform(0.12, 0.4)
