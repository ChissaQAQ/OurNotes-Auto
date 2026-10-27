"""统计与成就：从演奏记录（``data/records.jsonl``）和运行记录（``data/sessions.jsonl``）算出累计数据和成就，
写成几个页面：

- ``data/stats.html``：「统计与成就」窗口（见 :mod:`.stats_window`）里显示的完整页面，打完一局自动刷新；
- ``data/stats.md``：界面任务「打开统计窗口」的说明（两个界面都把这个文件当说明显示）；
- ``data/stats_mxu.md``：MXU 设置页「统计与成就」卡片的内容，卡片上按钮的图片是 ``data/stats_button_<记号>.svg``；
- ``data/stats_live.js``：正在运行的打歌任务在做什么（演奏哪首、等 LB……），统计页每隔几秒读一次、就地更新。

只是给人看的：出的错只记警告，不影响打歌（见 :class:`Tracker`）。Agent 不导入。
"""

from __future__ import annotations

import bisect
import hashlib
import itertools
import json
import logging
import math
import os
import time
from collections import Counter
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from urllib.parse import quote

from . import release, stats_window
from .agent.logs import client_name
from .records import PlayResult, RecordStore
from .report import ChartStats
from .sources import DIFFICULTIES

logger = logging.getLogger(__name__)

SESSIONS = "sessions.jsonl"  # 每次打歌任务一行
RUNNING = "running"  # 正在运行的打歌任务各一个 <id>.json（运行期间锁住同名的 .lock）；进程被强行结束时留下，下次开始时补进 SESSIONS
UNLOCKED = "achievements.json"  # 已解锁的成就和时间（记录被删了也不丢）
IMPORT_LOCK = "import.lock"  # 导入备份期间锁住（见 backup.py）；打歌任务开始时等它
IMPORT_WAIT_S = 120.0  # 最多等导入多久
PAGE_MD = "stats.md"
PAGE_MXU = "stats_mxu.md"
PAGE_HTML = "stats.html"
PAGE_VER = "stats_ver.js"  # 内容是 statsVer("<版本>")：页面每隔几秒读一次，版本变了就重新载入（file:// 下不能 fetch）
VER_MARK = "__STATS_VER__"  # 页面里写版本的地方
PAGE_LIVE = "stats_live.js"  # 内容是 statsLive([...])：正在运行的各在做什么（见 write_live），页面就地更新、不重新载入
MXU_CONFIG = Path("config", "mxu-OurNotesAuto.json")  # MXU 的配置：config/mxu-<interface.json 的 name>.json
MXU_PORT = 12701  # MXU 自带的本机网页服务的默认端口（MXU 设置里能改）
MXU_PORTS = 10  # 端口被占时 MXU 不提示，依次改用后面的端口，连同设置的一共试这么多个
PAGE_BUTTON = "stats_button_{}.svg"  # MXU 卡片上「在浏览器里打开」按钮的图片，{} 是这个包的记号（见 mxu_buttons）
PAGE_SIZE = 50  # 运行记录页上每页列出几次运行、几局
GAP_S = 600.0  # 没有运行记录的时段（统计功能加入之前）按演奏记录估算时长：相邻两局隔不到这么久算一直在运行
MERGE_S = 180.0  # 为换新版本退出的运行（挂机）隔不到这么久又开始了，算同一次连续运行；手动停止后再开始的不算


@dataclass
class Session:
    """一次打歌任务（重复刷歌、挂机……）的运行记录。"""

    start: float
    end: float
    task: str = ""
    idle: bool = False  # 挂机
    plays: int = 0
    fc: int = 0
    ap: int = 0
    failures: int = 0
    skips: int = 0
    lb_wait_s: float = 0.0  # 挂机时等 LB 恢复的时间
    restart: bool = False  # 为换上新版本退出（挂机），接着会用同样的参数重新开始

    @property
    def seconds(self) -> float:
        return max(0.0, self.end - self.start)


def parse_session(d) -> Session | None:
    """``sessions.jsonl`` 的一行（已经按 JSON 读出来的）；不认识的字段不管，读不了为 None。"""
    if not isinstance(d, dict):
        return None
    try:
        return Session(**{k: v for k, v in d.items() if k in Session.__dataclass_fields__})
    except TypeError:
        return None


def load_sessions(data_dir: Path) -> list[Session]:
    path = data_dir / SESSIONS
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            s = parse_session(json.loads(line))
        except ValueError:
            continue
        if s is not None:
            out.append(s)
    return out


def running_files(data_dir: Path) -> list[tuple[Path, Session | None]]:
    """正在运行的打歌任务的文件和内容（读不了的为 None），也包括进程被强行结束、还没补记的。"""
    out = []
    for path in sorted((data_dir / RUNNING).glob("*.json")):
        try:
            out.append((path, parse_session(json.loads(path.read_text(encoding="utf-8")))))
        except (OSError, ValueError):
            out.append((path, None))
    return out


def live_key(s: Session) -> str:
    """统计页上正在运行的那条和 ``stats_live.js`` 里的哪一项对应。"""
    return f"{s.start:.1f}"


def write_live(data_dir: Path) -> list[dict]:
    """写出 ``stats_live.js``：正在运行（锁着）的打歌任务各在做什么，即 ``running/<id>.json`` 的 ``now``
    （见 :meth:`Tracker.status`），加上 ``key`` 和最后一次更新的时刻 ``upd``（进程被强行结束后不再更新，页面上显示可能已停止）。"""
    rows = []
    for path in sorted((data_dir / RUNNING).glob("*.json")):
        if not release.is_locked(path.with_suffix(".lock")):
            continue
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        s = parse_session(d)
        if s is not None:
            now = d.get("now") if isinstance(d.get("now"), dict) else {}
            rows.append({**now, "key": live_key(s), "upd": round(s.end, 1)})
    release.write_text(data_dir / PAGE_LIVE, f"statsLive({json.dumps(rows, ensure_ascii=False)});\n")
    return rows


def session_key(s: Session) -> tuple[float, str]:
    """同一次运行的记号：补记和结束时的记录、导入的和已有的可能重复。"""
    return round(s.start, 1), s.task


def session_row(s: Session) -> dict:
    """写进 ``sessions.jsonl`` 的一行。"""
    row = asdict(s)
    row["start"], row["end"] = round(s.start, 1), round(s.end, 1)
    return row


def _dedupe(sessions: list[Session]) -> list[Session]:
    """同一次运行只留更新到最后的那条（一样时留后面的）。"""
    out: dict[tuple[float, str], Session] = {}
    for s in sessions:
        key = session_key(s)
        if key not in out or s.end >= out[key].end:
            out[key] = s
    return list(out.values())


@dataclass
class Library:
    """曲库：曲名、乐队、各谱面的等级。按乐队、等级算的成就要用；读不了曲库时为空，这些成就不前进。"""

    titles: dict[int, str] = field(default_factory=dict)
    bands: dict[int, str] = field(default_factory=dict)  # 曲库里的每首歌 → 乐队（不知道时为空字符串）
    levels: dict[tuple[int, str], int] = field(default_factory=dict)
    expert: int = field(default=0, init=False)  # 有 EXPERT 的歌
    band_expert: Counter = field(default_factory=Counter, init=False)  # 乐队 → 有 EXPERT 的歌

    def __post_init__(self) -> None:
        for (mid, diff) in self.levels:
            if diff == "expert" and mid in self.bands:
                self.expert += 1
                self.band_expert[self.bands[mid]] += 1

    @classmethod
    def from_catalog(cls, catalog) -> Library:
        """``catalog`` 是 :class:`~ournotes_auto.charts.catalog.Catalog`，没有时为空的曲库。"""
        if catalog is None:
            return cls()
        songs = catalog.songs.values()
        levels = {(s.music_id, d): s.level(d) for s in songs for d in s.difficulties}
        return cls(
            {s.music_id: s.display_title() for s in songs},
            {s.music_id: s.band for s in songs},
            {k: v for k, v in levels.items() if isinstance(v, int)},
        )

    def title(self, mid: int) -> str:
        return self.titles.get(mid) or str(mid)


