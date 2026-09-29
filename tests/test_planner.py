import pytest

from ournotes_auto.charts.model import Chart, FlickDirection, NoteKind, PathNode, PointNote, Slide, SlideHead, SlideTail, Span
from ournotes_auto.config import TouchParams
from ournotes_auto.geometry import Geometry
from ournotes_auto.planner import Action, Planner


def make_planner(**kw):
    return Planner(Geometry(1280, 720), TouchParams(**kw))


def test_geometry_defaults():
    g = Geometry(1280, 720)
    assert g.x_at(0) == pytest.approx(0.0985 * 1280)
    assert g.x_at(24) == pytest.approx(0.9015 * 1280)
    assert g.x_at(12) == pytest.approx(640)
    # 越往上越向中心收拢
    assert g.x_at(0, g.judge_y / 2) > g.x_at(0)
    assert g.x_at(0, g.horizon_y) == pytest.approx(g.center_x)


def test_tap_and_flick_events():
    chart = Chart(
        1,
        "expert",
        notes=[
            PointNote(1, 1000, NoteKind.TAP, Span(0, 4)),
            PointNote(2, 1000, NoteKind.FLICK, Span(20, 24), FlickDirection.RIGHT),
        ],
    )
    plan = make_planner(tap_hold_ms=30, flick_duration_ms=30, flick_steps=3).plan(chart)
    downs = [e for e in plan.events if e.action is Action.DOWN]
    assert len(downs) == 2 and {e.t_ms for e in downs} == {1000}
    assert downs[0].finger != downs[1].finger
    flick_moves = [e for e in plan.events if e.action is Action.MOVE]
    assert len(flick_moves) == 3
    assert flick_moves[-1].x > flick_moves[0].x  # 向右
    ups = [e for e in plan.events if e.action is Action.UP]
    assert sorted(e.t_ms for e in ups) == [1030, 1030]


def test_slide_follows_path_and_releases():
    s = Slide(
        1,
        [PathNode(0, Span(0, 4)), PathNode(100, Span(20, 24))],
        head=SlideHead.TAP,
        tail=SlideTail.RELEASE,
    )
    plan = make_planner(slide_sample_ms=10).plan(Chart(1, "expert", slides=[s]))
    ev = plan.events
    assert ev[0].action is Action.DOWN and ev[0].t_ms == 0
    assert ev[-1].action is Action.UP and ev[-1].t_ms == 100
    xs = [e.x for e in ev if e.action is Action.MOVE]
    assert xs == sorted(xs) and len(xs) >= 9


def test_finger_reuse_respects_gap():
    notes = [PointNote(i, i * 30.0, NoteKind.TAP, Span(0, 4)) for i in range(20)]
    plan = make_planner(tap_hold_ms=25, finger_gap_ms=20, max_fingers=2).plan(Chart(1, "x", notes=notes))
    # 每个触点：抬起到下次按下至少 20ms；2 个触点不够时会丢弃
    last_up = {}
    for e in plan.events:
        if e.action is Action.DOWN and e.finger in last_up:
            assert e.t_ms - last_up[e.finger] >= 20
        if e.action is Action.UP:
            last_up[e.finger] = e.t_ms
    assert len(plan.gestures) + len(plan.dropped) == 20


def test_same_time_order_down_before_up_of_other_gesture():
    # 同一时刻：手势 A 抬起、手势 B 按下，排序保持稳定且每个触点状态合法
    notes = [PointNote(1, 0, NoteKind.TAP, Span(0, 4)), PointNote(2, 35, NoteKind.TAP, Span(0, 4))]
    plan = make_planner(tap_hold_ms=35, finger_gap_ms=0, max_fingers=1).plan(Chart(1, "x", notes=notes))
    state = {}
    for e in plan.events:
        if e.action is Action.DOWN:
            assert not state.get(e.finger)
            state[e.finger] = True
        elif e.action is Action.UP:
            state[e.finger] = False


