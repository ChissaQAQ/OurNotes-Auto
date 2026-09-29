"""按 appmedia 结算截图（1000x461，FAST/SLOW 展开）的布局构造 OCR 结果。"""

from ournotes_auto.result_reader import (
    OcrItem,
    classify_label,
    merge,
    parse_fast_slow,
    parse_int,
    parse_totals,
    timing_counts,
)

W = 1000


def it(x, y, text, w=60, h=22):
    return OcrItem(x, y, w, h, text)


COMMON = [
    it(110, 10, "リザルト", 90),
    it(610, 15, "EXPERT Lv.24", 100),
    it(552, 118, "SCORE", 80, 26),
    it(755, 112, "1634177", 150, 34),
    it(552, 152, "HIGH SCORE", 80),
    it(812, 148, "1634177", 95),
    it(552, 178, "HIGH SCORE RATING", 130),
    it(793, 302, "ALL PERFECT", 105),
    it(788, 335, "COMBO", 60),
    it(862, 330, "767", 45, 28),
    it(760, 405, "次へ", 40),
]


def rows(values):
    out = []
    for i, (label, nums) in enumerate(values):
        y = 238 + i * 25
        out.append(it(546, y, label, 70, 20))
        for k, n in enumerate(nums):
            out.append(it(700 + k * 52, y, str(n), 30, 20))
    return out


def test_labels_tolerate_ocr_noise():
    assert classify_label("PERF CT") == "perfect"
    assert classify_label("GRFAT") == "great"
    assert classify_label("HIGH SCORE") is None
    assert classify_label("ALL PERFECT") is None
    assert classify_label("COMBO 767") == "combo"
    assert parse_int("1,634,177") == 1634177
    assert parse_int("2O4") == 204


def test_parse_totals_and_fast_slow():
    totals = parse_totals(
        COMMON + rows([("PERFECT", [489]), ("GREAT", [0]), ("GOOD", [0]), ("BAD", [0]), ("MISS", [0])]), W
    )
    assert totals.counts == {"perfect": 489, "great": 0, "good": 0, "bad": 0, "miss": 0}
    assert totals.score == 1634177
    assert totals.combo == 767
    fast, slow = parse_fast_slow(
        COMMON + rows([("PERF CT", [204, 285]), ("GREAT", [0, 0]), ("GOOD", [0, 0]), ("BAD", [0, 0]), ("MISS", [0, 0])]),
        W,
    )
    assert fast["perfect"] == 204 and slow["perfect"] == 285
    assert fast["miss"] == 0 and slow["miss"] == 0  # 不能把右侧 COMBO 的 767 读进来
    rc = merge(totals, fast, slow)
    assert timing_counts(rc) == (204, 285)


def test_merge_drops_inconsistent_rows():
    totals = parse_totals(COMMON + rows([("PERFECT", [480]), ("GREAT", [9]), ("GOOD", [0]), ("BAD", [0]), ("MISS", [0])]), W)
    fast, slow = parse_fast_slow(
        COMMON + rows([("PERFECT", [204, 285]), ("GREAT", [3, 6]), ("GOOD", [0, 0]), ("BAD", [0, 0]), ("MISS", [0, 0])]), W
    )
    rc = merge(totals, fast, slow)
    assert "perfect" not in rc.fast  # 204+285 > 480，视为误读
    assert timing_counts(rc) == (None, None)


def test_merge_accepts_unmarked_perfect():
    # 实测（迷星叫 EASY，offset -12ms）：PERFECT 342，只有 84+2 个标了 FAST/SLOW
    totals = parse_totals(COMMON + rows([("PERFECT", [342]), ("GREAT", [0]), ("GOOD", [0]), ("BAD", [0]), ("MISS", [0])]), W)
    fast, slow = parse_fast_slow(
        COMMON + rows([("PERFECT", [84, 2]), ("GREAT", [0, 0]), ("GOOD", [0, 0]), ("BAD", [0, 0]), ("MISS", [0, 0])]), W
    )
    rc = merge(totals, fast, slow)
    assert timing_counts(rc) == (84, 2)
    # 非 PERFECT 判定仍要求两列之和等于总数
    totals = parse_totals(COMMON + rows([("PERFECT", [340]), ("GREAT", [2]), ("GOOD", [0]), ("BAD", [0]), ("MISS", [0])]), W)
    fast, slow = parse_fast_slow(
        COMMON + rows([("PERFECT", [84, 2]), ("GREAT", [1, 0]), ("GOOD", [0, 0]), ("BAD", [0, 0]), ("MISS", [0, 0])]), W
    )
    assert timing_counts(merge(totals, fast, slow)) == (None, None)
