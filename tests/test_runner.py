"""用假对象验证全自动循环的编排逻辑。"""

import threading

import pytest

from ournotes_auto.charts.catalog import Catalog, Song
from ournotes_auto.charts.model import Chart
from ournotes_auto.config import Config
from ournotes_auto.player.executor import ExecStats
from ournotes_auto.player.guard import LifeDepleted, PlayInterrupted
from ournotes_auto.player.session import PlayOutcome, SyncFailed
from ournotes_auto.player.sync import SyncResult
from ournotes_auto.records import RecordStore
from ournotes_auto.result_reader import ResultCounts
from ournotes_auto.runner import Runner, SongLabel


class FakeNav:
    def __init__(self, title="诗超绊", diff="expert"):
        self.title, self.diff = title, diff
        self.calls = []

    def ensure_band_confirm(self, difficulty=None):
        self.calls.append(f"ensure:{difficulty}")

    def selected_song(self):
        return SongLabel(self.title, self.diff)

    def choose_next_song(self, mode):
        self.calls.append(f"next:{mode}")

    def clear_status_filter(self):
        self.calls.append("clear_status")

    def start_live(self, lb_short="zero"):
        self.calls.append(f"start:{lb_short}")

    def retry_live(self):
        self.calls.append("retry")

    def lb_status(self, check=False):
        self.calls.append("lb_check" if check else "lb")
        return None, None

    def read_result(self, expected_total=None):
        self.calls.append("result")
        return ResultCounts(
            counts={"perfect": 1000, "great": 27, "good": 0, "bad": 0, "miss": 0},
            fast={"perfect": 300, "great": 20, "good": 0, "bad": 0, "miss": 0},
            slow={"perfect": 700, "great": 7, "good": 0, "bad": 0, "miss": 0},
            score=123,
            combo=1027,
        )

    def leave_result(self):
        self.calls.append("leave")

    def run_daily(self, jobs):
        self.calls.append(f"daily:{','.join(jobs)}")
        return []

    def relogin(self):
        self.calls.append("relogin")


class FakeClient:
    def chart(self, mid, diff, title=""):
        return Chart(mid, diff, title)


class FakeSession:
    def __init__(self, fail=False, max_late=0.2, interrupt=False, stalls=(), fails=0, life_zeros=0):
        """``fail``：同步总是失败；``fails``：前几次同步失败，之后成功；``life_zeros``：前几次演奏中生命值归零。"""
        self.fail, self.max_late, self.interrupt = fail, max_late, interrupt
        self.fails = fails
        self.life_zeros = life_zeros
        self.stalls = list(stalls)
        self.learned_offset_ms = 0.0
        self.retries: list[bool] = []  # 每次 play 的 retry 参数

    def play(self, chart, stop=None, retry=False):
        self.retries.append(retry)
        failed = self.fail or self.fails > 0
        self.fails -= 1
        sync = SyncResult(1.0, 0.0, [], -40.0, 0.835, 1.0, 1.0, 190.0, not failed)
        if failed:
            raise SyncFailed("假失败", sync)
        if self.interrupt:
            raise PlayInterrupted("演奏中途离开了演奏画面")
        self.life_zeros -= 1
        if self.life_zeros >= 0:
            raise LifeDepleted("演奏中生命值降到 0（整体对不上了）")
        stats = ExecStats(sent=10, lateness_ms=[self.max_late] * 5)
        return PlayOutcome(chart, None, sync, stats, 3.0 + self.learned_offset_ms, self.stalls)


def make(tmp_path, mode="current", **kw):
    cfg = Config()
    cfg.loop.max_plays = 2
    cfg.loop.song_mode = mode
    catalog = Catalog({100008: Song(100008, ["詩超絆", "Utachou", "詩超絆", "诗超绊"])})
    nav = FakeNav()
    store = RecordStore(tmp_path)
    return Runner(cfg, nav, FakeSession(**kw), FakeClient(), catalog, store), nav, store