def test_release_lifts_early_when_another_gesture_presses():
    # 长条 A 在 300ms 松手，同一时刻长条 B 和点击 C 按下：A 提前 20ms 松手，停在路径上对应的位置
    a = Slide(1, [PathNode(0, Span(18, 24)), PathNode(300, Span(12, 18))], head=SlideHead.TAP, tail=SlideTail.RELEASE)
    b = Slide(2, [PathNode(300, Span(6, 12)), PathNode(600, Span(0, 6))], head=SlideHead.TAP, tail=SlideTail.RELEASE)
    chart = Chart(1, "expert", slides=[a, b])
    planner = make_planner(slide_sample_ms=10, handover_release_ms=20)
    plan = planner.plan(chart)
    ga, gb = plan.gestures
    assert ga.end == 280 and all(t <= 280 for t, _, _ in ga.points)
    assert ga.points[-1][1] == planner.geo.x_at(a.span_at(280).center)
    assert gb.end == 600  # B 结束时没有别的手势按下
    up_a = next(e for e in plan.events if e.action is Action.UP and e.finger == ga.finger)
    assert up_a.t_ms == 280
    # 关闭时按原时刻松手
    assert make_planner(handover_release_ms=0).plan(chart).gestures[0].end == 300


def test_head_flick_returns_to_path_gradually():
    # 起点向右划出 72px（30ms），之后 80ms 内逐渐回到路径上，不能一下跳回去
    s = Slide(
        1,
        [PathNode(0, Span(10, 14)), PathNode(400, Span(10, 14))],
        head=SlideHead.FLICK,
        head_direction=FlickDirection.RIGHT,
        tail=SlideTail.RELEASE,
        checkpoints=[200],
    )
    planner = make_planner(slide_sample_ms=8, flick_duration_ms=30, flick_steps=3, head_flick_return_ms=80)
    g = planner.plan(Chart(1, "expert", slides=[s])).gestures[0]
    x0 = g.points[0][1]
    assert [round(x - x0) for t, x, _ in g.points[1:4]] == [24, 48, 72]
    after = [(t, x - x0) for t, x, _ in g.points if t > 30]
    steps = [b[1] - a[1] for a, b in zip(after, after[1:])]
    assert all(-8 <= d <= 0 for d in steps)  # 每 8ms 最多回 72*8/80≈7px
    assert all(dx == 0 for t, dx in after if t >= 110)


def test_head_flick_return_finishes_before_checkpoint():
    s = Slide(
        1,
        [PathNode(0, Span(10, 14)), PathNode(400, Span(10, 14))],
        head=SlideHead.FLICK,
        head_direction=FlickDirection.LEFT,
        checkpoints=[70],
    )
    g = make_planner(slide_sample_ms=8, head_flick_return_ms=80).plan(Chart(1, "expert", slides=[s])).gestures[0]
    x0 = g.points[0][1]
    assert all(x == x0 for t, x, _ in g.points if t >= 70)


def _flick_paths(notes, **kw):
    planner = make_planner(**kw)
    plan = planner.plan(Chart(1, "expert", notes=notes))
    by_id = {int(g.source.split("#")[1].split("@")[0]): g for g in plan.gestures}
    return planner, {i: (g.points[0][1], g.points[-1][1]) for i, g in by_id.items()}


def test_flick_toward_neighbor_starts_shifted_away():
    # BIG MOUTH：[0,8]+[8,16] 同时左滑。右边那个划向邻居：起点右挪 72px，划完正好回到音符中心
    notes = [
        PointNote(1, 1000, NoteKind.FLICK, Span(0, 8), FlickDirection.LEFT),
        PointNote(2, 1000, NoteKind.FLICK, Span(8, 16), FlickDirection.LEFT),
    ]
    planner, paths = _flick_paths(notes)
    c1, c2 = planner.geo.x_at(4), planner.geo.x_at(12)
    assert paths[1] == (pytest.approx(c1), pytest.approx(c1 - 72))  # 左边那个背离邻居，不动
    assert paths[2] == (pytest.approx(c2 + 72), pytest.approx(c2))
    # 0 为关闭
    _, paths = _flick_paths(notes, flick_neighbor_shift=0)
    assert paths[2] == (pytest.approx(c2), pytest.approx(c2 - 72))


