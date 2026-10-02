"""选曲策略：AP 补完的抽歌、跳过、换难度与恢复设置。"""

import pytest

from ournotes_auto.charts.catalog import Catalog, Song
from ournotes_auto.config import Config
from ournotes_auto.nav.song_select import SongPick
from ournotes_auto.records import PlayResult
from ournotes_auto.sources import (
    ApComplete,
    ApFirst,
    ChallengeApFirst,
    ChallengeRotate,
    CurrentSong,
    RandomSong,
    SongList,
    make_source,
    parse_difficulties,
    parse_song_list,
)


class PickNav:
    """按难度预设随机选曲依次抽到的歌；抽完了就是空列表。"""

    def __init__(self, picks, category="原创"):
        self.picks = {d: list(p) for d, p in picks.items()}
        self.category = category
        self.diff = None
        self.calls = []

    def set_song_category(self, name):
        self.calls.append(f"category:{name}")
        before, self.category = self.category, name
        return before

    def set_song_filter(self, difficulty=None, status=None, reset=False):
        self.calls.append(f"filter:{difficulty}:{status}:{reset}")
        if difficulty is not None:
            self.diff = difficulty

    def random_song(self):
        self.calls.append("random")
        queue = self.picks.get(self.diff, [])
        return queue.pop(0) if queue else SongPick(None, empty=True)


def song(mid):
    return Song(mid, [str(mid)])


def result(mid, diff, great):
    return PlayResult(music_id=mid, difficulty=diff, perfect=100, great=great, good=0, bad=0, miss=0, max_combo=100 + great)


def test_ap_filters_each_difficulty_and_restores():
    nav = PickNav({"expert": [SongPick(1)], "hard": [SongPick(2)]})
    src = ApComplete(["expert", "hard"])
    assert src.advance(nav, True) and src.difficulty == "expert"
    assert nav.calls == ["category:全部", "filter:expert:not_ap:True", "random"]
    src.done(song(1), "expert", result(1, "expert", 0))  # AP
    assert src.advance(nav, False) and src.difficulty == "hard"
    assert nav.calls[3:] == ["random", "filter:hard:not_ap:False", "random"]  # 只在第一个难度前重置
    src.done(song(2), "hard", result(2, "hard", 0))
    assert not src.advance(nav, False)
    assert nav.calls.count("category:全部") == 1
    nav.calls.clear()
    src.close(nav)
    assert nav.calls == ["filter:None:any:False", "category:原创"]


def test_ap_rerolls_rejected_picks():
    locked = SongPick(None, locked=True)
    unknown = SongPick(None)
    nav = PickNav({"expert": [locked, unknown, SongPick(5)]})
    src = ApComplete(["expert"])
    assert src.advance(nav, True)
    assert nav.calls.count("random") == 3


def test_ap_gives_up_after_max_attempts():
    nav = PickNav({"expert": [SongPick(7), SongPick(7), SongPick(7), SongPick(8)]})
    src = ApComplete(["expert"], max_attempts=2)
    for _ in range(2):
        assert src.advance(nav, False)
        src.done(song(7), "expert", result(7, "expert", 3))
    assert (7, "expert") in src.skip
    # 第三次抽到 7 被跳过，重抽到 8
    assert src.advance(nav, False)
    assert nav.calls.count("random") == 4


def test_ap_failed_play_counts_as_attempt():
    src = ApComplete(["expert"], max_attempts=2)
    src.done(song(7), "expert", None)
    src.done(song(7), "expert", None)
    assert (7, "expert") in src.skip


def test_ap_skips_unplayable_immediately():
    src = ApComplete(["expert"])
    src.done(song(9), "expert", None, playable=False)
    assert (9, "expert") in src.skip


def test_ap_moves_on_after_too_many_rerolls():
    nav = PickNav({"expert": [SongPick(None, locked=True)] * 5, "hard": [SongPick(3)]})
    src = ApComplete(["expert", "hard"], max_rerolls=3)
    assert src.advance(nav, True) and src.difficulty == "hard"
    assert nav.calls.count("random") == 4


def test_ap_no_random_candidates():
    # 随机选曲没得抽时选中的还是原来那首：能打就打
    nav = PickNav({"expert": [SongPick(4, only=True)]})
    assert ApComplete(["expert"]).advance(nav, True)
    # 不能打（已放弃、未解锁）就直接换难度，不再重抽
    nav = PickNav({"expert": [SongPick(4, only=True)] * 5, "hard": [SongPick(None, locked=True, only=True)] * 5})
    src = ApComplete(["expert", "hard"])
    src.skip.add((4, "expert"))
    assert not src.advance(nav, True)
    assert nav.calls.count("random") == 2