def test_runner_plays_and_autotunes(tmp_path):
    runner, nav, store = make(tmp_path)
    stats = runner.run()
    assert stats.plays == 2 and stats.full_combo == 2 and stats.all_perfect == 0
    assert nav.calls[:5] == ["ensure:expert", "start:zero", "result", "leave", "ensure:expert"]
    assert nav.calls[-1] == "leave"  # 打满局数后不再换歌
    hist = store.history()
    assert len(hist) == 2 and hist[0].fast == 320 and hist[0].slow == 707
    # SLOW 多 → offset 提前（负方向），第二局使用第一局学到的值
    assert store.learned_offset_ms < 0
    assert hist[0].offset_ms == 3.0 and hist[1].offset_ms < 3.0
    assert hist[0].stalls is None


def test_runner_records_stalls(tmp_path):
    runner, _, store = make(tmp_path, stalls=[(141071.4, 141691.2)])
    runner.run()
    assert store.history()[0].stalls == [[141071, 141691]]


def test_runner_stops_after_consecutive_failures(tmp_path):
    runner, nav, store = make(tmp_path, fail=True)
    runner.cfg.loop.max_plays = 0
    runner.cfg.loop.max_failures = 3
    stats = runner.run()
    assert stats.failures == 3 and stats.plays == 0
    # 每局先重试 sync_retries 次；失败的局也要等结算并离开，才能继续下一局
    assert nav.calls.count("retry") == 3 * runner.cfg.loop.sync_retries
    assert nav.calls.count("result") == 3 and nav.calls.count("leave") == 3
    assert store.history() == []


def test_sync_failure_retries_same_live(tmp_path):
    """首音符同步失败：暂停后从头重试这一局，不干等歌曲放完（消耗的 LB 不浪费）。"""
    runner, nav, store = make(tmp_path, fails=1)
    runner.cfg.loop.max_plays = 1
    stats = runner.run()
    assert stats.plays == 1 and stats.failures == 0
    assert nav.calls == ["ensure:expert", "start:zero", "retry", "result", "leave"]
    assert len(store.history()) == 1
    assert runner.session.retries == [False, True]  # 重试后歌曲立即开始，同步要沿用上一次的基线


def test_life_zero_retries_same_live(tmp_path):
    """演奏中生命值归零（整体对不上了）：和同步失败一样暂停从头重试，重试次数用完了等结算、不记录。"""
    runner, nav, store = make(tmp_path, life_zeros=1)
    runner.cfg.loop.max_plays = 1
    stats = runner.run()
    assert stats.plays == 1 and stats.failures == 0
    assert nav.calls == ["ensure:expert", "start:zero", "retry", "result", "leave"]
    assert len(store.history()) == 1

    runner, nav, store = make(tmp_path / "all", life_zeros=10)
    runner.cfg.loop.max_plays = 0
    runner.cfg.loop.max_failures = 1
    stats = runner.run()
    assert stats.plays == 0 and stats.failures == 1
    assert nav.calls.count("retry") == runner.cfg.loop.sync_retries and nav.calls.count("result") == 1
    assert store.history() == []


def test_no_sync_retries(tmp_path):
    runner, nav, store = make(tmp_path, fails=1)
    runner.cfg.loop.max_plays = 0
    runner.cfg.loop.max_failures = 1
    runner.cfg.loop.sync_retries = 0
    stats = runner.run()
    assert stats.plays == 0 and stats.failures == 1
    assert "retry" not in nav.calls and nav.calls.count("result") == 1


def test_failed_retry_waits_out_song(tmp_path):
    """重试不成（如歌已经放完、暂停没生效）：和原来一样等结算，本局算失败。"""
    from ournotes_auto.runner import NavigationError

    runner, nav, store = make(tmp_path, fails=1)
    runner.cfg.loop.max_plays = 0
    runner.cfg.loop.max_failures = 1

    def broken():
        nav.calls.append("retry")
        raise NavigationError("没有在演奏画面上")

    nav.retry_live = broken
    stats = runner.run()
    assert stats.plays == 0 and stats.failures == 1
    assert nav.calls == ["ensure:expert", "start:zero", "retry", "result", "leave"]
    assert store.history() == []


