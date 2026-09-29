"""把截图的 OCR 结果保存为测试用的 JSON（tests/fixtures/screens/），并打印画面分类。

python tools/ocr_dump.py debug/e1.png:result debug/x5.png:band_confirm ...
（冒号后为保存的文件名；省略则只打印不保存）

不写入夹具的文字（玩家昵称、邀请码等）放在环境变量 OURNOTES_REDACT 里，用逗号分隔，
包含其中任何一项的识别结果都丢掉。
"""

import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ournotes_auto.nav.ocr import MaaOcr  # noqa: E402
from ournotes_auto.nav.screens import band_confirm_song, classify  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "screens"
REDACT = [s for s in os.environ.get("OURNOTES_REDACT", "").split(",") if s.strip()]
if not REDACT:
    print("提示：没有设置 OURNOTES_REDACT，保存的夹具里可能有玩家昵称、邀请码", file=sys.stderr)

ocr = MaaOcr(ROOT / "resource")
for arg in sys.argv[1:]:
    src, _, name = arg.partition(":")
    img = cv2.imdecode(np.fromfile(src, np.uint8), cv2.IMREAD_COLOR)
    items = [it for it in ocr.read(img) if not any(s.strip() in it.text for s in REDACT)]
    screen = classify(items)
    extra = f"  {band_confirm_song(items)}" if screen.name == "BAND_CONFIRM" else ""
    print(f"{src}: {screen.name}{extra}")
    if name:
        OUT.mkdir(parents=True, exist_ok=True)
        data = [[round(it.x, 1), round(it.y, 1), round(it.w, 1), round(it.h, 1), it.text] for it in items]
        (OUT / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
