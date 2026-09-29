"""解析 bdon.moe 的 ``nnnotes.live-score/1`` 谱面为 :class:`Chart`。

格式要点（以 0001_03 等谱面验证）：
- ``notes[]``：``tick``（480/拍）、``timeMs``（整数，向下取整）、
  ``laneStartFloat``/``laneEndFloat``（闭区间端点，右沿 = laneEndFloat + 1）、
  ``opName``、``judgementType``、``direction``（Normal=上 / Left / Right）、
  ``lineIds``、``lineEase``/``lineEaseR``、``judgement``（是否计入判定）。
- ``lines[]``：``{lineId, type: "long", noteIds, comboIds}``，noteIds 为长条路径节点
  （含 Hidden 形状控制点），comboIds 为中途判定点（Combo，judgementType=Trace）。
- ``tickSegments.bpm``：``[{startTick, startTimeMs, bpm}]``，用于从 tick 精确换算时间。
"""

from __future__ import annotations

import bisect
import logging
from collections.abc import Mapping
from typing import Any

from .model import (
    Chart,
    Ease,
    FlickDirection,
    NoteKind,
    PathNode,
    PointNote,
    Slide,
    SlideHead,
    SlideTail,
    Span,
)

logger = logging.getLogger(__name__)

FORMAT = "nnnotes.live-score/1"
TICKS_PER_BEAT = 480

_DIRECTIONS = {
    "Normal": FlickDirection.UP,
    "Up": FlickDirection.UP,
    "Left": FlickDirection.LEFT,
    "Right": FlickDirection.RIGHT,
}


class ChartFormatError(ValueError):
    pass


class TickClock:
    """tick → 毫秒。以各 BPM 段的起始 tick 累积计算，避免 ``startTimeMs`` 取整误差。"""

    def __init__(self, segments: list[Mapping[str, Any]]):
        if not segments:
            raise ChartFormatError("缺少 tickSegments.bpm")
        segs = sorted(segments, key=lambda s: s["startTick"])
        self._ticks: list[int] = []
        self._times: list[float] = []
        self._ms_per_tick: list[float] = []
        t = float(segs[0].get("startTimeMs", 0.0))
        for i, seg in enumerate(segs):
            bpm = float(seg["bpm"])
            if bpm <= 0:
                raise ChartFormatError(f"非法 BPM {bpm}")
            if i > 0:
                prev = segs[i - 1]
                t += (seg["startTick"] - prev["startTick"]) * self._ms_per_tick[-1]
            self._ticks.append(int(seg["startTick"]))
            self._times.append(t)
            self._ms_per_tick.append(60000.0 / (bpm * TICKS_PER_BEAT))

    def ms(self, tick: float) -> float:
        i = max(bisect.bisect_right(self._ticks, tick) - 1, 0)
        return self._times[i] + (tick - self._ticks[i]) * self._ms_per_tick[i]


def _span(note: Mapping[str, Any]) -> Span:
    left = note.get("laneStartFloat", note.get("laneStart"))
    right = note.get("laneEndFloat", note.get("laneEnd"))
    if left is None or right is None:
        raise ChartFormatError(f"音符 {note.get('id')} 缺少横向位置")
    return Span(float(left), float(right) + 1.0)


def _direction(note: Mapping[str, Any]) -> FlickDirection:
    return _DIRECTIONS.get(note.get("direction") or "Normal", FlickDirection.UP)


def _is_trace(note: Mapping[str, Any]) -> bool:
    return "Trace" in (note.get("judgementType") or "")


def _is_flick(note: Mapping[str, Any]) -> bool:
    return bool(note.get("flick")) or "Flick" in (note.get("opName") or "")