HARD_LEVEL = 26  # 「难关收割」算这个等级以上的谱面
NIGHT_HOURS = (2, 3, 4)  # 「夜猫子」：凌晨 2-5 点
# 彩蛋：在这些时候打完一局（本地时间）
DATE_EGGS: dict[str, Callable[[time.struct_time], bool]] = {
    "friday_13": lambda lt: lt.tm_mday == 13 and lt.tm_wday == 4,
    "april_fools": lambda lt: (lt.tm_mon, lt.tm_mday) == (4, 1),
    "leap_day": lambda lt: (lt.tm_mon, lt.tm_mday) == (2, 29),
    "halloween": lambda lt: (lt.tm_mon, lt.tm_mday) == (10, 31),
    "christmas": lambda lt: (lt.tm_mon, lt.tm_mday) == (12, 25),
    "new_year": lambda lt: (lt.tm_mon, lt.tm_mday, lt.tm_hour) == (1, 1, 0),
    "midnight": lambda lt: (lt.tm_hour, lt.tm_min) == (0, 0),
}


def _palindrome(score: int) -> bool:
    s = str(score)
    return len(s) >= 6 and s == s[::-1]


@dataclass
class Totals:
    """按时间顺序一局一局（一次一次运行）累加出来的数据。"""

    plays: int = 0
    fc: int = 0
    ap: int = 0
    unread: int = 0  # 结算没读全
    notes: int = 0
    perfect: int = 0
    score: int = 0
    max_combo: int = 0
    streak: int = 0  # 当前连续 AP 局数（结算没读全的局不算也不打断）
    best_streak: int = 0
    first: float | None = None
    per_day: Counter = field(default_factory=Counter)  # 本地日期 → 局数
    ap_per_day: Counter = field(default_factory=Counter)
    per_hour: Counter = field(default_factory=Counter)  # 几点（0-23）→ 局数
    best_day: int = 0
    best_day_date: date | None = None
    day_streak: int = 0  # 连续几天都有演奏
    best_day_streak: int = 0
    charts: dict[tuple[int, str], ChartStats] = field(default_factory=dict)
    charts_ap: int = 0  # 打出过 AP 的谱面
    songs_all_ap: int = 0  # 四个难度都打出过 AP 的歌
    max_chart_plays: int = 0
    late_first_ap: int = 0  # 打到第几局才第一次 AP（各谱面里最多的）
    run_s: float = 0.0  # 累计运行
    estimated_s: float = 0.0  # 其中按演奏记录估算的
    idle_s: float = 0.0  # 其中挂机
    lb_wait_s: float = 0.0
    skips: int = 0
    runs: int = 0
    longest_run_s: float = 0.0
    last: float | None = None
    best_ap_day: int = 0
    best_score: int = 0
    first_try_ap: int = 0  # 第一局就 AP 的谱面
    songs: int = 0  # 打过的歌
    songs_known: int = 0  # 其中曲库里有的
    expert_ap: int = 0  # 打出过 AP 的 EXPERT 谱面
    expert_known_ap: int = 0  # 其中曲库里有的
    top_level_ap: int = 0  # 打出过 AP 的谱面里最高的等级
    hard_ap: int = 0  # 打出过 AP 的 HARD_LEVEL 级以上的谱面
    max_chart_gap_days: float = 0.0  # 同一张谱面前后两局隔得最久的
    loop: int = 0  # 当前同一张谱面连续演奏的局数
    best_loop: int = 0
    band_plays: Counter = field(default_factory=Counter)  # 乐队 → 局数
    band_ap: set[str] = field(default_factory=set)  # 打出过 AP 的乐队
    band_expert_ap: Counter = field(default_factory=Counter)  # 乐队 → 打出过 AP 的 EXPERT 谱面
    one_great: int = 0  # 只差一个 GREAT（其余全 PERFECT）的局
    one_miss: int = 0  # 只有一个 MISS、其余全 PERFECT 的局
    balanced: int = 0  # FAST 和 SLOW 一样多的局
    stall_ap: int = 0  # 模拟器卡了还 AP 的局
    lucky: int = 0  # 分数末尾是 777 的局
    palindrome: int = 0  # 分数是回文的局
    heartbreak: int = 0  # 连续 AP 停在 99 局
    eggs: set[str] = field(default_factory=set)  # 碰上过的 DATE_EGGS
    overlaps: int = 0  # 和别的运行同时在跑的运行
    lib: Library = field(default_factory=Library, repr=False)
    _song_ap: dict[int, set[str]] = field(default_factory=dict, repr=False)
    _songs: set[int] = field(default_factory=set, repr=False)
    _last_chart: tuple[int, str] | None = field(default=None, repr=False)
    _last_day: date | None = field(default=None, repr=False)
    _run: float = field(default=0.0, repr=False)
    _run_end: float | None = field(default=None, repr=False)
    _resume: bool = field(default=False, repr=False)  # 上一次运行是为换新版本退出的，接着的这次算连续运行
    _max_end: float | None = field(default=None, repr=False)

    def add_play(self, r: PlayResult) -> None:
        self.plays += 1
        self.fc += bool(r.full_combo)
        self.ap += bool(r.all_perfect)
        self.unread += r.full_combo is None
        self.notes += sum(v for v in (r.perfect, r.great, r.good, r.bad, r.miss) if v is not None)
        self.perfect += r.perfect or 0
        self.score += r.score or 0
        self.max_combo = max(self.max_combo, r.max_combo or 0)
        self.best_score = max(self.best_score, r.score or 0)
        if r.all_perfect:
            self.streak += 1
            self.best_streak = max(self.best_streak, self.streak)
        elif r.full_combo is not None:
            self.heartbreak += self.streak == 99
            self.streak = 0
        self._add_fun(r)
        if self.first is None:
            self.first = r.timestamp
        self.last = r.timestamp
        day = date.fromtimestamp(r.timestamp)
        if day not in self.per_day:
            self.day_streak = self.day_streak + 1 if self._last_day == day - timedelta(days=1) else 1
            self.best_day_streak = max(self.best_day_streak, self.day_streak)
            self._last_day = day
        self.per_day[day] += 1
        self.ap_per_day[day] += bool(r.all_perfect)
        self.best_ap_day = max(self.best_ap_day, self.ap_per_day[day])
        lt = time.localtime(r.timestamp)
        self.per_hour[lt.tm_hour] += 1
        self.eggs.update(k for k, test in DATE_EGGS.items() if test(lt))
        if self.per_day[day] > self.best_day:
            self.best_day, self.best_day_date = self.per_day[day], day
        key = (r.music_id, r.difficulty)
        self.loop = self.loop + 1 if key == self._last_chart else 1
        self.best_loop = max(self.best_loop, self.loop)
        self._last_chart = key
        band = self.lib.bands.get(r.music_id)
        if r.music_id not in self._songs:
            self._songs.add(r.music_id)
            self.songs += 1
            self.songs_known += band is not None
        if band:
            self.band_plays[band] += 1
        chart = self.charts.setdefault(key, ChartStats())
        if chart.plays:
            self.max_chart_gap_days = max(self.max_chart_gap_days, (r.timestamp - chart.last) / 86400)
        had_ap = chart.ap > 0
        chart.add(r)
        self.max_chart_plays = max(self.max_chart_plays, chart.plays)
        if chart.ap and not had_ap:
            self._first_ap(r, chart.plays, band)

    def _first_ap(self, r: PlayResult, plays: int, band: str | None) -> None:
        """一张谱面第一次 AP（这是第 ``plays`` 局）。"""
        self.charts_ap += 1
        self.late_first_ap = max(self.late_first_ap, plays)
        self.first_try_ap += plays == 1
        diffs = self._song_ap.setdefault(r.music_id, set())
        diffs.add(r.difficulty)
        self.songs_all_ap += len(diffs) == len(DIFFICULTIES)
        level = self.lib.levels.get((r.music_id, r.difficulty), 0)
        self.top_level_ap = max(self.top_level_ap, level)
        self.hard_ap += level >= HARD_LEVEL
        if band:
            self.band_ap.add(band)
        if r.difficulty == "expert":
            self.expert_ap += 1
            self.expert_known_ap += band is not None
            if band:
                self.band_expert_ap[band] += 1

    def _add_fun(self, r: PlayResult) -> None:
        """「趣味」成就看的几种局。"""
        clean = r.good == 0 and r.bad == 0
        self.one_great += clean and r.great == 1 and r.miss == 0
        self.one_miss += clean and r.great == 0 and r.miss == 1
        self.balanced += r.fast is not None and r.fast > 0 and r.fast == r.slow
        self.stall_ap += bool(r.stalls) and bool(r.all_perfect)
        if r.score:
            self.lucky += r.score % 1000 == 777
            self.palindrome += _palindrome(r.score)

    def add_session(self, s: Session) -> None:
        self.runs += 1
        self.run_s += s.seconds
        self.idle_s += s.seconds if s.idle else 0.0
        self.lb_wait_s += s.lb_wait_s
        self.skips += s.skips
        if self._resume and self._run_end is not None and 0 <= s.start - self._run_end <= MERGE_S:
            self._run += s.end - self._run_end
        else:
            self._run = s.seconds
        self._run_end, self._resume = s.end, s.restart
        self.longest_run_s = max(self.longest_run_s, self._run)
        if self._max_end is not None and s.start < self._max_end - 1:  # 按结束时间先后加进来
            self.overlaps += 1
        self._max_end = s.end if self._max_end is None else max(self._max_end, s.end)


