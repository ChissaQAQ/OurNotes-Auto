"""演奏时监视连击数，找出断连的音符（调试 FC 用）。

演奏期间后台线程定时截屏，只保留右侧连击数的小图；演奏结束后逐张 OCR，连击数变小处即断连。
断连音符的位置用两种方法互相核对：
- 计数：之前的断连位置 + 断连前最后看到的连击数，数出是第几个判定（采样间隔内可能又判定了几个，是下界）；
- 时刻：断连前后两次采样的时刻换算到谱面时间（判定要等音符过线一段时间，所以画面上会晚一些）；
- 倒推：断连后第一次读到的连击数 n 说明那一刻之前又判定了 n 个，从那一刻往回数 n 个就是断掉的音符。
  断连后连击数常有一段时间读不出（隐藏或被特效盖住），这时下界会偏小很多，以倒推为准。

截屏走 ctypes（释放 GIL），对执行线程的影响可以从执行统计的迟到数据看出来。
"""

from __future__ import annotations

import bisect
import logging
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ..charts.model import Chart, SlideHead, SlideTail
from ..device.base import FrameSource
from .clock import now

logger = logging.getLogger(__name__)

COMBO_ROI = (1020, 305, 220, 95)  # 连击数（1280x720 下的 x, y, w, h），不含上方的 COMBO 字样
DESIGN_W, DESIGN_H = 1280, 720
DISPLAY_LAG_MS = 50.0  # 判定到截图里的连击数变化的大致延迟（渲染 + 截图），倒推断连音符时用