def test_ap_close_keeps_all_category_and_untouched():
    nav = PickNav({}, category="全部")
    src = ApComplete(["easy"])
    src.close(nav)  # 还没动过游戏设置
    assert nav.calls == []
    assert not src.advance(nav, True)
    nav.calls.clear()
    src.close(nav)
    assert nav.calls == ["filter:None:any:False"]


def test_ap_validates_arguments():
    with pytest.raises(ValueError):
        ApComplete([])
    with pytest.raises(ValueError):
        ApComplete(["master"])
    with pytest.raises(ValueError):
        ApComplete(["expert"], max_attempts=0)


def test_random_clears_status_filter_once():
    calls = []

    class Nav:
        def clear_status_filter(self):
            calls.append("clear")

        def choose_next_song(self, mode):
            calls.append(mode)

    src = RandomSong("expert")
    for first in (True, False, False):
        assert src.advance(Nav(), first)
    assert calls == ["clear", "random", "random"]


class ChooseNav(PickNav):
    def choose_next_song(self, mode):
        self.calls.append(f"choose:{mode}")


def test_ap_first_falls_back_to_random():
    nav = ChooseNav({"hard": [SongPick(4)]})
    src = ApFirst(["hard"], max_attempts=2)
    assert src.advance(nav, True) and src.difficulty == "hard"
    assert nav.calls == ["category:全部", "filter:hard:not_ap:True", "random"]
    src.done(song(4), "hard", result(4, "hard", 0))  # AP 后列表空了
    nav.calls.clear()
    assert src.advance(nav, False) and src.difficulty == "hard"
    assert nav.calls == ["random", "filter:None:any:False", "choose:random"]
    nav.calls.clear()
    assert src.advance(nav, False)
    assert nav.calls == ["choose:random"]  # 之后一直随机，不再筛选
    nav.calls.clear()
    src.close(nav)
    assert nav.calls == ["category:原创"]  # 游玩状况已经改回，只恢复分类


def test_ap_first_goes_through_difficulties_then_random_at_fallback():
    nav = ChooseNav({"expert": [SongPick(1)], "normal": [SongPick(2)]})
    src = ApFirst(["expert", "hard", "normal", "easy"], fallback="expert")
    assert src.advance(nav, True) and src.difficulty == "expert"
    src.done(song(1), "expert", result(1, "expert", 0))
    nav.calls.clear()
    assert src.advance(nav, False) and src.difficulty == "normal"  # HARD 已经全部 AP
    assert nav.calls == ["random", "filter:hard:not_ap:False", "random", "filter:normal:not_ap:False", "random"]
    src.done(song(2), "normal", result(2, "normal", 0))
    nav.calls.clear()
    assert src.advance(nav, False) and src.difficulty == "expert"  # 都补完了，按 EXPERT 随机
    assert nav.calls == ["random", "filter:easy:not_ap:False", "random", "filter:None:any:False", "choose:random"]
    assert nav.calls.count("category:全部") == 0


def test_ap_first_close_before_fallback():
    nav = ChooseNav({"expert": [SongPick(1)]}, category="全部")
    src = ApFirst(["expert"])
    assert src.advance(nav, True)
    nav.calls.clear()
    src.close(nav)
    assert nav.calls == ["filter:None:any:False"]


def test_parse_difficulties():
    assert parse_difficulties(" Expert,hard,,expert ") == ["expert", "hard"]
    with pytest.raises(ValueError, match="master"):
        parse_difficulties("expert,master")


def test_make_source():
    cfg = Config()
    assert isinstance(make_source(cfg), CurrentSong)
    cfg.loop.song_mode = "random"
    assert isinstance(make_source(cfg), RandomSong)
    cfg.loop.song_mode = "ap"
    cfg.loop.ap_difficulties = "hard,easy"
    cfg.loop.ap_max_attempts = 5
    src = make_source(cfg)
    assert isinstance(src, ApComplete) and src.difficulties == ["hard", "easy"] and src.max_attempts == 5
    cfg.loop.song_mode = "ap_first"
    cfg.game.difficulty = "normal"
    src = make_source(cfg)
    assert isinstance(src, ApFirst) and src.difficulties == ["normal"] and src.max_attempts == 5
    cfg.loop.ap_first_difficulties = "expert,hard,normal,easy"
    src = make_source(cfg)
    assert src.difficulties == ["expert", "hard", "normal", "easy"] and src.fallback == "normal"
    cfg.loop.song_mode = "list"
    with pytest.raises(ValueError, match="曲目目录"):
        make_source(cfg)
    cfg.loop.song_list = "100010, Symbol I@expert"
    src = make_source(cfg, CATALOG)
    assert isinstance(src, SongList) and src.entries == [(100010, "normal"), (100033, "expert")]
    cfg.loop.song_mode = "playlist"
    with pytest.raises(ValueError, match="选曲模式"):
        make_source(cfg, CATALOG)
    cfg.loop.song_mode = "rotate"
    with pytest.raises(ValueError, match="挑战演出"):
        make_source(cfg)


