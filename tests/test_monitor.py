import numpy as np

from ournotes_auto.charts.model import Chart, NoteKind, PathNode, PointNote, Slide, SlideHead, SlideTail, Span
from ournotes_auto.player.monitor import (
    ComboBreak,
    combo_reader,
    digit_boxes,
    find_breaks,
    judged_notes,
    load_samples,
    report_breaks,
    save_samples,
    trim_digits,
)


def samples_of(values):
    """小图里直接存连击数（-1 表示读不出）。"""
    return [(i * 0.1, np.array([v])) for i, v in enumerate(values)]


def read(img):
    v = int(img[0])
    return None if v < 0 else v


def test_find_breaks_skips_unreadable():
    breaks = find_breaks(samples_of([0, 3, 7, -1, 2, 5, 5, 6, 1, 2, 4]), read)
    assert [(b.before, b.after) for b in breaks] == [(7, 2), (6, 1)]
    assert breaks[0].t_before == 0.2 and breaks[0].t_after == 0.4


def test_find_breaks_ignores_misreads():
    # 427 多读成 4274、435 少读成 35、开头误读：都不是断连
    values = [4274, 420, 424, 427, 4274, 430, 433, 35, 436, 440, 3, 5, 9]
    breaks = find_breaks(samples_of(values), read)
    assert [(b.before, b.after) for b in breaks] == [(440, 3)]


def test_find_breaks_needs_restart_from_zero():
    # 110~112 漏读成 10~12：0.1s 内数不到 10，不是断连
    assert find_breaks(samples_of([105, 107, 109, 10, 11, 12, 113, 114]), read) == []
    # 小的误读，但之后的读数接着原来的数往上走：也不是断连
    assert find_breaks(samples_of([3, 5, 6, 2, 7, 8, 9]), read) == []


def test_combo_reader_checks_digit_count():
    crop = np.zeros((95, 220, 3), np.uint8)
    crop[10:85, 40:90] = 255
    crop[10:85, 100:130] = 255  # 两个数字块，第二个细长（1）
    assert combo_reader(lambda img: "21")(crop) == 21
    assert combo_reader(lambda img: "2")(crop) == 21  # 漏掉的是 1，由数字块补上
    assert combo_reader(lambda img: "")(crop) is None
    assert combo_reader(lambda img: "22")(crop) is None
    assert combo_reader(lambda img: "")(np.zeros((95, 220, 3), np.uint8)) is None


def test_save_load_samples(tmp_path):
    samples = [(1.5, np.full((4, 5, 3), 7, np.uint8)), (1.6, np.zeros((4, 5, 3), np.uint8))]
    loaded, t0, offset = load_samples(save_samples(tmp_path / "c.npz", samples, 1.25, -14.0))
    assert (t0, offset) == (1.25, -14.0)
    assert [t for t, _ in loaded] == [1.5, 1.6] and (loaded[0][1] == 7).all()


def test_save_samples_keeps_newest(tmp_path):
    import os

    samples = [(1.5, np.zeros((4, 5, 3), np.uint8))]
    for i in range(4):
        p = save_samples(tmp_path / f"{i}.npz", samples, 0.0, 0.0, keep=2)
        os.utime(p, (1000 + i, 1000 + i))
    assert sorted(p.name for p in tmp_path.glob("*.npz")) == ["2.npz", "3.npz"]


def test_digit_boxes_ignore_background_strips():
    def crop_with(*boxes):
        crop = np.zeros((95, 220, 3), np.uint8)
        for x, y, w, h in boxes:
            crop[y : y + h, x : x + w] = 255
        return crop

    # 左边贴边的舞台灯柱（细长，会被当成 1）：9 读成 19
    nine = (75, 8, 51, 81)
    assert digit_boxes(crop_with((0, 4, 12, 76), nine)) == [nine]
    assert combo_reader(lambda img: "9")(crop_with((0, 4, 12, 76), nine)) == 9
    # 没对齐的亮条：31 读成 131
    three, one = (46, 8, 50, 81), (111, 9, 29, 79)
    assert digit_boxes(crop_with((0, 45, 20, 50), three, one)) == [three, one]
    # 四位数几乎占满宽度，照样是一组
    four = [(1 + 56 * i, 6, 50, 83) for i in range(4)]
    assert digit_boxes(crop_with(*four)) == four


