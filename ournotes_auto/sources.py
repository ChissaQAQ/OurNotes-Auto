"""选曲策略：决定每局打哪首歌、哪个难度（由 :class:`~ournotes_auto.runner.Runner` 在每局开始前调用）。"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections import Counter
from typing import TYPE_CHECKING, Callable, Protocol

from .nav.screens import same_title

if TYPE_CHECKING:
    from .charts.catalog import Catalog, Song
    from .config import Config
    from .records import PlayResult, RecordStore
    from .runner import Navigator

logger = logging.getLogger(__name__)

SONG_MODES = ("current", "random", "ap", "ap_first", "list", "rotate")
CHALLENGE_SONG_MODES = ("current", "rotate", "ap_first")  # 挑战演出的选曲页没有随机选曲、筛选和分类
CHALLENGE_COSTS = (200, 400, 800, 1600)  # 挑战演出每局可选的挑战pt消耗
CHALLENGE_MAX_SONGS = 30  # 挑战演出转一圈最多看这么多首（防止曲名读得不稳定时一直转）
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


class ChallengeRotate(_Source):
    """挑战演出：第一局打当前选中的曲目，之后每局在挑战演出乐曲选择页换到列表里的下一首（最后一首之后回到第一首）。"""

    def __init__(self, difficulty: str):
        self.difficulty = difficulty

    def advance(self, nav: Navigator, first: bool) -> bool:
        if not first:
            nav.next_challenge_song()
        return True


class ChallengeApFirst(_Source):
    """挑战演出：优先打还没 AP 的歌。按 ``difficulties`` 依次，从选中的歌往下一首首看乐曲选择页右侧的
    ALL PERFECT 标记，打第一首没 AP 的；同一首打了 ``max_attempts`` 次还没 AP、或谱面站没有谱面的不再打。
    这个难度转一圈都没有要打的就换下一个难度；都没有了改为按 ``fallback`` 难度轮流打每首歌（同 rotate）。"""

    def __init__(self, difficulties: list[str], max_attempts: int = 3, fallback: str | None = None):
        if not difficulties:
            raise ValueError("至少要选一个难度")
        for d in [*difficulties, fallback or difficulties[0]]:
            if d not in DIFFICULTIES:
                raise ValueError(f"未知的难度：{d}")
        if max_attempts < 1:
            raise ValueError(f"每首最多尝试次数应至少为 1：{max_attempts}")
        self.difficulties = list(difficulties)
        self.max_attempts = max_attempts
        self.fallback = fallback or self.difficulties[0]
        self._index = 0
        self._rotate = False
        self._key: tuple[str, str] | None = None  # 这一局打的 (曲名, 难度)
        self.attempts: Counter[tuple[str, str]] = Counter()
        self.skip: set[tuple[str, str]] = set()  # 已 AP、已放弃或打不了的 (曲名, 难度)

    @property
    def difficulty(self) -> str:
        return self.fallback if self._rotate else self.difficulties[min(self._index, len(self.difficulties) - 1)]

    def _known(self, title: str, diff: str) -> tuple[str, str]:
        """之前读到过的同一首歌就沿用那次的键（每次 OCR 读出的曲名可能差几个字）。"""
        for t, d in [*self.attempts, *self.skip]:
            if d == diff and same_title(t, title):
                return t, d
        return title, diff

    def advance(self, nav: Navigator, first: bool) -> bool:
        while not self._rotate and self._index < len(self.difficulties):
            diff = self.difficulty
            seen: list[str] = []
            for _ in range(CHALLENGE_MAX_SONGS):
                title, ap = nav.challenge_song_ap(diff)
                if any(same_title(t, title) for t in seen):
                    break  # 转了一圈
                seen.append(title)
                key = self._known(title, diff)
                if ap:
                    self.skip.add(key)
                if key not in self.skip:
                    self._key = key
                    logger.info("挑战演出：%s %s 还没 AP", title, diff.upper())
                    return True
                nav.next_challenge_song()
            logger.info("挑战演出的歌 %s 都 AP 了（或已放弃）", diff.upper())
            self._index += 1
        self._key = None
        if not self._rotate:
            self._rotate = True
            logger.info("没有要补的歌了，改为 %s 轮流打每首歌", self.fallback.upper())
            return True  # 这一局打当前选中的，之后每局换下一首
        nav.next_challenge_song()
        return True

    def done(self, song: Song, difficulty: str, result: PlayResult | None, playable: bool = True) -> None:
        key = self._key
        if key is None:
            return
        if not playable:
            logger.warning("%s %s 打不了，跳过", song.display_title(), difficulty.upper())
            self.skip.add(key)
        elif result is not None and result.all_perfect:
            self.skip.add(key)
        else:
            self.attempts[key] += 1
            if self.attempts[key] >= self.max_attempts:
                logger.warning("%s %s 打了 %d 次没有 AP，不再打", song.display_title(), difficulty.upper(), self.attempts[key])
                self.skip.add(key)


class ApDoneCache:
    """记住「某难度已经没有要补的歌」（``data/state.json``）：优先没 AP 的歌（ap_first）下次开始时跳过这个难度的
    筛选和抽歌（每个难度约 10 秒）。

    - 按服（包名）和难度分开记，同时记下曲目目录里这个难度有哪些歌（摘要）。
    - 目录里这个难度的歌变了（新曲）、超过 ``hours`` 小时（这期间可能解锁了新的歌）就重新检查；
      切换账号（``switch-account``）时全部清除。``hours`` 为 0 时每次都检查。
    - 只在列表为空、或只剩未解锁 / 封面认不出的歌时记下；剩下的是这次放弃了的歌时不记（下次运行还会再打）。
    - AP 补完（ap）不看记录、每次都实际检查，并更新记录。
    """

    def __init__(
        self,
        store: RecordStore,
        package: str,
        catalog: Catalog,
        hours: float = 12.0,
        clock: Callable[[], float] = time.time,
    ):
        self.store = store
        self.package = package
        self.catalog = catalog
        self.hours = hours
        self.clock = clock

    def _digest(self, diff: str) -> str:
        ids = sorted(mid for mid, song in self.catalog.songs.items() if diff in song.difficulties)
        return hashlib.sha1(",".join(map(str, ids)).encode()).hexdigest()[:12]

    def hit(self, diff: str) -> bool:
        """上次确认过这个难度没有要补的歌、而且记录还有效。"""
        entry = self.store.ap_done(self.package, diff)
        if entry is None:
            return False
        age_h = (self.clock() - float(entry.get("time", 0))) / 3600
        if entry.get("catalog") != self._digest(diff):
            logger.info("%s：曲目目录变了（有新曲），重新检查有没有要补的歌", diff.upper())
        elif not 0 <= age_h < self.hours:
            logger.info("%s：上次确认没有要补的歌是 %.1f 小时前，重新检查", diff.upper(), age_h)
        else:
            logger.info(
                "%s：%.1f 小时前确认过没有要补的歌，跳过（新解锁的歌最晚 %g 小时后补到；运行一次 AP补完会重新检查）",
                diff.upper(),
                age_h,
                self.hours,
            )
            return True
        self.store.set_ap_done(self.package, diff, None)
        return False

    def update(self, diff: str, done: bool) -> None:
        """检查完这个难度：``done`` 为 True 时记下没有要补的歌，否则删掉记录。"""
        if done:
            self.store.set_ap_done(self.package, diff, {"time": round(self.clock(), 1), "catalog": self._digest(diff)})
        else:
            self.store.set_ap_done(self.package, diff, None)


class ApComplete(_Source):
    """全曲 AP 补完：按难度依次筛选「未ALL PERFECT」，用随机选曲抽歌。

    - 打出 AP 的歌会从筛选后的列表里消失；同一首歌打了 ``max_attempts`` 次还没 AP 就不再打。
    - 抽到不打的歌（未解锁、封面认不出、已放弃）就在乐曲选择页重抽。
    - 列表为空、随机选曲提示没有别的歌可抽，或连续 ``max_rerolls`` 次都抽到不打的歌时，换下一个难度。
    - 正常结束时把游玩状况改回「不指定」，分类改回原来的。
    - 有 ``cache`` 时把各难度的检查结果记下来（见 :class:`ApDoneCache`）。
    """

    use_cache = False  # 有记录的难度直接跳过（ap_first）；AP 补完每次都检查

    def __init__(
        self,
        difficulties: list[str],
        max_attempts: int = 3,
        max_rerolls: int = 12,
        cache: ApDoneCache | None = None,
    ):
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
        self.cache = cache
        self._index = 0
        self._filtered = False  # 当前难度的筛选已经设好
        self._reset = False  # 已经重置过筛选
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
                if self.use_cache and self.cache is not None and self.cache.hit(diff):
                    self._index += 1
                    continue
                if not self._touched:
                    self._touched = True
                    self._category = nav.set_song_category(AP_CATEGORY)
                logger.info("AP 补完：%s", diff.upper())
                # 第一次先重置（清掉收藏等别的筛选），之后只换难度
                nav.set_song_filter(diff, "not_ap", reset=not self._reset)
                self._reset = self._filtered = True
            lasting = True  # 抽到的不打的歌都是未解锁、封面认不出（不是这次放弃了的）
            for _ in range(self.max_rerolls):
                pick = nav.random_song()
                if pick.empty:
                    logger.info("%s 已经全部 AP（筛选后列表为空）", diff.upper())
                    self._checked(diff, True)
                    break
                why = self._reject(pick, diff)
                if why is None:
                    self._checked(diff, False)
                    return True
                if (pick.music_id, diff) in self.skip:
                    lasting = False
                if pick.only:
                    logger.info("%s 没有别的歌可抽了（选中的这首：%s）", diff.upper(), why)
                    self._checked(diff, lasting)
                    break
                logger.debug("重抽：%s", why)
            else:
                logger.warning("%s 连续 %d 次抽到不打的歌，换下一个难度", diff.upper(), self.max_rerolls)
                self._checked(diff, False)
            self._index += 1
            self._filtered = False
        return False

    def _checked(self, diff: str, done: bool) -> None:
        if self.cache is not None:
            self.cache.update(diff, done)

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

    use_cache = True

    def __init__(
        self,
        difficulties: list[str],
        max_attempts: int = 3,
        fallback: str | None = None,
        cache: ApDoneCache | None = None,
    ):
        super().__init__(difficulties, max_attempts, cache=cache)
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
            if self._touched:
                nav.set_song_filter(status="any")
            else:  # 各难度都跳过了，没动过筛选：同样在「全部」里抽
                self._touched = True
                self._category = nav.set_song_category(AP_CATEGORY)
                nav.clear_status_filter()
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


def make_source(cfg: Config, catalog: Catalog | None = None, store: RecordStore | None = None) -> SongSource:
    """``catalog`` 歌单模式（解析曲名）需要；有 ``catalog`` 和 ``store`` 时 AP 补完 / ap_first 记下各难度的检查结果。"""
    mode, difficulty = cfg.loop.song_mode, cfg.game.difficulty
    cache = None
    if catalog is not None and store is not None:
        cache = ApDoneCache(store, cfg.device.package, catalog, cfg.loop.ap_done_hours)
    if cfg.loop.challenge and mode not in CHALLENGE_SONG_MODES:
        raise ValueError(f"挑战演出只能用 {'/'.join(CHALLENGE_SONG_MODES)} 选曲模式，而不是 {mode}")
    if mode == "rotate" and not cfg.loop.challenge:
        raise ValueError("rotate 选曲模式只用于挑战演出（loop.challenge）")
    if mode == "ap":
        return ApComplete(parse_difficulties(cfg.loop.ap_difficulties), cfg.loop.ap_max_attempts, cache=cache)
    if difficulty not in DIFFICULTIES:
        raise ValueError(f"未知的难度：{difficulty}")
    if mode == "current":
        return CurrentSong(difficulty)
    if mode == "random":
        return RandomSong(difficulty)
    if mode == "rotate":
        return ChallengeRotate(difficulty)
    if mode == "ap_first" and cfg.loop.challenge:
        diffs = parse_difficulties(cfg.loop.ap_first_difficulties) or [difficulty]
        return ChallengeApFirst(diffs, cfg.loop.ap_max_attempts, fallback=difficulty)
    if mode == "ap_first":
        diffs = parse_difficulties(cfg.loop.ap_first_difficulties) or [difficulty]
        return ApFirst(diffs, cfg.loop.ap_max_attempts, fallback=difficulty, cache=cache)
    if mode == "list":
        if catalog is None:
            raise ValueError("歌单模式需要曲目目录")
        return SongList(parse_song_list(cfg.loop.song_list, catalog, difficulty))
    raise ValueError(f"未知的选曲模式：{mode}（可选 {'/'.join(SONG_MODES)}）")
