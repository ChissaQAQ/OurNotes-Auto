"""用合成画面验证首音符跟踪同步的外推精度。"""

import math
from dataclasses import replace

import numpy as np
import pytest

from ournotes_auto.charts.model import LANE_UNITS, Span
from ournotes_auto.config import SyncParams
from ournotes_auto.geometry import Geometry, GeometryParams
from ournotes_auto.player.sync import NoteTracker, SyncTimeout

W, H = 1280, 720


def render(geo: Geometry, span: Span, y_lead: float, height_px: float, rng) -> np.ndarray:
    frame = np.full((H, W, 3), 40, np.uint8)
    frame += rng.integers(0, 4, frame.shape, dtype=np.uint8)
    if y_lead <= 0:
        return frame
    y0 = max(int(y_lead - height_px), 0)
    for y in range(y0, min(int(y_lead), H)):
        x0 = int(geo.x_at(span.left, y))
        x1 = int(geo.x_at(span.right, y))
        frame[y, x0:x1] = (250, 240, 255)
    return frame


def simulate(true_geo, tracker_geo, fps, tau_s, rng, jitter_s=0.002, params=None, render_fps=0.0):
    """按指数逼近模型渲染首音符下落，返回同步结果与真实到达时刻。

    ``render_fps`` 为 0 时截到的是 1/fps 网格上的画面、时间戳晚 0~jitter_s；否则游戏按这个帧率渲染，
    截图时刻在 1/fps 网格上晚 0~jitter_s，截到的是这之前最近渲染的那一帧，时间戳就是截图时刻（与实机录像一致）。
    """
    span = Span(8, 14)
    first_ms = 5000.0
    arrival = 12.3456

    # 比较的是前沿到达时刻，不按流速修正（见 test_center_lag_scales_with_tau）；默认的画面没有渲染网格，不对齐
    tr = NoteTracker(tracker_geo, params or SyncParams(center_lag=0, render_fps=0), first_ms, [span])
    t = arrival - 4.0 * tau_s + rng.uniform(0, 1 / fps)
    phase = rng.uniform(0, 1 / render_fps) if render_fps else 0.0
    while t < arrival + 1.0:
        stamp = t + rng.uniform(0, jitter_s)
        shown = phase + math.floor((stamp - phase) * render_fps) / render_fps if render_fps else t
        y = true_geo.note_y(arrival - shown, tau_s)
        frame = render(true_geo, span, y, 0.04 * (y - true_geo.motion_horizon_y), rng)
        result = tr.feed(frame, stamp)
        if result is not None:
            return result, arrival
        t += 1 / fps
    raise AssertionError("未得到同步结果")


@pytest.mark.parametrize("fps,tau_s", [(30, 0.835), (60, 0.835), (60, 0.5), (60, 1.3), (120, 0.835)])
def test_tracker_extrapolates_arrival(fps, tau_s):
    """τ 与配置不同（流速改了）时也要准：样本足够多，先验不起主导作用。"""
    errs = []
    for seed in range(4):
        rng = np.random.default_rng(seed)
        geo = Geometry(W, H)
        result, arrival = simulate(geo, geo, fps, tau_s, rng)
        assert result.ok, result
        assert result.lead_ms > 120  # 在音符落下前就给出结果
        assert result.t0 == pytest.approx(result.arrival - 5.0)
        assert result.tau_s == pytest.approx(tau_s, rel=0.02)
        assert result.fps == pytest.approx(fps, rel=0.05)  # 出帧率（游戏变卡时重启）
        # 减去截图时刻抖动的均值 1ms
        errs.append((result.arrival - arrival) * 1000 - 1.0)
    # 前沿按整像素检测，有约 -1ms 的固定偏差（由 offset_ms 吸收）；随机误差要小
    assert abs(np.mean(errs)) < 3, errs
    assert np.std(errs) < 1.5, errs


