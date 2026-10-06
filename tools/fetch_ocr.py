"""下载界面导航用的 OCR 模型到 resource/model/ocr/（发布打包和从源码安装都用它）::

    python tools/fetch_ocr.py
    python tools/fetch_ocr.py --model ko_kr    # 另外下载韩文识别模型

韩文等其他语言的模型不随发布包分发，运行时选了对应的游戏语言会自动下载，这里的 --model 用于提前下好。
下载地址、校验值见 ournotes_auto/ocr_models.py；只用标准库，装依赖之前也能运行。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ournotes_auto.ocr_models import EXTRA, FetchError, fetch  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", type=Path, default=ROOT / "resource" / "model" / "ocr", help="保存目录")
    p.add_argument("--model", action="append", default=[], choices=sorted(EXTRA), help="另外下载的模型，可重复")
    p.add_argument("--force", action="store_true", help="已有且校验一致也重新下载")
    args = p.parse_args()
    try:
        for model in ("", *args.model):
            fetch(args.out, model, force=args.force)
    except FetchError as e:
        sys.exit(str(e))
    print(f"OCR 模型已就绪：{args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
