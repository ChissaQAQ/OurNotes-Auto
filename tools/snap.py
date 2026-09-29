"""调试用：通过 MuMu IPC 截图保存到 debug/，可选点击或拖动。

python tools/snap.py [名称] [--tap x,y] [--swipe x1,y1,x2,y2] [--wait 秒]
"""

import argparse
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ournotes_auto.config import load_config  # noqa: E402
from ournotes_auto.device.mumu_ipc import MuMuIpc  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("name", nargs="?", default="snap")
ap.add_argument("--tap", action="append", default=[])
ap.add_argument("--swipe", action="append", default=[])
ap.add_argument("--wait", type=float, default=0.0)
ap.add_argument("--hold", type=float, default=0.06)
args = ap.parse_args()
cfg = load_config("config.yaml")
ipc = MuMuIpc(cfg.device.mumu_path, cfg.device.instance, package=cfg.device.package)
ipc.connect()
for tap in args.tap:
    x, y = (int(v) for v in tap.split(","))
    ipc.finger_down(1, x, y)
    time.sleep(args.hold)
    ipc.finger_up(1)
    time.sleep(0.5)
for swipe in args.swipe:
    x1, y1, x2, y2 = (int(v) for v in swipe.split(","))
    steps = 20
    ipc.finger_down(1, x1, y1)
    for k in range(1, steps + 1):
        time.sleep(0.02)
        ipc.finger_move(1, x1 + (x2 - x1) * k // steps, y1 + (y2 - y1) * k // steps)
    time.sleep(0.3)  # 停住再松手，避免惯性滚动
    ipc.finger_up(1)
    time.sleep(0.5)
time.sleep(args.wait)
out = Path("debug") / f"{args.name}.png"
out.parent.mkdir(exist_ok=True)
cv2.imwrite(str(out), ipc.screenshot())
ipc.disconnect()
print(out)
