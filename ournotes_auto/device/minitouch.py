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
from pathlib import Path

from ..config import DeviceConfig
from .adb import adb_path, adb_serial

logger = logging.getLogger(__name__)

# 用自己的文件名：MaaFramework 的 ADB 控制器（界面操作）也可能在同一模拟器里跑 minitouch，
# 启动/退出时按进程名清理遗留进程不能误杀它（进程名最长 15 个字符）
PROC_NAME = "ournotes_touch"
REMOTE_BIN = f"/data/local/tmp/{PROC_NAME}"


def _local_binary(abi: str) -> Path:
    import MaaAgentBinary  # MaaFramework 附带的预编译 minitouch

    p = Path(MaaAgentBinary.__path__[0]) / "minitouch" / abi / "minitouch"
    if not p.is_file():
        raise FileNotFoundError(f"MaaAgentBinary 里没有 {abi} 的 minitouch")
    return p


class MinitouchTouch:
    """触点 0~9 即 minitouch 的 contact 0~9；down/move/up 先缓存，``flush`` 时一次提交（同一帧）。

    坐标：游戏画面（横屏，``size``）→ 触摸屏原生坐标（竖屏）。目前只支持屏幕顺时针转 90° 的横屏
    （MuMu 上观察到：原生 X = 短边 - 画面 y，原生 Y = 画面 x），启动时核对。
    """

    def __init__(self, cfg: DeviceConfig, size: tuple[int, int], device: str = "/dev/input/event4"):
        self.cfg = cfg
        self.size = size
        self.device = device
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
        rotation = re.search(r"SurfaceOrientation:\s*(\d)", self._run("shell", "dumpsys", "input"))
        if rotation is None or rotation[1] != "1":
            raise RuntimeError(f"minitouch 后端只支持横屏（SurfaceOrientation 1），当前 {rotation and rotation[1]}")
        abi = self._run("shell", "getprop", "ro.product.cpu.abi").strip()
        self._run("push", str(_local_binary(abi)), REMOTE_BIN, timeout_s=30)
        self._run("shell", "chmod", "755", REMOTE_BIN)
        self._spawn()
        logger.debug("minitouch 已启动（%s，触摸屏 %dx%d）", self._serial, self.max_x, self.max_y)

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
        try:
            self._proc.stdin.close()  # minitouch 读到 EOF 后自行退出
            self._proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            self._proc.kill()
        self._proc = None