@dataclass(frozen=True)
class Achievement:
    id: str
    group: str
    name: str
    desc: str
    target: float
    value: Callable[[Totals], float]
    unit: str = ""  # 「%」的按完成了百分之几算
    hidden: bool = False  # 解锁前只显示「???」和 hint
    tier: int = 1  # 徽章的等级，见 TIERS
    hint: str = ""  # 隐藏成就解锁前的提示


GROUPS = {"演奏": "♪", "完美": "★", "音符": "♫", "谱面": "◆", "乐队": "♬", "时长": "◔", "日常": "✦", "趣味": "☺"}  # 分组 → 徽章上的符号
TIERS = {1: "铜", 2: "银", 3: "金", 4: "传说"}


def _hours(t: Totals) -> float:
    return t.run_s / 3600


def _percent(done: int, total: int) -> float:
    """完成了百分之几；曲库为空时为 0。"""
    return 100 * min(done, total) / total if total else 0.0


def _band_done(t: Totals) -> float:
    """完成得最多的乐队：EXPERT 打出过 AP 的歌占这个乐队的百分之几。"""
    return max((_percent(t.band_expert_ap[b], n) for b, n in t.lib.band_expert.items() if b), default=0.0)


def _fun(id: str, name: str, desc: str, hint: str, target: float, value: Callable[[Totals], float], unit: str = "局", tier: int = 1) -> Achievement:
    """「趣味」：都是隐藏的。"""
    return Achievement(id, "趣味", name, desc, target, value, unit, hidden=True, tier=tier, hint=hint)


def _egg(id: str, name: str, desc: str, hint: str, tier: int = 1) -> Achievement:
    """在 DATE_EGGS 里的日子演奏过。"""
    return _fun(id, name, desc, hint, 1, lambda t: id in t.eggs, tier=tier)


