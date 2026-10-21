import json
from pathlib import Path

import pytest

from ournotes_auto.charts.model import Ease, FlickDirection, NoteKind, SlideHead, SlideTail, Span
from ournotes_auto.charts.parser import ChartFormatError, TickClock, parse_live_score

SAMPLE = Path(__file__).resolve().parents[1] / "_reference" / "data" / "notes_0001_03.json"


def note(id, op, tick, ls, le, **kw):
    base = {
        "id": id,
        "opName": op,
        "tick": tick,
        "timeMs": None,
        "laneStartFloat": ls,
        "laneEndFloat": le,
        "direction": "Normal",
        "lineIds": [],
        "lineEase": "Linear",
        "lineEaseR": "Linear",
        "judgement": True,
        "judgementType": "Normal",
        "flick": False,
    }
    base.update(kw)
    return base


def score(notes, lines=(), bpm=120, declared=None):
    return {
        "format": "nnnotes.live-score/1",
        "source": {"musicId": 1, "difficulty": "expert"},
        "judgementNoteCount": declared,
        "notes": notes,
        "lines": list(lines),
        "tickSegments": {"bpm": [{"startTick": 0, "startTimeMs": 0, "bpm": bpm}]},
    }


def test_tick_clock_multi_segment():
    clock = TickClock(
        [
            {"startTick": 0, "startTimeMs": 0, "bpm": 120},
            {"startTick": 1920, "startTimeMs": 2000, "bpm": 240},
        ]
    )
    assert clock.ms(0) == 0
    assert clock.ms(480) == pytest.approx(500)
    assert clock.ms(1920) == pytest.approx(2000)
    assert clock.ms(1920 + 480) == pytest.approx(2250)


def test_point_notes_and_span():
    data = score(
        [
            note(1, "Normal", 480, 6, 11),
            note(2, "Flick", 960, 0, 7, flick=True, judgementType="Flick", direction="Left"),
            note(3, "Trace", 1440, 3, 5, judgementType="Trace"),
            note(4, "Hidden", 1500, 3, 5, judgement=False),
        ],
        declared=3,
    )
    chart = parse_live_score(data)
    assert [n.kind for n in chart.notes] == [NoteKind.TAP, NoteKind.FLICK, NoteKind.TRACE]
    assert chart.notes[0].span == Span(6, 12)
    assert chart.notes[0].time_ms == pytest.approx(500)
    assert chart.notes[1].direction is FlickDirection.LEFT
    assert chart.parsed_judged == chart.judged_count == 3


def test_slide_with_hidden_and_combo():
    notes = [
        note(10, "SlideBegin", 0, 0, 3, lineIds=[1], lineEase="EaseIn", lineEaseR="EaseOut"),
        note(11, "Hidden", 480, 8, 11, lineIds=[1], judgement=False),
        note(12, "SlideEndFlick", 960, 8, 11, lineIds=[1], flick=True, judgementType="Flick", direction="Right"),
        note(100, "Combo", None, 4, 7, lineIds=[1], judgementType="Trace", timeMs=250),
    ]
    chart = parse_live_score(score(notes, [{"lineId": 1, "type": "long", "noteIds": [10, 11, 12], "comboIds": [100]}]))
    assert not chart.notes
    (s,) = chart.slides
    assert s.head is SlideHead.TAP and s.tail is SlideTail.FLICK
    assert s.tail_direction is FlickDirection.RIGHT
    assert s.checkpoints == [250]
    assert s.nodes[0].ease_left is Ease.EASE_IN and s.nodes[0].ease_right is Ease.EASE_OUT
    mid = s.span_at(250)
    # 左沿 EaseIn：1-(1-0.5)^2 = 0.75；右沿 EaseOut：0.25
    assert mid.left == pytest.approx(0 + 8 * 0.75)
    assert mid.right == pytest.approx(4 + 8 * 0.25)
    assert chart.parsed_judged == 3


def test_slide_end_keeps_tick_order():
    # 隐藏节点比终点早 1 tick；终点的 timeMs 与 tick 换算差超过 1.5ms 改用 timeMs 后反而更早（鳴らす EXPERT）
    notes = [
        note(10, "SlideBegin", 0, 0, 3, lineIds=[1]),
        note(11, "Hidden", 959, 8, 11, lineIds=[1], judgement=False, timeMs=999),
        note(12, "SlideEndFlick", 960, 7, 11, lineIds=[1], flick=True, judgementType="Flick", timeMs=998),
    ]
    chart = parse_live_score(score(notes, [{"lineId": 1, "type": "long", "noteIds": [10, 11, 12], "comboIds": []}]))
    (s,) = chart.slides
    assert s.tail is SlideTail.FLICK and not s.checkpoints
    assert s.nodes[-1].span == Span(7, 12)
    assert [n.time_ms for n in s.nodes] == sorted(n.time_ms for n in s.nodes)

def test_update_order_follows_notes_array():
    # 游戏按 notes 的排列顺序更新音符：长条取起点的位置，与 id 和 lineId 大小无关
    notes = [
        note(30, "Normal", 0, 0, 3),
        note(21, "SlideBegin", 0, 8, 11, lineIds=[9]),
        note(20, "SlideBegin", 0, 4, 7, lineIds=[2]),
        note(22, "SlideEnd", 480, 8, 11, lineIds=[9]),
        note(23, "SlideEnd", 480, 4, 7, lineIds=[2]),
    ]
    lines = [
        {"lineId": 2, "type": "long", "noteIds": [20, 23], "comboIds": []},
        {"lineId": 9, "type": "long", "noteIds": [21, 22], "comboIds": []},
    ]
    chart = parse_live_score(score(notes, lines))
    assert [n.order for n in chart.notes] == [0]
    assert {s.id: s.order for s in chart.slides} == {2: 2, 9: 1}

def test_guide_line_first_judged():
    notes = [
        note(1, "GuideBegin", 0, 0, 3, lineIds=[7], judgement=False),
        note(2, "SlideConnectionTrace", 240, 2, 5, lineIds=[7], judgementType="Trace"),
        note(3, "GuideEnd", 480, 4, 7, lineIds=[7], judgement=False),
    ]
    chart = parse_live_score(score(notes, [{"lineId": 7, "type": "guide", "noteIds": [1, 2, 3], "comboIds": []}]))
    (s,) = chart.slides
    assert s.guide and s.head is SlideHead.NONE and s.tail is SlideTail.NONE
    t0, spans = chart.first_hits()
    assert t0 == pytest.approx(250)
    assert spans[0].left == pytest.approx(2) and spans[0].right == pytest.approx(6)


def test_rejects_unknown_format():
    with pytest.raises(ChartFormatError):
        parse_live_score({"format": "other"})


@pytest.mark.skipif(not SAMPLE.exists(), reason="缺少样例谱面")
def test_sample_chart_counts():
    chart = parse_live_score(json.loads(SAMPLE.read_text(encoding="utf-8")))
    assert chart.judged_count == 768
    assert chart.parsed_judged == 768
    assert chart.first_hits()[0] == pytest.approx(6315.79, abs=0.01)
