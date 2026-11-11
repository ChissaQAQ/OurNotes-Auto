"""自然操作的会话状态、结构关联和有界分布；不依赖模拟器或实际等待。"""

import statistics

import pytest

from ournotes_auto.charts.model import Chart, NoteKind, PointNote, Span
from ournotes_auto.config import Config, HumanizeConfig, TouchParams
from ournotes_auto.geometry import Geometry
from ournotes_auto.humanize import Humanizer
from ournotes_auto.planner import Planner


def human(seed=1, **kwargs):
    return Humanizer(HumanizeConfig(enabled=True, **kwargs), seed=seed)


def song():
    return Chart(1, "expert", notes=[PointNote(i, 1000 + i * 500, NoteKind.TAP, Span(0, 4)) for i in range(100)], duration_ms=180000)


def test_disabled_keeps_existing_plans_identical():
    params = TouchParams(jitter_ms=16, position_jitter=1, great_ratio=0.03)
    chart = song()
    plain = Planner(Geometry(1280, 720), params, seed=7).plan(chart)
    disabled = Planner(Geometry(1280, 720), params, seed=7, humanizer=Humanizer(HumanizeConfig(), seed=9)).plan(chart)
    assert plain.events == disabled.events


def test_habits_persist_while_warmup_familiarity_and_recovery_change_spread():
    h, chart = human(), song()
    h.clock = lambda: 100.0
    habits = h.timing_bias, h.hand_bias, h.hold_scale, h.position_bias
    h.begin_chart(chart)
    initial = h.spread
    for _ in range(5):
        h.completed(chart)
        h.begin_chart(chart)
    assert h.spread < initial
    h.work_s = 3600
    h.begin_chart(chart)
    tired = h.spread
    h.recover(180)
    h.begin_chart(chart)
    assert h.spread < tired
    assert h.familiarity[chart.key] == 5
    assert habits == (h.timing_bias, h.hand_bias, h.hold_scale, h.position_bias)


def test_dense_patterns_have_more_variable_timing_and_shorter_holds():
    h = human()
    sparse = PointNote(1, 1000, NoteKind.TAP, Span(0, 4))
    dense = [PointNote(10 + i, 5000 + i * 50, NoteKind.TAP, Span(0, 4)) for i in range(20)]
    h.begin_chart(Chart(1, "expert", notes=[sparse, *dense]))
    hard = dense[10]
    assert h.load(hard) > h.load(sparse)
    easy_noise = [h.noise(sparse, 3) for _ in range(2000)]
    hard_noise = [h.noise(hard, 3) for _ in range(2000)]
    assert statistics.pstdev(hard_noise) > statistics.pstdev(easy_noise) * 1.5
    assert statistics.mean(h.tap_hold(35, hard) for _ in range(1000)) < statistics.mean(h.tap_hold(35, sparse) for _ in range(1000))
    assert all(35 <= h.tap_hold(35, n) <= 50 + 1e-9 for n in (sparse, hard) for _ in range(1000))  # 只加不减
    assert all(-3 <= n <= 3 for n in easy_noise + hard_noise)


def test_familiarity_storage_is_bounded():
    h = human()
    for i in range(200):
        h.completed(Chart(i, "expert"))
    assert len(h.familiarity) == 128
    assert "0_expert" not in h.familiarity


def test_play_session_and_planner_share_supplied_habits():
    from types import SimpleNamespace
    from ournotes_auto.player.session import PlaySession

    cfg, h = Config(), human()
    session = PlaySession(cfg, SimpleNamespace(size=(1280, 720)), None, humanizer=h)
    assert session.humanizer is h and session.planner.humanizer is h


def test_rest_by_count_duration_and_existing_idle():
    chart = song()
    h = human(rest_every_plays=1, rest_min_s=5, rest_max_s=10, rare_rest_chance=0, long_rest_minutes=0)
    now = [0.0]
    h.clock = lambda: now[0]
    assert h.rest_seconds() == (0, "")
    h.completed(chart)
    seconds, what = h.rest_seconds()
    assert 5 <= seconds <= 10 and what == "局间休息"
    now[0] = 120  # 等 LB 等已经空闲够久：算休息过了
    assert h.rest_seconds() == (0, "") and h.since_rest == 0
    h.config.rest_every_plays = 0
    h.config.long_rest_minutes = 2  # 每轮 1.6~2.4 分钟，一首 3 分钟就到
    h.config.long_rest_min_s, h.config.long_rest_max_s = 600, 900
    h.completed(chart)
    seconds, what = h.rest_seconds()
    assert 600 <= seconds <= 900 and what == "长休息"
    h.recover(30)  # 短休息不清零累计演奏时长
    assert h.rest_seconds()[1] == "长休息"
    h.recover(700)
    assert h.rest_seconds() == (0, "") and h.active_s == 0