class ComboWatcher:
    def __init__(self, source: FrameSource, interval_s: float = 0.1, roi=COMBO_ROI):
        self.source = source
        self.interval_s = interval_s
        w, h = source.size
        sx, sy = w / DESIGN_W, h / DESIGN_H
        x, y, rw, rh = roi
        self._box = (round(y * sy), round((y + rh) * sy), round(x * sx), round((x + rw) * sx))
        self.samples: list[tuple[float, np.ndarray]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="combo-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> list[tuple[float, np.ndarray]]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        return self.samples

    def _loop(self) -> None:
        y0, y1, x0, x1 = self._box
        next_t = now()
        while not self._stop.is_set():
            try:
                frame, t = self.source.grab()
            except Exception as e:  # 监视只用于调试，截图出错不能影响演奏
                logger.warning("连击数监视截图失败，停止监视：%s", e)
                return
            self.samples.append((t, frame[y0:y1, x0:x1].copy()))
            next_t += self.interval_s
            self._stop.wait(max(0.0, next_t - now()))


def digit_boxes(crop: np.ndarray, min_level: int = 150) -> list[tuple[int, int, int, int]]:
    """连击数每一位数字的外框 (x, y, w, h)，从左到右。

    数字是接近白色的高亮字，按「最暗通道 ≥ min_level」找出高度过半的连通块（各位数字之间有暗缝，
    背景里的亮斑不够高）；连击数未显示时为空。
    背景里的亮条（如舞台灯柱）也可能够高，细长的还会被当成「1」：同一个数的各位数字顶边、底边对齐且
    彼此挨着，按此分组后只取离小图中心最近的一组。
    """
    h, w = crop.shape[:2]
    mask = (crop.min(axis=2) >= min_level).astype(np.uint8)
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    groups: list[list[tuple[int, int, int, int]]] = []
    for box in sorted(tuple(int(v) for v in s[:4]) for s in stats[1:] if s[3] >= 0.4 * h):
        if groups and _same_number(groups[-1][-1], box):
            groups[-1].append(box)
        else:
            groups.append([box])
    if not groups:
        return []

    def off_center(g: list[tuple[int, int, int, int]]) -> float:
        return max(g[0][0] - w / 2, w / 2 - max(x + bw for x, _, bw, _ in g), 0)

    return min(groups, key=lambda g: (off_center(g), -len(g)))


def _same_number(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    """b 紧挨在 a 右边、上下对齐（实测相邻数字的间距 ≤ 0.37 字高，顶边 / 底边差 ≤ 0.03 字高）。"""
    ax, ay, aw, ah = a
    bx, by, _, bh = b
    size = max(ah, bh)
    return bx - (ax + aw) <= 0.45 * size and abs(by - ay) <= 0.06 * size and abs(by + bh - ay - ah) <= 0.06 * size


def trim_digits(crop: np.ndarray, boxes: list[tuple[int, int, int, int]] | None = None) -> np.ndarray | None:
    """只保留连击数字所在的横向范围（两侧留些边）：背景里的亮斑会被识别成多出来的一位数。"""
    h, w = crop.shape[:2]
    boxes = digit_boxes(crop) if boxes is None else boxes
    if not boxes:
        return None
    pad = round(0.2 * h)  # 贴边裁切时「1」常被读成「|」「]」
    x0 = max(boxes[0][0] - pad, 0)
    x1 = min(max(b[0] + b[2] for b in boxes) + pad, w)
    return crop[:, x0:x1]


ONE_ASPECT = 0.45  # 数字块宽 / 高小于这个值的是「1」（1 约 0.35，其他数字约 0.6）
ONE_LIKE = frozenset("1|Il![]iTJ")  # 细长的 1 被整行识别读成的字符
LOOK_ALIKE = str.maketrans("OoZz", "0077")  # 这套字体的 7 常被读成「Z」


def combo_reader(read_text: Callable[[np.ndarray], str]) -> Callable[[np.ndarray], int | None]:
    """用整行识别（``read_text``）读连击数小图。

    位数和每一位是不是 1 由数字块决定：细长的数字块就是 1。整行识别常漏掉细长的 1（「1188」读成「188」），
    或把它读成「T」「|」「[」，所以只拿识别结果里不像 1 的字符，按顺序对应宽的数字块，个数必须一致。
    """

    def read(crop: np.ndarray) -> int | None:
        boxes = digit_boxes(crop)
        if not boxes:
            return None
        text = unicodedata.normalize("NFKC", read_text(trim_digits(crop, boxes)))
        chars = [c for c in text.translate(LOOK_ALIKE) if not c.isspace() and c not in ONE_LIKE]
        wide = [w >= ONE_ASPECT * h for _, _, w, h in boxes]
        if len(chars) != sum(wide) or not all(c in "0123456789" for c in chars):
            return None
        rest = iter(chars)
        return int("".join(next(rest) if is_wide else "1" for is_wide in wide))

    return read


def save_samples(
    path: Path, samples: list[tuple[float, np.ndarray]], t0: float, offset_ms: float, keep: int | None = None
) -> Path:
    """保存监视截图（离线调试读数用，见 :func:`load_samples`）。

    ``keep`` 不为空时只保留同目录下最新的 ``keep`` 个 .npz（每个几十 MB）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    times = np.array([t for t, _ in samples])
    crops = np.stack([c for _, c in samples]) if samples else np.zeros((0, 1, 1, 3), np.uint8)
    np.savez_compressed(path, times=times, crops=crops, t0=t0, offset_ms=offset_ms)
    if keep is not None:
        old = sorted(path.parent.glob("*.npz"), key=lambda p: p.stat().st_mtime, reverse=True)[keep:]
        for p in old:
            p.unlink(missing_ok=True)
    return path


def load_samples(path: Path) -> tuple[list[tuple[float, np.ndarray]], float, float]:
    with np.load(path) as d:
        return list(zip(d["times"].tolist(), d["crops"])), float(d["t0"]), float(d["offset_ms"])


@dataclass
class ComboBreak:
    t_before: float  # 最后一次看到断连前连击数的时刻（perf_counter）
    t_after: float  # 第一次看到变小后连击数的时刻
    before: int
    after: int


def find_breaks(
    samples: list[tuple[float, np.ndarray]],
    read: Callable[[np.ndarray], int | None],
    max_rate: float = 40.0,
    confirm: int = 2,
) -> list[ComboBreak]:
    """``read`` 把小图读成连击数（读不出为 None，如断连后连击数隐藏时）。

    连击数只会逐渐增加，或断连后从 0 数起；对不上的读数（多读、少读一位，读错某一位）当作误读丢弃：
    - 增加量超过 ``max_rate``（个/秒）加少许余量的，丢弃；
    - 变小的，要能在两次读数之间从 0 数到，且后面 ``confirm`` 个读数都接着它往上数、而不是接着原来的数，
      才算断连，否则丢弃（所以结束前一刻的断连看不到）。
    """
    readings = [(t, v) for t, img in samples if (v := read(img)) is not None]

    def follows(prev: tuple[float, int], cur: tuple[float, int]) -> bool:
        return prev[1] <= cur[1] <= prev[1] + 3 + max_rate * (cur[0] - prev[0])

    breaks = []
    last: tuple[float, int] | None = None
    for i, cur in enumerate(readings):
        if last is not None and follows(last, cur):
            last = cur
            continue
        if last is not None and (cur[1] >= last[1] or not follows((last[0], 0), cur)):
            continue
        chain = readings[i : i + 1 + confirm]
        if len(chain) <= confirm or not all(follows(a, b) for a, b in zip(chain, chain[1:])):
            continue
        if last is not None and any(follows(last, c) for c in chain[1:]):
            continue
        if last is not None:  # 第一个读数同样要经过确认，以免开头的误读被当成起点
            breaks.append(ComboBreak(last[0], cur[0], last[1], cur[1]))
        last = cur
    return breaks


def judged_notes(chart: Chart) -> list[tuple[float, str]]:
    """按时间排序的全部判定点 (谱面时刻 ms, 说明)，与游戏里连击数的计数单位一致。"""
    out = []
    for n in chart.notes:
        extra = f" {n.direction.value}" if n.kind.value == "flick" else ""
        out.append((n.time_ms, f"{n.kind.value}#{n.id} [{n.span.left:g},{n.span.right:g}]{extra}"))
    for s in chart.slides:
        name = f"{'guide' if s.guide else 'long'}#{s.id}"
        if s.head is not SlideHead.NONE:
            span = s.nodes[0].span
            out.append((s.nodes[0].time_ms, f"{name} 起点 {s.head.value} [{span.left:g},{span.right:g}]"))
        out += [(t, f"{name} 中继") for t in s.checkpoints]
        if s.tail is not SlideTail.NONE:
            span = s.nodes[-1].span
            out.append((s.nodes[-1].time_ms, f"{name} 终点 {s.tail.value} [{span.left:g},{span.right:g}]"))
    out.sort(key=lambda e: e[0])
    return out


def report_breaks(chart: Chart, breaks: list[ComboBreak], t0: float, offset_ms: float) -> list[str]:
    """把每次断连对应到谱面上的音符，返回说明文字（每次断连一段）。"""
    notes = judged_notes(chart)
    times = [t for t, _ in notes]
    lines = []
    index = 0  # 已经数过的判定数（上一次断连的音符之后）
    for k, b in enumerate(breaks, 1):
        index += b.before
        # 画面时刻 → 谱面时刻（执行时按 t0 + (谱面时刻 + offset) 发送）
        ms_before = (b.t_before - t0) * 1000 - offset_ms
        ms_after = (b.t_after - t0) * 1000 - offset_ms
        head = (
            f"断连 {k}：连击 {b.before} → {b.after}（画面对应谱面 {ms_before:.0f}~{ms_after:.0f}ms），"
            f"按计数至少是第 {index + 1} 个判定"
        )
        # 倒推：读到 after 时已判定 seen 个，其中断连之后的 after 个都连上了
        guess = bisect.bisect_right(times, ms_after - DISPLAY_LAG_MS) - b.after - 1
        if guess > index:
            head += f"，按断连后的连击数倒推约为第 {guess + 1} 个"
            index = guess
        lines.append(head + "：")
        for i in range(max(0, index - 2), min(len(notes), index + 4)):
            t, desc = notes[i]
            lines.append(f"  {'→' if i == index else ' '} #{i + 1} {t:.0f}ms {desc}")
        index += 1  # 断掉的这一个；之后的 after 会计入下一次断连前的连击数
    return lines