ACHIEVEMENTS: tuple[Achievement, ...] = (
    Achievement("first_play", "演奏", "初次登台", "用本工具打完第一局", 1, lambda t: t.plays, "局"),
    Achievement("plays_100", "演奏", "小有名气", "累计演奏 100 局", 100, lambda t: t.plays, "局"),
    Achievement("plays_1000", "演奏", "千场巡演", "累计演奏 1,000 局", 1000, lambda t: t.plays, "局", tier=2),
    Achievement("plays_5000", "演奏", "人气乐队", "累计演奏 5,000 局", 5000, lambda t: t.plays, "局", tier=3),
    Achievement("plays_10000", "演奏", "万场不倦", "累计演奏 10,000 局", 10000, lambda t: t.plays, "局", tier=3),
    Achievement("plays_30000", "演奏", "传奇舞台", "累计演奏 30,000 局", 30000, lambda t: t.plays, "局", tier=4),
    Achievement("plays_100000", "演奏", "十万回响", "累计演奏 100,000 局", 100_000, lambda t: t.plays, "局", hidden=True, tier=4, hint="比「传奇舞台」更远的地方"),
    Achievement("first_fc", "完美", "一气呵成", "第一次 FULL COMBO", 1, lambda t: t.fc, "局"),
    Achievement("first_ap", "完美", "完美初体验", "第一次 ALL PERFECT", 1, lambda t: t.ap, "局"),
    Achievement("ap_100", "完美", "完美主义", "累计 100 局 ALL PERFECT", 100, lambda t: t.ap, "局", tier=2),
    Achievement("ap_1000", "完美", "千次完美", "累计 1,000 局 ALL PERFECT", 1000, lambda t: t.ap, "局", tier=3),
    Achievement("ap_10000", "完美", "万无一失", "累计 10,000 局 ALL PERFECT", 10000, lambda t: t.ap, "局", tier=4),
    Achievement("ap_day_100", "完美", "完美一日", "一天里打出 100 局 ALL PERFECT", 100, lambda t: t.best_ap_day, "局", tier=2),
    Achievement("streak_10", "完美", "渐入佳境", "连续 10 局 ALL PERFECT", 10, lambda t: t.best_streak, "局"),
    Achievement("streak_100", "完美", "心无旁骛", "连续 100 局 ALL PERFECT", 100, lambda t: t.best_streak, "局", tier=3),
    Achievement("streak_500", "完美", "无懈可击", "连续 500 局 ALL PERFECT", 500, lambda t: t.best_streak, "局", hidden=True, tier=4, hint="一口气连续 AP 很多很多局"),
    Achievement("streak_1000", "完美", "神之手", "连续 1,000 局 ALL PERFECT", 1000, lambda t: t.best_streak, "局", hidden=True, tier=4, hint="比「无懈可击」还要长的连续 AP"),
    Achievement("first_try_1", "完美", "一发入魂", "一张谱面第一局就打出 AP", 1, lambda t: t.first_try_ap, "张"),
    Achievement("first_try_100", "完美", "初见杀", "100 张谱面第一局就打出 AP", 100, lambda t: t.first_try_ap, "张", tier=3),
    Achievement("notes_100k", "音符", "十万音符", "累计打出 100,000 个音符", 100_000, lambda t: t.notes, "个"),
    Achievement("notes_1m", "音符", "百万音符", "累计打出 1,000,000 个音符", 1_000_000, lambda t: t.notes, "个", tier=2),
    Achievement("notes_10m", "音符", "千万音符", "累计打出 10,000,000 个音符", 10_000_000, lambda t: t.notes, "个", tier=3),
    Achievement("notes_50m", "音符", "音符之海", "累计打出 50,000,000 个音符", 50_000_000, lambda t: t.notes, "个", tier=4),
    Achievement("combo_1000", "音符", "千连击", "一局的最大连击达到 1,000", 1000, lambda t: t.max_combo, "连击", tier=2),
    Achievement("combo_1500", "音符", "连击风暴", "一局的最大连击达到 1,500", 1500, lambda t: t.max_combo, "连击", tier=3),
    Achievement("score_3m", "音符", "三百万分", "一局得分达到 3,000,000", 3_000_000, lambda t: t.best_score, "分", tier=2),
    Achievement("score_1e9", "音符", "十亿里程", "累计分数达到 10 亿", 1e9, lambda t: t.score, "分"),
    Achievement("score_1e10", "音符", "百亿里程", "累计分数达到 100 亿", 1e10, lambda t: t.score, "分", tier=2),
    Achievement("score_1e11", "音符", "千亿里程", "累计分数达到 1,000 亿", 1e11, lambda t: t.score, "分", tier=4),
    Achievement("charts_10", "谱面", "初窥曲库", "10 张不同的谱面打出过 AP", 10, lambda t: t.charts_ap, "张"),
    Achievement("charts_100", "谱面", "百曲通", "100 张不同的谱面打出过 AP", 100, lambda t: t.charts_ap, "张", tier=2),
    Achievement("charts_300", "谱面", "曲库大师", "300 张不同的谱面打出过 AP", 300, lambda t: t.charts_ap, "张", tier=3),
    Achievement("songs_50", "谱面", "曲库漫游", "50 首不同的歌都打过", 50, lambda t: t.songs, "首", tier=2),
    Achievement("songs_all", "谱面", "全曲巡礼", "曲库里的每首歌都打过", 100, lambda t: _percent(t.songs_known, len(t.lib.bands)), "%", tier=3),
    Achievement("expert_ap_50", "谱面", "专家认证", "50 张 EXPERT 谱面打出过 AP", 50, lambda t: t.expert_ap, "张", tier=3),
    Achievement("level_28", "谱面", "高难挑战", "一张 28 级以上的谱面打出 AP", 28, lambda t: t.top_level_ap, "级", tier=2),
    Achievement("hard_ap_10", "谱面", "难关收割", f"10 张 {HARD_LEVEL} 级以上的谱面打出过 AP", 10, lambda t: t.hard_ap, "张", tier=3),
    Achievement("all_diffs", "谱面", "四面俱到", "一首歌的四个难度都打出过 AP", 1, lambda t: t.songs_all_ap, "首", tier=2),
    Achievement("all_diffs_50", "谱面", "难度通吃", "50 首歌的四个难度都打出过 AP", 50, lambda t: t.songs_all_ap, "首", tier=4),
    Achievement("all_expert", "谱面", "全曲制霸", "曲库里每首歌的 EXPERT 都打出过 AP", 100, lambda t: _percent(t.expert_known_ap, t.lib.expert), "%", tier=4),
    Achievement("same_chart_100", "谱面", "单曲循环", "同一张谱面演奏 100 局", 100, lambda t: t.max_chart_plays, "局", hidden=True, tier=2, hint="反复打同一张谱面"),
    Achievement("same_chart_1000", "谱面", "刻进 DNA", "同一张谱面演奏 1,000 局", 1000, lambda t: t.max_chart_plays, "局", hidden=True, tier=3, hint="某张谱面你是不是太喜欢了点"),
    Achievement("late_ap", "谱面", "屡败屡战", "一张谱面打到第 3 局以上才第一次 AP", 3, lambda t: t.late_first_ap, "局", hidden=True, hint="多试几次总会成功的"),
    Achievement("late_ap_10", "谱面", "百折不挠", "一张谱面打到第 10 局以上才第一次 AP", 10, lambda t: t.late_first_ap, "局", hidden=True, tier=2, hint="有一张谱面让你吃了不少苦头"),
    Achievement("reunion", "谱面", "久别重逢", "隔了 30 天以上再打同一张谱面", 30, lambda t: t.max_chart_gap_days, "天", hidden=True, hint="和很久没见的谱面再会"),
    Achievement("bands_ap", "乐队", "全员集合", "5 个乐队的歌都打出过 AP", 5, lambda t: len(t.band_ap), "个", tier=2),
    Achievement("bands_100", "乐队", "雨露均沾", "5 个乐队的歌都演奏过 100 局", 5, lambda t: sum(n >= 100 for n in t.band_plays.values()), "个", tier=2),
    Achievement("band_1000", "乐队", "铁杆粉丝", "同一个乐队的歌演奏 1,000 局", 1000, lambda t: max(t.band_plays.values(), default=0), "局", tier=2),
    Achievement("band_5000", "乐队", "乐队专属", "同一个乐队的歌演奏 5,000 局", 5000, lambda t: max(t.band_plays.values(), default=0), "局", tier=3),
    Achievement("band_all_expert", "乐队", "乐队毕业", "一个乐队的每首歌的 EXPERT 都打出过 AP", 100, _band_done, "%", tier=3),
    Achievement("hours_10", "时长", "初露锋芒", "累计运行 10 小时", 10, _hours, "小时"),
    Achievement("hours_100", "时长", "勤勤恳恳", "累计运行 100 小时", 100, _hours, "小时", tier=2),
    Achievement("hours_500", "时长", "不眠乐手", "累计运行 500 小时", 500, _hours, "小时", tier=3),
    Achievement("hours_2000", "时长", "全年无休", "累计运行 2,000 小时", 2000, _hours, "小时", tier=4),
    Achievement("idle_100h", "时长", "挂机达人", "挂机累计 100 小时", 100, lambda t: t.idle_s / 3600, "小时", tier=2),
    Achievement("run_12h", "时长", "通宵演出", "一次连续运行 12 小时", 12, lambda t: t.longest_run_s / 3600, "小时", tier=2),
    Achievement("run_24h", "时长", "全天候", "一次连续运行 24 小时", 24, lambda t: t.longest_run_s / 3600, "小时", hidden=True, tier=3, hint="让它一直跑下去"),
    Achievement("run_48h", "时长", "永不落幕", "一次连续运行 48 小时", 48, lambda t: t.longest_run_s / 3600, "小时", hidden=True, tier=4, hint="比「全天候」还要久"),
    Achievement("lb_wait_24h", "时长", "耐心等待", "挂机时累计等 LB 恢复 24 小时", 24, lambda t: t.lb_wait_s / 3600, "小时", tier=2),
    Achievement("runs_100", "时长", "常驻乐手", "运行打歌任务 100 次", 100, lambda t: t.runs, "次", tier=2),
    Achievement("skips_100", "时长", "精打细算", "用「跳过」消耗 LB 100 次", 100, lambda t: t.skips, "次"),
    Achievement("days_7", "日常", "一周常客", "7 天有演奏", 7, lambda t: len(t.per_day), "天"),
    Achievement("days_30", "日常", "月度乐手", "30 天有演奏", 30, lambda t: len(t.per_day), "天", tier=2),
    Achievement("days_100", "日常", "百日坚持", "100 天有演奏", 100, lambda t: len(t.per_day), "天", tier=3),
    Achievement("days_365", "日常", "四季轮回", "365 天有演奏", 365, lambda t: len(t.per_day), "天", tier=4),
    Achievement("day_streak_7", "日常", "风雨无阻", "连续 7 天每天都有演奏", 7, lambda t: t.best_day_streak, "天", tier=2),
    Achievement("day_streak_30", "日常", "日日不辍", "连续 30 天每天都有演奏", 30, lambda t: t.best_day_streak, "天", tier=3),
    Achievement("day_streak_100", "日常", "百日不断", "连续 100 天每天都有演奏", 100, lambda t: t.best_day_streak, "天", tier=4),
    Achievement("all_hours", "日常", "二十四时", "0 点到 23 点的每个钟点都演奏过", 24, lambda t: len(t.per_hour), "个", tier=2),
    Achievement("night_100", "日常", "夜猫子", "凌晨 2-5 点累计演奏 100 局", 100, lambda t: sum(t.per_hour[h] for h in NIGHT_HOURS), "局", hidden=True, hint="大家都睡着的时候"),
    Achievement("day_300", "日常", "爆肝之日", "一天里演奏 300 局", 300, lambda t: t.best_day, "局", hidden=True, tier=3, hint="某一天打了特别多局"),
    Achievement("day_500", "日常", "肝帝", "一天里演奏 500 局", 500, lambda t: t.best_day, "局", hidden=True, tier=4, hint="比「爆肝之日」还要拼"),
    Achievement("anniversary", "日常", "一周年", "第一局之后满一年还在演奏", 365, lambda t: (t.last - t.first) / 86400 if t.first else 0, "天", hidden=True, tier=3, hint="时间过得真快"),
    _fun("one_great", "白璧微瑕", "只差一个 GREAT 就 ALL PERFECT", "离完美只差一点点", 1, lambda t: t.one_great),
    _fun("one_miss", "百密一疏", "只有一个 MISS，其余全是 PERFECT", "偏偏漏了一个", 1, lambda t: t.one_miss),
    _fun("heartbreak_99", "功亏一篑", "连续 AP 停在了第 99 局", "就差一局到三位数", 1, lambda t: t.heartbreak, tier=2),
    _fun("balanced", "不偏不倚", "一局的 FAST 和 SLOW 一样多", "快一点和慢一点打了个平手", 1, lambda t: t.balanced),
    _fun("stall_ap", "处变不惊", "模拟器卡了一下还打出 AP", "画面卡住了也不慌", 1, lambda t: t.stall_ap),
    _fun("lucky_777", "幸运七", "分数的末尾是 777", "老虎机最爱的数字", 1, lambda t: t.lucky),
    _fun("palindrome", "回文分数", "分数正着读倒着读都一样", "倒过来念也一样", 1, lambda t: t.palindrome),
    _fun("loop_50", "停不下来", "同一张谱面连续演奏 50 局", "这首歌在脑子里循环播放", 50, lambda t: t.best_loop, tier=2),
    _fun("multi_run", "分身术", "两个打歌任务同时在运行", "一个人当两个人用", 1, lambda t: t.overlaps, "次"),
    _egg("friday_13", "黑色星期五", "在 13 号星期五演奏", "一个不太吉利的日子"),
    _egg("april_fools", "这不是玩笑", "在 4 月 1 日演奏", "说真话也没人信的一天"),
    _egg("leap_day", "四年一遇", "在 2 月 29 日演奏", "日历上不常出现的一天", tier=2),
    _egg("halloween", "不给糖就捣蛋", "在 10 月 31 日演奏", "南瓜灯亮起的夜晚"),
    _egg("christmas", "圣夜演出", "在 12 月 25 日演奏", "铃儿响叮当"),
    _egg("new_year", "新年第一曲", "在 1 月 1 日 0 点那一小时里演奏", "倒数结束之后的第一首歌"),
    _egg("midnight", "零点钟声", "在 0:00 那一分钟里打完一局", "时针和分针在最上面重合"),
)


