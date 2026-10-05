"""全自动循环：识别曲目 → 开始演奏 → 同步并执行 → 读取结算 → 修正 offset → 下一首。

界面操作由 :class:`Navigator` 负责（MaaFramework 实现见 ``nav``），本模块只负责编排，便于用假对象测试。
"""

from __future__ import annotations

import logging
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple, Protocol

from .charts.bdon import BdonClient, ChartNotFound
from .charts.catalog import Catalog, Song
from .charts.model import Chart
from .config import Config, parse_daily_time
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
# 挂机遇到服务器维护：隔这么久回到标题画面重新登录一次，看开服没有
MAINTENANCE_POLL_S = 600.0
# 挂机定时领取：录音室练习没领成时最多隔这么久再领；日常没领全时隔这么久再领，一天最多领这么多次
STUDIO_RETRY_S = 1800.0
DAILY_RETRY_S = 600.0
DAILY_TRIES = 3
# 挂机每天领取的日常（录音室练习由定时收获领，看故事要等剧情播完，不在这里领）
IDLE_DAILY_JOBS = ("missions", "pass", "limited", "beginner", "tgw", "gifts")
# 游戏出帧率按最近这么多次同步的中位数看（见 loop.min_fps）
FPS_WINDOW = 5


def next_daily_time(now: float, at: tuple[int, int]) -> float:
    """``now``（Unix 秒）之后下一次到本地时间 ``at``（时, 分）的时刻。"""
    t = datetime.fromtimestamp(now).replace(hour=at[0], minute=at[1], second=0, microsecond=0)
    if t.timestamp() <= now:
        t += timedelta(days=1)
    return t.timestamp()


class NavigationError(RuntimeError):
    pass


class ScreenFrozen(NavigationError):
    """停在认不出的画面上、画面一直不动（多半是没见过的页面或弹窗在等人操作），重试也没用。"""


class GameUpdateRequired(ScreenFrozen):
    """游戏弹出「检测到新版本」，只能去应用商店更新安装包（只有「前往商店」，不点）：和画面卡住一样，干等没用，任务立刻停下。"""


class ServerMaintenance(NavigationError):
    """游戏显示服务器维护页：开服之前什么都做不了。"""


class LbExhausted(Exception):
    """LB 已用完（且不允许改为消耗 0 继续打）。"""


class CpExhausted(LbExhausted):
    """挑战演出的挑战pt（CP）不够一局的消耗了：和 LB 用完一样结束。"""


class SongLabel(NamedTuple):
    """乐队确认页上显示的曲目信息；识别不到的项为 None。"""

    title: str
    difficulty: str | None = None
    level: int | None = None
    jacket: tuple[int, float] | None = None  # 封面匹配到的 (musicId, 相关系数)


class Navigator(Protocol):
    def ensure_band_confirm(self, difficulty: str | None = None) -> None:
        """从任意画面进入自由演出（``loop.challenge`` 时为挑战演出）的乐队确认页（LIVE START 所在页）；
        经过乐曲选择页时选 ``difficulty``。"""
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
        挑战演出的 CP 不够一局时抛出 :class:`CpExhausted`。
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

    def next_challenge_song(self) -> None:
        """挑战演出乐曲选择页：选中下一首（最后一首之后回到第一首）。"""
        ...

    def challenge_song_ap(self, difficulty: str) -> tuple[str, bool]:
        """挑战演出乐曲选择页：选上 ``difficulty``，返回选中的歌的曲名和它在这个难度是否已经 AP。"""
        ...

    def run_daily(self, jobs) -> list[str]:
        """领取日常（``jobs`` 为 ``nav.daily.DAILY_JOBS`` 里的项目），返回出错的项目名，最后停在主界面。"""
        ...

    def relogin(self) -> None:
        """停在服务器维护页时回到标题画面重新登录，等到进入游戏；还在维护时抛 :class:`ServerMaintenance`。"""
        ...

    def restart_game(self) -> bool:
        """重启游戏，等到重新进入游戏（主界面）；不能重启时返回 False。"""
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
    maintenance: bool = False  # 因服务器维护停止


