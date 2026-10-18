"""经 adb 在模拟器里运行 minitouch、直接往触摸屏的 evdev 设备写事件的触控后端（截图仍走 MuMu IPC）。

MuMu IPC 的触控偶尔会出错：内核抓包看到同一时刻的两个按下都没出现，其中一个到抬起的时刻才冒出来、
之后一直按着（抬起丢了）。这条路径绕开 MuMu 的输入转发。只连配置里的模拟器实例，不会碰 USB 真机。

命令经 ``adb shell`` 的标准输入送给 ``minitouch -i``，不用 socket 模式：socket 模式的 minitouch
只服务一个客户端、监听队列很短，别的程序连上 adb forward 的端口后，adbd 会阻塞在连接这个
socket 上，整个 adbd 卡死（实测演奏中途触控全部停掉，只能在模拟器里杀掉 minitouch 才恢复）。
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..config import DeviceConfig
from .adb import adb_path, adb_serial

logger = logging.getLogger(__name__)

# 用自己的文件名：MaaFramework 的 ADB 控制器（界面操作）也可能在同一模拟器里跑 minitouch，
# 启动/退出时按进程名清理遗留进程不能误杀它（进程名最长 15 个字符）
PROC_NAME = "ournotes_touch"
REMOTE_BIN = f"/data/local/tmp/{PROC_NAME}"
DEFAULT_DEVICE = "/dev/input/event4"  # MuMu 上 display 0（只开一个标签时游戏就在这里）的触摸屏


def _local_binary(abi: str) -> Path:
    import MaaAgentBinary  # MaaFramework 附带的预编译 minitouch

    p = Path(MaaAgentBinary.__path__[0]) / "minitouch" / abi / "minitouch"
    if not p.is_file():
        raise FileNotFoundError(f"MaaAgentBinary 里没有 {abi} 的 minitouch")
    return p


@dataclass(frozen=True)
class TouchScreen:
    path: str  # evdev 节点
    orientation: int | None  # 这块屏幕的旋转（同 SurfaceOrientation），读不到为 None
    active: bool


def touch_screens(dumpsys: str) -> dict[int, TouchScreen]:
    """``dumpsys input`` 里每块屏幕（displayId）的触摸屏。MuMu 开多个标签时每个标签是一块屏幕，各有一个触摸屏（#84）。

    Event Hub 段是编号和节点（``4: Xiaomi Touchscreen`` 下面的 ``Path: /dev/input/event4``），Input Reader 段的
    各设备有 ``EventHub Devices: [ 4 ]``，触摸屏的 Touch Input Mapper 下有
    ``Viewport INTERNAL: displayId=0, ..., orientation=1, ..., isActive=[1]``。
    """
    hub, _, reader = dumpsys.partition("Input Reader State")
    paths: dict[int, str] = {}
    hub_id = None
    for line in hub.splitlines():
        if m := re.match(r"\s+(-?\d+): ", line):
            hub_id = int(m[1])
        elif (m := re.match(r"\s+Path: (\S+)", line)) and hub_id is not None:
            paths.setdefault(hub_id, m[1])
    screens: dict[int, TouchScreen] = {}
    for block in re.split(r"\n {2}Device -?\d+:", reader):
        hub_ids = re.search(r"EventHub Devices: \[\s*(-?\d+)", block)
        _, mapper, rest = block.partition("Touch Input Mapper")
        rest = re.split(r"\n(?! {5})", rest, maxsplit=1)[0]  # 只看这个设备的映射器（缩进更深的几行）
        vp = re.search(r"Viewport \w+: displayId=(\d+).*", rest)
        if not (mapper and hub_ids and vp and int(hub_ids[1]) in paths):
            continue
        orientation = re.search(r"orientation=(\d)", vp[0])
        active = re.search(r"isActive=\[(\d)\]", vp[0])
        screens.setdefault(
            int(vp[1]),
            TouchScreen(paths[int(hub_ids[1])], orientation and int(orientation[1]), active is None or active[1] == "1"),
        )
    return screens


class MinitouchTouch:
    """触点 0~9 即 minitouch 的 contact 0~9；down/move/up 先缓存，``flush`` 时一次提交（同一帧）。

    坐标：游戏画面（横屏，``size``）→ 触摸屏原生坐标（竖屏）。目前只支持屏幕顺时针转 90° 的横屏
    （MuMu 上观察到：原生 X = 短边 - 画面 y，原生 Y = 画面 x），启动时核对。

    触摸屏：给了 ``device`` 就用它；否则按 ``display_id()``（游戏所在的屏幕，截图用的那块）从 dumpsys input 里找，
    游戏重启后换了屏幕就跟着换，找不到时用 :data:`DEFAULT_DEVICE`。
    """

    def __init__(
        self,
        cfg: DeviceConfig,
        size: tuple[int, int],
        device: str | None = None,
        display_id: Callable[[], int] | None = None,
    ):
        self.cfg = cfg
        self.size = size
        self.device = device or DEFAULT_DEVICE
        self._display_id = None if device else display_id
        self._display: int | None = None  # 当前触摸屏所在的屏幕
        self._adb = adb_path(cfg)
        self._serial = adb_serial(cfg)
        self._proc: subprocess.Popen | None = None
        self._stdin = None
        self._buf: list[str] = []
        self._down: set[int] = set()
        self.max_x = self.max_y = 0

    def _run(self, *args: str, timeout_s: float = 15.0) -> str:
        r = subprocess.run(
            [self._adb, "-s", self._serial, *args], check=True, capture_output=True, text=True, timeout=timeout_s
        )
        return r.stdout

    def start(self) -> None:
        subprocess.run([self._adb, "connect", self._serial], check=True, capture_output=True, timeout=15)
        info = self._run("shell", "dumpsys", "input")
        screen = self._bind(info)
        if screen is not None and screen.orientation is not None:
            rotation = str(screen.orientation)
        else:
            m = re.search(r"SurfaceOrientation:\s*(\d)", info)
            rotation = m and m[1]
        if rotation != "1":
            raise RuntimeError(f"minitouch 后端只支持横屏（SurfaceOrientation 1），当前 {rotation}")
        abi = self._run("shell", "getprop", "ro.product.cpu.abi").strip()
        self._run("push", str(_local_binary(abi)), REMOTE_BIN, timeout_s=30)
        self._run("shell", "chmod", "755", REMOTE_BIN)
        self._spawn()
        logger.debug(
            "minitouch 已启动（%s，%s，display %s，触摸屏 %dx%d）",
            self._serial,
            self.device,
            self._display,
            self.max_x,
            self.max_y,
        )

    def _bind(self, info: str | None = None) -> TouchScreen | None:
        """换成游戏所在屏幕的触摸屏（``info`` 为 dumpsys input 的输出，没给就现查）。固定了触摸屏时什么也不做。"""
        if self._display_id is None:
            return None
        display = self._display_id()
        screen = touch_screens(info if info is not None else self._run("shell", "dumpsys", "input")).get(display)
        self._display = display
        if screen is None:
            log = logger.warning if display else logger.debug
            log("没找到 display %d 的触摸屏，用 %s", display, DEFAULT_DEVICE)
            self.device = DEFAULT_DEVICE
            return None
        if display:
            logger.info("触摸屏：display %d 的 %s", display, screen.path)
        if not screen.active:
            logger.warning("游戏所在的屏幕（display %d）不在前台，点击可能无效：在 MuMu 里把游戏的标签切到前面", display)
        self.device = screen.path
        return screen

    def _spawn(self) -> None:
        self._kill_server()
        self._proc = subprocess.Popen(
            [self._adb, "-s", self._serial, "shell", "-T", REMOTE_BIN, "-i", "-d", self.device],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        header = self._read_header()
        m = re.search(r"^\^ (\d+) (\d+) (\d+) (\d+)$", header, re.M)
        if m is None:
            raise RuntimeError(f"minitouch 握手失败：{header!r}")
        self.max_x, self.max_y = int(m[2]), int(m[3])
        self._stdin = self._proc.stdin
        # 继续读走 stderr，免得管道写满后 minitouch 卡住
        threading.Thread(target=self._drain, args=(self._proc.stderr,), daemon=True).start()

    def _read_header(self, timeout_s: float = 10.0) -> str:
        """``-i`` 模式下握手信息（``v 1`` / ``^ ...`` / ``$ pid``）写在 stderr。"""
        timer = threading.Timer(timeout_s, self._proc.kill)  # 超时就结束 adb，readline 随即返回空
        timer.start()
        lines: list[str] = []
        try:
            for raw in iter(self._proc.stderr.readline, b""):
                lines.append(raw.decode(errors="replace").strip())
                if lines[-1].startswith("$ "):
                    return "\n".join(lines)
        finally:
            timer.cancel()
        raise RuntimeError(f"minitouch 握手失败：{lines!r}")

    @staticmethod
    def _drain(stream) -> None:
        for raw in iter(stream.readline, b""):
            logger.debug("minitouch: %s", raw.decode(errors="replace").strip())

    def _kill_server(self) -> None:
        """杀掉上次遗留的进程（按进程名精确匹配；-f 会连执行 pkill 的 shell 一起杀掉）。"""
        subprocess.run(
            [self._adb, "-s", self._serial, "shell", "pkill", "-x", PROC_NAME], capture_output=True, timeout=15
        )

    def _native(self, x: int, y: int) -> tuple[int, int]:
        w, h = self.size
        return round(self.max_x - y * self.max_x / h), round(x * self.max_y / w)

    def down(self, finger: int, x: int, y: int) -> None:
        nx, ny = self._native(x, y)
        self._buf.append(f"d {finger} {nx} {ny} 50\n")
        self._down.add(finger)

    def move(self, finger: int, x: int, y: int) -> None:
        nx, ny = self._native(x, y)
        self._buf.append(f"m {finger} {nx} {ny} 50\n")

    def up(self, finger: int) -> None:
        self._buf.append(f"u {finger}\n")
        self._down.discard(finger)

    def flush(self) -> None:
        if not self._buf:
            return
        self._buf.append("c\n")
        data = "".join(self._buf).encode()
        self._buf.clear()
        if self._display_id is not None and self._display_id() != self._display:
            # 游戏重启后换了一块屏幕（MuMu 多标签）：换触摸屏重开 minitouch，这一批会晚约半秒
            self._stop_proc()
            self._bind()
            self._spawn()
        try:
            self._write(data)
        except OSError:
            # adb 断了（比如别的程序重启了 adb server）：重启 minitouch 再发一次，这一批会晚约半秒
            logger.warning("minitouch 连接断开，重启", exc_info=True)
            self._stop_proc()
            self._spawn()
            self._write(data)

    def _write(self, data: bytes) -> None:
        self._stdin.write(data)
        self._stdin.flush()

    def release_all(self) -> None:
        if self._stdin is None:
            return
        for f in sorted(self._down):
            self.up(f)
        self.flush()

    def tap(self, x: int, y: int, hold_s: float = 0.06) -> None:
        """界面操作用的单击（触点 9，避开演奏用的低编号触点）。"""
        self.down(9, x, y)
        self.flush()
        time.sleep(hold_s)
        self.up(9)
        self.flush()

    def close(self) -> None:
        try:
            self.release_all()
        finally:
            self._stdin = None
            if self._proc is not None:
                self._stop_proc()
                self._kill_server()

    def _stop_proc(self) -> None:
        self._stdin = None  # 重开失败时 release_all 不再往关掉的管道写
        if self._proc is None:
            return
        try:
            self._proc.stdin.close()  # minitouch 读到 EOF 后自行退出
            self._proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            self._proc.kill()
        self._proc = None