def compute(
    records: list[PlayResult], sessions: list[Session], library: Library | None = None
) -> tuple[Totals, dict[str, float]]:
    """按时间顺序累加，返回最后的数据和各成就第一次达成的时间。

    不在任何一次运行记录里的演奏（统计功能加入之前打的）按演奏记录估算时长（见 GAP_S），其余按运行记录算。
    按每局判断而不是按第一次运行记录之前、之后：导入了别处的记录后，两边统计功能加入的时间不一样。
    没有曲库时按乐队、等级算的成就不前进。
    """
    records = sorted(records, key=lambda r: r.timestamp)
    sessions = sorted(sessions, key=lambda s: s.start)
    starts = [s.start for s in sessions]
    ends = list(itertools.accumulate((s.end for s in sessions), max))  # 前 i 次里最晚的结束时间

    def outside(t: float) -> bool:
        i = bisect.bisect_right(starts, t) - 1
        return i < 0 or t > ends[i]

    early = [r.timestamp for r in records if outside(r.timestamp)]
    gaps = [b - a for a, b in zip(early, early[1:]) if b - a <= GAP_S]
    typical = median(gaps) if gaps else 0.0  # 每段开头那局算一局的典型时长
    events: list[tuple[float, PlayResult | Session]] = [(r.timestamp, r) for r in records]
    events += [(s.end, s) for s in sessions]
    events.sort(key=lambda e: e[0])
    totals = Totals(lib=library or Library())
    reached: dict[str, float] = {}
    pending = list(ACHIEVEMENTS)
    prev: float | None = None
    segment = 0.0
    for when, item in events:
        if isinstance(item, Session):
            totals.add_session(item)
            prev = None  # 估算的一段不跨过运行记录
        else:
            totals.add_play(item)
            if outside(when):
                if prev is not None and when - prev <= GAP_S:
                    step = when - prev
                    segment += step
                else:
                    step = segment = typical
                totals.run_s += step
                totals.estimated_s += step
                totals.longest_run_s = max(totals.longest_run_s, segment)
                prev = when
        left = []
        for a in pending:
            if a.value(totals) >= a.target:
                reached[a.id] = when
            else:
                left.append(a)
        pending = left
    return totals, reached


def load_unlocked(path: Path) -> dict[str, float] | None:
    """已解锁的成就 → 时间；文件没有或坏了时为 None。"""
    try:
        return parse_unlocked(path.read_text(encoding="utf-8"))
    except OSError:
        return None


def parse_unlocked(text: str) -> dict[str, float] | None:
    """``achievements.json`` 的内容 → 已解锁的成就和时间；格式不对时为 None。"""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    unlocked = data.get("unlocked") if isinstance(data, dict) else None
    if not isinstance(unlocked, dict):
        return None
    return {k: float(v) for k, v in unlocked.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}


@dataclass
class View:
    """生成页面要用的全部数据。"""

    totals: Totals
    unlocked: dict[str, float]
    sessions: list[Session]
    now: float
    title: Callable[[int], str] = str
    live: list[Session] = field(default_factory=list)  # 正在运行的
    plays: list[PlayResult] = field(default_factory=list)  # 全部演奏记录，新的在前
    library: Library = field(default_factory=Library)
    doing: dict[str, dict] = field(default_factory=dict)  # 正在运行的在做什么：live_key → stats_live.js 里的一项


