"""选曲策略：决定每局打哪首歌、哪个难度（由 :class:`~ournotes_auto.runner.Runner` 在每局开始前调用）。"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .charts.catalog import Catalog, Song
    from .config import Config
    from .records import PlayResult
    from .runner import Navigator

logger = logging.getLogger(__name__)

SONG_MODES = ("current", "random", "ap", "ap_first", "list")
DIFFICULTIES = ("easy", "normal", "hard", "expert")
AP_CATEGORY = "全部"  # AP 补完包括翻唱
LIST_CATEGORY = "全部"  # 歌单里可能有翻唱


class SongSource(Protocol):
    difficulty: str  # 在乐曲选择页要选的难度（乐队确认页上已选好的难度以画面为准）

    def advance(self, nav: Navigator, first: bool) -> bool:
        """让下一局要打的曲目出现在乐队确认页（``first`` 为第一局）；没有要打的了返回 False。"""
        ...

    def done(self, song: Song, difficulty: str, result: PlayResult | None, playable: bool = True) -> None:
        """一局结束。``result`` 为 None 表示没打成；``playable`` 为 False 表示这首打不了（谱面站没有）。"""
        ...

    def close(self, nav: Navigator) -> None:
        """正常结束（不是被停止）时调用，恢复改过的游戏设置。"""
        ...


class _Source:
    difficulty: str

    def done(self, song: Song, difficulty: str, result: PlayResult | None, playable: bool = True) -> None:
        pass

    def close(self, nav: Navigator) -> None:
        pass


class CurrentSong(_Source):
    """一直打当前选中的曲目。"""

    def __init__(self, difficulty: str):
        self.difficulty = difficulty

    def advance(self, nav: Navigator, first: bool) -> bool:
        return True


class RandomSong(_Source):
    """第一局打当前选中的曲目，之后每局在乐曲选择页点「随机选曲」。

    随机选曲只在筛选后的列表里抽，所以第一次换歌前把「游玩状况」筛选改回「不指定」
    （AP 补完被中途停止时会留下「未ALL PERFECT」）。
    """

    def __init__(self, difficulty: str):
        self.difficulty = difficulty
        self._checked = False

    def advance(self, nav: Navigator, first: bool) -> bool:
        if not first:
            if not self._checked:
                nav.clear_status_filter()
                self._checked = True
            nav.choose_next_song("random")
        return True


class ApComplete(_Source):
    """全曲 AP 补完：按难度依次筛选「未ALL PERFECT」，用随机选曲抽歌。

    - 打出 AP 的歌会从筛选后的列表里消失；同一首歌打了 ``max_attempts`` 次还没 AP 就不再打。
    - 抽到不打的歌（未解锁、封面认不出、已放弃）就在乐曲选择页重抽。
    - 列表为空、随机选曲提示没有别的歌可抽，或连续 ``max_rerolls`` 次都抽到不打的歌时，换下一个难度。
    - 正常结束时把游玩状况改回「不指定」，分类改回原来的。
    """

    def __init__(self, difficulties: list[str], max_attempts: int = 3, max_rerolls: int = 12):
        if not difficulties:
            raise ValueError("AP 补完至少要选一个难度")
        for d in difficulties:
            if d not in DIFFICULTIES:
                raise ValueError(f"未知的难度：{d}")
        if max_attempts < 1:
            raise ValueError(f"每首最多尝试次数应至少为 1：{max_attempts}")
        self.difficulties = list(difficulties)
        self.max_attempts = max_attempts
        self.max_rerolls = max_rerolls
        self._index = 0
        self._filtered = False  # 当前难度的筛选已经设好
        self._touched = False  # 改过游戏里的分类 / 筛选
        self._category: str | None = None  # 开始前的分类
        self.attempts: Counter[tuple[int, str]] = Counter()
        self.skip: set[tuple[int, str]] = set()  # 已 AP 或已放弃的 (musicId, 难度)

    @property
    def difficulty(self) -> str:
        return self.difficulties[min(self._index, len(self.difficulties) - 1)]

    def advance(self, nav: Navigator, first: bool) -> bool:
        while self._index < len(self.difficulties):
            diff = self.difficulty
            if not self._filtered:
                if not self._touched:
                    self._touched = True
                    self._category = nav.set_song_category(AP_CATEGORY)
                logger.info("AP 补完：%s", diff.upper())
                nav.set_song_filter(diff, "not_ap", reset=True)
                self._filtered = True
            for _ in range(self.max_rerolls):
                pick = nav.random_song()
                if pick.empty:
                    logger.info("%s 已经全部 AP（筛选后列表为空）", diff.upper())
                    break
                why = self._reject(pick, diff)
                if why is None:
                    return True
                if pick.only:
                    logger.info("%s 没有别的歌可抽了（选中的这首：%s）", diff.upper(), why)
                    break
                logger.info("重抽：%s", why)
            else:
                logger.warning("%s 连续 %d 次抽到不打的歌，换下一个难度", diff.upper(), self.max_rerolls)
            self._index += 1
            self._filtered = False
        return False

    def _reject(self, pick, diff: str) -> str | None:
        if pick.locked:
            return "未解锁"
        if pick.music_id is None:
            return "封面认不出"
        if (pick.music_id, diff) in self.skip:
            return f"{pick.music_id} 已放弃或已 AP"
        return None

    def done(self, song: Song, difficulty: str, result: PlayResult | None, playable: bool = True) -> None:
        key = (song.music_id, difficulty)
        if not playable:
            logger.warning("%s %s 打不了，跳过", song.display_title(), difficulty.upper())
            self.skip.add(key)
            return
        if result is not None and result.all_perfect:
            self.skip.add(key)  # 游戏的筛选会去掉它；万一列表没刷新也不再打
            return
        self.attempts[key] += 1
        if self.attempts[key] >= self.max_attempts:
            logger.warning("%s %s 打了 %d 次没有 AP，不再打", song.display_title(), difficulty.upper(), self.attempts[key])
            self.skip.add(key)

    def close(self, nav: Navigator) -> None:
        if not self._touched:
            return
        nav.set_song_filter(status="any")
        self._restore_category(nav)

    def _restore_category(self, nav: Navigator) -> None:
        if self._category is not None and self._category != AP_CATEGORY:
            nav.set_song_category(self._category)


class ApFirst(ApComplete):
    """优先打还没 AP 的歌（重复刷歌 / 清体力用）：按 ``difficulties`` 依次补（规则同 AP 补完），
    都没有可打的了（都 AP 了或都放弃了），就把游玩状况改回「不指定」，之后按 ``fallback`` 难度
    （默认第一个难度）随机选曲，直到打够局数或 LB 用完。"""

    def __init__(self, difficulties: list[str], max_attempts: int = 3, fallback: str | None = None):
        super().__init__(difficulties, max_attempts)
        fallback = fallback or self.difficulties[0]
        if fallback not in DIFFICULTIES:
            raise ValueError(f"未知的难度：{fallback}")
        self.fallback = fallback
        self._random = False

    @property
    def difficulty(self) -> str:
        return self.fallback if self._random else super().difficulty

    def advance(self, nav: Navigator, first: bool) -> bool:
        if not self._random:
            if super().advance(nav, first):
                return True
            self._random = True
            logger.info("没有要补的歌了，改为 %s 随机选曲", self.difficulty.upper())
            nav.set_song_filter(status="any")
        nav.choose_next_song("random")
        return True

    def close(self, nav: Navigator) -> None:
        if self._random:
            self._restore_category(nav)
        else:
            super().close(nav)


class SongList(_Source):
    """按歌单依次打，打完一轮从头再来：每局在乐曲选择页的列表里找到下一首选中（见 ``select_song``）。

    - 开始前把分类切到「全部」、游玩状况筛选改回「不指定」（不然有的歌不在列表里），正常结束时分类改回原来的。
    - 未解锁、列表里找不到的歌跳过，之后不再找；谱面站没有谱面的 (曲目, 难度) 也不再打。整张歌单都打不了就结束。
    """

    def __init__(self, entries: list[tuple[int, str]]):
        if not entries:
            raise ValueError("歌单是空的")
        self.entries = list(entries)
        self.difficulty = self.entries[0][1]
        self._index = -1
        self._touched = False
        self._category: str | None = None
        self.missing: set[int] = set()  # 未解锁或列表里找不到
        self.unplayable: set[tuple[int, str]] = set()

    def advance(self, nav: Navigator, first: bool) -> bool:
        if not self._touched:
            self._touched = True
            self._category = nav.set_song_category(LIST_CATEGORY)
            nav.clear_status_filter()
        for _ in range(len(self.entries)):
            self._index = (self._index + 1) % len(self.entries)
            mid, diff = self.entries[self._index]
            if mid in self.missing or (mid, diff) in self.unplayable:
                continue
            pick = nav.select_song(mid)
            if pick is None or pick.empty or pick.locked:
                logger.warning("%d %s，跳过", mid, "未解锁" if pick and pick.locked else "在列表里找不到")
                self.missing.add(mid)
                continue
            self.difficulty = diff
            return True
        logger.warning("歌单里没有能打的歌了")
        return False

    def done(self, song: Song, difficulty: str, result: PlayResult | None, playable: bool = True) -> None:
        if not playable:
            logger.warning("%s %s 打不了，跳过", song.display_title(), difficulty.upper())
            self.unplayable.add((song.music_id, difficulty))

    def close(self, nav: Navigator) -> None:
        if self._category is not None and self._category != LIST_CATEGORY:
            nav.set_song_category(self._category)


def parse_song_list(text: str, catalog: Catalog, default_difficulty: str) -> list[tuple[int, str]]:
    """歌单文字 → [(musicId, 难度)]。

    每项是曲目 ID 或曲名（任意语言，模糊匹配），后面可以加「@难度」；用逗号、分号或换行分隔
    （曲名里有逗号的用 ID）。同名的不同版本（如两首「春日影」）只能用 ID 区分。
    """
    entries: list[tuple[int, str]] = []
    unknown: list[str] = []
    for part in re.split(r"[,，;；\n]", text):
        part = part.strip()
        if not part:
            continue
        name, sep, diff = part.rpartition("@")
        if not sep:
            name, diff = part, default_difficulty
        name, diff = name.strip(), diff.strip().lower()
        if diff not in DIFFICULTIES:
            raise ValueError(f"歌单「{part}」的难度应为 {'/'.join(DIFFICULTIES)}")
        found = catalog.lookup(name)
        if found is None:
            unknown.append(name)
            continue
        song, score = found
        if diff not in song.difficulties:
            raise ValueError(f"歌单「{part}」：{song.display_title()}（{song.music_id}）没有 {diff.upper()} 谱面")
        logger.info(
            "歌单：%s → %s（%d）%s%s",
            name,
            song.display_title(),
            song.music_id,
            diff.upper(),
            f"，相似度 {score:.0f}" if score < 100 else "",
        )
        entries.append((song.music_id, diff))
    if unknown:
        raise ValueError(f"歌单里找不到这些曲目：{'、'.join(unknown)}")
    if not entries:
        raise ValueError("歌单是空的")
    return entries


def parse_difficulties(text: str) -> list[str]:
    """``"expert,hard"`` → ``["expert", "hard"]``（保持顺序、去重）。"""
    out: list[str] = []
    for part in text.split(","):
        d = part.strip().lower()
        if not d:
            continue
        if d not in DIFFICULTIES:
            raise ValueError(f"未知的难度：{d}（可选 {'/'.join(DIFFICULTIES)}）")
        if d not in out:
            out.append(d)
    return out


def make_source(cfg: Config, catalog: Catalog | None = None) -> SongSource:
    """``catalog`` 只有歌单模式（解析曲名）需要。"""
    mode, difficulty = cfg.loop.song_mode, cfg.game.difficulty
    if mode == "ap":
        return ApComplete(parse_difficulties(cfg.loop.ap_difficulties), cfg.loop.ap_max_attempts)
    if difficulty not in DIFFICULTIES:
        raise ValueError(f"未知的难度：{difficulty}")
    if mode == "current":
        return CurrentSong(difficulty)
    if mode == "random":
        return RandomSong(difficulty)
    if mode == "ap_first":
        diffs = parse_difficulties(cfg.loop.ap_first_difficulties) or [difficulty]
        return ApFirst(diffs, cfg.loop.ap_max_attempts, fallback=difficulty)
    if mode == "list":
        if catalog is None:
            raise ValueError("歌单模式需要曲目目录")
        return SongList(parse_song_list(cfg.loop.song_list, catalog, difficulty))
    raise ValueError(f"未知的选曲模式：{mode}（可选 {'/'.join(SONG_MODES)}）")
