"""MuMu 模拟器 external_renderer_ipc.dll 的 ctypes 封装（截图 + 多点触控）。

接口依据 MuMu 官方头文件 external_renderer_ipc.h，并参照 MaaFramework / MAA 的用法：
- ``nemu_connect`` 传安装根目录（如 ``D:\\MuMuPlayer``）与实例序号，返回 >0 的 handle；
- 截图为 RGBA、行序自下而上，需翻转并转成 BGR；返回 0 表示成功；
- 触控坐标直接用截图坐标（DLL 内部按屏幕旋转换算）；finger 取 1~10，
  对同一 finger 再次 down 即为移动。
- 同一实例重复 connect 返回同一个 handle，disconnect 一次即断开全部，故本模块按实例做引用计数。
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
from ctypes import POINTER, byref, c_char_p, c_int, c_ubyte, c_uint, c_wchar_p

import numpy as np

logger = logging.getLogger(__name__)

_SIGS: dict[str, tuple[list, object]] = {
    "nemu_connect": ([c_wchar_p, c_int], c_int),
    "nemu_disconnect": ([c_int], None),
    "nemu_get_display_id": ([c_int, c_char_p, c_int], c_int),
    "nemu_capture_display": ([c_int, c_uint, c_int, POINTER(c_int), POINTER(c_int), POINTER(c_ubyte)], c_int),
    "nemu_input_text": ([c_int, c_int, c_char_p], c_int),
    "nemu_input_event_touch_down": ([c_int, c_int, c_int, c_int], c_int),
    "nemu_input_event_touch_up": ([c_int, c_int], c_int),
    "nemu_input_event_key_down": ([c_int, c_int, c_int], c_int),
    "nemu_input_event_key_up": ([c_int, c_int, c_int], c_int),
    "nemu_input_event_finger_touch_down": ([c_int, c_int, c_int, c_int, c_int], c_int),
    "nemu_input_event_finger_touch_up": ([c_int, c_int, c_int], c_int),
}

DLL_SUBDIRS = (
    r"nx_main\sdk",
    r"nx_device\15.0\shell\sdk",
    r"nx_device\12.0\shell\sdk",
    r"shell\sdk",
)

RET_OK = 0
RET_RPC_EXCEPTION = 1
RET_NO_CONNECTION = 2
RET_BAD_ARG = 3
RET_FAILED = 4

# Linux input-event-codes（不是 Android keycode）
KEY_ESC, KEY_ENTER, KEY_HOME, KEY_BACK = 1, 28, 102, 158


class MuMuIpcError(RuntimeError):
    def __init__(self, func: str, ret: int):
        super().__init__(f"{func} 失败（返回 {ret}）")
        self.func, self.ret = func, ret


def find_dll(install_dir: str) -> str:
    for sub in DLL_SUBDIRS:
        p = os.path.join(install_dir, sub, "external_renderer_ipc.dll")
        if os.path.isfile(p):
            return p
    raise FileNotFoundError(f"在 {install_dir} 下找不到 external_renderer_ipc.dll")


_dlls: dict[str, ctypes.CDLL] = {}
_refs: dict[tuple[str, int], tuple[int, int]] = {}  # (安装目录, 实例) -> (handle, 引用数)
_global_lock = threading.Lock()


def _load_quiet(path: str) -> ctypes.CDLL:
    """DLL 静态链接 CRT，每次触控都往 stdout/stderr 打日志。其 CRT 在加载时从进程标准句柄初始化，
    所以加载期间把标准输出/错误临时换成 NUL，只让 DLL 的输出静音，不影响 Python 自身的输出。"""
    if os.name != "nt":
        return ctypes.CDLL(path)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetStdHandle.restype = ctypes.c_void_p
    k32.GetStdHandle.argtypes = [ctypes.c_uint32]
    k32.SetStdHandle.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.CreateFileW.argtypes = [
        c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p
    ]
    STD_OUTPUT, STD_ERROR = 0xFFFFFFF5, 0xFFFFFFF4  # (DWORD)-11, (DWORD)-12
    nul = k32.CreateFileW("NUL", 0x40000000, 3, None, 3, 0, None)  # GENERIC_WRITE, 共享读写, OPEN_EXISTING
    if nul in (None, ctypes.c_void_p(-1).value):
        return ctypes.CDLL(path)
    saved = {h: k32.GetStdHandle(h) for h in (STD_OUTPUT, STD_ERROR)}
    try:
        for h in saved:
            k32.SetStdHandle(h, nul)
        return ctypes.CDLL(path)
    finally:
        for h, v in saved.items():
            k32.SetStdHandle(h, v)


def _load_dll(path: str) -> ctypes.CDLL:
    dll = _dlls.get(path)
    if dll is None:
        dll = _load_quiet(path)
        for name, (argtypes, restype) in _SIGS.items():
            fn = getattr(dll, name)
            fn.argtypes = argtypes
            fn.restype = restype
        _dlls[path] = dll
    return dll


class MuMuIpc:
    """一个 MuMu 实例的连接。所有 DLL 调用默认串行（``single_lock=True``）。"""

    def __init__(
        self,
        install_dir: str,
        index: int,
        package: str | None = None,
        app_index: int = 0,
        dll_path: str | None = None,
        single_lock: bool = True,
    ):
        self.install_dir = os.path.abspath(install_dir)
        self.index = int(index)
        self.package = package
        self.app_index = int(app_index)
        self.dll_path = dll_path or find_dll(self.install_dir)
        self.dll = _load_dll(self.dll_path)
        self.handle = 0
        self.display_id = 0
        self.width = 0
        self.height = 0
        self._buf = None
        self._cap_lock = threading.RLock()
        self._in_lock = self._cap_lock if single_lock else threading.RLock()

    # ------------------------------------------------------------ 连接
    def connect(self) -> int:
        with self._cap_lock, self._in_lock:
            if self.handle:
                self._disconnect_locked()
            key = (self.install_dir, self.index)
            with _global_lock:
                h, n = _refs.get(key, (0, 0))
                if not h:
                    h = self.dll.nemu_connect(self.install_dir, self.index)
                    if h <= 0:
                        raise MuMuIpcError(f"nemu_connect({self.install_dir!r}, {self.index})，模拟器实例是否已启动？", h)
                _refs[key] = (h, n + 1)
            self.handle = h
            self.refresh_display_id()
            self._query_size()
            logger.info("已连接 MuMu 实例 %d（handle %d，display %d，%dx%d）", self.index, h, self.display_id, self.width, self.height)
            return h

    def _disconnect_locked(self) -> None:
        if not self.handle:
            return
        key = (self.install_dir, self.index)
        with _global_lock:
            h, n = _refs.get(key, (0, 0))
            if n <= 1:
                self.dll.nemu_disconnect(self.handle)
                _refs.pop(key, None)
            else:
                _refs[key] = (h, n - 1)
        self.handle = 0
        self._buf = None

    def disconnect(self) -> None:
        with self._cap_lock, self._in_lock:
            self._disconnect_locked()

    def __enter__(self) -> "MuMuIpc":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.disconnect()

    def refresh_display_id(self) -> int:
        """包名优先，失败退 "default"，再退 0。游戏重启后需重新获取。"""
        d = -1
        if self.package:
            d = self.dll.nemu_get_display_id(self.handle, self.package.encode("utf-8"), self.app_index)
        if d < 0:
            d = self.dll.nemu_get_display_id(self.handle, b"default", 0)
        self.display_id = d if d >= 0 else 0
        return self.display_id

    # ------------------------------------------------------------ 截图
    def _query_size(self) -> tuple[int, int]:
        w, h = c_int(0), c_int(0)
        ret = self.dll.nemu_capture_display(self.handle, self.display_id, 0, byref(w), byref(h), None)
        if ret != RET_OK:
            raise MuMuIpcError("nemu_capture_display(取尺寸)", ret)
        self.width, self.height = w.value, h.value
        self._buf = (c_ubyte * (self.width * self.height * 4))()
        return self.width, self.height

    def capture_raw(self) -> np.ndarray:
        """DLL 原始 RGBA（自下而上）；返回的是内部缓冲的视图，下次截图会被覆盖。"""
        with self._cap_lock:
            if not self.handle:
                raise MuMuIpcError("nemu_capture_display", RET_NO_CONNECTION)
            w, h = c_int(0), c_int(0)
            n = len(self._buf)
            ret = self.dll.nemu_capture_display(self.handle, self.display_id, n, byref(w), byref(h), self._buf)
            if ret != RET_OK:
                raise MuMuIpcError("nemu_capture_display", ret)
            if w.value * h.value * 4 != n:
                raise MuMuIpcError("nemu_capture_display(分辨率已变化，需要重连)", RET_FAILED)
            self.width, self.height = w.value, h.value
            return np.frombuffer(self._buf, dtype=np.uint8).reshape(h.value, w.value, 4)

    def screenshot(self) -> np.ndarray:
        """BGR、自上而下的图像。"""
        raw = self.capture_raw()
        return np.ascontiguousarray(raw[::-1, :, 2::-1])

    # ------------------------------------------------------------ 触控（截图坐标）
    def _chk(self, name: str, ret: int) -> None:
        if ret != RET_OK:
            raise MuMuIpcError(name, ret)

    def finger_down(self, finger: int, x: int, y: int) -> None:
        with self._in_lock:
            self._chk(
                "finger_touch_down",
                self.dll.nemu_input_event_finger_touch_down(self.handle, self.display_id, finger, int(x), int(y)),
            )

    finger_move = finger_down

    def finger_up(self, finger: int) -> None:
        with self._in_lock:
            self._chk("finger_touch_up", self.dll.nemu_input_event_finger_touch_up(self.handle, self.display_id, finger))

    def key_down(self, linux_keycode: int) -> None:
        with self._in_lock:
            self._chk("key_down", self.dll.nemu_input_event_key_down(self.handle, self.display_id, linux_keycode))

    def key_up(self, linux_keycode: int) -> None:
        with self._in_lock:
            self._chk("key_up", self.dll.nemu_input_event_key_up(self.handle, self.display_id, linux_keycode))