@pytest.mark.parametrize("tau_s", [0.262, 0.835, 1.3])
def test_center_lag_scales_with_tau(tau_s):
    """前沿比音符中间早到的时间与 τ 成正比：offset 是在流速 5.00（τ=0.835）下得到的，其他流速按 τ 之差修正。"""
    geo = Geometry(W, H)
    p = SyncParams(tau_s=tau_s, render_fps=0)
    front, _ = simulate(geo, geo, 60, tau_s, np.random.default_rng(0), params=replace(p, center_lag=0))
    center, _ = simulate(geo, geo, 60, tau_s, np.random.default_rng(0), params=p)
    assert center.ok and center.arrival == front.arrival
    shift_ms = (center.t0 - front.t0) * 1000
    assert shift_ms == pytest.approx(p.center_lag * (center.tau_s - 0.835) * 1000, abs=1e-6)
    assert shift_ms == pytest.approx(p.center_lag * (tau_s - 0.835) * 1000, abs=0.5)


def test_render_alignment_removes_frame_phase_error():
    """游戏 60fps 渲染、截图 59fps：截到的画面比截图时刻早多少在一局里缓慢漂移，流速 10.00（τ=0.262）时跟踪很短、
    平均不掉，每局误差很大；对齐到渲染网格后每局误差小得多，平均值不变（offset_ms 不用重新校准）。"""
    geo = Geometry(W, H)
    raw, aligned = [], []
    for seed in range(8):
        for errs, fps in ((raw, 0), (aligned, 60)):
            p = SyncParams(tau_s=0.262, center_lag=0, render_fps=fps)
            result, arrival = simulate(geo, geo, 59, 0.262, np.random.default_rng(seed), params=p, render_fps=60)
            assert result.ok, result
            errs.append((result.arrival - arrival) * 1000)
            assert fps == 0 or any("对齐到 60fps" in n for n in result.notes), result.notes
    assert np.std(aligned) < np.std(raw) / 2, (raw, aligned)
    assert np.ptp(aligned) < 12, aligned
    assert abs(np.mean(aligned) - np.mean(raw)) < 4, (raw, aligned)


def test_render_alignment_skipped_without_render_grid():
    """画面不是按 60fps 网格渲染的（截图时刻就是画面时刻）：对齐后残差降不下来，不对齐。"""
    geo = Geometry(W, H)
    for seed in range(3):
        result, _ = simulate(geo, geo, 57, 0.835, np.random.default_rng(seed), params=SyncParams(center_lag=0))
        assert result.ok and not any("对齐" in n for n in result.notes), result.notes


def test_horizon_error_gives_constant_bias():
    """运动消失线的误差产生的偏差约 3ms / 0.001 屏高，且基本与流速无关，可以被 offset_ms 吸收。"""
    guess = Geometry(W, H)
    for true_h, lo, hi in ((-0.0536, -5, 0), (-0.058, -14, -6)):
        true_geo = Geometry(W, H, GeometryParams(horizon_y=true_h))
        means = []
        for tau_s in (0.5, 1.2):
            errs = []
            for seed in range(2):
                result, arrival = simulate(true_geo, guess, 60, tau_s, np.random.default_rng(seed))
                assert result.ok, result
                errs.append((result.arrival - arrival) * 1000 - 1.0)
            means.append(np.mean(errs))
        assert all(lo < m < hi for m in means), means
        assert abs(means[0] - means[1]) < 4, means


@pytest.mark.parametrize("true_h", [-0.12, -0.0526, -0.02])
def test_full_track_measures_motion(true_h):
    """calibrate motion 模式：跟踪完整轨迹，拟合消失线与 τ。"""
    from ournotes_auto.calibrate import motion_params

    true_geo = Geometry(W, H, GeometryParams(horizon_y=true_h))
    params = motion_params(SyncParams())
    for seed in range(2):
        result, arrival = simulate(true_geo, Geometry(W, H), 60, 0.7, np.random.default_rng(seed), params=params)
        assert result.ok, result
        assert abs((result.arrival - arrival) * 1000 - 1.0) < 3.0, result
        assert result.horizon_y / H == pytest.approx(true_h, abs=0.004)
        assert result.tau_s == pytest.approx(0.7, rel=0.01)


