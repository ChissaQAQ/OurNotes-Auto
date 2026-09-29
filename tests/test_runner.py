"""用假对象验证全自动循环的编排逻辑。"""

import pytest

from ournotes_auto.charts.catalog import Catalog, Song
from ournotes_auto.charts.model import Chart
from ournotes_auto.config import Config
from ournotes_auto.player.executor import ExecStats
from ournotes_auto.player.guard import PlayInterrupted
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


class FakeClient:
    def chart(self, mid, diff, title=""):
        return Chart(mid, diff, title)


class FakeSession:
    def __init__(self, fail=False, max_late=0.2, interrupt=False, stalls=()):
        self.fail, self.max_late, self.interrupt = fail, max_late, interrupt
        self.stalls = list(stalls)
        self.learned_offset_ms = 0.0

    def play(self, chart, stop=None):
        sync = SyncResult(1.0, 0.0, [], -40.0, 0.835, 1.0, 1.0, 190.0, not self.fail)
        if self.fail:
            raise SyncFailed("假失败", sync)
        if self.interrupt:
            raise PlayInterrupted("演奏中途离开了演奏画面")
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
    # 失败的局也要等结算并离开，才能继续下一局
    assert nav.calls.count("result") == 3 and nav.calls.count("leave") == 3
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


def test_until_lb_empty_needs_lb_cost(tmp_path):
    import pytest

    cfg = Config()
    cfg.loop.until_lb_empty = True
    with pytest.raises(ValueError):
        Runner(cfg, FakeNav(), FakeSession(), FakeClient(), Catalog({}), RecordStore(tmp_path))


def test_stop_during_navigation_is_not_a_failure(tmp_path):
    from ournotes_auto.runner import NavigationError

    runner, nav, store = make(tmp_path)

    def stopped(lb_short="zero"):
        runner.stop.set()
        raise NavigationError("已停止")

    nav.start_live = stopped
    stats = runner.run()
    assert stats.plays == 0 and stats.failures == 0


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
