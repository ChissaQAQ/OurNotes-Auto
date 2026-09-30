"""把一张图做成软件图标：docs/ui/icon.png（256×256，interface.json 的 icon 与 README 用）
和 docs/ui/logo.ico（16～256 多尺寸，MFAA 的窗口与托盘图标，组装时复制到 Assets/logo.ico）::

    python tools/make_icon.py 图片.png

非正方形的图居中补透明边；缩放前预乘 alpha，免得全透明像素的底色渗到边缘。
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PNG_OUT = ROOT / "docs" / "ui" / "icon.png"
ICO_OUT = ROOT / "docs" / "ui" / "logo.ico"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def load_square(path: Path) -> np.ndarray:
    img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        sys.exit(f"读不了 {path}")
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
    elif img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    h, w = img.shape[:2]
    side = max(h, w)
    out = np.zeros((side, side, 4), np.uint8)
    y, x = (side - h) // 2, (side - w) // 2
    out[y : y + h, x : x + w] = img
    return out


def resize(img: np.ndarray, size: int) -> np.ndarray:
    """BGRA 缩放到 size×size，全透明像素的颜色置零。"""
    a = img[..., 3:].astype(np.float32) / 255
    pre = np.concatenate([img[..., :3] * a, a * 255], axis=2)
    interp = cv2.INTER_AREA if size < img.shape[0] else cv2.INTER_LANCZOS4
    pre = np.clip(cv2.resize(pre, (size, size), interpolation=interp), 0, 255)
    alpha = pre[..., 3:].round()
    bgr = np.where(alpha > 0, pre[..., :3] * 255 / np.maximum(pre[..., 3:], 1e-6), 0)
    return np.concatenate([np.clip(bgr, 0, 255).round(), alpha], axis=2).astype(np.uint8)


def _dib(img: np.ndarray) -> bytes:
    """ICO 里的 32 位 BMP 条目：高度写两倍，像素自下而上，后跟 1 位的 AND 掩码。"""
    h, w = img.shape[:2]
    xor = img[::-1].tobytes()
    mask = np.packbits(img[::-1, :, 3] == 0, axis=1)
    row = (w + 31) // 32 * 4
    mask = np.pad(mask, ((0, 0), (0, row - mask.shape[1]))).tobytes()
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, len(xor) + len(mask), 0, 0, 0, 0)
    return header + xor + mask


def ico_bytes(img: np.ndarray, sizes=ICO_SIZES) -> bytes:
    """256 用 PNG 压缩，其余用 BMP，兼容老的读取方式。"""
    entries = []
    for s in sizes:
        im = resize(img, s)
        entries.append((s, cv2.imencode(".png", im)[1].tobytes() if s >= 256 else _dib(im)))
    head = struct.pack("<HHH", 0, 1, len(entries))
    offset = len(head) + 16 * len(entries)
    table, blobs = b"", b""
    for s, data in entries:
        table += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    return head + table + blobs


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("src", type=Path, help="源图片（最好是透明底的 PNG，256×256 以上）")
    args = p.parse_args()
    img = load_square(args.src)
    PNG_OUT.write_bytes(cv2.imencode(".png", resize(img, 256))[1].tobytes())
    ICO_OUT.write_bytes(ico_bytes(img))
    print(f"已生成 {PNG_OUT.relative_to(ROOT)} 与 {ICO_OUT.relative_to(ROOT)}（源图 {img.shape[0]}×{img.shape[0]}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
