"""演奏期间确认游戏还停在演奏画面。

右上角的暂停按钮是不透明的界面元素，演奏画面上总能原样看到；暂停菜单（变暗模糊）、闪退回桌面、
切到别的应用时都看不到。连续 ``lost_s`` 秒看不到就置位 ``abort``，让执行器停止发送触控，
否则会一直点到暂停菜单的按钮（判定线附近正好是「终止」「重试」「继续」）或桌面图标。

同时看暂停按钮左边的生命值条：连续 ``life_zero_s`` 秒是空的说明已经整体对不上了（自由演出 LIFE 归零
也不会中断，接着打只是浪费时间），同样停止触控，交给调用方暂停重试。暂停菜单里「重试」不会恢复生命值，
归零后重试的那一局从头到尾都是 0，只能不看。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import cv2
import numpy as np

from ..device.base import FrameSource
from .clock import now

logger = logging.getLogger(__name__)

TEMPLATE_PATH = Path("resource/image/play_pause.png")
PAUSE_POS = (1218, 16)  # 模板在 1280x720 画面上的左上角
MARGIN = 8  # 搜索范围四周多留的像素
# 演奏画面约 1.0，暂停菜单下约 0.68，其他画面不到 0.6
MATCH_THRESHOLD = 0.85
DESIGN_W, DESIGN_H = 1280, 720
# 暂停按钮左边的生命值条（1280x720 上的 x, y, w, h）：亮着的长度和生命值成正比，1000 为满。
# 平时绿色，超过 1000 的部分在左边叠青色，300 以下变红；空的部分是很暗的蓝色（最亮的通道约 50）
LIFE_BAR = (1013, 44, 187, 6)
LIT_LEVEL = 150
LIFE_EMPTY = 0.02  # 亮着的列不到这个比例算生命值归零（归零后一直是 0，不会再回复）


class PlayInterrupted(RuntimeError):
    """演奏中途离开了演奏画面，已停止发送触控。"""


class LifeDepleted(RuntimeError):
    """演奏中生命值降到 0（整体对不上了），已停止发送触控，还在演奏画面上。"""


def load_template(path: Path = TEMPLATE_PATH) -> np.ndarray | None:
    try:
        data = np.fromfile(path, np.uint8)
    except OSError:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


def pause_visible(frame: np.ndarray, template: np.ndarray) -> bool:
    """画面右上角是否有演奏画面的暂停按钮（暂停菜单下变暗模糊，认不出）。"""
    th, tw = template.shape[:2]
    x0, y0 = max(PAUSE_POS[0] - MARGIN, 0), max(PAUSE_POS[1] - MARGIN, 0)
    x1, y1 = min(PAUSE_POS[0] + tw + MARGIN, DESIGN_W), min(PAUSE_POS[1] + th + MARGIN, DESIGN_H)
    h, w = frame.shape[:2]
    sx, sy = w / DESIGN_W, h / DESIGN_H
    win = frame[round(y0 * sy) : round(y1 * sy), round(x0 * sx) : round(x1 * sx)]
    if win.shape[1::-1] != (x1 - x0, y1 - y0):
        win = cv2.resize(win, (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA)
    return float(cv2.matchTemplate(win, template, cv2.TM_CCOEFF_NORMED).max()) >= MATCH_THRESHOLD


def life_fill(frame: np.ndarray) -> float:
    """右上角生命值条亮着的比例：生命值 1000 及以上为 1，归零为 0。"""
    x, y, w, h = LIFE_BAR
    fh, fw = frame.shape[:2]
    sx, sy = fw / DESIGN_W, fh / DESIGN_H
    bar = frame[round(y * sy) : round((y + h) * sy), round(x * sx) : round((x + w) * sx)]
    return float((bar.mean(axis=0).max(axis=1) > LIT_LEVEL).mean())


class PlayGuard:
    def __init__(
        self,
        source: FrameSource,
        template: np.ndarray,
        lost_s: float = 2.0,
        interval_s: float = 0.5,
        life_zero_s: float = 0.0,
    ):
        """``life_zero_s``：生命值条连续这么久是空的就停止触控；0 为不看生命值。"""
        self.source = source
        self.template = template
        self.lost_s = lost_s
        self.interval_s = interval_s
        self.life_zero_s = life_zero_s
        self.abort = threading.Event()
        self.lost = False  # 是因为看不到演奏画面而停止的
        self.life_zero = False  # 是因为生命值归零而停止的
        self._done = threading.Event()
        self._thread: threading.Thread | None = None

    def visible(self, frame: np.ndarray) -> bool:
        return pause_visible(frame, self.template)

    def start(self, stop: threading.Event | None = None) -> threading.Event:
        """开始检查，返回交给执行器的停止信号（``stop`` 置位时同样会置位）。"""
        self._thread = threading.Thread(target=self._loop, args=(stop,), name="play-guard", daemon=True)
        self._thread.start()
        return self.abort

    def stop(self) -> None:
        self._done.set()
        if self._thread is not None:
            self._thread.join()

    def _loop(self, stop: threading.Event | None) -> None:
        checking = True
        seen = now()
        alive = False  # 这一局看到过生命值条亮着
        empty_at = None  # 生命值条开始变空的时刻
        while not self._done.wait(self.interval_s):
            if stop is not None and stop.is_set():
                self.abort.set()
                return
            if not checking:
                continue  # 截图出了问题，只转发 stop
            try:
                frame, _ = self.source.grab()
                ok = self.visible(frame)
                # 只在认得出演奏画面时看生命值（别的画面上那个位置是什么都有可能）
                fill = life_fill(frame) if ok and self.life_zero_s > 0 else None
            except Exception as e:  # noqa: BLE001 - 检查失败不能影响演奏
                logger.warning("演奏画面检查失败，不再检查：%s", e)
                checking = False
                continue
            t = now()
            if ok:
                seen = t
            elif t - seen >= self.lost_s:
                logger.error("%.1fs 没有看到演奏画面（被暂停、闪退或切到了别的画面），停止触控", t - seen)
                self.lost = True
                self.abort.set()
                return
            # 重试不会恢复生命值：归零后重试，一开始就是 0，这一局看不出来，要先看到条亮着
            alive = alive or (fill is not None and fill >= LIFE_EMPTY)
            empty_at = (empty_at or t) if alive and fill is not None and fill < LIFE_EMPTY else None
            if empty_at is not None and t - empty_at >= self.life_zero_s:
                logger.error("生命值降到 0，整体对不上了，停止触控")
                self.life_zero = True
                self.abort.set()
                return