def test_make_source_challenge():
    cfg = Config()
    cfg.loop.challenge = True
    assert isinstance(make_source(cfg), CurrentSong)
    cfg.loop.song_mode = "rotate"
    assert isinstance(make_source(cfg), ChallengeRotate)
    cfg.loop.song_mode = "ap_first"
    cfg.game.difficulty = "hard"
    src = make_source(cfg)
    assert isinstance(src, ChallengeApFirst) and src.difficulties == ["hard"] and src.fallback == "hard"
    cfg.loop.ap_first_difficulties = "expert,hard"
    cfg.loop.ap_max_attempts = 2
    src = make_source(cfg)
    assert src.difficulties == ["expert", "hard"] and src.fallback == "hard" and src.max_attempts == 2
    for mode in ("random", "ap", "list"):
        cfg.loop.song_mode = mode
        with pytest.raises(ValueError, match="挑战演出"):
            make_source(cfg, CATALOG)


class RotateNav:
    def __init__(self):
        self.calls = 0

    def next_challenge_song(self):
        self.calls += 1


def test_challenge_rotate_changes_song_after_first():
    nav = RotateNav()
    src = ChallengeRotate("expert")
    assert src.advance(nav, True) and nav.calls == 0  # 第一局打当前选中的
    assert src.advance(nav, False) and src.advance(nav, False) and nav.calls == 2
    assert src.difficulty == "expert"


class ApNav:
    """挑战演出乐曲选择页：``songs`` 依次排列、选中第 ``sel`` 首，``ap`` 里是已 AP 的 (曲名, 难度)。"""

    def __init__(self, songs, ap=(), sel=0):
        self.songs = list(songs)
        self.ap = set(ap)
        self.sel = sel
        self.calls = []

    def challenge_song_ap(self, difficulty):
        title = self.songs[self.sel]
        self.calls.append(f"{title}:{difficulty}")
        return title, (title, difficulty) in self.ap

    def next_challenge_song(self):
        self.calls.append("next")
        self.sel = (self.sel + 1) % len(self.songs)


def test_challenge_ap_first_skips_ap_songs():
    nav = ApNav(["A", "B", "C"], ap={("A", "expert")})
    src = ChallengeApFirst(["expert"])
    assert src.advance(nav, True) and nav.songs[nav.sel] == "B" and src.difficulty == "expert"
    assert nav.calls == ["A:expert", "next", "B:expert"]
    src.done(song(2), "expert", result(2, "expert", 0))  # B 打出了 AP
    nav.ap.add(("B", "expert"))
    nav.calls.clear()
    assert src.advance(nav, False) and nav.songs[nav.sel] == "C"  # 再次演出后还选着 B
    assert nav.calls == ["B:expert", "next", "C:expert"]


def test_challenge_ap_first_gives_up_then_rotates():
    """同一首打 max_attempts 次没 AP 就不再打；都 AP 或放弃了就改为轮流打（这一局打选中的，之后每局换下一首）。"""
    nav = ApNav(["A", "B"], ap={("A", "expert")}, sel=1)
    src = ChallengeApFirst(["expert"], max_attempts=2)
    for _ in range(2):
        assert src.advance(nav, False) and nav.songs[nav.sel] == "B"
        src.done(song(2), "expert", result(2, "expert", 3))
    nav.calls.clear()
    assert src.advance(nav, False) and src.difficulty == "expert"
    assert nav.calls == ["B:expert", "next", "A:expert", "next", "B:expert"]  # 转了一圈
    nav.calls.clear()
    assert src.advance(nav, False) and src.advance(nav, False)
    assert nav.calls == ["next", "next"]


def test_challenge_ap_first_difficulties_in_order():
    nav = ApNav(["A", "B"], ap={("A", "expert"), ("B", "expert"), ("B", "hard")})
    src = ChallengeApFirst(["expert", "hard"], fallback="expert")
    assert src.advance(nav, True) and src.difficulty == "hard" and nav.songs[nav.sel] == "A"
    assert nav.calls == ["A:expert", "next", "B:expert", "next", "A:expert", "A:hard"]  # 读回 A 才知道转了一圈
    src.done(song(1), "hard", None, playable=False)  # 谱面站没有：不再打
    nav.calls.clear()
    assert src.advance(nav, False) and src.difficulty == "expert"  # 都没有了：按 fallback 轮流打
    assert nav.calls == ["A:hard", "next", "B:hard", "next", "A:hard"]