def _client(root: Path) -> str:
    """哪个界面：从界面启动时看环境变量；双击快捷方式打开统计窗口时没有，按发布包是哪个界面的算。"""
    return client_name() or str((release.read_release(root) or {}).get("ui", "")).upper()


def mxu_button_name(data_dir: Path) -> str:
    """按钮图片的文件名，带这个包的记号：别的包的 MXU 读不到这张图，卡片上不会出现开别的包页面的按钮。"""
    return PAGE_BUTTON.format(hashlib.sha1(str(data_dir.resolve()).encode("utf-8")).hexdigest()[:8])


def mxu_buttons(data_dir: Path) -> list[tuple[str, str]]:
    """MXU 设置页卡片上「在浏览器里打开」按钮，每个 MXU 可能在用的端口一个：(页面网址, 按钮图片的网址)。

    卡片里的链接只能开网址（本地路径、快捷方式会被 MXU 去掉），借 MXU 自带的本机网页服务读包目录里的文件
    （/api/local-file）。端口被占时（MXU 被强行结束后，端口常被它启动过的程序占着）MXU 不提示、改用后面的端口，
    卡片里又不能运行脚本，所以每个端口都放一个按钮，按钮本身是从那个端口读的图片：读不到的不占位置，
    只显示 MXU 真正在用的端口上那个。MXU 设置里关掉了网页服务时没有按钮。"""
    data_dir = data_dir.resolve()
    port = MXU_PORT
    with suppress(OSError, ValueError, AttributeError):
        settings = json.loads((data_dir.parent / MXU_CONFIG).read_text(encoding="utf-8")).get("settings") or {}
        if settings.get("webServerEnabled") is False:
            return []
        if isinstance(settings.get("webServerPort"), int) and 0 < settings["webServerPort"] < 65536:
            port = settings["webServerPort"]
    page, image = (f"/api/local-file?path={quote(f'{data_dir.name}/{name}')}" for name in (PAGE_HTML, mxu_button_name(data_dir)))
    return [(f"http://127.0.0.1:{p}{page}", f"http://127.0.0.1:{p}{image}") for p in range(port, min(port + MXU_PORTS, 65536))]


def _write_button(data_dir: Path, svg: str) -> None:
    """写出这个包的按钮图片（内容不变时不写），删掉以前的（包目录换了地方时记号会变）。"""
    name = mxu_button_name(data_dir)
    for old in data_dir.glob(PAGE_BUTTON.format("*")):
        if old.name != name:
            with suppress(OSError):
                old.unlink()
    path = data_dir / name
    with suppress(OSError):
        if path.read_text(encoding="utf-8") == svg:
            return
    release.write_text(path, svg)


def refresh(
    data_dir: str | Path = "data",
    title: Callable[[int], str] | None = None,
    live: Session | None = None,
    now: float | None = None,
    library: Library | None = None,
) -> View:
    """重新统计、记下新解锁的成就（日志里各报一行）、写出几个页面。``live`` 是这个进程正在运行的这次，
    别的实例正在运行的从 ``data/running`` 读。``title`` 没给时按曲库（``library``）显示曲名。"""
    from . import stats_page

    d = Path(data_dir)
    mine = [live] if live is not None else []
    others, busy = [], []
    for path, s in running_files(d):
        if s is not None:
            others.append(s)
            if release.is_locked(path.with_suffix(".lock")):
                busy.append(s)
    sessions = _dedupe(load_sessions(d) + others + mine)
    running = [s for s in sessions if any(s is r for r in busy + mine)]
    records = RecordStore(d).history(limit=None)
    library = library or Library()
    totals, reached = compute(records, sessions, library)
    stored = load_unlocked(d / UNLOCKED)
    known = stored or {}
    new = [a for a in ACHIEVEMENTS if a.id in reached and a.id not in known]
    unlocked = {**reached, **{k: min(v, reached.get(k, v)) for k, v in known.items()}}
    if unlocked != stored:
        release.write_text(d / UNLOCKED, json.dumps({"unlocked": unlocked}, ensure_ascii=False, indent=2))
    count = achieved_text(unlocked)
    if len(new) > 1 and (stored is None or len(new) > 5):  # 第一次统计、加了新成就后：按以往的记录一下解锁很多个
        names = "" if stored is None else "：" + "、".join(f"「{a.name}」" for a in new)
        logger.info("按以往的演奏记录解锁了 %d 个成就%s（%s）", len(new), names, count)
    else:
        for a in new:
            logger.info("★ 解锁成就「%s」：%s（%s）", a.name, a.desc, count)
    plays = sorted(records, key=lambda r: -r.timestamp)
    doing = {r["key"]: r for r in write_live(d)}  # 先于页面写：新页面读到的一定包括刚开始的这次、不包括刚结束的
    view = View(totals, unlocked, sessions, time.time() if now is None else now, title or library.title, running, plays, library, doing)
    root = d.resolve().parent
    shortcut = root / stats_window.SHORTCUT
    release.write_text(d / PAGE_MD, render_md(view, d / PAGE_HTML, _client(root), shortcut if shortcut.exists() else None))
    _write_button(d, stats_page.render_mxu_button())
    release.write_text(d / PAGE_MXU, stats_page.render_mxu_md(view, mxu_buttons(d)))
    page = stats_page.render_html(view)
    ver = hashlib.sha1(page.encode("utf-8")).hexdigest()[:12]
    release.write_text(d / PAGE_HTML, page.replace(VER_MARK, ver, 1))
    release.write_text(d / PAGE_VER, f'statsVer("{ver}");\n')  # 写完页面再写版本，页面看到新版本时读到的一定是新页面
    return view


def open_page(path: Path) -> str:
    """打开统计窗口（已经开着时切到前面），返回怎么打开的，见 :func:`.stats_window.open_window`。"""
    return stats_window.open_window(path, path.resolve().parent.parent)


_ids = itertools.count()


