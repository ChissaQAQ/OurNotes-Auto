"""本地演奏记录汇总。"""

from ournotes_auto.records import PlayResult, RecordStore
from ournotes_auto.report import report_lines, summarize

T0 = 1790600000.0  # 2026-09-29


def play(mid, diff="expert", great=0, miss=0, t=0.0, read=True):
    if not read:
        return PlayResult(mid, diff, timestamp=T0 + t)
    return PlayResult(mid, diff, perfect=100, great=great, good=0, bad=0, miss=miss, max_combo=100, timestamp=T0 + t)


def test_summarize():
    s = summarize([play(1), play(1, great=2), play(2, miss=1), play(3, read=False)])
    assert (s.plays, s.fc, s.ap, s.unread) == (4, 2, 1, 1)
    one = s.charts[(1, "expert")]
    assert (one.plays, one.fc, one.ap) == (2, 2, 1)
    assert one.best.great == 0
    assert s.charts[(3, "expert")].best is None


def test_report_lines():
    results = [
        play(1, t=0),
        play(2, miss=3, t=10),
        play(2, miss=1, great=1, t=20),
        play(3, "hard", t=30),
        play(4, great=5, t=40),
        play(5, read=False, t=50),
    ]
    lines = report_lines(results, lambda mid: f"曲{mid}", recent=2, not_ap=2)
    assert lines[0].startswith("本工具共演奏 6 局（2026-09-") and lines[0].endswith("FC 3，AP 2，结果没读全 1")
    assert lines[1] == "打出过 AP 的谱面：EXPERT 1，HARD 1，NORMAL 0，EASY 0"
    assert lines[2] == "打过但还没 AP（3 张，最近打的在前）："
    assert lines[3] == "  曲5 EXPERT：1 局"
    assert lines[4] == "  曲4 EXPERT：1 局，最好 P100 G5 g0 B0 M0"
    assert lines[5] == "  ……还有 1 张"
    assert lines[6] == "最近 2 局："
    assert lines[7].endswith("曲5 EXPERT  P? G? g? B? M?  结果没读全")
    assert lines[8].endswith("曲4 EXPERT  P100 G5 g0 B0 M0  FC")


def test_report_best_prefers_fewest_mistakes():
    lines = report_lines([play(2, miss=3), play(2, miss=1, great=1), play(2, miss=4)], str, recent=0)
    assert lines[-1] == "  2 EXPERT：3 局，最好 P100 G1 g0 B0 M1"


def test_report_chart_offsets():
    offsets = {
        "1_expert": {"offset_ms": 1.25, "note_speed": 5.0},
        "2_hard": {"offset_ms": -3.0, "note_speed": 5.0},
        "3_expert": {"offset_ms": 0.0, "note_speed": 5.0},
    }
    lines = report_lines([play(1)], lambda mid: f"曲{mid}", recent=0, chart_offsets=offsets)
    i = lines.index("按谱面修正的 offset（2 张，偏得多的在前；正值 = 推迟）：")
    assert lines[i + 1 : i + 3] == ["  曲2 HARD：-3.0ms（流速 5.0）", "  曲1 EXPERT：+1.2ms（流速 5.0）"]


def test_report_empty():
    assert report_lines([], str) == ["还没有演奏记录（data/records.jsonl）"]


def test_history_all(tmp_path):
    store = RecordStore(tmp_path)
    for i in range(25):
        store.append(play(i))
    assert len(store.history()) == 20
    assert len(store.history(None)) == 25
    assert store.history(None)[0].music_id == 0