def parse_live_score(
    data: Mapping[str, Any],
    music_id: int | None = None,
    difficulty: str | None = None,
    title: str = "",
) -> Chart:
    fmt = data.get("format")
    if fmt != FORMAT:
        raise ChartFormatError(f"不支持的谱面格式：{fmt!r}")
    source = data.get("source") or {}
    music_id = int(music_id if music_id is not None else source.get("musicId", 0))
    difficulty = difficulty or source.get("difficulty", "")
    if data.get("mirror"):
        logger.warning("谱面 %s_%s 标记为 mirror，按原样解析", music_id, difficulty)

    clock = TickClock((data.get("tickSegments") or {}).get("bpm") or [])
    by_id: dict[int, Mapping[str, Any]] = {n["id"]: n for n in data.get("notes", [])}
    mismatched: dict[Any, float] = {}  # 音符 id → tick 换算与 timeMs 之差（同一音符会被多次换算）

    def time_of(note: Mapping[str, Any]) -> float:
        tick = note.get("tick")
        raw = note.get("timeMs")
        if tick is None:
            if raw is None:
                raise ChartFormatError(f"音符 {note.get('id')} 缺少时间")
            return float(raw)
        t = clock.ms(tick)
        if raw is not None and abs(t - raw) > 1.5:
            mismatched[note.get("id")] = t - raw
            return float(raw)
        return t

    # —— 长条 ——
    slides: list[Slide] = []
    in_line: set[int] = set()
    judged_ids: set[int] = set()  # 多条长条可共用同一终点，按音符 id 去重计数
    for line in data.get("lines", []):
        kind = line.get("type")
        node_ids = [i for i in line.get("noteIds", []) if i in by_id]
        combo_ids = [i for i in line.get("comboIds", []) if i in by_id]
        in_line.update(node_ids)
        in_line.update(combo_ids)
        if kind not in ("long", "guide"):
            logger.warning("未知 line 类型 %r（line %s），按长条处理", kind, line.get("lineId"))
        if len(node_ids) < 2:
            logger.warning("line %s 节点不足，跳过", line.get("lineId"))
            continue
        raw_nodes = sorted((by_id[i] for i in node_ids), key=time_of)
        path = [
            PathNode(
                time_of(n),
                _span(n),
                Ease.parse(n.get("lineEase")),
                Ease.parse(n.get("lineEaseR")),
            )
            for n in raw_nodes
        ]
        head_raw, tail_raw = raw_nodes[0], raw_nodes[-1]
        if not head_raw.get("judgement"):
            head = SlideHead.NONE
        elif _is_flick(head_raw):
            head = SlideHead.FLICK
        elif _is_trace(head_raw):
            head = SlideHead.TRACE
        else:
            head = SlideHead.TAP
        if not tail_raw.get("judgement"):
            tail = SlideTail.NONE
        elif _is_flick(tail_raw):
            tail = SlideTail.FLICK
        elif _is_trace(tail_raw):
            tail = SlideTail.TRACE
        else:
            tail = SlideTail.RELEASE
        checkpoints = [time_of(n) for n in raw_nodes[1:-1] if n.get("judgement")]
        checkpoints += [time_of(by_id[c]) for c in combo_ids if by_id[c].get("judgement")]
        if head is SlideHead.NONE and not checkpoints and tail is SlideTail.NONE:
            continue  # 纯装饰路径
        slides.append(
            Slide(
                id=int(line.get("lineId", len(slides))),
                nodes=path,
                head=head,
                tail=tail,
                head_direction=_direction(head_raw),
                tail_direction=_direction(tail_raw),
                critical=bool(head_raw.get("critical")),
                checkpoints=checkpoints,
                guide=kind == "guide",
            )
        )
        judged_ids.update(n["id"] for n in raw_nodes if n.get("judgement"))
        judged_ids.update(c for c in combo_ids if by_id[c].get("judgement"))

    # —— 单点音符 ——
    notes: list[PointNote] = []
    for n in data.get("notes", []):
        if n["id"] in in_line or not n.get("judgement"):
            continue
        if n.get("lineIds"):
            logger.warning("音符 %s 引用了未知 line %s，按单点处理", n["id"], n["lineIds"])
        if _is_trace(n):
            kind = NoteKind.TRACE
        elif _is_flick(n):
            kind = NoteKind.FLICK
        else:
            kind = NoteKind.TAP
        notes.append(
            PointNote(
                id=int(n["id"]),
                time_ms=time_of(n),
                kind=kind,
                span=_span(n),
                direction=_direction(n),
                critical=bool(n.get("critical")),
            )
        )
        judged_ids.add(n["id"])

    if mismatched:
        worst = max(mismatched.items(), key=lambda kv: abs(kv[1]))
        logger.warning(
            "谱面 %s_%s 有 %d 个音符的 tick 换算与 timeMs 相差超过 1.5ms（最大：音符 %s，%+.1fms），采用 timeMs",
            music_id,
            difficulty,
            len(mismatched),
            worst[0],
            worst[1],
        )
    judged = len(judged_ids)
    declared = int(data.get("judgementNoteCount") or source.get("fullComboCount") or 0)
    if declared and declared != judged:
        logger.warning("谱面 %s_%s 判定数 %d 与声明 %d 不一致", music_id, difficulty, judged, declared)

    chart = Chart(
        music_id=music_id,
        difficulty=difficulty,
        title=title,
        notes=notes,
        slides=slides,
        judged_count=declared or judged,
        duration_ms=float(data.get("lastNoteTimeMs") or 0.0),
    )
    chart.parsed_judged = judged
    if not chart.duration_ms:
        chart.duration_ms = chart.last_ms
    return chart