def test_interrupted_play_leaves_recovery_to_navigation(tmp_path):
    """演奏中途离开了演奏画面（暂停、闪退）：不等结算，下一局开头的导航从当前画面恢复。"""
    runner, nav, store = make(tmp_path, interrupt=True)
    runner.cfg.loop.max_plays = 0
    runner.cfg.loop.max_failures = 2
    stats = runner.run()
    assert stats.failures == 2 and stats.plays == 0
    assert "result" not in nav.calls and nav.calls.count("ensure:expert") == 2
    assert store.history() == []


def test_unreliable_execution_skips_autotune(tmp_path):
    runner, nav, store = make(tmp_path, max_late=9.0)
    runner.cfg.loop.max_plays = 1
    runner.run()
    assert len(store.history()) == 1
    assert store.learned_offset_ms == 0.0


def test_full_combo_fixes_misread_combo(tmp_path):
    runner, nav, store = make(tmp_path)
    runner.cfg.loop.max_plays = 1
    read = nav.read_result
    nav.read_result = lambda expected_total=None: ResultCounts(**{**read().__dict__, "combo": 27})  # 1027 少读了一位
    runner.run()
    assert store.history()[0].max_combo == 1027


@pytest.mark.parametrize("combo, kept", [(159, None), (1159, 1159), (2000, None), (None, None)])
def test_impossible_combo_is_dropped(tmp_path, combo, kept):
    """断了 8 次、共 1777 个判定时最大连击至少 197：1159 少读开头的 1 成了 159，不记录。"""
    runner, nav, store = make(tmp_path)
    runner.cfg.loop.max_plays = 1
    counts = {"perfect": 1769, "great": 0, "good": 0, "bad": 8, "miss": 0}
    read = nav.read_result
    nav.read_result = lambda expected_total=None: ResultCounts(**{**read().__dict__, "counts": counts, "combo": combo})
    runner.run()
    assert store.history()[0].max_combo == kept


def test_failed_song_change_keeps_result(tmp_path):
    from ournotes_auto.runner import NavigationError

    runner, nav, store = make(tmp_path, mode="random")

    def broken(mode):
        nav.calls.append("next")
        raise NavigationError("假换歌失败")

    nav.choose_next_song = broken
    stats = runner.run()
    assert stats.plays == 2 and stats.failures == 0
    assert len(store.history()) == 2 and nav.calls.count("next") == 1


def test_song_change_keeps_failing(tmp_path):
    from ournotes_auto.runner import NavigationError

    runner, nav, store = make(tmp_path, mode="random")
    runner.cfg.loop.max_plays = 0
    runner.cfg.loop.max_failures = 3

    def broken(mode):
        raise NavigationError("假换歌失败")

    nav.choose_next_song = broken
    stats = runner.run()
    # 第一局不换歌；之后连续 3 次换歌失败就停，期间照常打当前曲目
    assert stats.plays == 3 and stats.failures == 0


def test_random_mode_changes_song_between_plays(tmp_path):
    runner, nav, store = make(tmp_path, mode="random")
    runner.cfg.loop.max_plays = 3
    runner.run()
    assert [c for c in nav.calls if c.startswith(("ensure", "next"))] == [
        "ensure:expert",
        "next:random",
        "ensure:expert",
        "next:random",
        "ensure:expert",
    ]


def test_until_lb_empty(tmp_path):
    from ournotes_auto.runner import LbExhausted

    runner, nav, store = make(tmp_path)
    runner.cfg.loop.max_plays = 0
    runner.cfg.loop.until_lb_empty = True
    runner.cfg.game.lb_cost = 3
    starts = []

    def start_live(lb_short="zero"):
        starts.append(lb_short)
        if len(starts) == 3:
            raise LbExhausted("LB 已用完")

    nav.start_live = start_live
    stats = runner.run()
    assert stats.plays == 2 and stats.failures == 0
    assert starts == ["stop"] * 3


