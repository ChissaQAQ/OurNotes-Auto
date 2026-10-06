"""界面导航用的 OCR 模型：下载地址、校验值和下载（``tools/fetch_ocr.py`` 与运行时按需下载共用）。

模型是 PaddleOCR（Apache-2.0）的 PP-OCRv6 small，用 MaaCommonAssets（MIT）转好的 ONNX 版本，
固定到某次提交并校验 SHA-256；两边的许可证一起下载，随模型分发。
其他语言的识别模型（``EXTRA``）不随发布包分发，选了对应的游戏语言才下载：
한국어用 ko_kr/ 下的 PP-OCRv3 韩文识别模型（检测模型用同一个 v6 的）。
只用标准库，装依赖之前也能运行。
"""

from __future__ import annotations

import hashlib
import shutil
import urllib.request
from collections.abc import Callable
from pathlib import Path

COMMIT = "dabcd4681ac990dc4361de26416d986abd80e4aa"
MODEL_URL = f"https://github.com/MaaXYZ/MaaCommonAssets/raw/{COMMIT}/OCR/ppocr_v6/small"
KO_URL = f"https://github.com/MaaXYZ/MaaCommonAssets/raw/{COMMIT}/OCR/ppocr_v3/ko_kr"
DET_SHA256 = "66c0f34caaf432553710fd9973a7134d8cf924db7109310c3d2562dcc39b209d"
MODEL_FILES = ("det.onnx", "rec.onnx", "keys.txt")
# 保存的文件名（相对保存目录）: (下载地址, SHA-256)
FILES = {
    "det.onnx": (f"{MODEL_URL}/det.onnx", DET_SHA256),
    "rec.onnx": (f"{MODEL_URL}/rec.onnx", "7dcf6298d77d2a6eb44c1ebeed990826ca895a9c68d955a3eace00763d052949"),
    "keys.txt": (f"{MODEL_URL}/keys.txt", "b5f2bfe2bdd9448429e3e82b51c789775d9b42f2403d082b00662eb77e401c5d"),
    "LICENSE-MaaCommonAssets.txt": (
        f"https://github.com/MaaXYZ/MaaCommonAssets/raw/{COMMIT}/LICENSE",
        "2f8ab70ffd1bd53863a27835be02757a657de693e2a2aef9111dbbfa2c5737f0",
    ),
    "LICENSE-PaddleOCR.txt": (
        "https://github.com/PaddlePaddle/PaddleOCR/raw/v3.7.0/LICENSE",
        "3840c5c0c61c294264d2dd77b8777be6ddd90121ef4e0e64abcd22edea581d6e",
    ),
}
# 模型名（也是保存目录下的子目录）: 同上
EXTRA = {
    "ko_kr": {
        "ko_kr/det.onnx": (f"{MODEL_URL}/det.onnx", DET_SHA256),
        "ko_kr/rec.onnx": (f"{KO_URL}/rec.onnx", "4266592670045361b50919afb3afefcab344d11f0d79ec231762f737927e87f6"),
        "ko_kr/keys.txt": (f"{KO_URL}/keys.txt", "aa1fdc8ae8f7cd40a0ec4edb472eb0421e11427e6ccfee9915440742c18b0a20"),
    },
}


class FetchError(Exception):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def missing(out: Path, model: str = "") -> list[str]:
    """``out`` 下 ``model``（空串是默认模型）缺的文件。只看在不在，不校验。"""
    return [f for f in MODEL_FILES if not (out / model / f).is_file()]


def fetch(out: Path, model: str = "", force: bool = False, log: Callable[[str], None] = print) -> None:
    """把 ``model``（空串是默认模型和许可证）下载到 ``out``；已有且校验一致的文件跳过。"""
    for name, (url, digest) in (EXTRA[model] if model else FILES).items():
        dst = out / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not force and dst.is_file() and sha256(dst) == digest:
            log(f"{name}：已是最新")
            continue
        tmp = dst.with_name(dst.name + ".part")
        # 和默认模型相同的文件（检测模型）直接复制
        same = out / Path(name).name
        if model and FILES.get(same.name, (None, None))[1] == digest and same.is_file() and sha256(same) == digest:
            shutil.copyfile(same, tmp)
            tmp.replace(dst)
            continue
        log(f"{name}：下载中……")
        try:
            with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
        except OSError as e:
            tmp.unlink(missing_ok=True)
            raise FetchError(f"{name} 下载失败：{e}") from None
        if sha256(tmp) != digest:
            tmp.unlink()
            raise FetchError(f"{name} 校验失败（下载不完整或上游文件变了）")
        tmp.replace(dst)
