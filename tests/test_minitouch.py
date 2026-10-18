from pathlib import Path

from ournotes_auto.config import DeviceConfig
from ournotes_auto.device.minitouch import DEFAULT_DEVICE, MinitouchTouch, TouchScreen, touch_screens


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


DUMPSYS = Path(__file__).parent / "fixtures" / "dumpsys" / "mumu_input.txt"

# MuMu 开了三个标签：每个标签一块屏幕、一个触摸屏（#84 的结构，编号是编的）
MULTI = """Event Hub State:
  Devices:
    4: Xiaomi Touchscreen
      Classes: TOUCH | TOUCH_MT
      Path: /dev/input/event4
    12: Xiaomi Touchscreen
      Classes: TOUCH | TOUCH_MT
      Path: /dev/input/event12
    16: Xiaomi Touchscreen
      Classes: TOUCH | TOUCH_MT
      Path: /dev/input/event16
Input Reader State (Nums of device: 3):
  Device 3: Xiaomi Touchscreen
    EventHub Devices: [ 4 ] 
    Touch Input Mapper (mode - DIRECT):
      Viewport INTERNAL: displayId=0, uniqueId=local:1, port=0, orientation=1, isActive=[1]
  Device 9: Xiaomi Touchscreen
    EventHub Devices: [ 12 ] 
    Touch Input Mapper (mode - DIRECT):
      Viewport VIRTUAL: displayId=6, uniqueId=virtual:6, port=<none>, orientation=1, isActive=[1]
  Device 11: Xiaomi Touchscreen
    EventHub Devices: [ 16 ] 
    Touch Input Mapper (mode - DIRECT):
      Viewport VIRTUAL: displayId=13, uniqueId=virtual:13, port=<none>, orientation=0, isActive=[0]
  Device 12: Xiaomi Keyboard
    EventHub Devices: [ 17 ] 
    Keyboard Input Mapper:
      Viewport INTERNAL: displayId=20, orientation=1
"""


def test_touch_screens_single():
    """只开一个标签（实机 dumpsys）：display 0 是 event4。"""
    assert touch_screens(DUMPSYS.read_text(encoding="utf-8")) == {0: TouchScreen("/dev/input/event4", 1, True)}


def test_touch_screens_multi():
    assert touch_screens(MULTI) == {
        0: TouchScreen("/dev/input/event4", 1, True),
        6: TouchScreen("/dev/input/event12", 1, True),
        13: TouchScreen("/dev/input/event16", 0, False),
    }


def bound(display, dumpsys=MULTI):
    t = MinitouchTouch(DeviceConfig(adb_path="adb"), (1280, 720), display_id=lambda: display)
    return t, t._bind(dumpsys)


def test_bind_display():
    t, screen = bound(6)
    assert t.device == "/dev/input/event12" and screen.orientation == 1
    t, screen = bound(5)  # 找不到：用默认的
    assert t.device == DEFAULT_DEVICE and screen is None
    t = MinitouchTouch(DeviceConfig(adb_path="adb"), (1280, 720), device="/dev/input/event9", display_id=lambda: 6)
    assert t._bind(MULTI) is None and t.device == "/dev/input/event9"  # 固定了触摸屏


def test_rebind_when_display_changes():
    """游戏重启后换了屏幕：换触摸屏重开 minitouch，再发这一批。"""
    display = [0]
    t = MinitouchTouch(DeviceConfig(adb_path="adb"), (1280, 720), display_id=lambda: display[0])
    t._bind(MULTI)
    t.max_x, t.max_y = 720, 1280
    t._stdin = FakeStdin()
    fresh = FakeStdin()
    calls = []

    def spawn():
        calls.append(("spawn", t.device))
        t._stdin = fresh

    t._stop_proc = lambda: calls.append("stop")
    t._run = lambda *args, **kw: MULTI
    t._spawn = spawn
    t.tap(897, 571)
    assert calls == [] and t._stdin is not fresh
    display[0] = 6
    t.down(0, 897, 571)
    t.flush()
    assert calls == ["stop", ("spawn", "/dev/input/event12")]
    assert fresh.sent == ["d 0 149 897 50\nc\n"]