def test_trim_digits_drops_side_glow():
    crop = np.zeros((95, 220, 3), np.uint8)
    crop[10:85, 60:90] = 255  # 一位数字
    crop[80:94, 190:205] = 255  # 右下角的亮斑
    digits = trim_digits(crop)
    assert digits.shape[1] == 30 + 2 * 19
    assert trim_digits(np.zeros((95, 220, 3), np.uint8)) is None


def test_judged_notes_counts_slide_parts():
    s = Slide(
        5,
        [PathNode(100, Span(0, 4)), PathNode(300, Span(4, 8))],
        head=SlideHead.TAP,
        tail=SlideTail.NONE,
        checkpoints=[200],
    )
    chart = Chart(1, "x", notes=[PointNote(1, 150, NoteKind.TAP, Span(0, 4))], slides=[s])
    assert [t for t, _ in judged_notes(chart)] == [100, 150, 200]


def test_report_breaks_indexes():
    notes = [PointNote(i, i * 100.0, NoteKind.TAP, Span(0, 4)) for i in range(20)]
    chart = Chart(1, "x", notes=notes)
    # 画面时刻 t 对应谱面 (t - 0.5) * 1000 + 20
    # 第 5 个判定（400ms）断连；之后又连了 3 个，第 9 个（800ms）再断
    breaks = [ComboBreak(0.8, 0.95, 4, 0), ComboBreak(1.23, 1.35, 3, 0)]
    lines = report_breaks(chart, breaks, t0=0.5, offset_ms=-20)
    first = lines[0]
    assert "第 5 个判定" in first and "320~470ms" in first and "倒推" not in first
    assert any(line.startswith("  → #5 ") for line in lines)
    second = next(line for line in lines if line.startswith("断连 2"))
    assert "第 9 个判定" in second and "倒推" not in second
    assert any(line.startswith("  → #9 ") for line in lines)


def test_report_breaks_estimates_from_after():
    notes = [PointNote(i, i * 100.0, NoteKind.TAP, Span(0, 4)) for i in range(20)]
    chart = Chart(1, "x", notes=notes)
    # 断连前最后读到 2（之后读不出），断连后第一次读到 3 时谱面约 1680ms：
    # 那时已判定 17 个（到 1600ms），最后 3 个连上了，断的是第 14 个（1300ms）
    lines = report_breaks(chart, [ComboBreak(1.0, 2.16, 2, 3)], t0=0.5, offset_ms=-20)
    assert "至少是第 3 个判定" in lines[0] and "倒推约为第 14 个" in lines[0]
    assert any(line.startswith("  → #14 ") for line in lines)


def test_combo_reader_trusts_narrow_boxes_as_one():
    crop = np.zeros((95, 220, 3), np.uint8)
    crop[10:85, 10:36] = 255  # 1
    crop[10:85, 60:86] = 255  # 1
    crop[10:85, 104:150] = 255  # 0
    crop[10:85, 158:203] = 255  # 7
    assert combo_reader(lambda img: "T107")(crop) == 1107
    assert combo_reader(lambda img: "|1O7")(crop) == 1107
    assert combo_reader(lambda img: "11Z7")(crop) == 1177  # Z 是 7
    assert combo_reader(lambda img: "11B7")(crop) is None  # 宽的数字块读不成数字
    assert combo_reader(lambda img: "107")(crop) == 1107  # 漏掉一个 1
    assert combo_reader(lambda img: "[07")(crop) == 1107
    assert combo_reader(lambda img: "1117")(crop) is None  # 宽的数字块读成了 1
    assert combo_reader(lambda img: "10")(crop) is None  # 宽的数字块少读了一个