@pytest.mark.parametrize("flag", ["until_lb_empty", "wait_lb"])
def test_until_lb_empty_needs_lb_cost(tmp_path, flag):
    cfg = Config()
    setattr(cfg.loop, flag, True)
    with pytest.raises(ValueError):
        Runner(cfg, FakeNav(), FakeSession(), FakeClient(), Catalog({}), RecordStore(tmp_path))


class FakeStop(threading.Event):
    """记下挂机每次睡多久，不真的睡；``now`` 是睡过的总时长（给假时钟用）。"""

    def __init__(self):
        super().__init__()
        self.waits = []
        self.now = 0.0

    def wait(self, timeout=None):
        self.waits.append(timeout)
        self.now += timeout or 0
        return self.is_set()


def idle(tmp_path, statuses, exhausted_at=(2,)):
    """挂机：第 ``exhausted_at`` 次 LIVE START 时 LB 用完，之后 ``lb_status`` 依次读到 ``statuses``。"""
    from ournotes_auto.runner import LbExhausted

    runner, nav, store = make(tmp_path, mode="random")
    runner.cfg.loop.max_plays = 3
    runner.cfg.loop.wait_lb = True
    runner.cfg.game.lb_cost = 3
    runner.stop = FakeStop()
    statuses = list(statuses)
    starts = []

    def start_live(lb_short="zero"):
        starts.append(lb_short)
        nav.calls.append("start")
        if len(starts) in exhausted_at:
            raise LbExhausted("LB 已用完")

    def lb_status(check=False):
        nav.calls.append("lb_check" if check else "lb")
        status = statuses.pop(0)
        if isinstance(status, Exception):
            raise status
        return status

    nav.start_live, nav.lb_status = start_live, lb_status
    return runner, nav, starts, statuses


def test_wait_lb_resumes_same_song(tmp_path):
    """LB 用完后停在乐队确认页，按恢复倒计时睡到恢复够每局消耗数（弹窗核对过）再接着打这首。"""
    from ournotes_auto.runner import LB_POLL_MARGIN_S, LB_POLL_S

    seq = [(0, 120), (None, None), (3, 1700), (2, None), (3, 1500), (3, None)]
    runner, nav, starts, statuses = idle(tmp_path, seq)
    stats = runner.run()
    assert stats.plays == 3 and stats.failures == 0 and not statuses
    assert starts == ["stop"] * 4
    assert runner.stop.waits == [120 + LB_POLL_MARGIN_S, LB_POLL_S, LB_POLL_S]
    # 用完时不换歌：从 LB 用完到恢复后再开始之间只有等待
    waiting = ["ensure:expert", "lb"] * 3 + ["lb_check", "ensure:expert", "lb", "lb_check"]
    expected = ["ensure:expert", "start", "next:random", "ensure:expert", "start", *waiting]
    expected += ["ensure:expert", "start", "next:random", "ensure:expert", "start"]
    calls = [c for c in nav.calls if c not in ("result", "leave", "clear_status")]
    assert calls[: len(expected)] == expected


def test_stop_while_waiting_lb_is_not_a_failure(tmp_path):
    runner, nav, starts, _ = idle(tmp_path, [(1, 600)])

    def wait(timeout=None):
        runner.stop.waits.append(timeout)
        runner.stop.set()
        return True

    runner.stop.wait = wait
    stats = runner.run()
    assert stats.plays == 1 and stats.failures == 0 and len(starts) == 2


