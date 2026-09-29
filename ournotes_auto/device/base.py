"""设备抽象：演奏时使用的截图源与触控后端接口。

坐标统一为「游戏画面像素」：横屏方向、原点左上，尺寸为 :attr:`FrameSource.size`。
各后端自行负责转换到设备的原生坐标（如竖屏旋转、触摸屏分辨率缩放）。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class TouchBackend(Protocol):
    def down(self, finger: int, x: int, y: int) -> None: ...

    def move(self, finger: int, x: int, y: int) -> None: ...

    def up(self, finger: int) -> None: ...

    def flush(self) -> None:
        """提交此前累积的操作（即时发送的后端可为空操作）。"""
        ...

    def release_all(self) -> None:
        """抬起所有触点（异常退出时调用）。"""
        ...


@runtime_checkable
class FrameSource(Protocol):
    @property
    def size(self) -> tuple[int, int]:
        """(宽, 高)，横屏。"""
        ...

    def grab(self) -> tuple[np.ndarray, float]:
        """返回 (BGR 图像 HxWx3 uint8, 截图完成时的 perf_counter 时刻)。"""
        ...
