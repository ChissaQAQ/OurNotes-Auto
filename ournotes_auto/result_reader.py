"""从结算画面的 OCR 结果中读取判定计数。

不依赖固定坐标：先找到 PERFECT / GREAT / GOOD / BAD / MISS / SCORE / COMBO 等标签，
再取与标签同一行、位于其右侧的数字。结算页有两种状态：

- 默认：每个判定一列总数；
- 点击 ⇄ 后：每个判定两列，依次为 FAST、SLOW。

两种状态各识别一次即可得到完整的 ``PlayResult`` 计数。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

JUDGEMENTS = ("perfect", "great", "good", "bad", "miss")
# OCR 常见误读：PERFECT 被背景遮挡成 PERFE、GREAT 读成 GRFAT 等，用前缀 + 编辑距离容错
_LABELS = {
    "perfect": ("PERFECT",),
    "great": ("GREAT",),
    "good": ("GOOD",),
    "bad": ("BAD",),
    "miss": ("MISS",),
    "score": ("SCORE",),
    "combo": ("COMBO", "MAXCOMBO"),
}
_EXCLUDE = ("HIGHSCORE", "SCORERATING", "LIVESCORE")
_DIGITS = re.compile(r"\d[\d,.\s]*")


@dataclass(frozen=True)
class OcrItem:
    x: float
    y: float
    w: float
    h: float
    text: str

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def right(self) -> float:
        return self.x + self.w


@dataclass
class ResultCounts:
    counts: dict[str, int]  # judgement -> 总数
    fast: dict[str, int]
    slow: dict[str, int]
    score: int | None = None
    combo: int | None = None


def _norm(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", unicodedata.normalize("NFKC", text).upper())


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def classify_label(text: str) -> str | None:
    t = _norm(text)
    if len(t) < 3 or any(t.startswith(x) for x in _EXCLUDE):
        return None
    # 标签里的 O 常被读成 0（G00D、COMB0）
    letters = re.sub(r"\d", "", t.replace("0", "O"))
    best, best_d = None, 99
    for key, names in _LABELS.items():
        for name in names:
            # 标签和数字可能被 OCR 合并成一项（如 "COMBO 767"），只比较字母部分
            cand = letters[: len(name)] if len(letters) >= len(name) else letters
            d = _edit_distance(cand, name)
            if len(cand) >= len(name) - 1 and d < best_d:
                best, best_d = key, d
    if best is None:
        return None
    tol = 1 if len(_LABELS[best][0]) <= 5 else 2
    return best if best_d <= tol else None


def parse_int(text: str) -> int | None:
    t = unicodedata.normalize("NFKC", text).replace("O", "0").replace("o", "0").replace("l", "1").replace("I", "1")
    m = _DIGITS.search(t)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group())
    return int(digits) if digits else None


def _row_numbers(label: OcrItem, items: list[OcrItem], max_dx: float) -> list[int]:
    """与标签同一行、在其右侧的数字（从左到右）。"""
    tol = max(label.h * 0.6, 4.0)
    same_row = [it for it in items if it is not label and abs(it.cy - label.cy) <= tol]
    # 同一行右侧还有别的标签（如 MISS 行右边的 COMBO）时，只取到它为止
    stop = min(
        (it.x for it in same_row if it.x > label.right - label.w * 0.2 and classify_label(it.text) is not None),
        default=float("inf"),
    )
    row = []
    for it in same_row:
        if it.x < label.right - label.w * 0.2 or it.x - label.right > max_dx or it.x >= stop:
            continue
        if classify_label(it.text) is not None:
            continue
        n = parse_int(it.text)
        if n is not None:
            row.append((it.x, n))
    # 标签本身带数字（OCR 把 "COMBO 767" 合为一项）
    own = _norm(label.text)
    m = re.search(r"\d+$", own)
    if m and not row:
        row.append((label.right, int(m.group())))
    return [n for _, n in sorted(row)]


def find_labels(items: list[OcrItem]) -> dict[str, OcrItem]:
    """每种标签取一个（同名多个时取最靠下的，避开顶部的 HIGH SCORE 之类）。"""
    found: dict[str, OcrItem] = {}
    for it in items:
        key = classify_label(it.text)
        if key is None:
            continue
        if key not in found or it.cy > found[key].cy:
            found[key] = it
    return found


def read_labels(items: list[OcrItem], screen_w: float) -> dict[str, list[int]]:
    return {k: _row_numbers(v, items, screen_w * 0.35) for k, v in find_labels(items).items()}


def parse_totals(items: list[OcrItem], screen_w: float) -> ResultCounts:
    rows = read_labels(items, screen_w)
    counts = {j: rows[j][0] for j in JUDGEMENTS if rows.get(j)}
    score = max(rows["score"]) if rows.get("score") else None
    combo = rows["combo"][-1] if rows.get("combo") else None
    return ResultCounts(counts=counts, fast={}, slow={}, score=score, combo=combo)


def parse_fast_slow(items: list[OcrItem], screen_w: float) -> tuple[dict[str, int], dict[str, int]]:
    rows = read_labels(items, screen_w)
    fast, slow = {}, {}
    for j in JUDGEMENTS:
        nums = rows.get(j) or []
        if len(nums) >= 2:
            fast[j], slow[j] = nums[0], nums[1]
    return fast, slow


def merge(totals: ResultCounts, fast: dict[str, int], slow: dict[str, int]) -> ResultCounts:
    """合并两种状态的读数；FAST+SLOW 与总数矛盾的判定视为误读并丢弃。

    PERFECT 只有偏差超出中心约 ±2.5ms 的才标 FAST/SLOW（长条中间节点、滑条尾按住到底等也不标），
    所以 FAST+SLOW 只需不超过总数；其余判定两列之和应等于总数。
    MISS 没有 FAST/SLOW 之分时（两列都是 0 或缺失）不影响结果。
    """
    ok_fast, ok_slow = {}, {}
    for j in JUDGEMENTS:
        if j in fast and j in slow:
            total = totals.counts.get(j)
            n = fast[j] + slow[j]
            if total is None or j == "miss" or n == total or (j == "perfect" and n <= total):
                ok_fast[j], ok_slow[j] = fast[j], slow[j]
    return ResultCounts(totals.counts, ok_fast, ok_slow, totals.score, totals.combo)


def timing_counts(rc: ResultCounts, include=("perfect", "great", "good", "bad")) -> tuple[int | None, int | None]:
    """用于 offset 修正的 FAST/SLOW 总数（不含 MISS：漏键多半不是时机问题）。"""
    if not all(j in rc.fast for j in include if rc.counts.get(j, 0) > 0):
        return None, None
    return sum(rc.fast.get(j, 0) for j in include), sum(rc.slow.get(j, 0) for j in include)
