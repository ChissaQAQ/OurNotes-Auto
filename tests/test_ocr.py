"""MaaOcr 每次识别后清掉 Tasker 记下的识别详情（#94）；清不了照常识别。"""

from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from ournotes_auto.nav.ocr import MaaOcr

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "resource" / "model" / "ocr"
FIXTURES = Path(__file__).parent / "fixtures"


class FakeTasker:
    def __init__(self, clear=lambda: True):
        self.cleared = 0
        self._clear = clear

    def post_recognition(self, kind, param, image):
        reco = SimpleNamespace(all_results=[SimpleNamespace(text="LIVE", box=[10, 20, 30, 40], score=0.9)])
        detail = SimpleNamespace(nodes=[SimpleNamespace(recognition=reco)])
        return SimpleNamespace(wait=lambda: SimpleNamespace(get=lambda: detail))

    def clear_cache(self):
        self.cleared += 1
        return self._clear()


def _ocr(tasker) -> MaaOcr:
    ocr = MaaOcr.__new__(MaaOcr)  # 不加载模型
    ocr.model, ocr.design, ocr._text = "", (1280, 720), (lambda t: t)
    ocr._tasker = tasker
    ocr._clear_ok = True
    return ocr


IMAGE = np.zeros((720, 1280, 3), np.uint8)


def test_clears_cache_after_each_read():
    tasker = FakeTasker()
    ocr = _ocr(tasker)
    assert [i.text for i in ocr.read(IMAGE)] == ["LIVE"]
    assert ocr.read_text(IMAGE, (0, 0, 100, 50)) == "LIVE"
    assert tasker.cleared == 2


def _boom():
    raise RuntimeError("MaaTaskerClearCache")


@pytest.mark.parametrize("clear", [_boom, lambda: False])
def test_clear_failure_keeps_reading(clear):
    """清不了（出错或返回失败）：这次的结果照常返回，之后不再清。"""
    tasker = FakeTasker(clear)
    ocr = _ocr(tasker)
    assert [i.text for i in ocr.read(IMAGE)] == ["LIVE"]
    assert [i.text for i in ocr.read(IMAGE)] == ["LIVE"]
    assert tasker.cleared == 1


@pytest.mark.skipif(
    not all((MODEL_DIR / f).is_file() for f in ("det.onnx", "rec.onnx", "keys.txt")),
    reason="缺少 OCR 模型 resource/model/ocr",
)
def test_real_clear_cache_keeps_results():
    """真的 MaaFramework：清缓存成功，清过之后识别结果不变。"""
    ocr = MaaOcr(ROOT / "resource")
    img = cv2.imdecode(np.fromfile(FIXTURES / "results" / "totals_ap.jpg", np.uint8), cv2.IMREAD_COLOR)
    first = ocr.read(img)
    assert first and ocr._clear_ok
    assert ocr.read(img) == first
