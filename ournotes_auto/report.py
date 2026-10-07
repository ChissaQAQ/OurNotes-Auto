"""本地演奏记录（``data/records.jsonl``）的汇总，供 ``records`` 命令和界面任务「记录汇总」显示。

只统计本工具打的局；游戏里的 AP 状态以游戏为准（AP 补完按游戏里的「未ALL PERFECT」筛选选歌）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .records import PlayResult
from .sources import DIFFICULTIES


@dataclass
class ChartStats:
    """一张谱面（曲目 + 难度）的战绩。"""

    plays: int = 0
    fc: int = 0
    ap: int = 0
    best: PlayResult | None = None  # 非 PERFECT 判定最少的一局
    last: float = 0.0

    def add(self, r: PlayResult) -> None:
        self.plays += 1
        self.fc += bool(r.full_combo)
        self.ap += bool(r.all_perfect)
        self.last = max(self.last, r.timestamp)
        if r.full_combo is not None and (self.best is None or _misses(r) < _misses(self.best)):
            self.best = r


@dataclass
class Summary:
    plays: int = 0
    fc: int = 0
    ap: int = 0
    unread: int = 0  # 结算没读全
    first: float | None = None
    charts: dict[tuple[int, str], ChartStats] = field(default_factory=dict)


def _misses(r: PlayResult) -> int:
    return (r.great or 0) + (r.good or 0) + (r.bad or 0) + (r.miss or 0)


def summarize(results: list[PlayResult]) -> Summary:
    s = Summary()
    for r in results:
        s.plays += 1
        s.fc += bool(r.full_combo)
        s.ap += bool(r.all_perfect)
        s.unread += r.full_combo is None
        s.first = r.timestamp if s.first is None else min(s.first, r.timestamp)
        s.charts.setdefault((r.music_id, r.difficulty), ChartStats()).add(r)
    return s


def judgements(r: PlayResult) -> str:
    return f"P{r.perfect} G{r.great} g{r.good} B{r.bad} M{r.miss}".replace("None", "?")


def verdict(r: PlayResult) -> str:
    if r.all_perfect:
        return "AP"
    if r.full_combo:
        return "FC"
    return "结果没读全" if r.full_combo is None else ""


def report_lines(
    results: list[PlayResult],
    title: Callable[[int], str],
    recent: int = 10,
    not_ap: int = 10,
    chart_offsets: dict[str, dict] | None = None,
) -> list[str]:
    """汇总成几行文字。``title`` 把 musicId 换成曲名。``chart_offsets`` 是按谱面学到的 offset
    （:meth:`~ournotes_auto.records.RecordStore.chart_offsets`），列出偏得最多的几张。"""
    if not results:
        return ["还没有演奏记录（data/records.jsonl）"]
    s = summarize(results)
    since = time.strftime("%Y-%m-%d", time.localtime(s.first))
    lines = [f"本工具共演奏 {s.plays} 局（{since} 起）：FC {s.fc}，AP {s.ap}" + (f"，结果没读全 {s.unread}" if s.unread else "")]

    ap_charts = {d: 0 for d in DIFFICULTIES}
    for (_, diff), c in s.charts.items():
        if c.ap and diff in ap_charts:
            ap_charts[diff] += 1
    lines.append("打出过 AP 的谱面：" + "，".join(f"{d.upper()} {ap_charts[d]}" for d in reversed(DIFFICULTIES)))

    pending = sorted(((k, c) for k, c in s.charts.items() if not c.ap), key=lambda kc: -kc[1].last)
    if pending:
        lines.append(f"打过但还没 AP（{len(pending)} 张，最近打的在前）：")
        for (mid, diff), c in pending[:not_ap]:
            best = f"，最好 {judgements(c.best)}" if c.best else ""
            lines.append(f"  {title(mid)} {diff.upper()}：{c.plays} 局{best}")
        if len(pending) > not_ap:
            lines.append(f"  ……还有 {len(pending) - not_ap} 张")

    learned = sorted(
        ((k, e) for k, e in (chart_offsets or {}).items() if e.get("offset_ms")),
        key=lambda ke: -abs(ke[1]["offset_ms"]),
    )
    if learned:
        lines.append(f"按谱面修正的 offset（{len(learned)} 张，偏得多的在前；正值 = 推迟）：")
        for key, e in learned[:not_ap]:
            mid, _, diff = key.partition("_")
            name = title(int(mid)) if mid.isdigit() else mid
            lines.append(f"  {name} {diff.upper()}：{e['offset_ms']:+.1f}ms（流速 {e.get('note_speed')}）")
        if len(learned) > not_ap:
            lines.append(f"  ……还有 {len(learned) - not_ap} 张")

    if recent > 0:
        lines.append(f"最近 {min(recent, s.plays)} 局：")
        for r in results[-recent:][::-1]:
            when = time.strftime("%m-%d %H:%M", time.localtime(r.timestamp))
            lines.append(f"  {when}  {title(r.music_id)} {r.difficulty.upper()}  {judgements(r)}  {verdict(r)}".rstrip())
    return lines