def test_flick_shift_needs_same_time_neighbor_on_one_side_only():
    right = PointNote(1, 1000, NoteKind.FLICK, Span(8, 16), FlickDirection.RIGHT)
    planner, paths = _flick_paths([right, PointNote(2, 1500, NoteKind.TAP, Span(16, 24))])
    assert paths[1][0] == pytest.approx(planner.geo.x_at(12))  # 邻居不在同一时刻
    # 两侧都有邻居：不挪，按下点不能靠近另一侧
    both = [right, PointNote(2, 1000, NoteKind.TAP, Span(16, 24)), PointNote(3, 1000, NoteKind.TAP, Span(0, 8))]
    _, paths = _flick_paths(both)
    assert paths[1][0] == pytest.approx(planner.geo.x_at(12))
    # 进行中的长条也算邻居
    s = Slide(9, [PathNode(500, Span(18, 22)), PathNode(1500, Span(18, 22))])
    plan = planner.plan(Chart(1, "expert", notes=[right], slides=[s]))
    g = next(g for g in plan.gestures if g.source.startswith("flick"))
    assert g.points[-1][1] == pytest.approx(planner.geo.x_at(12))


def test_flick_shift_stays_inside_narrow_note():
    # 宽 2 的音符：起点最多挪到离边沿 0.5 个单位处
    notes = [
        PointNote(1, 1000, NoteKind.FLICK, Span(10, 12), FlickDirection.LEFT),
        PointNote(2, 1000, NoteKind.TAP, Span(4, 8)),
    ]
    planner, paths = _flick_paths(notes)
    assert paths[1][0] == pytest.approx(planner.geo.x_at(11.5))


def _gesture(notes, source, slides=(), **kw):
    plan = make_planner(flick_duration_ms=30, flick_steps=3, **kw).plan(Chart(1, "expert", notes=notes, slides=list(slides)))
    return next(g for g in plan.gestures if g.source.startswith(source))


def test_flick_presses_early_when_tap_follows_in_same_place():
    # 焚音打 EXPERT：上划后 105ms 同一位置点击，上划按下的触点被这个点击认领（提前 105ms 判 BAD）
    notes = [
        PointNote(1, 895, NoteKind.TAP, Span(8, 16)),
        PointNote(2, 1000, NoteKind.FLICK, Span(8, 16)),
        PointNote(3, 1105, NoteKind.TAP, Span(8, 16)),
    ]
    g = _gesture(notes, "flick#2")
    # 按下离之后的点击超过 155ms（认领范围 130 + 余量 25），离之前的点击 50ms；按住不动，到原时刻再划
    assert [(t, round(y)) for t, _, y in g.points] == [(945, 571), (1000, 571), (1010, 547), (1020, 523), (1030, 499)]
    # 0 为关闭
    assert _gesture(notes, "flick#2", claim_guard_ms=0).start == 1000


def test_flick_does_not_press_early_without_conflict():
    flick = PointNote(1, 1000, NoteKind.FLICK, Span(8, 16))
    # 点击的判定区（两侧各加 1 个单位）够不到上划的按下点
    assert _gesture([flick, PointNote(2, 1105, NoteKind.TAP, Span(17, 24))], "flick").start == 1000
    # critical 点击只认领提前 58ms 以内的按下
    critical = PointNote(2, 1105, NoteKind.TAP, Span(8, 16), critical=True)
    assert _gesture([flick, critical], "flick").start == 1000
    # 前后都太近、提前按下也避不开：保持原样
    notes = [flick, PointNote(2, 940, NoteKind.TAP, Span(8, 16)), PointNote(3, 1105, NoteKind.TAP, Span(8, 16))]
    assert _gesture(notes, "flick").start == 1000


def test_trace_and_slide_heads_press_early_on_conflict():
    tap = PointNote(2, 1105, NoteKind.TAP, Span(8, 16))
    # 追踪本来就提前 25ms 按下，离点击 130ms：再提前 30ms
    trace = PointNote(1, 1000, NoteKind.TRACE, Span(8, 16))
    assert _gesture([trace, tap], "trace", trace_lead_ms=25).start == 945
    # 起点滑动的长条同样处理，之后的路径不变
    s = Slide(9, [PathNode(1000, Span(8, 16)), PathNode(1400, Span(8, 16))], head=SlideHead.FLICK)
    g = _gesture([tap], "slide", slides=[s], slide_sample_ms=100)
    assert g.points[0][0] == 945 and g.points[1][0] == 1000
    assert g.points[0][1:] == g.points[1][1:]
    # 引导线起点按下时同一时刻有点击：被认领也是 PERFECT，不处理
    guide = Slide(8, [PathNode(1000, Span(8, 16)), PathNode(1400, Span(8, 16))], head=SlideHead.NONE, checkpoints=[1200])
    assert _gesture([PointNote(2, 1000, NoteKind.TAP, Span(8, 16))], "slide", slides=[guide], trace_lead_ms=25).start == 975


