"""OCR 模型下载：其他语言的模型按需下载，和默认模型相同的文件直接复制。"""

import hashlib
import io

import pytest

from ournotes_auto import context, ocr_models
from ournotes_auto.config import Config


def _serve(monkeypatch, files: dict[str, bytes]):
    """把 FILES/EXTRA 换成 ``files`` 的内容和校验值，urlopen 从 ``files`` 取；返回下载过的文件名。"""
    got = []
    table = {name: (name, hashlib.sha256(data).hexdigest()) for name, data in files.items()}
    monkeypatch.setattr(ocr_models, "FILES", {k: v for k, v in table.items() if "/" not in k})
    monkeypatch.setattr(ocr_models, "EXTRA", {"ko_kr": {k: v for k, v in table.items() if "/" in k}})

    def urlopen(url, timeout):
        got.append(url)
        return io.BytesIO(files[url])

    monkeypatch.setattr(ocr_models.urllib.request, "urlopen", urlopen)
    return got


def test_fetch_extra_copies_shared_det(tmp_path, monkeypatch):
    got = _serve(monkeypatch, {"det.onnx": b"det", "rec.onnx": b"rec", "ko_kr/det.onnx": b"det", "ko_kr/rec.onnx": b"ko"})
    ocr_models.fetch(tmp_path, log=lambda s: None)
    assert got == ["det.onnx", "rec.onnx"]
    assert ocr_models.missing(tmp_path, "ko_kr")
    ocr_models.fetch(tmp_path, "ko_kr", log=lambda s: None)
    assert got[2:] == ["ko_kr/rec.onnx"]
    assert (tmp_path / "ko_kr/det.onnx").read_bytes() == b"det"
    assert (tmp_path / "ko_kr/rec.onnx").read_bytes() == b"ko"


def test_fetch_rejects_bad_digest(tmp_path, monkeypatch):
    _serve(monkeypatch, {"det.onnx": b"det"})
    monkeypatch.setitem(ocr_models.FILES, "det.onnx", ("det.onnx", "0" * 64))
    with pytest.raises(ocr_models.FetchError):
        ocr_models.fetch(tmp_path, log=lambda s: None)
    assert not list(tmp_path.iterdir())


def test_open_ocr_fetches_only_selected_model(tmp_path, monkeypatch):
    fetched = []
    monkeypatch.setattr(context, "OCR_DIR", tmp_path)
    monkeypatch.setattr(ocr_models, "fetch", lambda out, model, log: fetched.append(model))
    monkeypatch.setattr("ournotes_auto.nav.ocr.MaaOcr", lambda bundle, model: model)
    cfg = Config()
    assert context.open_ocr(cfg) == ""
    cfg.loop.ocr_model = "ko_kr"
    assert context.open_ocr(cfg) == "ko_kr"
    assert fetched == ["ko_kr"]