def test_fit_arrival_prior():
    """样本很少时 τ 先验让外推保持合理；无先验时两参数拟合也能精确恢复。"""
    from ournotes_auto.player.sync import fit_arrival

    geo = Geometry(W, H)
    dts = np.linspace(2.0, 1.5, 6)
    ts = 10.0 - dts
    ys = np.array([geo.note_y(d, 0.9) for d in dts])
    free = fit_arrival(ts, ys, geo.judge_y, geo.motion_horizon_y)
    assert free.arrival == pytest.approx(10.0, abs=1e-6) and free.tau == pytest.approx(0.9)
    noisy = ys + np.array([0.5, -0.5, 0.5, -0.5, 0.5, -0.5])
    loose = fit_arrival(ts, noisy, geo.judge_y, geo.motion_horizon_y)
    tight = fit_arrival(ts, noisy, geo.judge_y, geo.motion_horizon_y, tau_prior=0.9, tau_rel_sigma=0.01)
    assert abs(tight.arrival - 10.0) < abs(loose.arrival - 10.0)
    assert tight.tau == pytest.approx(0.9, rel=0.02)


def test_tracker_waits_for_static_scene():
    rng = np.random.default_rng(2)
    geo = Geometry(W, H)
    tr = NoteTracker(geo, SyncParams(stable_frames=5), 1000.0, [Span(0, 6)])
    for i in range(30):
        frame = rng.integers(0, 255, (H, W, 3), dtype=np.uint8)
        assert tr.feed(frame, i / 60) is None
    assert not tr.armed


def test_tracker_rejects_fast_jump():
    """前沿跳得比音符可能的速度快得多（如介绍卡片淡出）时放弃这条轨迹，重新等待静止。"""
    geo = Geometry(W, H)
    tr = NoteTracker(geo, SyncParams(stable_frames=2), 1000.0, [Span(0, 6)])
    rng = np.random.default_rng(3)
    blank = render(geo, Span(0, 6), -1, 0, rng)
    for i in range(5):
        tr.feed(blank, i / 60)
    assert tr.feed(render(geo, Span(0, 6), H * 0.05, 6, rng), 0.1) is None
    assert tr.feed(render(geo, Span(0, 6), H * 0.3, 60, rng), 0.12) is None
    assert not tr.armed and not tr.samples


def test_tracker_rejects_implausible_track():
    """每帧变化都不大、但整体运动规律不像音符的轨迹也要放弃。"""
    geo = Geometry(W, H)
    tr = NoteTracker(geo, SyncParams(stable_frames=2), 1000.0, [Span(8, 14)])
    rng = np.random.default_rng(6)
    for i in range(5):
        tr.feed(render(geo, Span(8, 14), -1, 0, rng), i / 60)
    rejected = False
    for k in range(40):  # 屏幕上匀速下移，比音符在画面上部快得多
        y = 12 + 6 * k
        tr.feed(render(geo, Span(8, 14), y, 10, rng), 0.1 + k / 60)
        if not tr.armed:
            rejected = True
            break
    assert rejected


def test_tracker_rearms_after_scene_change():
    """介绍卡淡出等整体变化不能被当成音符。"""
    rng = np.random.default_rng(4)
    geo = Geometry(W, H)
    tr = NoteTracker(geo, SyncParams(stable_frames=3), 1000.0, [Span(8, 14)])
    for i in range(5):
        tr.feed(render(geo, Span(8, 14), -1, 0, rng), i / 60)
    assert tr.armed
    flash = render(geo, Span(8, 14), -1, 0, rng)
    flash[:] = 230
    assert tr.feed(flash, 6 / 60) is None
    assert not tr.armed and tr._first_seen is None
    # 画面重新静止后再次就绪
    for i in range(7, 12):
        tr.feed(render(geo, Span(8, 14), -1, 0, rng), i / 60)
    assert tr.armed


def test_tracker_ignores_colored_glow():
    """走在音符前面的彩色特效（如紫色光锥）不能当成音符：只跟偏白的像素。"""
    geo = Geometry(W, H)
    span = Span(8, 14)
    tau_s, arrival = 0.835, 12.3456

    def run(params):
        rng = np.random.default_rng(8)
        tr = NoteTracker(geo, params, 5000.0, [span])
        t = arrival - 4.5 * tau_s
        while t < arrival + 1.0:
            y = geo.note_y(arrival - t, tau_s)
            frame = render(geo, span, y, 0.04 * (y - geo.motion_horizon_y), rng)
            if t < arrival - 0.6:  # 光锥比音符早 0.6s 到达判定线
                yg = geo.note_y(arrival - 0.6 - t, tau_s)
                y0 = max(int(yg - 0.1 * (yg - geo.motion_horizon_y)), 0)
                for yy in range(y0, min(int(yg), H)):
                    frame[yy, int(geo.x_at(span.left, yy)) : int(geo.x_at(span.right, yy))] = (200, 60, 230)
            result = tr.feed(frame, t)
            if result is not None:
                return result
            t += 1 / 60
        raise AssertionError("未得到同步结果")

    result = run(SyncParams())
    assert result.ok, result
    assert abs(result.arrival - arrival) * 1000 < 5
    fooled = run(SyncParams(min_whiteness=0))
    assert abs(fooled.arrival - arrival) > 0.3


