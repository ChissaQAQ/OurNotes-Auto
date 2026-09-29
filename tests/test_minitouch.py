from ournotes_auto.config import DeviceConfig
from ournotes_auto.device.minitouch import MinitouchTouch


class FakeStdin:
    def __init__(self):
        self.sent = []
        self._pending = b""

    def write(self, data):
        self._pending += data

    def flush(self):
        if self._pending:
            self.sent.append(self._pending.decode())
            self._pending = b""


def make_touch():
    t = MinitouchTouch(DeviceConfig(adb_path="adb"), (1280, 720))
    t.max_x, t.max_y = 720, 1280
    t._stdin = FakeStdin()
    return t


def test_native_coordinates():
    """画面 (897, 571) 在 MuMu 触摸屏上是 X=149、Y=897（内核抓包里看到的）。"""
    t = make_touch()
    assert t._native(897, 571) == (149, 897)
    assert t._native(0, 0) == (720, 0)


def test_flush_commits_batch():
    t = make_touch()
    t.down(3, 897, 571)
    t.down(5, 383, 571)
    assert t._stdin.sent == []
    t.flush()
    t.up(3)
    t.move(5, 400, 571)
    t.flush()
    t.flush()  # 没有新操作时不发送
    assert t._stdin.sent == ["d 3 149 897 50\nd 5 149 383 50\nc\n", "u 3\nm 5 149 400 50\nc\n"]


def test_release_all():
    t = make_touch()
    t.down(1, 100, 100)
    t.down(2, 200, 100)
    t.up(1)
    t.flush()
    t.release_all()
    assert t._stdin.sent[-1] == "u 2\nc\n"


class BrokenStdin(FakeStdin):
    def flush(self):
        raise BrokenPipeError


def test_restart_on_broken_pipe():
    """adb 断开时重启 minitouch，并把这一批重发给新进程。"""
    t = make_touch()
    t._stdin = BrokenStdin()
    fresh = FakeStdin()
    calls = []

    def spawn():
        calls.append("spawn")
        t._stdin = fresh

    t._stop_proc = lambda: calls.append("stop")
    t._spawn = spawn
    t.down(0, 897, 571)
    t.flush()
    assert calls == ["stop", "spawn"]
    assert fresh.sent == ["d 0 149 897 50\nc\n"]