def test_rest_rounds_vary_and_some_rests_are_long():
    chart = song()
    h = human(rest_every_plays=8, rest_min_s=30, rest_max_s=90, long_rest_minutes=0)
    rounds, rests = [], []
    for _ in range(300):
        n = 0
        while h.rest_seconds() == (0, ""):
            h.completed(chart)
            n += 1
        rounds.append(n)
        seconds, what = h.rest_seconds()
        rests.append(seconds)
        assert (30 <= seconds <= 90) if what == "局间休息" else (180 <= seconds <= 540 and what == "局间休息（久一点）")
        h.recover(seconds)
    assert min(rounds) >= 5 and max(rounds) <= 11 and len(set(rounds)) >= 5  # 8 局的 60%~140%
    assert 0.05 < sum(s > 90 for s in rests) / len(rests) < 0.2  # 约 12% 特别久


def test_zero_rest_lengths_do_not_wait_but_restart_counting():
    h = human(rest_every_plays=1, rest_min_s=0, rest_max_s=0, rare_rest_chance=0, long_rest_minutes=0)
    h.completed(song())
    assert h.rest_seconds() == (0, "") and h.since_rest == 0


def test_fatigue_fades_a_little_on_short_rest_and_mostly_on_long_rest():
    h = human()
    h.work_s = 3600
    h.recover(60)
    assert 3000 < h.work_s < 3600
    h.recover(1200)
    assert h.work_s < 3600 * 0.15


def test_habits_are_the_same_all_game_day_and_change_with_day_or_player(monkeypatch):
    from ournotes_auto import humanize

    def habits(key):
        h = Humanizer(HumanizeConfig(enabled=True), habit_key=key)
        return h.timing_bias, h.hand_bias, h.hold_scale, h.position_bias, h.cap_scale

    monkeypatch.setattr(humanize, "game_day", lambda: "2026-11-10")
    day1 = habits("k:pkg:")
    assert habits("k:pkg:") == day1  # 同一天的另一次运行
    assert habits("k:pkg:alt") != day1  # 另一个账号
    monkeypatch.setattr(humanize, "game_day", lambda: "2026-11-11")
    assert habits("k:pkg:") != day1
    h = Humanizer(HumanizeConfig(enabled=True), habit_key="k:pkg:")
    monkeypatch.setattr(humanize, "game_day", lambda: "2026-11-12")
    h.begin_chart(song())  # 跨过换日的运行在下一首换成新一天的习惯
    assert (h.timing_bias, h.hand_bias, h.hold_scale, h.position_bias, h.cap_scale) == habits("k:pkg:")


def test_daily_scale():
    h = human()
    assert 0.85 <= h.daily_scale() <= 1.0
    h.config.enabled = False
    assert h.daily_scale() == 1.0


def test_disabled_rest_policies_and_no_spontaneous_greats():
    h = human(rest_every_plays=0, long_rest_minutes=0)
    chart = song()
    for _ in range(20):
        h.completed(chart)
    assert h.rest_seconds() == (0, "")
    plan = Planner(Geometry(1280, 720), TouchParams(jitter_ms=20), seed=1, humanizer=h).plan(chart)
    for n, g in zip(chart.notes, plan.gestures):
        assert abs(g.start - n.time_ms) <= 23.00001
        assert 0 < g.end - g.start <= 50.00001
    assert not plan.dropped


@pytest.mark.parametrize("seed", range(5))
def test_seeded_behavior_is_reproducible(seed):
    chart = song()
    params = TouchParams(jitter_ms=16, position_jitter=1, great_ratio=0.05)
    plans = [Planner(Geometry(1280, 720), params, seed=seed, humanizer=human(seed)).plan(chart) for _ in range(2)]
    assert plans[0].events == plans[1].events