class FrameRateWatch:
    """游戏连续运行十几个小时后会越来越卡（实测 17 小时后从 60fps 掉到 30fps 左右，同步失败、整首对不上），
    重启游戏就恢复了。最近 ``window`` 次同步时测到的出帧率中位数低于 ``min_fps`` 时该重启了；
    重启后还是这么低（电脑本身忙不过来、模拟器限了帧率等），重启没用，之后不再管。"""

    def __init__(self, min_fps: float, window: int = FPS_WINDOW):
        self.min_fps = min_fps
        self.enabled = min_fps > 0
        self._recent: deque[float] = deque(maxlen=window)
        self._restarted = False  # 刚重启过，还没看到帧率恢复

    def add(self, fps: float | None) -> None:
        if fps:
            self._recent.append(fps)

    def slow(self) -> float | None:
        """该重启游戏时返回最近的出帧率（中位数），否则 None。"""
        if not self.enabled or len(self._recent) < (self._recent.maxlen or 0):
            return None
        fps = statistics.median(self._recent)
        if fps >= self.min_fps:
            self._restarted = False
            return None
        if self._restarted:
            self.enabled = False
            logger.warning("重启游戏后出帧率还是只有 %.0ffps（电脑太忙或模拟器限了帧率？），不再因为卡顿重启", fps)
            return None
        return fps

    def restarted(self) -> None:
        self._recent.clear()
        self._restarted = True


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
        self.clock = time.monotonic  # 定时领取录音室练习用（测试里换成假时钟）
        self.wall_clock = time.time  # 每天定时领取日常用（按电脑的本地时间）
        self._next_claim = 0.0  # 下次领取录音室练习的时刻：开始时先领一次
        lc = config.loop
        if lc.until_lb_empty and not config.game.lb_cost:
            raise ValueError("打到 LB 用完需要设置 game.lb_cost 为 1~3")
        if lc.wait_lb and config.game.lb_cost is None:
            raise ValueError("挂机需要设置 game.lb_cost 为 0~3")
        if str(lc.daily_claim_time).strip():
            parse_daily_time(lc.daily_claim_time)  # 时间格式不对时现在就报错
        self._next_daily: float | None = None  # 下次领取日常的时刻：开始后第一次到点时领
        self._daily_tries = 0
        self.frame_rate = FrameRateWatch(lc.min_fps)

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
        except ConnectionError as e:
            # 谱面站一时连不上：算这局失败，下一局再试（连续失败够多才停）
            raise NavigationError(str(e)) from e
        self.session.learned_offset_ms = self.store.learned_offset_ms
        lc = self.cfg.loop
        if lc.wait_lb and not self.cfg.game.lb_cost:
            lb_short = "auto"  # 挂机消耗 0：有 LB 就消耗，用完消耗 0 接着打，不等
        else:
            lb_short = "stop" if lc.until_lb_empty or lc.wait_lb else "zero"
        self.nav.start_live(lb_short)
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
            finally:
                self.frame_rate.add(self.session.last_fps)
            try:
                self.nav.retry_live()
            except ServerMaintenance:
                raise
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
        except (ScreenFrozen, ServerMaintenance):
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

    def _claim_studio(self) -> None:
        """到时间就去领录音室练习（收获，``loop.studio_claim_hours``，开始时先领一次），领完停在主界面，
        下一局开头的导航会回到乐队确认页。领不成只记录，过一会儿（最多 STUDIO_RETRY_S）再领；
        画面卡住不动、服务器维护时照常停止。"""
        hours = self.cfg.loop.studio_claim_hours
        if hours <= 0 or self.clock() < self._next_claim:
            return
        logger.info("领取录音室练习（收获）")
        try:
            ok = not self.nav.run_daily(["studio"])  # 领取时出的错 run_daily 自己记了
        except (ScreenFrozen, ServerMaintenance):
            raise
        except (NavigationError, TimeoutError) as e:
            if self.stop.is_set():
                return
            logger.error("领取录音室练习：%s", e)
            ok = False
        wait = hours * 3600 if ok else min(hours * 3600, STUDIO_RETRY_S)
        self._next_claim = self.clock() + wait
        if not ok:
            logger.warning("录音室练习没领成，%.0f 分钟后再领", wait / 60)

    @property
    def _daily_at(self) -> tuple[int, int] | None:
        """每天领取日常的时间（时, 分），不领时为 None。"""
        text = self.cfg.loop.daily_claim_time
        return parse_daily_time(text) if str(text).strip() else None

    def _daily_due(self) -> float:
        """下次领取日常的时刻（Unix 秒）。"""
        if self._next_daily is None:
            self._next_daily = next_daily_time(self.wall_clock(), self._daily_at)
        return self._next_daily

    def _claim_daily(self) -> None:
        """挂机每天到 ``loop.daily_claim_time`` 回主界面领一次日常（任务列表里的「领取日常」只在挂机前跑一次，
        挂机跨天后就领不到了；游戏 23:00 日期变更，默认 22:30 领）。有没领成的隔 DAILY_RETRY_S 再领一遍，
        一天最多 DAILY_TRIES 次；画面卡住不动、服务器维护时照常停止。"""
        at = self._daily_at
        if at is None or self.wall_clock() < self._daily_due():
            return
        self._daily_tries += 1
        logger.info("领取日常（每天 %d:%02d）", *at)
        try:
            ok = not self.nav.run_daily(IDLE_DAILY_JOBS)  # 领取时出的错 run_daily 自己记了
        except (ScreenFrozen, ServerMaintenance):
            raise
        except (NavigationError, TimeoutError) as e:
            if self.stop.is_set():
                return
            logger.error("领取日常：%s", e)
            ok = False
        now = self.wall_clock()
        if not ok and self._daily_tries < DAILY_TRIES:
            self._next_daily = now + DAILY_RETRY_S
            logger.warning("日常没领全，%.0f 分钟后再领一遍", DAILY_RETRY_S / 60)
            return
        if not ok:
            logger.warning("日常领了 %d 遍还没领全，明天再领", self._daily_tries)
        self._daily_tries = 0
        self._next_daily = next_daily_time(now, at)

    def _restart_if_slow(self) -> None:
        """游戏变卡了（见 :class:`FrameRateWatch`）就重启游戏，回到主界面后照常换歌。"""
        fps = self.frame_rate.slow()
        if fps is None:
            return
        logger.warning("最近几局游戏只有 %.0ffps（连续运行太久会越来越卡，容易同步失败、打错），重启游戏", fps)
        self.frame_rate.restarted()  # 重启后没能回到游戏（抛异常）时交给下一局的导航，不接着重启
        if not self.nav.restart_game():
            self.frame_rate.enabled = False

    def _claim_timed(self) -> None:
        """挂机的定时领取：录音室练习、每天的日常（到时间才领）。"""
        self._claim_studio()
        if not self.stop.is_set():
            self._claim_daily()

    def _claim_wait(self) -> float:
        """距下次定时领取还有多少秒（都不领时为无穷大）。"""
        wait = float("inf")
        if self.cfg.loop.studio_claim_hours > 0:
            wait = max(0.0, self._next_claim - self.clock())
        if self._daily_at is not None:
            wait = min(wait, max(0.0, self._daily_due() - self.wall_clock()))
        return wait

    def _wait_lb(self) -> None:
        """挂机：停在乐队确认页等 LB 恢复到每局消耗数（LB 少于它时每局奖励也少）。
        每次醒来都重新进入乐队确认页：等待期间游戏可能日期变更、回到标题画面重新登录；
        到了定时领取录音室练习、日常的时间也会醒来去领。"""
        need = self.cfg.game.lb_cost
        last: int | None = -1
        while True:
            self._claim_timed()
            if self.stop.is_set():
                raise NavigationError("已停止")
            self.nav.ensure_band_confirm(self.source.difficulty)
            held, left = self.nav.lb_status()
            if held is not None and held >= need:
                # 顶栏的小字偶尔读错：继续前用弹窗核对（读多了会一开始又说用完，反复点 LIVE START）
                held, _ = self.nav.lb_status(check=True)
            if held is not None and held >= need:
                logger.info("LB 恢复到 %d 个，继续", held)
                return
            wait = LB_POLL_S if left is None else min(LB_POLL_S, left + LB_POLL_MARGIN_S)
            wait = min(wait, self._claim_wait())
            if held != last:  # 每恢复一个报一次
                eta = "" if left is None else f"，下一个约 {left // 60}:{left % 60:02d} 后恢复"
                logger.info("LB 持有 %s 个，每局消耗 %d 个：等待恢复%s", "?" if held is None else held, need, eta)
                last = held
            else:
                logger.debug("LB 持有 %s 个，%.0fs 后再看", held, wait)
            if self.stop.wait(wait):
                raise NavigationError("已停止")

    def _wait_maintenance(self, e: ServerMaintenance) -> None:
        """挂机遇到服务器维护：每隔 MAINTENANCE_POLL_S 回到标题画面重新登录一次，开服、进到游戏后返回。
        重新登录遇到别的问题时也返回，交给下一局开头的导航（还在维护会再回到这里）。"""
        logger.warning("%s，每 %.3g 分钟回到标题画面看一次开服没有", e, MAINTENANCE_POLL_S / 60)
        while not self.stop.wait(MAINTENANCE_POLL_S):
            try:
                self.nav.relogin()
            except ServerMaintenance:
                logger.info("还在维护")
                continue
            except ScreenFrozen:
                raise
            except (NavigationError, TimeoutError) as e:
                if not self.stop.is_set():
                    logger.warning("维护后重新登录：%s", e)
                return
            logger.info("已开服，继续挂机")
            return

    def run(self) -> RunStats:
        try:
            self._loop()
        except (ScreenFrozen, ServerMaintenance) as e:
            # 干等画面不会变、维护期间什么都做不了，重试也没用（恢复选曲页的设置同样做不了）
            logger.error("%s，停止", e)
            self.stats.failures += 1
            self.stats.maintenance = isinstance(e, ServerMaintenance)
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
            try:
                if not wait_lb:
                    self._restart_if_slow()
                    self._claim_timed()  # 在换歌之前领：领完从主界面回来再选歌
                    if self.stop.is_set() or not self._advance(first) or self.stop.is_set():
                        break
                first = False
                if wait_lb:
                    self._wait_lb()
                    wait_lb = False
                result = self.play_once()
            except ServerMaintenance as e:
                # 挂机等到开服接着挂（这局没开始或中途被打断，不算失败；之后照常换歌）；其他任务直接失败
                if not lc.wait_lb:
                    raise
                self._wait_maintenance(e)
                continue
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
