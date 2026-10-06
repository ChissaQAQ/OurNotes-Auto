"""下载界面导航用的 OCR 模型到 resource/model/ocr/（发布打包和从源码安装都用它）::

    python tools/fetch_ocr.py

模型是 PaddleOCR（Apache-2.0）的 PP-OCRv6 small，用 MaaCommonAssets（MIT）转好的 ONNX 版本，
固定到某次提交并校验 SHA-256；两边的许可证一起下载，随模型分发。
游戏语言是한국어时用 ko_kr/ 下的 PP-OCRv3 韩文识别模型（检测模型用同一个 v6 的）。
已有且校验一致的文件跳过；只用标准库，装依赖之前也能运行。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "dabcd4681ac990dc4361de26416d986abd80e4aa"
MODEL_URL = f"https://github.com/MaaXYZ/MaaCommonAssets/raw/{COMMIT}/OCR/ppocr_v6/small"
KO_URL = f"https://github.com/MaaXYZ/MaaCommonAssets/raw/{COMMIT}/OCR/ppocr_v3/ko_kr"
DET_SHA256 = "66c0f34caaf432553710fd9973a7134d8cf924db7109310c3d2562dcc39b209d"
# 保存的文件名（相对保存目录）: (下载地址, SHA-256)
FILES = {
    "det.onnx": (f"{MODEL_URL}/det.onnx", DET_SHA256),
    "rec.onnx": (f"{MODEL_URL}/rec.onnx", "7dcf6298d77d2a6eb44c1ebeed990826ca895a9c68d955a3eace00763d052949"),
    "keys.txt": (f"{MODEL_URL}/keys.txt", "b5f2bfe2bdd9448429e3e82b51c789775d9b42f2403d082b00662eb77e401c5d"),
    "ko_kr/det.onnx": (f"{MODEL_URL}/det.onnx", DET_SHA256),
    "ko_kr/rec.onnx": (f"{KO_URL}/rec.onnx", "4266592670045361b50919afb3afefcab344d11f0d79ec231762f737927e87f6"),
    "ko_kr/keys.txt": (f"{KO_URL}/keys.txt", "aa1fdc8ae8f7cd40a0ec4edb472eb0421e11427e6ccfee9915440742c18b0a20"),
    "LICENSE-MaaCommonAssets.txt": (
        f"https://github.com/MaaXYZ/MaaCommonAssets/raw/{COMMIT}/LICENSE",
        "2f8ab70ffd1bd53863a27835be02757a657de693e2a2aef9111dbbfa2c5737f0",
    ),
    "LICENSE-PaddleOCR.txt": (
        "https://github.com/PaddlePaddle/PaddleOCR/raw/v3.7.0/LICENSE",
        "3840c5c0c61c294264d2dd77b8777be6ddd90121ef4e0e64abcd22edea581d6e",
    ),
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", type=Path, default=ROOT / "resource" / "model" / "ocr", help="保存目录")
    p.add_argument("--force", action="store_true", help="已有且校验一致也重新下载")
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for name, (url, digest) in FILES.items():
        dst = args.out / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not args.force and dst.is_file() and sha256(dst) == digest:
            print(f"{name}：已是最新")
            continue
        tmp = dst.with_name(dst.name + ".part")
        print(f"{name}：下载中……")
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        if sha256(tmp) != digest:
            tmp.unlink()
            sys.exit(f"{name} 校验失败（下载不完整或上游文件变了）")
        tmp.replace(dst)
    print(f"OCR 模型已就绪：{args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
