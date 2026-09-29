"""谱面 → 触控事件序列。

每个需要操作的音符生成一个「手势」（按下 → 若干移动 → 抬起），
再给手势分配触点编号（同一触点前一次抬起后至少间隔 ``finger_gap_ms`` 才能再次按下）。
所有时间都是谱面时间（毫秒），执行时再加上同步得到的起点与 ``offset_ms``。
"""

from __future__ import annotations

import bisect
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum

from .charts.model import Chart, FlickDirection, NoteKind, PointNote, Slide, SlideHead, SlideTail, Span
from .config import TouchParams
from .geometry import Geometry

logger = logging.getLogger(__name__)

# 游戏的判定参数（主数据 MasterLiveJudgementTiming / MasterLiveJudgementAreaOffset，判定辅助 0）。
# 点击类音符（点击、长条起点）认领判定区里 ±130ms 内新按下的触点，critical 的提前方向只有 58ms；
# 提前 50ms（critical 67ms）以内是 PERFECT。判定区纵向是整列，横向是音符区间两侧各加若干单位
CLAIM_MS = 130.0
CLAIM_CRITICAL_MS = 58.0
PERFECT_MS = 50.0
PERFECT_CRITICAL_MS = 67.0
AREA_TAP = 1.0  # Default
AREA_SLIDE_BEGIN = 2.0  # SlideBegin
AREA_CRITICAL = 2.8  # EasyDefault / EasySlideBegin
# 上划提前 83ms 以内仍是 PERFECT：提前按下时，按下点离音符时刻最多这么远（留出一帧余量）
MAX_PRESS_LEAD_MS = 75.0
PRESS_LEAD_STEP_MS = 5.0
# 提前松手时，移到松手点的这次移动比抬起早这么久单独发：和抬起同一批发出时实测不起作用
# （推测游戏看到的松手位置还是上一批的）
LIFT_MOVE_MS = 4.0

# 拟人化（TouchParams 的 jitter_ms / great_ratio / position_jitter）
# 故意打成 GREAT 的点击偏离判定时刻的范围：取 PERFECT ±50ms 与 GREAT ±83ms 的正中间，
# 单局的同步误差（实测偶尔有 15ms 上下）在 ±14ms 以内都还是 GREAT，再大就有一部分变成 PERFECT / GOOD
GREAT_SHIFT_MS = (64.0, 69.0)
GREAT_SKIP_HEAD_MS = 1000.0  # 第一个音符之后这么久以内不挑：同步完成时离开头的操作本来就没多少余量
DRIFT_KNOT_MS = 1500.0  # 时机漂移每隔这么久取一个随机值
MAX_NOISE_MS = 3.0  # 每个手势独立的时机抖动上限
POSITION_EDGE = 1.0  # 触控点离音符两侧至少这么多单位
POSITION_GAP = 0.5  # 挪动后的按下点离附近点击类音符判定区的边界至少这么多单位（留出几何标定误差）
POSITION_Y = 0.02  # 触控点上下挪动的最大幅度 / 屏高（判定区纵向是整列）
HELD_STEP_MS = 10.0  # 检查同时按住的两个长条的相对位置时的采样间隔
NO_OFFSET = (0.0, 0.0)


class Action(IntEnum):
    DOWN = 0
    MOVE = 1
    UP = 2


@dataclass(frozen=True)
class TouchEvent:
    t_ms: float
    action: Action
    finger: int
    x: int
    y: int


@dataclass
class Gesture:
    """一次完整的按下—移动—抬起。points[0] 为按下点，points[-1] 为抬起点。"""

    points: list[tuple[float, float, float]]  # (t_ms, x, y)
    source: str = ""
    finger: int = -1

    @property
    def start(self) -> float:
        return self.points[0][0]

    @property
    def end(self) -> float:
        return self.points[-1][0]


@dataclass
class Plan:
    events: list[TouchEvent]
    gestures: list[Gesture]
    dropped: list[Gesture] = field(default_factory=list)

    @property
    def first_ms(self) -> float:
        return self.events[0].t_ms if self.events else 0.0

    @property
    def last_ms(self) -> float:
        return self.events[-1].t_ms if self.events else 0.0