class NoisyNav(ApNav):
    """每次读出的曲名末尾都不一样（OCR 读错）。"""

    def challenge_song_ap(self, difficulty):
        title, ap = super().challenge_song_ap(difficulty)
        return title[:-1] + "あいうえお"[len(self.calls) % 5], ap


def test_challenge_ap_first_noisy_titles():
    """同一首歌每次读出的曲名差几个字也认得出：放弃过的不再打，转一圈能停下。"""
    nav = NoisyNav(["夢我夢中", "これはぼくたちの生存のあらすじ"], ap={("夢我夢中", "expert")}, sel=1)
    src = ChallengeApFirst(["expert"], max_attempts=1)
    assert src.advance(nav, True) and len(nav.calls) == 1
    src.done(song(1), "expert", result(1, "expert", 2))
    nav.calls.clear()
    assert src.advance(nav, False) and src._rotate
    assert len(nav.calls) == 5 and nav.calls.count("next") == 2


def test_challenge_ap_first_single_song():
    nav = ApNav(["A"], ap={("A", "expert")})
    nav.next_challenge_song = lambda: nav.calls.append("next")  # 只有一首：换不了
    src = ChallengeApFirst(["expert"])
    assert src.advance(nav, True) and nav.calls == ["A:expert", "next", "A:expert"]


@pytest.mark.parametrize("args", [([],), (["master"],), (["expert"], 0), (["expert"], 3, "master")])
def test_challenge_ap_first_invalid(args):
    with pytest.raises(ValueError):
        ChallengeApFirst(*args)


# ---------------------------------------------------------------- 歌单

ALL = {d: {"level": 20} for d in ("easy", "normal", "hard", "expert")}
CATALOG = Catalog(
    {
        100010: Song(100010, ["無路矢", "Muroshi", "無路矢", "无路矢"], difficulties=ALL),
        100033: Song(100033, ["Symbol I : △"], difficulties=ALL),
        100034: Song(100034, ["Symbol II : 🜁"], difficulties={"expert": {"level": 25}}),
    }
)


def test_parse_song_list():
    assert parse_song_list("100010", CATALOG, "expert") == [(100010, "expert")]
    text = " 无路矢 @ Hard ；Symbol II : 🜁\n\n100010，"
    assert parse_song_list(text, CATALOG, "expert") == [(100010, "hard"), (100034, "expert"), (100010, "expert")]


@pytest.mark.parametrize(
    "text, msg",
    [
        ("", "空的"),
        (" , ,", "空的"),
        ("无路矢@master", "难度"),
        ("Symbol II : 🜁@easy", "没有 EASY"),
        ("100010, 完全不存在的歌名xyz, 999", "找不到.*完全不存在的歌名xyz、999"),
    ],
)
def test_parse_song_list_errors(text, msg):
    with pytest.raises(ValueError, match=msg):
        parse_song_list(text, CATALOG, "expert")


class ListNav:
    """select_song 按预设返回；没预设的曲目当作列表里没有。"""

    def __init__(self, picks, category="原创"):
        self.picks = picks
        self.category = category
        self.calls = []

    def set_song_category(self, name):
        self.calls.append(f"category:{name}")
        before, self.category = self.category, name
        return before

    def clear_status_filter(self):
        self.calls.append("clear")

    def select_song(self, mid):
        self.calls.append(mid)
        return self.picks.get(mid)


def test_song_list_cycles_and_restores():
    nav = ListNav({1: SongPick(1), 2: SongPick(2)})
    src = SongList([(1, "expert"), (2, "hard")])
    assert src.advance(nav, True) and src.difficulty == "expert"
    assert nav.calls == ["category:全部", "clear", 1]
    assert src.advance(nav, False) and src.difficulty == "hard"
    assert src.advance(nav, False) and src.difficulty == "expert"
    assert nav.calls[3:] == [2, 1]
    nav.calls.clear()
    src.close(nav)
    assert nav.calls == ["category:原创"]


def test_song_list_skips_missing_locked_and_unplayable():
    nav = ListNav({1: SongPick(1, locked=True), 3: SongPick(3), 4: SongPick(4)}, category="全部")
    src = SongList([(1, "expert"), (2, "expert"), (3, "expert"), (4, "hard")])
    assert src.advance(nav, True)
    assert nav.calls == ["category:全部", "clear", 1, 2, 3]
    src.done(song(3), "expert", None, playable=False)
    nav.calls.clear()
    assert src.advance(nav, False) and src.difficulty == "hard"
    assert src.advance(nav, False) and src.difficulty == "hard"
    assert nav.calls == [4, 4]  # 1、2、3 不再找
    src.done(song(4), "hard", None, playable=False)
    assert not src.advance(nav, False)
    nav.calls.clear()
    src.close(nav)
    assert nav.calls == []  # 本来就是「全部」


def test_song_list_empty():
    with pytest.raises(ValueError):
        SongList([])