def draw_bar_lines(frame: np.ndarray, geo: Geometry, ys) -> None:
    """画横贯整条轨道的小节线（2 像素高的灰白线）。实机的小节线偏暗，只有一部分像素过得了偏白高亮的门槛：
    这里隔一个像素亮一个，暗的那些过不了门槛但也比轨道亮得多。"""
    for yb in ys:
        for yy in range(max(int(yb) - 1, 0), min(int(yb) + 1, H)):
            x0, x1 = int(geo.x_at(0, yy)), int(geo.x_at(LANE_UNITS, yy))
            frame[yy, x0:x1] = (110, 110, 110)
            frame[yy, x0:x1:2] = (190, 190, 190)


@pytest.mark.parametrize("on_note", [False, True])
def test_tracker_ignores_bar_lines(on_note):
    """开了「小节线显示」：比首音符先落下的小节线不能当成首音符（#18），压在首音符中间的小节线不影响跟踪。"""
    geo = Geometry(W, H)
    span = Span(8, 14)
    tau_s, arrival = 0.835, 12.3456

    def run(params):
        rng = np.random.default_rng(9)
        tr = NoteTracker(geo, params, 5000.0, [span])
        t = arrival - 4.5 * tau_s
        while t < arrival + 1.0:
            y = geo.note_y(arrival - t, tau_s)
            height = 0.04 * (y - geo.motion_horizon_y)
            frame = render(geo, span, y, height, rng)
            # 每 1.2s 一条小节线，最近的一条比首音符早 0.6s 到达判定线；等静止时就有小节线在动
            ys = [geo.note_y(arrival - 0.6 - 1.2 * k - t, tau_s) for k in range(4)]
            if on_note:
                ys.append(y - height / 2)
            draw_bar_lines(frame, geo, ys)
            result = tr.feed(frame, t)
            if result is not None:
                return result
            t += 1 / 60
        return None

    result = run(SyncParams())
    assert result is not None and result.ok, result
    assert abs(result.arrival - arrival) * 1000 < 5
    # 不检查小节线时一直等不到画面静止（实机上 4s 后直接开始跟踪，就会跟上小节线）
    fooled = run(SyncParams(bar_line_ratio=0))
    assert fooled is None or not fooled.ok or abs(fooled.arrival - arrival) > 0.3


def test_tracker_rejects_late_start():
    """推算的歌曲开始时刻离开始同步太远，说明错过了第一个音符，跟上的是后面的音符。"""
    geo = Geometry(W, H)

    def run(lead_s: float):
        rng = np.random.default_rng(7)
        span = Span(8, 14)
        tr = NoteTracker(geo, SyncParams(max_start_delay_s=20.0), 1000.0, [span])
        arrival = lead_s + 1.0 + 4 * 0.835
        tr.feed(render(geo, span, -1, 0, rng), 0.0)  # 开始同步的时刻
        t = arrival - 4 * 0.835
        while t < arrival + 1.0:
            y = geo.note_y(arrival - t, 0.835)
            result = tr.feed(render(geo, span, y, 0.04 * (y - geo.motion_horizon_y), rng), t)
            if result is not None:
                return result
            t += 1 / 60
        raise AssertionError("未得到同步结果")

    assert run(9.0).ok
    late = run(25.0)
    assert not late.ok
    assert any("第一个音符" in n for n in late.notes), late.notes