def test_wait_lb_keeps_waiting_after_navigation_error(tmp_path):
    """等待中导航失败（如日期变更后重新登录失败）算一次失败，下一轮接着等，不换歌。"""
    from ournotes_auto.runner import NavigationError

    runner, nav, starts, statuses = idle(tmp_path, [NavigationError("假导航失败"), (3, None), (3, None)])
    stats = runner.run()
    assert stats.plays == 3 and stats.failures == 1 and not statuses
    assert nav.calls.count("next:random") == 2
    assert runner.stop.waits == []


def test_stop_during_navigation_is_not_a_failure(tmp_path):
    from ournotes_auto.runner import NavigationError

    runner, nav, store = make(tmp_path)

    def stopped(lb_short="zero"):
        runner.stop.set()
        raise NavigationError("已停止")

    nav.start_live = stopped
    stats = runner.run()
    assert stats.plays == 0 and stats.failures == 0


def test_idle_claims_studio_on_schedule(tmp_path):
    """定时收获：开始时先领一次（在换歌前）；等 LB 时到了领取时间也醒来去领，领完回乐队确认页接着等。"""
    seq = [(0, 1000), (0, 400), (3, None), (3, None)]
    runner, nav, starts, statuses = idle(tmp_path, seq)
    runner.cfg.loop.studio_claim_hours = 0.25
    runner.clock = lambda: runner.stop.now
    stats = runner.run()
    assert stats.plays == 3 and stats.failures == 0 and not statuses
    assert runner.stop.waits == [600, 300]  # 第二次本该睡到 LB 恢复（405s），先醒来领
    waiting = ["ensure:expert", "lb", "ensure:expert", "lb", "daily:studio", "ensure:expert", "lb", "lb_check"]
    expected = ["daily:studio", "ensure:expert", "start", "next:random", "ensure:expert", "start", *waiting]
    expected += ["ensure:expert", "start", "next:random", "ensure:expert", "start"]
    calls = [c for c in nav.calls if c not in ("result", "leave", "clear_status")]
    assert calls[: len(expected)] == expected


def test_studio_claim_failure_keeps_playing(tmp_path):
    """领不成（回不到主界面、领取出错）只记录，过一个间隔再领，不算演奏失败。"""
    from ournotes_auto.runner import NavigationError

    runner, nav, store = make(tmp_path)
    runner.cfg.loop.max_plays = 3
    runner.cfg.loop.studio_claim_hours = 1
    now = [0.0]
    runner.clock = lambda: now[0]
    outcomes = [NavigationError("回不到主界面"), ["录音室练习"], []]

    def run_daily(jobs):
        nav.calls.append("daily")
        out = outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    def leave_result():
        nav.calls.append("leave")
        now[0] += 3600  # 每局之间过了一个间隔

    nav.run_daily, nav.leave_result = run_daily, leave_result
    stats = runner.run()
    assert stats.plays == 3 and stats.failures == 0 and not outcomes
    assert [c for c in nav.calls if c in ("daily", "leave")] == ["daily", "leave"] * 3


@pytest.mark.parametrize("frozen", [False, True])
def test_studio_claim_stopped_or_frozen(tmp_path, frozen):
    """领取时被停止：不算失败；画面卡住不动：照常停止。都不再打歌。"""
    from ournotes_auto.runner import NavigationError, ScreenFrozen

    runner, nav, store = make(tmp_path)
    runner.cfg.loop.studio_claim_hours = 4

    def run_daily(jobs):
        if frozen:
            raise ScreenFrozen("画面 60s 没有变化")
        runner.stop.set()
        raise NavigationError("已停止")

    nav.run_daily = run_daily
    stats = runner.run()
    assert stats.plays == 0 and stats.failures == int(frozen)
    assert not any(c.startswith("start") for c in nav.calls)


class ApSource:
    """记录 Runner 对选曲策略的回调。"""

    difficulty = "hard"

    def __init__(self, songs=2):
        self.left = songs
        self.log = []

    def advance(self, nav, first):
        self.log.append(("advance", first))
        self.left -= 1
        return self.left >= 0

    def done(self, song, difficulty, result, playable=True):
        self.log.append(("done", song.music_id, difficulty, result is not None, playable))

    def close(self, nav):
        self.log.append(("close",))