class Tracker:
    """一次打歌任务：记下运行时长和局数（``data/running/<id>.json``，结束时并进 ``data/sessions.jsonl``），
    开始、每局后、等 LB 恢复时刷新统计页。出错只记一次警告，不影响打歌。"""

    def __init__(
        self,
        data_dir: str | Path = "data",
        task: str = "",
        idle: bool = False,
        title: Callable[[int], str] | None = None,
        clock: Callable[[], float] = time.time,
        library: Library | None = None,
    ):
        self.dir = Path(data_dir)
        self.title = title
        self.library = library
        self.clock = clock
        now = clock()
        self.session = Session(start=now, end=now, task=task, idle=idle)
        self.file = self.dir / RUNNING / f"{os.getpid()}-{next(_ids)}.json"
        self._lock = release.FileLock(self.file.with_suffix(".lock"))
        self._failed = False
        self.now: dict = {}  # 正在做什么（见 status），和运行记录一起写在 running/<id>.json 里

    def start(self) -> None:
        self._safe(self._start)

    def update(self, stats) -> None:
        """``stats`` 是 :class:`~ournotes_auto.runner.RunStats`。"""
        self._safe(self._update, stats, False)

    def finish(self, stats) -> None:
        self._safe(self._update, stats, True)

    def status(self, kind: str, detail: str = "", **extra) -> None:
        """正在做什么：``kind`` 如 ``play`` ``lb``（统计页上显示成什么见 :data:`.stats_page.LIVE_KINDS`），
        ``detail`` 是补充的一句（曲名……），``extra`` 是页面上倒计时用的时刻（``eta`` 下一个 LB 恢复、``daily`` 下次领取日常）。
        只写 ``stats_live.js``，不重新统计，统计页就地更新。"""
        self._safe(self._status, kind, detail, extra)

    def _start(self) -> None:
        # 先锁上：导入备份看到有任务在运行就不导入，别的实例开始时也不会当成没正常结束的；再等正在进行的导入完成
        self._lock.acquire()
        self._wait_import()
        for path, stale in running_files(self.dir):  # 上次没正常结束（进程被强行结束）的：按最后一次更新补记
            lock = path.with_suffix(".lock")
            if release.is_locked(lock):  # 别的实例正在运行
                continue
            if stale is not None:
                self._append(stale)
            with suppress(OSError):
                path.unlink(missing_ok=True)
                lock.unlink()
        self._update_files(done=False)

    def _wait_import(self) -> None:
        """正在导入备份（要改演奏记录）时等它完成，一般只要几秒；等太久就不等了。"""
        lock = self.dir / IMPORT_LOCK
        if not release.is_locked(lock):
            return
        logger.info("正在导入备份，导入完成后开始")
        deadline = time.monotonic() + IMPORT_WAIT_S
        while release.is_locked(lock):
            if time.monotonic() > deadline:
                logger.warning("导入备份太久没有完成，不等了")
                return
            time.sleep(0.2)

    def _update(self, stats, done: bool) -> None:
        s = self.session
        s.end = self.clock()
        s.plays, s.fc, s.ap = stats.plays, stats.full_combo, stats.all_perfect
        s.failures, s.skips = stats.failures, stats.skips
        s.lb_wait_s = round(stats.lb_wait_s, 1)
        s.restart = done and stats.restart
        view = self._update_files(done)
        if done and s.plays:
            t = view.totals
            logger.info(
                "本次运行 %s；累计运行 %s、演奏 %s 局（AP %s），%s",
                duration(s.seconds), duration(t.run_s), f"{t.plays:,}", f"{t.ap:,}", achieved_text(view.unlocked),
            )

    def _update_files(self, done: bool) -> View:
        if done:
            self._append(self.session)
            self._lock.release()
            with suppress(OSError):  # 删不掉时下次开始会再补记一遍，统计时去重
                self.file.unlink(missing_ok=True)
                self._lock.path.unlink()
        else:
            self._lock.acquire()
            self._write()
        return refresh(self.dir, self.title, None if done else self.session, self.clock(), self.library)

    def _status(self, kind: str, detail: str, extra: dict) -> None:
        if not self._lock.held:  # 还没开始或已经结束
            return
        now = self.clock()
        self.now = {
            "kind": kind,
            "detail": detail,
            # 还在做同一件事（等 LB 时每次醒来都会报一遍）时从开始做的时候算；两局之间总会换歌，每局从头算
            "since": self.now["since"] if self.now.get("kind") == kind else round(now, 1),
            **{k: round(v, 1) if isinstance(v, float) else v for k, v in extra.items() if v is not None},
        }
        self.session.end = now  # 进程被强行结束时，补记的运行时长算到这里
        self._write()
        write_live(self.dir)

    def _write(self) -> None:
        release.write_text(self.file, json.dumps({**session_row(self.session), "now": self.now}, ensure_ascii=False))

    def _append(self, s: Session) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / SESSIONS).open("a", encoding="utf-8") as f:
            f.write(json.dumps(session_row(s), ensure_ascii=False) + "\n")

    def _safe(self, fn, *args) -> None:
        try:
            fn(*args)
        except Exception as e:  # noqa: BLE001 - 统计出错不影响打歌
            if self._failed:
                logger.debug("更新统计页失败：%s", e)
            else:
                logger.warning("更新统计页失败（不影响演奏）：%s", e)
                self._failed = True


# ---------------------------------------------------------------- 文字


def duration(seconds: float) -> str:
    if seconds < 3570:  # 59.5 分钟起显示成小时，不出现「60 分钟」
        return f"{seconds / 60:.0f} 分钟"
    return f"{seconds / 3600:,.1f} 小时"


def achieved_text(unlocked: dict[str, float]) -> str:
    return f"成就 {sum(a.id in unlocked for a in ACHIEVEMENTS)} / {len(ACHIEVEMENTS)}"


def _pct(a: int, b: int) -> str:
    return f"{a / b:.1%}" if b else "-"


