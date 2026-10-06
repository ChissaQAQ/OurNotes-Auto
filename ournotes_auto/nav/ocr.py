"""MaaFramework OCR：只借用它的识别能力，截图与点击仍走设备后端。

MaaFramework 的 Tasker 必须绑定控制器才能工作，这里绑定一个什么都不做的控制器，
再用 ``post_recognition`` 对传入的截图直接识别。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import numpy as np

from ..result_reader import OcrItem
from .lang import HANGUL, localize

logger = logging.getLogger(__name__)

MODEL_FILES = ("det.onnx", "rec.onnx", "keys.txt")
_NUMBER = re.compile(r"[\d\s/:,.%+-]*\d[\d\s/:,.%+-]*")
_KANA = re.compile(r"[\u3040-\u30ff]")


def _pick(main: str, extra: str, main_score: float = 0.0, extra_score: float = 0.0) -> str:
    """同一个框两个模型读到的字：默认模型读出假名（日文曲名）就用它，另一个模型读出韩文就用另一个，
    其余（数字、英文、符号）用默认模型的（韩文模型的字表里没有「/」「:」「,」，「2/10」会读成「210」）。
    默认模型读出的是数字（「0/5」）、又不比另一个短时也用它，韩文模型会读成「이5」这样（「1회남음」默认模型只读出「1」）。
    默认模型很有把握、韩文模型没把握时也用默认模型的：B 站登录界面不随游戏语言变，还是简中（「退出登录」1.00 对「原出끔志」0.32）。"""
    if _KANA.search(main) or not HANGUL.search(extra):
        return main
    if main_score >= 0.9 and main_score - extra_score >= 0.15:
        return main
    if _NUMBER.fullmatch(main) and len(main.replace(" ", "")) >= len(extra.replace(" ", "")):
        return main
    return extra


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
    """``read`` 返回的坐标已换算到 ``design`` 尺寸（默认 1280x720），界面坐标表都按该尺寸书写。

    读到的文字经 :func:`~ournotes_auto.nav.lang.localize` 换成简中界面上的说法（``raw`` 时原样返回）。
    """

    def __init__(
        self,
        bundle: str | Path = "resource",
        model: str = "",
        design: tuple[int, int] = (1280, 720),
        raw: bool = False,
    ):
        from maa.resource import Resource
        from maa.tasker import Tasker

        for m in {"", model}:
            model_dir = Path(bundle) / "model" / "ocr" / m
            missing = [f for f in MODEL_FILES if not (model_dir / f).is_file()]
            if missing:
                raise OcrUnavailable(
                    f"缺少 OCR 模型 {model_dir}/{{{','.join(missing)}}}：请运行 python tools/fetch_ocr.py 下载"
                )
        # 指定了其他模型（如韩文）时两个模型都识别、逐框挑（见 _pick）；两个模型的检测模型相同，框一致
        self.model = model
        self.design = design
        self._text = (lambda t: t) if raw else localize
        self._resource = Resource()
        if not self._resource.post_bundle(Path(bundle)).wait().succeeded:
            raise OcrUnavailable(f"MaaFramework 加载资源 {bundle} 失败")
        self._controller = _null_controller()
        self._controller.post_connection().wait()
        self._tasker = Tasker()
        if not self._tasker.bind(self._resource, self._controller) or not self._tasker.inited:
            raise OcrUnavailable("MaaFramework Tasker 初始化失败")

    def _run(self, image: np.ndarray, roi: tuple[int, int, int, int] | None, only_rec: bool):
        results, sx, sy = self._run_model("", image, roi, only_rec)
        if self.model:
            extra = {tuple(r.box): r for r in self._run_model(self.model, image, roi, only_rec)[0]}
            for r in results:
                if e := extra.get(tuple(r.box)):
                    r.text = _pick(r.text, e.text, r.score, e.score)
        return results, sx, sy

    def _run_model(self, model: str, image: np.ndarray, roi: tuple[int, int, int, int] | None, only_rec: bool):
        from maa.pipeline import JOCR, JRecognitionType

        h, w = image.shape[:2]
        sx, sy = w / self.design[0], h / self.design[1]
        param = JOCR(threshold=0.0, model=model, only_rec=only_rec)
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
            text = self._text(r.text.strip())
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
        return self._text(results[0].text.strip()) if results else ""