def test_runner_reports_to_source(tmp_path):
    runner, nav, store = make(tmp_path)
    runner.cfg.loop.max_plays = 0
    nav.diff = "hard"
    runner.source = src = ApSource(songs=2)
    stats = runner.run()
    assert stats.plays == 2
    assert src.log == [
        ("advance", True),
        ("done", 100008, "hard", True, True),
        ("advance", False),
        ("done", 100008, "hard", True, True),
        ("advance", False),
        ("close",),
    ]
    assert nav.calls[0] == "ensure:hard"


def test_runner_marks_missing_chart_unplayable(tmp_path):
    from ournotes_auto.charts.bdon import ChartNotFound

    runner, nav, store = make(tmp_path)
    runner.cfg.loop.max_plays = 0
    runner.cfg.loop.max_failures = 1
    runner.source = src = ApSource(songs=5)

    def missing(mid, diff, title=""):
        raise ChartNotFound(f"{mid}_{diff}")

    runner.client.chart = missing
    stats = runner.run()
    assert stats.failures == 1
    assert ("done", 100008, "expert", False, False) in src.log
    assert src.log[-1] == ("close",)


def test_runner_skips_close_when_stopped(tmp_path):
    from ournotes_auto.runner import NavigationError

    runner, nav, store = make(tmp_path)
    runner.source = src = ApSource()

    def stopped(lb_short="zero"):
        runner.stop.set()
        raise NavigationError("已停止")

    nav.start_live = stopped
    runner.run()
    assert ("close",) not in src.log


def test_failed_leave_keeps_result(tmp_path):
    """离开结算页失败（如没见过的结算页）：这一局已经记下，只算导航失败。"""
    from ournotes_auto.runner import NavigationError

    runner, nav, store = make(tmp_path)
    runner.cfg.loop.max_plays = 1
    runner.source = src = ApSource()

    def broken():
        raise NavigationError("未能离开结算页")

    nav.leave_result = broken
    stats = runner.run()
    assert stats.plays == 1 and stats.full_combo == 1 and stats.failures == 1
    assert len(store.history()) == 1
    assert ("done", 100008, "expert", True, True) in src.log


@pytest.mark.parametrize("where", ["leave", "advance"])
def test_frozen_screen_stops_at_once(tmp_path, where):
    """画面卡住不动：不再重试，也不去恢复选曲页的设置。"""
    from ournotes_auto.runner import ScreenFrozen

    runner, nav, store = make(tmp_path)
    runner.cfg.loop.max_plays = 0
    runner.source = src = ApSource(songs=5)

    def frozen(*a):
        raise ScreenFrozen("画面 60s 没有变化")

    if where == "leave":
        nav.leave_result = frozen
    else:
        advance = src.advance
        src.advance = lambda nav, first: advance(nav, first) if first else frozen()
    stats = runner.run()
    assert stats.plays == 1 and stats.failures == 1
    assert len(store.history()) == 1
    assert ("close",) not in src.log
    assert [e for e in src.log if e[0] == "advance"] == [("advance", True)]


@pytest.mark.parametrize("where", ["start", "retry", "advance", "studio"])
def test_maintenance_fails_task_at_once(tmp_path, where):
    """服务器维护：不是挂机时任务直接失败（打过几局也算失败），不重试也不去恢复选曲页的设置。"""
    from ournotes_auto.runner import ServerMaintenance

    runner, nav, store = make(tmp_path, fails=1 if where == "retry" else 0)
    runner.cfg.loop.max_plays = 0
    runner.cfg.loop.studio_claim_hours = 4 if where == "studio" else 0
    runner.source = src = ApSource(songs=5)

    def maintenance(*a):
        raise ServerMaintenance("服务器维护中")

    if where == "start":
        nav.start_live = maintenance
    elif where == "retry":
        nav.retry_live = maintenance
    elif where == "advance":
        advance = src.advance
        src.advance = lambda nav, first: advance(nav, first) if first else maintenance()
    else:
        nav.run_daily = maintenance
    stats = runner.run()
    assert stats.maintenance and stats.failures == 1
    assert stats.plays == (1 if where == "advance" else 0)
    assert ("close",) not in src.log
    assert "relogin" not in nav.calls


