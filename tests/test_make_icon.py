import importlib.util
import struct
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("make_icon", ROOT / "tools" / "make_icon.py")
make_icon = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_icon)


def test_resize_drops_hidden_colour():
    img = np.zeros((40, 40, 4), np.uint8)
    img[..., 1] = 255  # 全透明像素带绿色底
    img[11:29, 11:29] = (200, 100, 50, 255)
    out = make_icon.resize(img, 16)
    edge = out[(out[..., 3] > 0) & (out[..., 3] < 255)]
    assert len(edge) and (edge[:, 1] <= 100 + 1).all()  # 边缘不渗绿
    assert not out[out[..., 3] == 0][:, :3].any()


def test_ico_layout():
    img = np.zeros((64, 64, 4), np.uint8)
    img[16:48, 16:48] = (0, 0, 255, 255)
    data = make_icon.ico_bytes(img, sizes=(16, 256))
    assert struct.unpack("<HHH", data[:6]) == (0, 1, 2)
    entries = [struct.unpack("<BBBBHHII", data[6 + 16 * i : 22 + 16 * i]) for i in range(2)]
    (w16, h16, *_, size16, off16), (w256, h256, *_, size256, off256) = entries
    assert (w16, h16, w256, h256) == (16, 16, 0, 0)
    assert struct.unpack("<Iii", data[off16 : off16 + 12]) == (40, 16, 32)
    assert size16 == 40 + 16 * 16 * 4 + 16 * 4  # 像素 + 每行补齐到 4 字节的掩码
    assert data[off256 : off256 + 4] == b"\x89PNG" and off256 + size256 == len(data)