class Planner:
    def __init__(self, geometry: Geometry, params: TouchParams, seed: int | None = None):
        self.geo = geometry
        self.p = params
        self._rng = random.Random(seed)

    # —— 手势生成 ——

    def _flick_vector(self, direction: FlickDirection) -> tuple[float, float]:
        d = self.p.flick_distance * self.geo.height
        if direction is FlickDirection.LEFT:
            return -d, 0.0
        if direction is FlickDirection.RIGHT:
            return d, 0.0
        return 0.0, -d

    def _flick_points(
        self, t: float, x: float, y: float, direction: FlickDirection
    ) -> list[tuple[float, float, float]]:
        dx, dy = self._flick_vector(direction)
        steps = max(1, self.p.flick_steps)
        dur = self.p.flick_duration_ms
        return [(t + dur * k / steps, x + dx * k / steps, y + dy * k / steps) for k in range(1, steps + 1)]

    def _flick_shifts(self, chart: Chart) -> dict[int, float]:
        """横滑音符起点的横向偏移（像素，带符号），按音符 id。

        BIG MOUTH EXPERT 里相邻的两个音符同时同向横滑（[0,8]+[8,16] 左滑、[8,16]+[16,24] 右滑）
        一局 6 对里 3 对漏判其中一个；间隔 4 个单位的同向横滑、相邻的同时上滑都从未失败。
        推测游戏按触点当前位置找音符、判定区比音符略宽，划向邻居的那根手指被邻居的判定抢走了。
        起点往反方向挪，让划完的位置不比按下时更靠近邻居；两侧都有邻居时不挪（按下点也不能靠近邻居）。
        """
        k = self.p.flick_neighbor_shift
        if k <= 0:
            return {}
        tol = max(1.0, self.p.flick_duration_ms)
        notes = sorted(chart.notes, key=lambda n: n.time_ms)
        times = [n.time_ms for n in notes]
        out: dict[int, float] = {}
        for n in notes:
            if n.kind is not NoteKind.FLICK or n.direction is FlickDirection.UP:
                continue
            t = n.time_ms
            near = notes[bisect.bisect_left(times, t - tol) : bisect.bisect_right(times, t + tol)]
            spans = [o.span for o in near if o is not n]
            # 这段时间里按着的长条也占着一根手指
            spans += [s.span_at(t) for s in chart.slides if s.start_ms - tol <= t <= s.end_ms + tol]
            right = any(s.left >= n.span.right - 1e-6 for s in spans)
            left = any(s.right <= n.span.left + 1e-6 for s in spans)
            toward, away = (right, left) if n.direction is FlickDirection.RIGHT else (left, right)
            if not toward or away:
                continue
            dx, _ = self._flick_vector(n.direction)
            margin = min(1.0, n.span.width / 4)
            lo = self.geo.x_at(n.span.left + margin)
            hi = self.geo.x_at(n.span.right - margin)
            x = self.geo.x_at(n.span.center)
            out[n.id] = min(max(x - dx * k, lo), hi) - x
        return out

    @staticmethod
    def _claims(chart: Chart) -> list[tuple[float, float, float, bool, object]]:
        """点击类音符（普通点击、起点是点击的长条）的认领范围 (时刻, 判定区左, 右, critical, 音符或长条)，按时刻排序。"""
        claims: list[tuple[float, float, float, bool, object]] = []
        for n in chart.notes:
            if n.kind is NoteKind.TAP:
                ext = AREA_CRITICAL if n.critical else AREA_TAP
                claims.append((n.time_ms, n.span.left - ext, n.span.right + ext, n.critical, n))
        for s in chart.slides:
            if s.head is SlideHead.TAP:
                span = s.nodes[0].span
                ext = AREA_CRITICAL if s.critical else AREA_SLIDE_BEGIN
                claims.append((s.start_ms, span.left - ext, span.right + ext, s.critical, s))
        claims.sort(key=lambda c: c[0])
        return claims

    def _touch_offsets(self, chart: Chart) -> tuple[dict[int, tuple[float, float]], dict[int, tuple[float, float]]]:
        """拟人化：触控点的随机偏移（横向为偏离中心的比例 -1~1，纵向为像素），分别按音符 id、长条 id。

        点击类音符认领判定区里新的按下时不看离谁更近：春日影 EXPERT 里点击挪进旁边同时开始的长条起点的判定区，
        按下被长条起点认领，点击判 MISS。所以按下点对附近每个点击类音符的判定区，都和中心保持同样的里外关系，
        会被哪些音符认领与不挪时相同。横滑音符不挪：它和同一时刻的邻居靠 ``_flick_shifts`` 保持距离。

        按住长条的手指对同时按住的别的长条的判定区也是这样：步拾道 EXPERT 里相邻的两个长条一起走，
        左边的在尾部变宽、横滑时手指越过了右边长条的手指，右边长条的下一个中继判 MISS。
        """
        k = self.p.position_jitter
        if k <= 0:
            return {}, {}
        dy = POSITION_Y * self.geo.height
        claims = self._claims(chart)
        times = [c[0] for c in claims]
        # 按下时刻离音符时刻最多差时机偏移（上划、追踪等另有提前按下），再加上认领范围与余量
        reach = CLAIM_MS + self.p.claim_guard_ms + self.p.jitter_ms

        def keep(span: Span, left: float, right: float, lo: float, hi: float) -> tuple[float, float]:
            # 收窄可挪范围（占可移动范围的比例，lo ≤ 0 ≤ hi）：挪动后的位置与判定区 [left, right] 的里外关系和中心相同
            c = span.center
            room = span.width / 2 - POSITION_EDGE
            if room <= 0:
                return lo, hi
            if c < left:
                hi = min(hi, max(0.0, left - POSITION_GAP - c) / room)
            elif c > right:
                lo = max(lo, min(0.0, right + POSITION_GAP - c) / room)
            else:
                lo = max(lo, min(0.0, left + POSITION_GAP - c) / room)
                hi = min(hi, max(0.0, right - POSITION_GAP - c) / room)
            return lo, hi

        def pressed(t: float, span: Span, owner: object, lead: float) -> tuple[float, float]:
            lo, hi = -1.0, 1.0
            for _, left, right, _, o in claims[bisect.bisect_left(times, t - lead - reach) : bisect.bisect_right(times, t + reach)]:
                if o is not owner:
                    lo, hi = keep(span, left, right, lo, hi)
            return lo, hi

        def held(s: Slide, lo: float, hi: float) -> tuple[float, float]:
            for o in chart.slides:
                a, b = max(s.start_ms, o.start_ms), min(s.end_ms, o.end_ms)
                if o is s or a > b:
                    continue
                ts = {a, b, *(n.time_ms for n in (*s.nodes, *o.nodes) if a <= n.time_ms <= b)}
                ts.update(a + i * HELD_STEP_MS for i in range(1, int((b - a) // HELD_STEP_MS) + 1))
                ext = AREA_CRITICAL if o.critical else AREA_SLIDE_BEGIN
                for t in ts:
                    span = o.span_at(t)
                    lo, hi = keep(s.span_at(t), span.left - ext, span.right + ext, lo, hi)
            return lo, hi

        def rand(bounds: tuple[float, float]) -> tuple[float, float]:
            u, v = self._rng.uniform(-k, k), self._rng.uniform(-k, k)
            lo, hi = bounds
            return u * (hi if u > 0 else -lo), v * dy

        notes = {
            n.id: rand(pressed(n.time_ms, n.span, n, 0.0 if n.kind is NoteKind.TAP else MAX_PRESS_LEAD_MS))
            for n in chart.notes
            if n.kind is not NoteKind.FLICK or n.direction is FlickDirection.UP
        }
        slides = {
            s.id: rand(
                held(s, *pressed(s.start_ms, s.nodes[0].span, s, 0.0 if s.head is SlideHead.TAP else MAX_PRESS_LEAD_MS))
            )
            for s in chart.slides
        }
        return notes, slides

    def _press_leads(
        self,
        chart: Chart,
        note_off: dict[int, tuple[float, float]] | None = None,
        slide_off: dict[int, tuple[float, float]] | None = None,
        slack: float = 0.0,
    ) -> tuple[dict[int, float], dict[int, float]]:
        """按下本身不认领音符的手势（上划、追踪、起点不是点击的长条）额外提前按下的时间（毫秒），
        分别按音符 id、长条 id。

        焚音打 EXPERT 里上划后 105ms 在同一位置接点击：上划按下的触点被那个点击认领（提前 105ms，判 BAD），
        点击自己的触点又认领了下一个点击，连着 4 组一局 8 个 BAD，上划本身靠划动照样 PERFECT。
        这时提前按下并按住、到原时刻再划，让按下点与前后的点击类音符都隔开认领范围加 ``claim_guard_ms``。
        被认领也仍是 PERFECT 的不处理（如引导线提前 25ms 按下、同一时刻有点击）。
        ``note_off`` / ``slide_off`` 是 ``_touch_offsets`` 的触控点偏移，``slack`` 是时机随机偏移让手势之间多错开的量。
        """
        g = self.p.claim_guard_ms
        if g <= 0:
            return {}, {}
        g += slack
        note_off, slide_off = note_off or {}, slide_off or {}
        claims = self._claims(chart)
        times = [c[0] for c in claims]

        def claimed(d: float, x: float, harmful_only: bool) -> bool:
            """在 d 时刻、横向 x 处按下的触点会不会被点击类音符认领（harmful_only：只算认领成 PERFECT 以外的）。"""
            lo = bisect.bisect_right(times, d - g)
            hi = bisect.bisect_right(times, d + CLAIM_MS + g)
            for t, left, right, critical, _ in claims[lo:hi]:
                ahead = t - d
                if not left <= x <= right or ahead > (CLAIM_CRITICAL_MS if critical else CLAIM_MS) + g:
                    continue
                # 感知到的按下只会因为按帧取样更晚，认领的提前量最多多出同步误差
                if harmful_only and ahead <= (PERFECT_CRITICAL_MS if critical else PERFECT_MS) - 10:
                    continue
                return True
            return False

        p = self.p
        note_leads: dict[int, float] = {}
        slide_leads: dict[int, float] = {}
        downs = []  # (音符时刻, 原按下时刻, 横向位置, 结果表, id, 说明)
        for n in chart.notes:
            x = _unit_x(n.span, note_off.get(n.id, NO_OFFSET)[0])
            if n.kind is NoteKind.FLICK:
                downs.append((n.time_ms, n.time_ms - p.flick_lead_ms, x, note_leads, n.id, "flick"))
            elif n.kind is NoteKind.TRACE:
                downs.append((n.time_ms, n.time_ms - p.trace_lead_ms, x, note_leads, n.id, "trace"))
        for s in chart.slides:
            if s.head is SlideHead.FLICK:
                d0 = s.start_ms - p.flick_lead_ms
            elif s.head in (SlideHead.TRACE, SlideHead.NONE):
                d0 = s.start_ms - p.trace_lead_ms
            else:
                continue
            x = _unit_x(s.nodes[0].span, slide_off.get(s.id, NO_OFFSET)[0])
            downs.append((s.start_ms, d0, x, slide_leads, s.id, "slide"))
        for t, d0, x, out, key, what in downs:
            if not claimed(d0, x, True):
                continue
            lead = PRESS_LEAD_STEP_MS
            while t - (d0 - lead) <= MAX_PRESS_LEAD_MS:
                if not claimed(d0 - lead, x, False):
                    out[key] = lead
                    logger.debug("%s#%d@%.0f 提前 %.0fms 按下，避免被之后的点击认领", what, key, t, lead)
                    break
                lead += PRESS_LEAD_STEP_MS
            else:
                logger.warning("%s#%d@%.0f 的按下会被附近的点击认领，提前按下也避不开", what, key, t)
        if note_leads or slide_leads:
            logger.info("%d 个手势提前按下，避免被之后的点击认领", len(note_leads) + len(slide_leads))
        return note_leads, slide_leads

    def _great_shifts(self, chart: Chart) -> dict[int, float]:
        """拟人化：随机挑一些普通点击故意打成 GREAT，返回音符 id → 按下的偏移（毫秒，负为提前）。

        提前或延后按下时，这个触点可能被附近别的音符认领，别的触点也可能被它认领（见 ``_press_leads``），
        所以只挑认领范围内、判定区挨着的地方没有别的音符（含长条的节点和中途判定点）的点击。
        critical 音符没有 GREAT 这一档，不挑。
        """
        ratio = self.p.great_ratio
        if ratio <= 0 or not chart.notes:
            return {}
        total = chart.judged_count or len(chart.notes) + len(chart.slides)
        want = sum(self._rng.random() < ratio for _ in range(total))
        if not want:
            return {}
        others: list[tuple[float, float, float, object]] = []  # (时刻, 判定区左, 右, 音符或长条)
        for n in chart.notes:
            ext = AREA_CRITICAL if n.critical else AREA_TAP
            others.append((n.time_ms, n.span.left - ext, n.span.right + ext, n))
        for s in chart.slides:
            ext = AREA_CRITICAL if s.critical else AREA_SLIDE_BEGIN
            for t in {*(node.time_ms for node in s.nodes), *s.checkpoints}:
                span = s.span_at(t)
                others.append((t, span.left - ext, span.right + ext, s))
        others.sort(key=lambda o: o[0])
        times = [o[0] for o in others]
        # 认领范围加上余量，再加上别的手势自己的时机偏移
        reach = CLAIM_MS + self.p.claim_guard_ms + self.p.jitter_ms

        def alone(n: PointNote, shift: float) -> bool:
            lo = bisect.bisect_left(times, n.time_ms - reach + min(shift, 0.0))
            hi = bisect.bisect_right(times, n.time_ms + reach + max(shift, 0.0))
            return all(o is n or right < n.span.left or left > n.span.right for _, left, right, o in others[lo:hi])

        first_ms, _ = chart.first_hits()
        pool = [
            n
            for n in chart.notes
            if n.kind is NoteKind.TAP and not n.critical and n.time_ms >= first_ms + GREAT_SKIP_HEAD_MS
        ]
        self._rng.shuffle(pool)
        out: dict[int, float] = {}
        for n in pool:
            if len(out) >= want:
                break
            shift = self._rng.uniform(*GREAT_SHIFT_MS)
            for sign in self._rng.sample((-1.0, 1.0), 2):
                if alone(n, sign * shift):
                    out[n.id] = sign * shift
                    break
        logger.info("拟人化：%d 个点击故意打成 GREAT（目标 %d 个，判定总数 %d）", len(out), want, total)
        return out

    def _drift(self, start: float, end: float) -> Callable[[float], float]:
        """拟人化的时机漂移：每隔 ``DRIFT_KNOT_MS`` 取一个随机值（正态分布，标准差为幅度的一半，截断在 ± 幅度），
        之间线性过渡。相距 200ms 的两个时刻最多差 0.27 倍幅度，手势之间的先后安排基本不受影响。"""
        j = self.p.jitter_ms
        n = int((end - start) // DRIFT_KNOT_MS) + 2
        knots = [min(max(self._rng.gauss(0.0, j / 2), -j), j) for _ in range(n)]

        def at(t: float) -> float:
            u = min(max((t - start) / DRIFT_KNOT_MS, 0.0), n - 1.0)
            i = min(int(u), n - 2)
            return knots[i] + (knots[i + 1] - knots[i]) * (u - i)

        return at

    def _point_gesture(
        self,
        note: PointNote,
        shift: float = 0.0,
        lead: float = 0.0,
        great: float = 0.0,
        off: tuple[float, float] = NO_OFFSET,
    ) -> Gesture:
        x = self.geo.x_at(_unit_x(note.span, off[0])) + shift
        y = self.geo.touch_y + off[1]
        t = note.time_ms + great
        p = self.p
        if note.kind is NoteKind.FLICK:
            t0 = t - p.flick_lead_ms
            pts = [(t0, x, y)] + self._flick_points(t0, x, y, note.direction)
            if lead > 0:  # 提前按下，按住不动，到原时刻再划
                pts.insert(0, (t0 - lead, x, y))
        elif note.kind is NoteKind.TRACE:
            pts = [(t - p.trace_lead_ms - lead, x, y), (t + p.trace_hold_ms, x, y)]
        else:
            pts = [(t, x, y), (t + p.tap_hold_ms, x, y)]
        return Gesture(pts, source=f"{note.kind.value}#{note.id}@{note.time_ms:.0f}")

    def _slide_gesture(
        self, slide: Slide, lift_early_ms: float = 0.0, lead: float = 0.0, off: tuple[float, float] = NO_OFFSET
    ) -> Gesture:
        p = self.p
        u, dy = off
        y = self.geo.touch_y + dy
        start, end = slide.start_ms, slide.end_ms

        def at(t: float) -> tuple[float, float, float]:
            return t, self.geo.x_at(_unit_x(slide.span_at(t), u)), y

        times = {start, end}
        times.update(n.time_ms for n in slide.nodes)
        step = max(p.slide_sample_ms, 1.0)
        k = 1
        while start + k * step < end:
            times.add(start + k * step)
            k += 1
        pts = [at(t) for t in sorted(times)]
        if slide.head is SlideHead.FLICK:
            # 起点滑动：按下后先朝指定方向划出，再在 head_flick_return_ms 内慢慢回到路径上。
            # 以前划完 2ms 就瞬间拉回（反向跳约 56px），偶尔起点判 MISS（everscape、KOHAKU），
            # 推测游戏按帧取触点位置时漏看了划出、却看到了这次反向的跳动
            t0 = start - p.flick_lead_ms
            flick = self._flick_points(t0, pts[0][1], pts[0][2], slide.head_direction)
            t1, fx, fy = flick[-1]
            _, px, py = at(t1)
            ox, oy = fx - px, fy - py
            # 下一个判定（中继或终点）前必须回到路径上
            nxt = min([c for c in slide.checkpoints if c > t1] + [end])
            ret = min(p.head_flick_return_ms, nxt - t1)
            rest = []
            for t, x, y in pts[1:]:
                if t <= t1:
                    continue
                w = 1.0 - (t - t1) / ret if ret > 0 else 0.0
                rest.append((t, x + ox * w, y + oy * w) if w > 0 else (t, x, y))
            pts = [(t0, pts[0][1], pts[0][2])] + flick + rest
        elif slide.head in (SlideHead.TRACE, SlideHead.NONE) and p.trace_lead_ms > 0:
            pts.insert(0, (start - p.trace_lead_ms, pts[0][1], pts[0][2]))

        _, ex, ey = pts[-1]
        if slide.tail is SlideTail.FLICK:
            pts += self._flick_points(end, ex, ey, slide.tail_direction)
        elif slide.tail in (SlideTail.TRACE, SlideTail.NONE):
            pts.append((end + p.trace_hold_ms, ex, ey))
        elif slide.tail is SlideTail.RELEASE:
            cut = end - lift_early_ms
            if lift_early_ms > 0 and cut - LIFT_MOVE_MS > pts[0][0]:
                # 尾部很快横移时（起死开战 EXPERT 结尾 29ms 横移 6 个单位），提前松手的位置还在半路上、
                # 离终点区间两个多单位，位置随机偏到落后的一侧就认不出这次松手：终点一直不判，
                # 到下一次按进终点的判定区才判 MISS。所以松手点夹进终点区间（两侧同样留出 POSITION_EDGE）
                tail = slide.nodes[-1].span
                room = max(0.0, tail.width / 2 - POSITION_EDGE)
                ux = min(max(_unit_x(slide.span_at(cut), u), tail.center - room), tail.center + room)
                x = self.geo.x_at(ux)
                pts = [q for q in pts if q[0] < cut - LIFT_MOVE_MS] + [(cut - LIFT_MOVE_MS, x, y), (cut, x, y)]
            elif p.slide_release_ms > 0:
                pts.append((end + p.slide_release_ms, ex, ey))
        if lead > 0:
            pts.insert(0, (pts[0][0] - lead, pts[0][1], pts[0][2]))
        return Gesture(pts, source=f"slide#{slide.id}@{start:.0f}-{end:.0f}")

    def gestures(self, chart: Chart) -> list[Gesture]:
        p = self.p
        note_off, slide_off = self._touch_offsets(chart)
        # 时机随机偏移：每组同时按下的手势共用一个 ±noise 的独立抖动，漂移在相距 200ms 以内最多差 0.27 倍幅度
        noise = min(MAX_NOISE_MS, p.jitter_ms / 4)
        slack = 2 * noise + 0.3 * p.jitter_ms
        shifts = self._flick_shifts(chart)
        note_leads, slide_leads = self._press_leads(chart, note_off, slide_off, slack)
        greats = self._great_shifts(chart)
        out = [
            self._point_gesture(
                n, shifts.get(n.id, 0.0), note_leads.get(n.id, 0.0), greats.get(n.id, 0.0), note_off.get(n.id, NO_OFFSET)
            )
            for n in chart.notes
        ]
        # 故意打成 GREAT 的点击不再加时机偏移，免得超出 GREAT 的范围
        fixed = {id(g) for g, n in zip(out, chart.notes) if n.id in greats}
        slides = [
            self._slide_gesture(s, lead=slide_leads.get(s.id, 0.0), off=slide_off.get(s.id, NO_OFFSET))
            for s in chart.slides
        ]
        if p.handover_release_ms > 0:
            # 长条终点松手的同一时刻另有手势按下（一抬一按落在同一帧）时，游戏有时认不出这次松手：
            # 终点判 MISS，或者旁边同时开始的长条漏掉第一个中继。提前一点松手，让两者分在不同的帧
            # （同一时刻的漂移相同，只需再让出两边的独立抖动）
            starts = sorted(g.start for g in out + slides)
            for i, s in enumerate(chart.slides):
                if s.tail is SlideTail.RELEASE and _has_near(starts, s.end_ms, 1.0):
                    slides[i] = self._slide_gesture(
                        s, p.handover_release_ms + 2 * noise, slide_leads.get(s.id, 0.0), slide_off.get(s.id, NO_OFFSET)
                    )
        out += slides
        if p.jitter_ms > 0 and out:
            drift = self._drift(min(g.start for g in out), max(g.end for g in out))
            # 按下时刻相差不到两倍抖动幅度的手势用同一个独立抖动，不打乱同时按下的先后
            # （判定区挨着的音符同时按下时，先到的触点可能被别的音符认领）
            last, j = float("-inf"), 0.0
            for g in sorted((g for g in out if id(g) not in fixed), key=lambda g: g.start):
                if g.start - last > 2 * noise:
                    j = self._rng.uniform(-noise, noise)
                last = g.start
                g.points = [(t + drift(t) + j, x, y) for t, x, y in g.points]
        out.sort(key=lambda g: (g.start, g.end))
        return out

    # —— 触点分配 ——

    def assign(self, gestures: list[Gesture]) -> list[Gesture]:
        """贪心分配触点：选最早空闲的触点。返回无法分配而被丢弃的手势。"""
        free_at = [float("-inf")] * max(1, self.p.max_fingers)
        dropped = []
        for g in gestures:
            ready = [f for f, t in enumerate(free_at) if t + self.p.finger_gap_ms <= g.start]
            if not ready:
                dropped.append(g)
                logger.warning("触点不足，丢弃手势 %s", g.source)
                continue
            # 优先复用最久未用的触点，降低同一触点快速连按的概率
            f = min(ready, key=lambda i: free_at[i])
            g.finger = f
            free_at[f] = g.end
        return dropped

    def _clamp(self, x: float, y: float) -> tuple[int, int]:
        xi = min(max(int(round(x)), 0), self.geo.width - 1)
        yi = min(max(int(round(y)), 0), self.geo.height - 1)
        return xi, yi

    def plan(self, chart: Chart) -> Plan:
        gestures = self.gestures(chart)
        dropped = self.assign(gestures)
        kept = [g for g in gestures if g.finger >= 0]
        events: list[TouchEvent] = []
        for g in kept:
            last = None
            for i, (t, x, y) in enumerate(g.points):
                xi, yi = self._clamp(x, y)
                if i == 0:
                    events.append(TouchEvent(t, Action.DOWN, g.finger, xi, yi))
                elif (xi, yi) != last:
                    events.append(TouchEvent(t, Action.MOVE, g.finger, xi, yi))
                last = (xi, yi)
            t_end = g.points[-1][0]
            events.append(TouchEvent(t_end, Action.UP, g.finger, *last))
        # 稳定排序：同一时刻的事件保持手势内的先后顺序（如滑动的最后一次移动先于抬起）
        events.sort(key=lambda e: e.t_ms)
        return Plan(events=events, gestures=kept, dropped=dropped)


def _has_near(sorted_times: list[float], t: float, tol: float) -> bool:
    i = bisect.bisect_left(sorted_times, t - tol)
    return i < len(sorted_times) and sorted_times[i] <= t + tol


def _unit_x(span: Span, u: float) -> float:
    """区间 ``span`` 里的触控横向位置（单位）：``u``（-1~1）为偏离中心的比例，两侧至少留出 ``POSITION_EDGE``。"""
    return span.center + u * max(0.0, span.width / 2 - POSITION_EDGE)