def test_idle_waits_out_maintenance(tmp_path):
    """挂机遇到服务器维护：每 10 分钟回到标题画面重新登录看一次，开服后接着挂（这局不算失败）。"""
    from ournotes_auto.runner import MAINTENANCE_POLL_S, ServerMaintenance

    runner, nav, starts, statuses = idle(tmp_path, [], exhausted_at=())
    starts_live = nav.start_live
    logins = [ServerMaintenance("服务器维护中"), ServerMaintenance("服务器维护中"), None]

    def start_live(lb_short="zero"):
        starts_live(lb_short)
        if len(starts) == 2:
            raise ServerMaintenance("服务器维护中")

    def relogin():
        nav.calls.append("relogin")
        out = logins.pop(0)
        if out is not None:
            raise out

    nav.start_live, nav.relogin = start_live, relogin
    stats = runner.run()
    assert stats.plays == 3 and stats.failures == 0 and not stats.maintenance and not logins
    assert runner.stop.waits == [MAINTENANCE_POLL_S] * 3
    calls = [c for c in nav.calls if c not in ("result", "leave", "clear_status")]
    expected = ["ensure:expert", "start", "next:random", "ensure:expert", "start", "relogin", "relogin", "relogin"]
    assert calls[: len(expected) + 2] == [*expected, "next:random", "ensure:expert"]


def test_idle_maintenance_while_waiting_lb(tmp_path):
    """等 LB 恢复时遇到维护：开服后接着等 LB、打原来那首（不换歌）。"""
    from ournotes_auto.runner import MAINTENANCE_POLL_S, ServerMaintenance

    runner, nav, starts, statuses = idle(tmp_path, [ServerMaintenance("服务器维护中"), (3, None), (3, None)])
    stats = runner.run()
    assert stats.plays == 3 and stats.failures == 0 and not statuses
    assert runner.stop.waits == [MAINTENANCE_POLL_S]
    assert nav.calls.count("relogin") == 1 and nav.calls.count("next:random") == 2


@pytest.mark.parametrize("error", ["stop", "frozen", "update", "other"])
def test_idle_maintenance_wait_ends(tmp_path, error):
    """等开服时被停止：不算失败；重新登录时画面卡住、要求更新游戏：照常停止；遇到别的问题：交给下一局的导航。"""
    from ournotes_auto.runner import GameUpdateRequired, NavigationError, ScreenFrozen, ServerMaintenance

    runner, nav, starts, statuses = idle(tmp_path, [], exhausted_at=())
    runner.cfg.loop.max_plays = 2
    starts_live = nav.start_live

    def start_live(lb_short="zero"):
        starts_live(lb_short)
        if len(starts) == 1:
            if error == "stop":
                runner.stop.set()
            raise ServerMaintenance("服务器维护中")

    def relogin():
        nav.calls.append("relogin")
        if error == "frozen":
            raise ScreenFrozen("画面 60s 没有变化")
        if error == "update":
            raise GameUpdateRequired("游戏有新版本，请先手动更新游戏")
        raise NavigationError("180s 内未能进入游戏")

    nav.start_live, nav.relogin = start_live, relogin
    stats = runner.run()
    assert not stats.maintenance
    if error == "stop":
        assert stats.plays == 0 and stats.failures == 0 and "relogin" not in nav.calls
    elif error in ("frozen", "update"):
        assert stats.plays == 0 and stats.failures == 1
    else:
        assert stats.plays == 2 and stats.failures == 0 and nav.calls.count("relogin") == 1
