"""全自动循环：识别曲目 → 开始演奏 → 同步并执行 → 读取结算 → 修正 offset → 下一首。

界面操作由 :class:`Navigator` 负责（MaaFramework 实现见 ``nav``），本模块只负责编排，便于用假对象测试。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple, Protocol

from .charts.bdon import BdonClient, ChartNotFound
from .charts.catalog import Catalog, Song
from .charts.model import Chart
from .config import Config
from .player.guard import LifeDepleted, PlayInterrupted
from .player.session import PlayOutcome, PlaySession, SyncFailed
from .player.sync import SyncTimeout
from .records import PlayResult, RecordStore
from .result_reader import ResultCounts, timing_counts

if TYPE_CHECKING:
    from .sources import SongSource

logger = logging.getLogger(__name__)

# 挂机等 LB 恢复：按顶栏的恢复倒计时睡到恢复后再看（多等几秒），最多隔这么久看一次
# （倒计时读不到时；等待期间游戏也可能日期变更，要重新登录）
LB_POLL_S = 600.0
LB_POLL_MARGIN_S = 5.0


class NavigationError(RuntimeError):
    pass


class ScreenFrozen(NavigationError):
    """停在认不出的画面上、画面一直不动（多半是没见过的页面或弹窗在等人操作），重试也没用。"""


class LbExhausted(Exception):
    """LB 已用完（且不允许改为消耗 0 继续打）。"""


class SongLabel(NamedTuple):
    """乐队确认页上显示的曲目信息；识别不到的项为 None。"""

    title: str
    difficulty: str | None = None
    level: int | None = None
    jacket: tuple[int, float] | None = None  # 封面匹配到的 (musicId, 相关系数)


class Navigator(Protocol):
    def ensure_band_confirm(self, difficulty: str | None = None) -> None:
        """从任意画面进入自由演出的乐队确认页（LIVE START 所在页）；经过乐曲选择页时选 ``difficulty``。"""
        ...

    def selected_song(self) -> SongLabel:
        """乐队确认页上的曲名、难度与等级。"""
        ...

    def choose_next_song(self, mode: str) -> None:
        """按 ``mode`` 换歌（current 时什么都不做）。"""
        ...

    def start_live(self, lb_short: str = "zero") -> None:
        """点击 LIVE START 后立即返回（首音符同步需要尽早开始看画面）。

        LB 用完（弹出恢复 LIVE BOOST）时：``zero`` 改为消耗 0 继续；``stop`` 抛出 :class:`LbExhausted`。
        """
        ...

    def retry_live(self) -> None:
        """演奏中暂停 → 重试，这首歌从头开始，点完立即返回；不在演奏画面或重试没生效时抛 NavigationError。"""
        ...

    def lb_status(self, check: bool = False) -> tuple[int | None, int | None]:
        """乐队确认页上的 LB 持有数和下一个恢复的倒计时（秒），读不到的项为 None；``check`` 时再用消耗设置弹窗核对持有数。"""
        ...

    def read_result(self, expected_total: int | None = None) -> ResultCounts:
        """等待结算页，读取判定总数（``expected_total`` 为谱面音符数，用来核对读数），再切换 FAST/SLOW 读取分列。"""
        ...

    def leave_result(self) -> None:
        """离开结算页并回到乐队确认页（もう一回ライブ 或 ホーム → 重新进入）。"""
        ...

    # 以下供选曲策略（sources）使用

    def clear_status_filter(self) -> None:
        """把乐曲选择页的「游玩状况」筛选改回「不指定」。"""
        ...

    def set_song_category(self, name: str) -> str | None:
        """切换乐曲选择页的分类（原创 / 翻唱 / 全部），返回切换前的分类。"""
        ...

    def set_song_filter(self, difficulty: str | None = None, status: str | None = None, reset: bool = False) -> None:
        """设置筛选的难度和游玩状况（``status`` 见 ``nav.song_select.STATUS_OPTIONS``）。"""
        ...

    def random_song(self):
        """点「随机选曲」，返回抽到的歌（``nav.song_select.SongPick``）。"""
        ...

    def select_song(self, music_id: int):
        """在列表里找到并选中 ``music_id``，返回 ``SongPick``；列表里没有时返回 None。"""
        ...


def identify_song(catalog: Catalog, label: SongLabel, difficulty: str) -> tuple[Song, str]:
    """封面优先，曲名兜底；返回 (曲目, 依据说明)，认不出或证据矛盾时抛 NavigationError。

    封面匹配到的曲目与画面上的等级不符时，只有曲名也指向它才采用（等级偶尔读错），否则宁可不打。
    同系列曲名（如 Symbol I～IV）只差编号和符号，OCR 容易混淆，曲名匹配时也用等级排除。
    """
    by_title = catalog.match(label.title, difficulty=difficulty, level=label.level)
    if label.jacket is not None and label.jacket[0] in catalog.songs:
        song = catalog.get(label.jacket[0])
        level = song.level(difficulty)
        if label.level is None or level == label.level or (by_title is not None and by_title[0] is song):
            return song, f"封面相似度 {label.jacket[1]:.2f}"
        raise NavigationError(
            f"封面像 {song.display_title()}（{song.music_id}），但它的 {difficulty} 等级为 {level}，"
            f"画面上为 {label.level}，曲名 {label.title!r} 也对不上"
        )
    if by_title is None:
        raise NavigationError(f"无法识别曲目：曲名 {label.title!r}（{difficulty} Lv.{label.level}），封面也不确定")
    return by_title[0], f"曲名匹配度 {by_title[1]:.0f}"


@dataclass
class RunStats:
    plays: int = 0
    full_combo: int = 0
    all_perfect: int = 0
    failures: int = 0


class Runner:
    def __init__(
        self,
        config: Config,
        nav: Navigator,
        session: PlaySession,
        client: BdonClient,
        catalog: Catalog,
        store: RecordStore,
        stop: threading.Event | None = None,
        source: SongSource | None = None,
    ):
        from .sources import make_source

        self.cfg = config
        self.nav = nav
        self.session = session
        self.client = client
        self.catalog = catalog
        self.store = store
        self.stop = stop or threading.Event()
        self.source = source or make_source(config, catalog)
        self.stats = RunStats()
        lc = config.loop
        if (lc.until_lb_empty or lc.wait_lb) and not config.game.lb_cost:
            raise ValueError(f"{'挂机' if lc.wait_lb else '打到 LB 用完'}需要设置 game.lb_cost 为 1~3")

    def _identify(self):
        label = self.nav.selected_song()
        want = self.source.difficulty
        diff = label.difficulty
        if diff is not None and diff != want:
            logger.warning("当前难度为 %s，配置为 %s；按画面上的难度演奏", diff, want)
        diff = diff or want
        song, basis = identify_song(self.catalog, label, diff)
        logger.info("曲目：%s（%d，%s，%s）", song.display_title(), song.music_id, diff, basis)
        return song, diff

    def play_once(self) -> PlayResult | None:
        self.nav.ensure_band_confirm(self.source.difficulty)
        song, diff = self._identify()
        try:
            chart = self.client.chart(song.music_id, diff, song.display_title())
        except ChartNotFound as e:
            self.source.done(song, diff, None, playable=False)
            raise NavigationError(f"谱面站没有 {song.music_id}_{diff}") from e
        self.session.learned_offset_ms = self.store.learned_offset_ms
        lc = self.cfg.loop
        self.nav.start_live("stop" if lc.until_lb_empty or lc.wait_lb else "zero")
        outcome = self._play(chart)
        counts = self.nav.read_result(chart.judged_count or None)
        # 先记下这一局再离开结算页：离开时出错（如遇到没见过的结算页）也不丢记录
        result = None if outcome is None else self._record(outcome, counts)
        self.source.done(song, diff, result)
        if result is not None:
            self.stats.plays += 1
            self.stats.full_combo += bool(result.full_combo)
            self.stats.all_perfect += bool(result.all_perfect)
        self.nav.leave_result()
        return result

    def _play(self, chart: Chart) -> PlayOutcome | None:
        """演奏一局，返回 None 表示放弃（歌还在放，之后照常等结算）。

        首音符同步失败、演奏中生命值归零（整体对不上了）时暂停、从头重试，最多 ``loop.sync_retries`` 次：
        干等这首歌放完几乎拿不到分，这一局消耗的 LB 就浪费了（「终止」也拿不到演出奖励）。
        """
        retries = 0
        while True:
            try:
                return self.session.play(chart, self.stop, retry=retries > 0)
            except (SyncFailed, SyncTimeout, LifeDepleted) as e:
                if retries >= self.cfg.loop.sync_retries or self.stop.is_set():
                    # 自由演出 LIFE 归零也不会中断，等歌曲结束后照常离开结算页
                    logger.error("本局放弃：%s", e)
                    return None
                retries += 1
                logger.warning("%s，从头重试（第 %d 次）", e, retries)
            except PlayInterrupted as e:
                # 暂停菜单、闪退后的桌面等交给下一局开头的导航处理
                raise NavigationError(str(e)) from e
            try:
                self.nav.retry_live()
            except NavigationError as e:
                logger.error("重试失败，本局放弃：%s", e)
                return None

    def _record(self, outcome: PlayOutcome, rc: ResultCounts) -> PlayResult:
        judgements = ("perfect", "great", "good", "bad")
        if self.cfg.play.touch.great_ratio > 0:
            judgements = ("perfect", "good", "bad")  # 故意打的 GREAT 偏离 60ms 以上，不拿来修正 offset
        fast, slow = timing_counts(rc, judgements)
        total = sum(v for v in rc.counts.values() if v is not None)
        missed = sum(rc.counts.get(j) or 0 for j in ("good", "bad", "miss"))
        combo = rc.combo
        complete = len(rc.counts) == 5 and None not in rc.counts.values()
        if complete and total and not missed and combo != total:
            # 全连时最大连击就是总数（结算页的连击数偶尔少读开头细长的 1）
            combo = total
        elif complete and combo is not None and not -(-(total - missed) // (missed + 1)) <= combo <= total:
            # 断了 missed 次，最长的一段至少有平均长度
            logger.warning("最大连击读数 %d 与判定数矛盾，不记录", combo)
            combo = None
        result = PlayResult(
            music_id=outcome.chart.music_id,
            difficulty=outcome.chart.difficulty,
            perfect=rc.counts.get("perfect"),
            great=rc.counts.get("great"),
            good=rc.counts.get("good"),
            bad=rc.counts.get("bad"),
            miss=rc.counts.get("miss"),
            fast=fast,
            slow=slow,
            max_combo=combo,
            score=rc.score,
            offset_ms=outcome.offset_ms,
            sync_error_ms=outcome.sync.rms_ms,
            max_late_ms=outcome.stats.max_late_ms,
            stalls=[[round(a), round(b)] for a, b in outcome.stalls] or None,
        )
        # 执行经常迟到或被中断时，FAST/SLOW 不代表 offset 偏差，只记录不修正（偶发一两次迟到不影响）；
        # 大量 MISS 多半是同步到了错误的音符（整体错开），FAST/SLOW 同样没有意义
        st = outcome.stats
        late = st.late_count(4.0)
        if st.aborted or late > max(3, 0.01 * len(st.lateness_ms)):
            why = f"本局执行不稳定（{late} 批迟到超过 4ms）"
        elif total and missed > 0.1 * total:
            why = f"GOOD/BAD/MISS 共 {missed} 个（总 {total}），可能同步到了错误的音符"
        else:
            why = None
        if why is None:
            self.store.autotune(result, self.cfg.autotune)
        else:
            self.store.append(result)
            logger.warning("%s，不修正 offset", why)
        tag = "AP" if result.all_perfect else "FC" if result.full_combo else ""
        if outcome.stalls:
            tag += f"（演奏中模拟器卡顿 {len(outcome.stalls)} 次）"
        logger.info(
            "结果：P%s G%s g%s B%s M%s  COMBO %s  FAST/SLOW %s/%s %s",
            result.perfect,
            result.great,
            result.good,
            result.bad,
            result.miss,
            result.max_combo,
            fast,
            slow,
            tag,
        )
        return result

    def _advance(self, first: bool) -> bool:
        """换到下一局的曲目；换歌失败只记录（下一局的 ensure_band_confirm 会从任意画面恢复，只是这次没换成歌）。"""
        try:
            more = self.source.advance(self.nav, first)
        except ScreenFrozen:
            raise
        except (NavigationError, TimeoutError) as e:
            if self.stop.is_set():
                return False
            self._change_failures += 1
            logger.error("换歌失败：%s", e)
            if self._change_failures >= self.cfg.loop.max_failures:
                logger.error("连续换歌失败 %d 次，停止", self._change_failures)
                return False
            return True
        self._change_failures = 0
        if not more:
            logger.info("没有要打的曲目了")
        return more

    def _wait_lb(self) -> None:
        """挂机：停在乐队确认页等 LB 恢复到每局消耗数（LB 少于它时每局奖励也少）。
        每次醒来都重新进入乐队确认页：等待期间游戏可能日期变更、回到标题画面重新登录。"""
        need = self.cfg.game.lb_cost
        last: int | None = -1
        while True:
            self.nav.ensure_band_confirm(self.source.difficulty)
            held, left = self.nav.lb_status()
            if held is not None and held >= need:
                # 顶栏的小字偶尔读错：继续前用弹窗核对（读多了会一开始又说用完，反复点 LIVE START）
                held, _ = self.nav.lb_status(check=True)
            if held is not None and held >= need:
                logger.info("LB 恢复到 %d 个，继续", held)
                return
            wait = LB_POLL_S if left is None else min(LB_POLL_S, left + LB_POLL_MARGIN_S)
            if held != last:  # 每恢复一个报一次
                eta = "" if left is None else f"，下一个约 {left // 60}:{left % 60:02d} 后恢复"
                logger.info("LB 持有 %s 个，每局消耗 %d 个：等待恢复%s", "?" if held is None else held, need, eta)
                last = held
            else:
                logger.debug("LB 持有 %s 个，%.0fs 后再看", held, wait)
            if self.stop.wait(wait):
                raise NavigationError("已停止")

    def run(self) -> RunStats:
        try:
            self._loop()
        except ScreenFrozen as e:
            # 干等画面不会变，重试也没用（恢复选曲页的设置同样做不了）
            logger.error("%s，停止", e)
            self.stats.failures += 1
        else:
            if not self.stop.is_set():
                try:
                    self.source.close(self.nav)
                except (NavigationError, TimeoutError) as e:
                    logger.warning("恢复选曲页的设置失败：%s", e)
        logger.info(
            "共演奏 %d 局：FC %d，AP %d，失败 %d",
            self.stats.plays,
            self.stats.full_combo,
            self.stats.all_perfect,
            self.stats.failures,
        )
        return self.stats

    def _loop(self) -> None:
        lc = self.cfg.loop
        consecutive = 0
        self._change_failures = 0
        first = True
        wait_lb = False  # 上一局因 LB 用完没开始（挂机）：等恢复后接着打这首，不换歌
        while not self.stop.is_set() and (lc.max_plays <= 0 or self.stats.plays < lc.max_plays):
            if not wait_lb and (not self._advance(first) or self.stop.is_set()):
                break
            first = False
            try:
                if wait_lb:
                    self._wait_lb()
                    wait_lb = False
                result = self.play_once()
            except LbExhausted as e:
                if not lc.wait_lb:
                    logger.info("%s，结束", e)
                    break
                wait_lb = True
                continue
            except ScreenFrozen:
                raise
            except (NavigationError, TimeoutError) as e:
                if self.stop.is_set():
                    break
                result = None
                logger.error("%s", e)
            if result is None:
                consecutive += 1
                self.stats.failures += 1
                if consecutive >= lc.max_failures:
                    logger.error("连续失败 %d 次，停止", consecutive)
                    break
            else:
                consecutive = 0