def _date(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _when(ts: float) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


def progress_text(a: Achievement, value: float) -> str:
    """没达成时往下取整，不会显示成「10 万 / 10 万个」。"""
    big = next(((scale, word) for scale, word in ((10**8, "亿"), (10**4, "万")) if a.target >= scale * 10), None)
    scale, digits = (big[0], 0) if big else (1, 1 if a.unit == "小时" else 0)  # 音符数按万、分数按亿显示
    shown = min(value, a.target) / scale
    if value < a.target:
        shown = math.floor(shown * 10**digits) / 10**digits
    if a.unit == "%":
        return f"{shown:.0f}%"
    if big:
        return f"{shown:,.0f} {big[1]} / {a.target / scale:,.0f} {big[1]}{a.unit}"
    return f"{shown:,.{digits}f} / {a.target:,.0f} {a.unit}"


def secret_text(a: Achievement) -> str:
    """隐藏成就解锁前显示的说明。"""
    return f"隐藏成就：{a.hint}" if a.hint else "隐藏成就：达成后显示"


def next_goals(view: View, count: int = 3) -> list[Achievement]:
    """还没解锁、离达成最近的几个（不含隐藏成就）。"""
    todo = [a for a in ACHIEVEMENTS if a.id not in view.unlocked and not a.hidden]
    return sorted(todo, key=lambda a: -a.value(view.totals) / a.target)[:count]


def recent_unlocked(view: View, count: int) -> list[Achievement]:
    got = [a for a in ACHIEVEMENTS if a.id in view.unlocked]
    return sorted(got, key=lambda a: -view.unlocked[a.id])[:count]


def summary_lines(view: View) -> list[str]:
    """任务「打开统计窗口」在日志里显示的几行（演奏局数等 report_lines 已经写了）。"""
    t = view.totals
    lines = [
        f"累计运行 {duration(t.run_s)}"
        + (f"（其中挂机 {duration(t.idle_s)}）" if t.idle_s else "")
        + f"，演奏 {len(t.per_day)} 天，最长连续 AP {t.best_streak} 局"
    ]
    got = sum(a.id in view.unlocked for a in ACHIEVEMENTS)
    recent = recent_unlocked(view, 3)
    tail = "，最近解锁：" + "、".join(f"「{a.name}」" for a in recent) if recent else ""
    lines.append(f"成就 {got} / {len(ACHIEVEMENTS)}{tail}")
    return lines


def _diff_rows(t: Totals) -> list[tuple[str, int, int, int, int]]:
    """各难度：(难度, 局数, AP 局数, 打过的谱面, AP 过的谱面)，从高到低。"""
    rows = []
    for d in reversed(DIFFICULTIES):
        charts = [c for (_, diff), c in t.charts.items() if diff == d]
        rows.append((d.upper(), sum(c.plays for c in charts), sum(c.ap for c in charts), len(charts), sum(1 for c in charts if c.ap)))
    return rows


def _facts(view: View) -> list[tuple[str, str]]:
    """「更多数据」：(项目, 数值)。"""
    t = view.totals
    rows = [("开始使用", _date(t.first) if t.first is not None else "-")]
    if t.estimated_s:
        rows.append(("统计功能加入前的运行时长", f"{duration(t.estimated_s)}（按演奏记录估算）"))
    if t.runs:  # 下面这些只有运行记录里有
        rows += [
            ("运行次数", f"{t.runs:,} 次"),
            ("其中挂机", duration(t.idle_s)),
            ("挂机时等 LB 恢复", duration(t.lb_wait_s)),
            ("用「跳过」消耗 LB", f"{t.skips:,} 次"),
        ]
    rows += [
        ("最长一次连续运行", duration(t.longest_run_s)),
        ("PERFECT", f"{t.perfect:,} 个"),
        ("累计分数", f"{t.score:,}"),
        ("一局最大连击", f"{t.max_combo:,}"),
        ("当前连续 AP", f"{t.streak:,} 局"),
        ("单日最多", f"{t.best_day:,} 局" + (f"（{t.best_day_date.isoformat()}）" if t.best_day_date else "")),
        ("最长连续演奏", f"{t.best_day_streak} 天"),
        ("打过的谱面", f"{len(t.charts):,} 张，其中 {t.charts_ap:,} 张打出过 AP"),
        ("四个难度都 AP 的歌", f"{t.songs_all_ap:,} 首"),
    ]
    if t.unread:
        rows.append(("结算没读全的局", f"{t.unread:,} 局（不算 AP / FC）"))
    return rows


REFRESH_HINT = {
    "MXU": "打开设置再回来可以刷新",
    "MFAA": "点一下别的任务再点回来可以刷新",
    "MFAAVALONIA": "点一下别的任务再点回来可以刷新",
}
WINDOW_TEXT = "成就徽章、每日局数、各谱面战绩，打完一局自动刷新"
RECORDS_TEXT = "在日志里列出演奏记录（各难度 AP 过的谱面、打过还没 AP 的谱面、最近 10 局），不操作模拟器。只统计本工具打的局。"
BACKUP_TEXT = "换包前在「统计与成就」窗口右上角的「备份与恢复」里导出，在新的包里导入就能接着统计（成就也在里面）"
IMPORT_TEXT = "以前在别的包里打过？在「统计与成就」窗口右上角的「备份与恢复」里导入，把记录和成就接过来"


def backup_text(view: View) -> str:
    """统计页底部的提示：有记录时提醒换包前导出，没有时提醒可以导入。"""
    return BACKUP_TEXT if view.totals.plays or view.totals.runs else IMPORT_TEXT


def _md(text: str) -> str:
    """表格里的文字：转义 markdown 和 HTML 的特殊字符。"""
    for ch in "\\`*_[]<>|#":
        text = text.replace(ch, "\\" + ch)
    return text


def headline(view: View) -> str:
    """一行总结：累计运行 · 演奏 · ALL PERFECT · 成就。"""
    t = view.totals
    return f"累计运行 {duration(t.run_s)} · 演奏 {t.plays:,} 局 · ALL PERFECT {t.ap:,} · {achieved_text(view.unlocked)}"


def md_body(view: View) -> list[str]:
    """累计数据、成就进度、最近解锁、下一个目标、全部成就、更多数据。只用文字、表格和少量 HTML
    （两个界面都会缓存图片，不放图）。"""
    t = view.totals
    got = sum(a.id in view.unlocked for a in ACHIEVEMENTS)
    total = len(ACHIEVEMENTS)
    out = []
    if not t.plays and not t.runs:
        out += ["还没有演奏记录：打完一局后这里会显示累计数据和成就。", ""]
    else:
        out += [
            "| 累计运行 | 演奏 | ALL PERFECT | FULL COMBO |",
            "|:-:|:-:|:-:|:-:|",
            f"| {duration(t.run_s)} | {t.plays:,} 局 | {t.ap:,}（{_pct(t.ap, t.plays)}） | {t.fc:,}（{_pct(t.fc, t.plays)}） |",
            "",
            "| 音符 | 最长连续 AP | 演奏天数 | 单日最多 |",
            "|:-:|:-:|:-:|:-:|",
            f"| {t.notes:,} | {t.best_streak:,} 局 | {len(t.per_day):,} 天 | {t.best_day:,} 局 |",
            "",
        ]
    out += [f'**成就 {got} / {total}** <progress value="{got}" max="{total}"></progress>', ""]
    recent = recent_unlocked(view, 5)
    if recent:
        out += ["**最近解锁**", ""]
        out += [f"- {_date(view.unlocked[a.id])}「{a.name}」{a.desc}" for a in recent]
        out.append("")
    goals = next_goals(view)
    if goals:
        out += ["**下一个目标**", ""]
        out += [f"- 「{a.name}」{a.desc}：{progress_text(a, a.value(t))}" for a in goals]
        out.append("")
    out += ["<details><summary>全部成就</summary>", "", "| 成就 | 条件 | 进度 |", "|---|---|---|"]
    for a in ACHIEVEMENTS:
        when = view.unlocked.get(a.id)
        if when is not None:
            out.append(f"| {a.name} | {a.desc} | {_date(when)} 解锁 |")
        elif a.hidden:
            out.append(f"| ??? | {_md(secret_text(a))} | - |")
        else:
            out.append(f"| {a.name} | {a.desc} | {progress_text(a, a.value(t))} |")
    out += ["", "</details>", ""]
    if t.plays or t.runs:
        out += ["<details><summary>更多数据</summary>", "", "| 项目 | 数值 |", "|---|---|"]
        out += [f"| {k} | {_md(v)} |" for k, v in _facts(view)]
        out += ["", "| 难度 | 局数 | AP | 打过的谱面 | AP 过的谱面 |", "|---|--:|--:|--:|--:|"]
        out += [f"| {d} | {n:,} | {ap:,} | {c:,} | {cap:,} |" for d, n, ap, c, cap in _diff_rows(t)]
        out += ["", "</details>", ""]
    return out


def render_md(view: View, page: Path, client: str = "", shortcut: Path | None = None) -> str:
    """界面任务「打开统计窗口」的说明。MXU 设置页有「统计与成就」卡片，这里只放一行总结和怎么打开；
    MFAA 和命令行放完整的统计，MFAA 加一个打开窗口的链接（``shortcut`` 是包目录里的快捷方式，没有时链接到页面）。"""
    hint = REFRESH_HINT.get(client, "MFAA 点一下别的任务再点回来、MXU 打开设置再回来可以刷新")
    out = [f"更新于 {_when(view.now)}（每局后更新，{hint}）", ""]
    if client == "MXU":  # MXU 只能打开网址，不放链接和路径
        t = view.totals
        if t.plays or t.runs:
            out += [f"**{headline(view)}**", ""]
        else:
            out += ["还没有演奏记录：打完一局后这里会显示累计数据和成就。", ""]
        out += [
            "完整的统计与成就在「设置」页最上面的「统计与成就」卡片里；运行这个任务会打开单独的「统计与成就」窗口"
            f"（{WINDOW_TEXT}），双击包目录里的「统计与成就」快捷方式也能打开。",
            "",
            f"运行这个任务时还会{RECORDS_TEXT}",
            "",
            f"{backup_text(view)}。",
        ]
        return "\n".join(out) + "\n"
    out += md_body(view)
    out += [f"运行这个任务：打开「统计与成就」窗口（{WINDOW_TEXT}），{RECORDS_TEXT}", "", f"{backup_text(view)}。", ""]
    if client in ("MFAA", "MFAAVALONIA"):
        out.append(f"[打开统计与成就窗口](<{shortcut}>)" if shortcut else f"[在浏览器里打开完整页面](<{page.resolve()}>)")
    else:
        out.append(f"完整页面：{page.resolve()}")
    return "\n".join(out) + "\n"