# ---------------------------------------------------------------- 拟人化


def _by_id(plan):
    return {int(g.source.split("#")[1].split("@")[0]): g for g in plan.gestures}


def _seeded(seed, **kw):
    return Planner(Geometry(1280, 720), TouchParams(**kw), seed=seed)


def test_humanize_off_by_default():
    notes = [PointNote(i, 1000 + i * 250.0, NoteKind.TAP, Span(i % 20, i % 20 + 4)) for i in range(40)]
    chart = Chart(1, "expert", notes=notes)
    assert _seeded(1).plan(chart).events == _seeded(2).plan(chart).events


def test_position_jitter_stays_inside_note():
    notes = [PointNote(i, i * 200.0, NoteKind.TAP, Span(4, 10)) for i in range(200)]
    notes.append(PointNote(900, 50000, NoteKind.TAP, Span(10, 12)))  # 宽 2：两侧各留 1 个单位，不挪
    notes.append(PointNote(901, 50500, NoteKind.FLICK, Span(14, 18), FlickDirection.LEFT))  # 横滑不挪
    planner = _seeded(3, position_jitter=1.0)
    geo = planner.geo
    by_id = _by_id(planner.plan(Chart(1, "expert", notes=notes)))
    xs = [by_id[i].points[0][1] for i in range(200)]
    ys = [by_id[i].points[0][2] for i in range(200)]
    assert geo.x_at(5) - 1e-6 <= min(xs) and max(xs) <= geo.x_at(9) + 1e-6
    assert max(xs) - min(xs) > 0.8 * (geo.x_at(9) - geo.x_at(5))
    assert all(abs(y - geo.touch_y) <= 0.02 * 720 + 1e-6 for y in ys) and len(set(ys)) > 100
    assert by_id[900].points[0][1] == pytest.approx(geo.x_at(11))
    assert by_id[901].points[0][1:] == (pytest.approx(geo.x_at(16)), pytest.approx(geo.touch_y))


def test_position_jitter_follows_slide_path():
    s = Slide(1, [PathNode(0, Span(0, 6)), PathNode(1000, Span(18, 24))], head=SlideHead.TAP)
    for seed in range(10):
        planner = _seeded(seed, position_jitter=1.0, slide_sample_ms=50)
        g = planner.plan(Chart(1, "expert", slides=[s])).gestures[0]
        assert len({y for _, _, y in g.points}) == 1  # 整条长条同一个上下偏移
        for t, x, _ in g.points:
            span = s.span_at(t)
            assert planner.geo.x_at(span.left + 1) - 1e-6 <= x <= planner.geo.x_at(span.right - 1) + 1e-6


def test_position_jitter_keeps_claim_areas():
    # 春日影 EXPERT：点击和旁边同时开始的长条起点（判定区两侧各宽 2 个单位）。按下点挪进对方的判定区会被对方认领
    notes, slides = [], []
    for i in range(50):
        t = 1000 + i * 1000.0
        notes.append(PointNote(i, t, NoteKind.TAP, Span(12, 18)))
        slides.append(Slide(100 + i, [PathNode(t, Span(6, 12)), PathNode(t + 600, Span(0, 6))], head=SlideHead.TAP))
    planner = _seeded(6, position_jitter=1.0)
    geo = planner.geo

    def unit(x):
        return (x - geo.x_at(0)) / geo.unit_px

    gs = planner.plan(Chart(1, "expert", notes=notes, slides=slides)).gestures
    taps = [unit(g.points[0][1]) for g in gs if g.source.startswith("tap")]
    heads = [unit(g.points[0][1]) for g in gs if g.source.startswith("slide")]
    # 点击留在长条起点判定区 [4,14] 之外，长条起点留在点击判定区 [11,19] 之外（各隔 0.5），另一侧照常挪
    assert 14.5 - 1e-6 <= min(taps) and max(taps) <= 17 + 1e-6 and max(taps) - min(taps) > 2
    assert 7 - 1e-6 <= min(heads) and max(heads) <= 10.5 + 1e-6 and max(heads) - min(heads) > 3

    # 同一轨道 100ms 一个：中心本来就在前后音符的判定区里，挪动不出这些判定区，照常挪满
    stream = [PointNote(i, 1000 + i * 100.0, NoteKind.TAP, Span(4, 10)) for i in range(200)]
    xs = [unit(g.points[0][1]) for g in _seeded(6, position_jitter=1.0).plan(Chart(1, "expert", notes=stream)).gestures]
    assert 5 - 1e-6 <= min(xs) and max(xs) <= 9 + 1e-6 and max(xs) - min(xs) > 3.5


