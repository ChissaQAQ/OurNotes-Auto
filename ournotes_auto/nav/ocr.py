"""MaaFramework OCR：只借用它的识别能力，截图与点击仍走设备后端。

MaaFramework 的 Tasker 必须绑定控制器才能工作，这里绑定一个什么都不做的控制器，
再用 ``post_recognition`` 对传入的截图直接识别。
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from ..result_reader import OcrItem

logger = logging.getLogger(__name__)

MODEL_FILES = ("det.onnx", "rec.onnx", "keys.txt")


class OcrUnavailable(RuntimeError):
    pass


def _null_controller():
    from maa.controller import CustomController

    class NullController(CustomController):
        def connect(self) -> bool:
            return True

        def request_uuid(self) -> str:
            return "ournotes-null"

        def start_app(self, intent: str) -> bool:
            return False

        def stop_app(self, intent: str) -> bool:
            return False

        def screencap(self) -> np.ndarray:
            return np.zeros((720, 1280, 3), np.uint8)

        def click(self, x: int, y: int) -> bool:
            return False

        def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int) -> bool:
            return False

        def touch_down(self, contact: int, x: int, y: int, pressure: int) -> bool:
            return False

        def touch_move(self, contact: int, x: int, y: int, pressure: int) -> bool:
            return False

        def touch_up(self, contact: int) -> bool:
            return False

        def click_key(self, keycode: int) -> bool:
            return False

        def input_text(self, text: str) -> bool:
            return False

        def key_down(self, keycode: int) -> bool:
            return False

        def key_up(self, keycode: int) -> bool:
            return False

    return NullController()


class MaaOcr:
    """``read`` 返回的坐标已换算到 ``design`` 尺寸（默认 1280x720），界面坐标表都按该尺寸书写。"""

    def __init__(self, bundle: str | Path = "resource", model: str = "", design: tuple[int, int] = (1280, 720)):
        from maa.resource import Resource
        from maa.tasker import Tasker

        model_dir = Path(bundle) / "model" / "ocr" / model
        missing = [f for f in MODEL_FILES if not (model_dir / f).is_file()]
        if missing:
            raise OcrUnavailable(
                f"缺少 OCR 模型 {model_dir}/{{{','.join(missing)}}}：请运行 python tools/fetch_ocr.py 下载"
            )
        self.model = model
        self.design = design
        self._resource = Resource()
        if not self._resource.post_bundle(Path(bundle)).wait().succeeded:
            raise OcrUnavailable(f"MaaFramework 加载资源 {bundle} 失败")
        self._controller = _null_controller()
        self._controller.post_connection().wait()
        self._tasker = Tasker()
        if not self._tasker.bind(self._resource, self._controller) or not self._tasker.inited:
            raise OcrUnavailable("MaaFramework Tasker 初始化失败")

    def _run(self, image: np.ndarray, roi: tuple[int, int, int, int] | None, only_rec: bool):
        from maa.pipeline import JOCR, JRecognitionType

        h, w = image.shape[:2]
        sx, sy = w / self.design[0], h / self.design[1]
        param = JOCR(threshold=0.0, model=self.model, only_rec=only_rec)
        if roi is not None:
            x, y, rw, rh = roi
            param.roi = (round(x * sx), round(y * sy), round(rw * sx), round(rh * sy))
        job = self._tasker.post_recognition(JRecognitionType.OCR, param, np.ascontiguousarray(image)).wait()
        detail = job.get()
        reco = detail.nodes[0].recognition if detail and detail.nodes else None
        return (reco.all_results if reco else []), sx, sy

    def read(self, image: np.ndarray, roi: tuple[int, int, int, int] | None = None) -> list[OcrItem]:
        """检测并识别 BGR 截图中的全部文字；``roi`` 为设计尺寸下的 (x, y, w, h)。"""
        results, sx, sy = self._run(image, roi, only_rec=False)
        items = []
        for r in results:
            text = r.text.strip()
            if not text:
                continue
            bx, by, bw, bh = r.box
            items.append(OcrItem(bx / sx, by / sy, bw / sx, bh / sy, text))
        return items

    def read_text(self, image: np.ndarray, roi: tuple[int, int, int, int] | None = None) -> str:
        """把 ``roi``（省略时为整张图）整体当作一行文字识别（不做检测）。

        结算页上孤立的单个数字（如 0、2）经常被检测到却识别为空，逐格识别可靠得多。
        """
        results, _, _ = self._run(image, roi, only_rec=True)
        return results[0].text.strip() if results else ""