def test_tracker_preset_baseline_after_retry():
    """暂停重试后歌曲立即开始、首音符一直在动：沿用上一次静止时的基线才跟得上它，不等静止就会错过。"""
    geo = Geometry(W, H)
    span = Span(8, 14)
    rng = np.random.default_rng(9)
    first = NoteTracker(geo, SyncParams(), 2000.0, [span])
    for i in range(12):
        first.feed(render(geo, span, -1, 0, rng), i / 60)
    assert first.armed and first.clean_baseline is not None

    arrival = 10.0  # 歌曲在 8.0s 开始，0.3s 后才开始同步，首音符已经进入跟踪区
    begin = arrival - 2.0 + 0.3

    def run(tracker):
        t = begin
        while t < arrival + 1.0:
            y = geo.note_y(arrival - t, 0.835)
            result = tracker.feed(render(geo, span, y, 0.04 * (y - geo.motion_horizon_y), rng), t)
            if result is not None:
                return result
            t += 1 / 60
        return None

    preset = NoteTracker(geo, SyncParams(), 2000.0, [span], baseline=first.clean_baseline, max_start_delay_s=1.5)
    assert preset.armed
    result = run(preset)
    assert result is not None and result.ok, result
    assert abs(result.arrival - arrival) * 1000 < 5
    assert result.t0 - begin == pytest.approx(-0.3, abs=0.01)
    assert run(NoteTracker(geo, SyncParams(), 2000.0, [span])) is None


def test_tracker_preset_baseline_rearm_and_mismatch():
    """沿用基线时出现非音符变化：换回沿用的基线（不重新等静止）；基线尺寸和跟踪区不符就照常等静止。"""
    geo = Geometry(W, H)
    span = Span(8, 14)
    rng = np.random.default_rng(10)
    first = NoteTracker(geo, SyncParams(stable_frames=3), 1000.0, [span])
    for i in range(5):
        first.feed(render(geo, span, -1, 0, rng), i / 60)
    tr = NoteTracker(geo, SyncParams(), 1000.0, [span], baseline=first.clean_baseline)
    flash = render(geo, span, -1, 0, rng)
    flash[:] = 230
    assert tr.feed(flash, 0.0) is None
    assert tr.armed and tr._first_seen is None
    assert np.array_equal(tr._baseline, first.clean_baseline)
    assert tr.clean_baseline is None  # 没有真正静止过，不能当下一次的基线
    other = NoteTracker(geo, SyncParams(), 1000.0, [Span(0, 6)], baseline=first.clean_baseline)
    assert not other.armed


def test_tracker_retry_start_delay():
    """重试时歌曲开始时刻几乎就是开始同步的时刻：推算的开始时刻晚了 3s 就是跟错了音符。"""
    geo = Geometry(W, H)
    span = Span(8, 14)

    def run(max_start_delay_s):
        rng = np.random.default_rng(11)
        tr = NoteTracker(geo, SyncParams(), 1000.0, [span], max_start_delay_s=max_start_delay_s)
        arrival = 3.0 + 1.0
        for i in range(12):
            tr.feed(render(geo, span, -1, 0, rng), i / 120)
        t = arrival - 4 * 0.835
        while t < arrival + 1.0:
            y = geo.note_y(arrival - t, 0.835)
            result = tr.feed(render(geo, span, y, 0.04 * (y - geo.motion_horizon_y), rng), t)
            if result is not None:
                return result
            t += 1 / 60
        raise AssertionError("未得到同步结果")

    assert run(None).ok
    late = run(1.5)
    assert not late.ok and any("第一个音符" in n for n in late.notes), late.notes


def test_dump_and_replay(tmp_path):
    from ournotes_auto.player.sync import replay_dump

    rng = np.random.default_rng(5)
    geo = Geometry(W, H)
    span = Span(8, 14)
    tr = NoteTracker(geo, SyncParams(), 5000.0, [span], record_frames=400)
    arrival = 12.0
    t, result = arrival - 3.0, None
    while result is None:
        y = geo.note_y(arrival - t, 0.835)
        result = tr.feed(render(geo, span, y, 0.04 * (y - geo.motion_horizon_y), rng), t)
        t += 1 / 60
    path = tr.dump(tmp_path / "sync.npz", result)
    again = replay_dump(path, geo, SyncParams())
    assert again is not None and again.arrival == pytest.approx(result.arrival, abs=1e-9)
    # 不检查小节线时去掉记录里的轨道采样点照样重放
    plain = replay_dump(path, geo, SyncParams(bar_line_ratio=0))
    assert plain is not None and plain.arrival == pytest.approx(result.arrival, abs=1e-9)