def test_early_release_stays_in_tail_span():
    # 起死开战 EXPERT：结尾 28ms 从 [6,12] 横移到 [0,6]，同一时刻有点击按下，长条提前松手。
    # 半路松手的位置夹进终点区间（两侧留出 1），开了时机抖动（多让出 6ms）和位置随机也不出界
    s = Slide(1, [PathNode(0, Span(0, 6)), PathNode(88, Span(6, 12)), PathNode(116, Span(0, 6))], head=SlideHead.TAP)
    chart = Chart(1, "expert", notes=[PointNote(2, 116, NoteKind.TAP, Span(12, 20))], slides=[s])
    planner = make_planner(handover_release_ms=20)
    plan = planner.plan(chart)
    g = _by_id(plan)[1]
    assert g.end == 96 and g.points[-1][1] == pytest.approx(planner.geo.x_at(5))
    # 移到松手点提前 4ms 单独发，不和抬起同一批
    ev = [e for e in plan.events if e.finger == g.finger]
    assert [e.action for e in ev[-2:]] == [Action.MOVE, Action.UP] and ev[-1].t_ms - ev[-2].t_ms == 4
    assert ev[-2].x == round(planner.geo.x_at(5))
    for seed in range(20):
        planner = _seeded(seed, jitter_ms=16, position_jitter=1.0)
        x = _by_id(planner.plan(chart))[1].points[-1][1]
        assert planner.geo.x_at(1) - 1e-6 <= x <= planner.geo.x_at(5) + 1e-6


def test_position_jitter_keeps_held_slides_apart():
    # 步拾道 EXPERT：两个长条并排走，左边的尾部变宽到 [0,11] 再横滑。手指不能挪进对方的判定区（两侧各宽 2）
    a = Slide(1, [PathNode(0, Span(16, 24)), PathNode(230, Span(8, 16)), PathNode(1000, Span(8, 16))], head=SlideHead.TAP)
    b = Slide(
        2,
        [PathNode(0, Span(6, 14)), PathNode(230, Span(0, 8)), PathNode(460, Span(0, 8)), PathNode(461, Span(0, 11))],
        head=SlideHead.TAP,
        tail=SlideTail.FLICK,
        tail_direction=FlickDirection.RIGHT,
    )
    chart = Chart(1, "expert", slides=[a, b])
    xa, xb = [], []
    for seed in range(40):
        planner = _seeded(seed, position_jitter=1.0, slide_sample_ms=10)
        geo = planner.geo
        by_id = _by_id(planner.plan(chart))
        xa += [(x - geo.x_at(0)) / geo.unit_px for t, x, _ in by_id[1].points if 230 <= t <= 461]
        xb += [(x - geo.x_at(0)) / geo.unit_px for t, x, _ in by_id[2].points if 230 <= t <= 461]
    # b 的中心在 a 的判定区 [6,18] 外（离边界至少 0.5）；a 的中心 12 在 b 的判定区外，b 变宽成 [−2,13] 后在里面
    assert max(xb) <= 5.5 + 1e-6 and max(xb) - min(xb) > 2
    assert 10.5 - 1e-6 <= min(xa) and max(xa) <= 12.5 + 1e-6 and max(xa) - min(xa) > 1.5


def test_simultaneous_presses_share_noise():
    notes = []
    for i in range(100):
        t = 1000 + i * 300.0
        notes += [PointNote(2 * i, t, NoteKind.TAP, Span(0, 4)), PointNote(2 * i + 1, t, NoteKind.TAP, Span(20, 24))]
    by_id = _by_id(_seeded(7, jitter_ms=16).plan(Chart(1, "expert", notes=notes)))
    assert all(by_id[2 * i].start == by_id[2 * i + 1].start for i in range(100))
    assert len({round(by_id[2 * i].start - (1000 + i * 300.0), 3) for i in range(100)}) > 50


