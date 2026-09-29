"""端到端模拟：虚拟游戏按主机时钟渲染第一个音符的下落画面，检查触控是否在判定时刻到达。"""

import threading
import time

import numpy as np
import pytest

from ournotes_auto.charts.model import Chart, NoteKind, PointNote, Span
from ournotes_auto.config import Config
from ournotes_auto.geometry import Geometry
from ournotes_auto.planner import Action
from ournotes_auto.player.clock import now, sleep_until
from ournotes_auto.player.session import PlaySession, TimedSource

W, H = 640, 360


class SimGame:
    """60fps 虚拟画面：乐曲在 song_start 开始，音符按指数逼近模型下落（时间常数 ``tau_s``）。"""

    def __init__(self, chart: Chart, song_start: float, fps=60, tau_s=0.835, capture_delay_s=0.004):
        self.geo = Geometry(W, H)
        self.chart = chart
        self.song_start = song_start
        self.frame_s = 1 / fps
        self.tau_s = tau_s
        self.delay = capture_delay_s
        self.next_frame = now()
        self.rng = np.random.default_rng(0)

    @property
    def size(self):
        return W, H

    def _y(self, dt_s):
        return self.geo.note_y(dt_s, self.tau_s)

    def grab(self):
        sleep_until(self.next_frame)
        t_display = self.next_frame
        self.next_frame += self.frame_s
        frame = np.full((H, W, 3), 30, np.uint8)
        song_t = t_display - self.song_start
        for n in self.chart.notes:
            dt = n.time_ms / 1000 - song_t
            if -0.05 < dt < self.tau_s * 3.5:
                y = self._y(dt)
                if y <= 0:
                    continue
                y0 = max(int(y - 0.04 * (y - self.geo.motion_horizon_y)), 0)
                for yy in range(y0, min(int(y), H)):
                    frame[yy, int(self.geo.x_at(n.span.left, yy)) : int(self.geo.x_at(n.span.right, yy))] = 245
        # 截图在显示之后 delay 秒才拿到
        sleep_until(t_display + self.delay)
        return frame, now()


class LogTouch:
    def __init__(self):
        self.downs = []
        self.released = False

    def down(self, finger, x, y):
        self.downs.append(now())

    def move(self, finger, x, y):
        pass

    def up(self, finger):
        pass

    def flush(self):
        pass

    def release_all(self):
        self.released = True


@pytest.mark.parametrize("tau_s", [0.5, 0.835])
def test_session_hits_first_notes_on_time(tau_s):
    notes = [PointNote(i, 3500 + i * 250, NoteKind.TAP, Span(4 * (i % 6), 4 * (i % 6) + 4)) for i in range(8)]
    chart = Chart(1, "expert", notes=notes)
    cfg = Config()
    cfg.play.sync.stable_frames = 10
    cfg.play.guard_lost_s = 0  # 虚拟画面上没有暂停按钮
    song_start = now() + 0.3
    game = SimGame(chart, song_start, tau_s=tau_s)
    touch = LogTouch()
    outcome = PlaySession(cfg, game, touch).play(chart, stop=threading.Event())
    assert outcome.sync.ok, outcome.sync
    errs = [(t - (song_start + n.time_ms / 1000)) * 1000 for t, n in zip(touch.downs, notes)]
    # 误差 = 截图延迟（4ms，同步把显示时刻估计晚了）+ 前沿偏差（前沿比中心早到，估计提前）
    # 都是常量，由 offset_ms 吸收；这里只检查量级和一致性
    assert all(abs(e) < 15 for e in errs), errs
    assert max(errs) - min(errs) < 2.0, errs
    assert outcome.stalls == []


def test_timed_source_merges_queued_stalls():
    class Stalling:
        size = (4, 3)

        def __init__(self):
            self.lock = threading.Lock()
            self.delays = [0.0, 0.15, 0.0]

        def grab(self):
            with self.lock:  # 两个线程共用截图源，和 MuMuFrameSource 一样排队
                time.sleep(self.delays.pop(0))
                return np.zeros((3, 4, 3), np.uint8), now()

    src = TimedSource(Stalling())
    src.grab()
    threads = [threading.Thread(target=src.grab) for _ in range(2)]
    for t in threads:
        t.start()
        time.sleep(0.02)
    for t in threads:
        t.join()
    (stall,) = src.stalls()  # 第二个线程排队等的是同一次卡顿
    assert 0.15 <= stall[1] - stall[0] < 0.3
