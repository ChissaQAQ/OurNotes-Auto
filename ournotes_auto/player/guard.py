"""演奏期间确认游戏还停在演奏画面。

右上角的暂停按钮是不透明的界面元素，演奏画面上总能原样看到；暂停菜单（变暗模糊）、闪退回桌面、
切到别的应用时都看不到。连续 ``lost_s`` 秒看不到就置位 ``abort``，让执行器停止发送触控，
否则会一直点到暂停菜单的按钮（判定线附近正好是「终止」「重试」「继续」）或桌面图标。
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


class PlayInterrupted(RuntimeError):
    """演奏中途离开了演奏画面，已停止发送触控。"""


def load_template(path: Path = TEMPLATE_PATH) -> np.ndarray | None:
    try:
        data = np.fromfile(path, np.uint8)
    except OSError:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


class PlayGuard:
    def __init__(
        self,
        source: FrameSource,
        template: np.ndarray,
        lost_s: float = 2.0,
        interval_s: float = 0.5,
    ):
        self.source = source
        self.template = template
        self.lost_s = lost_s
        self.interval_s = interval_s
        th, tw = template.shape[:2]
        x0, y0 = max(PAUSE_POS[0] - MARGIN, 0), max(PAUSE_POS[1] - MARGIN, 0)
        x1, y1 = min(PAUSE_POS[0] + tw + MARGIN, DESIGN_W), min(PAUSE_POS[1] + th + MARGIN, DESIGN_H)
        self._size = (x1 - x0, y1 - y0)
        self._design_box = (x0, y0, x1, y1)
        self.abort = threading.Event()
        self.lost = False  # 是因为看不到演奏画面而停止的
        self._done = threading.Event()
        self._thread: threading.Thread | None = None

    def visible(self, frame: np.ndarray) -> bool:
        h, w = frame.shape[:2]
        x0, y0, x1, y1 = self._design_box
        sx, sy = w / DESIGN_W, h / DESIGN_H
        win = frame[round(y0 * sy) : round(y1 * sy), round(x0 * sx) : round(x1 * sx)]
        if win.shape[1::-1] != self._size:
            win = cv2.resize(win, self._size, interpolation=cv2.INTER_AREA)
        return float(cv2.matchTemplate(win, self.template, cv2.TM_CCOEFF_NORMED).max()) >= MATCH_THRESHOLD

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
        while not self._done.wait(self.interval_s):
            if stop is not None and stop.is_set():
                self.abort.set()
                return
            if not checking:
                continue  # 截图出了问题，只转发 stop
            try:
                frame, _ = self.source.grab()
                ok = self.visible(frame)
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