def _great_chart():
    notes = [PointNote(i, 1000 + i * 500.0, NoteKind.TAP, Span(8, 12)) for i in range(40)]
    notes += [
        PointNote(50, 30000, NoteKind.TAP, Span(0, 4), critical=True),  # critical 没有 GREAT 这一档
        PointNote(51, 31000, NoteKind.FLICK, Span(8, 12)),
        # 52 和 53 相隔 150ms、判定区挨着：都不挑
        PointNote(52, 32000, NoteKind.TAP, Span(16, 20)),
        PointNote(53, 32150, NoteKind.TAP, Span(20, 24)),
        # 同一时刻但离得远：都可以挑
        PointNote(54, 33000, NoteKind.TAP, Span(0, 4)),
        PointNote(55, 33000, NoteKind.TAP, Span(20, 24)),
    ]
    return Chart(1, "expert", notes=notes), {n.id: n.time_ms for n in notes}


@pytest.mark.parametrize("jitter", [0.0, 16.0])
def test_great_shifts_only_isolated_plain_taps(jitter):
    chart, times = _great_chart()
    by_id = _by_id(_seeded(4, great_ratio=1.0, jitter_ms=jitter).plan(chart))
    shift = {i: g.start - times[i] for i, g in by_id.items()}
    greats = {i for i, s in shift.items() if abs(s) >= 60}
    assert greats == set(range(2, 40)) | {54, 55}  # 开头 1s 以内的 0、1 也不挑
    assert all(64 <= abs(shift[i]) <= 69 for i in greats)  # 不再叠加时机偏移
    assert {s > 0 for i, s in shift.items() if i in greats} == {True, False}
    assert all(abs(s) <= jitter + 3 + 1e-6 for i, s in shift.items() if i not in greats)


def test_great_ratio_counts_judged_notes():
    notes = [PointNote(i, 1000 + i * 300.0, NoteKind.TAP, Span(0, 4) if i % 2 else Span(12, 16)) for i in range(1000)]
    chart = Chart(1, "expert", notes=notes)
    times = {n.id: n.time_ms for n in notes}
    for seed in range(3):
        by_id = _by_id(_seeded(seed, great_ratio=0.1).plan(chart))
        count = sum(abs(g.start - times[i]) >= 60 for i, g in by_id.items())
        assert 60 <= count <= 140  # 约 10%（二项分布，标准差约 9.5）


def test_timing_drift_bounded_and_smooth():
    notes = [PointNote(i, 1000 + i * 100.0, NoteKind.TAP, Span(0, 4) if i % 2 else Span(12, 16)) for i in range(600)]
    by_id = _by_id(_seeded(5, jitter_ms=16).plan(Chart(1, "expert", notes=notes)))
    offs = [by_id[n.id].start - n.time_ms for n in notes]
    assert max(abs(o) for o in offs) <= 16 + 3 + 1e-6
    assert max(offs) - min(offs) > 10
    # 相隔 100ms：漂移最多差 2×16×100/1500，另加两边各 ±3ms 的独立抖动
    assert all(abs(a - b) <= 2 * 16 * 100 / 1500 + 6 + 1e-6 for a, b in zip(offs, offs[1:]))
    # 手势内部的时长基本不变（按住 35ms）
    assert all(abs(by_id[n.id].end - by_id[n.id].start - 35) < 1 for n in notes)


def test_handover_gap_survives_jitter():
    # 长条终点松手时另有点击按下：开了时机偏移，松手仍比按下早 19ms 以上（分在不同的帧）
    s = Slide(1, [PathNode(0, Span(18, 24)), PathNode(3000, Span(12, 18))], head=SlideHead.TAP, tail=SlideTail.RELEASE)
    tap = PointNote(2, 3000, NoteKind.TAP, Span(0, 4))
    for seed in range(30):
        gs = _seeded(seed, jitter_ms=16, handover_release_ms=20).plan(Chart(1, "expert", notes=[tap], slides=[s])).gestures
        slide = next(g for g in gs if g.source.startswith("slide"))
        press = next(g for g in gs if g.source.startswith("tap"))
        assert press.start - slide.end >= 19
