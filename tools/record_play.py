"""点 LIVE START 后录制演奏画面（最大通道灰度，x 每 4 像素取 1），用于离线分析几何与运动。

python tools/record_play.py 秒数 输出前缀 [x,y | --no-tap]
"""
import sys, time
from pathlib import Path
import cv2
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ournotes_auto.config import load_config
from ournotes_auto.device.mumu import MuMuFrameSource, MuMuTouch, open_mumu

secs, prefix = float(sys.argv[1]), sys.argv[2]
cfg = load_config("config.yaml")
ipc = open_mumu(cfg.device)
src = MuMuFrameSource(ipc, idle_s=1.0)
touch = MuMuTouch(ipc)
n_max = int(secs * 70)
buf = np.zeros((n_max, 720, 320), np.uint8)
ts = np.zeros(n_max)
color = []
tap = next((a for a in sys.argv[3:] if "," in a), "1140,648")
if "--no-tap" not in sys.argv:
    touch.tap(*(int(v) for v in tap.split(",")))
t_start = time.perf_counter()
n = 0
while time.perf_counter() - t_start < secs and n < n_max:
    f, t = src.grab()
    buf[n] = f[:, ::4].max(axis=2)
    ts[n] = t
    if n % 60 == 0:
        color.append(f)
    n += 1
f, _ = src.grab()
cv2.imwrite(f"{prefix}_last.png", f)
np.save(f"{prefix}_frames.npy", buf[:n])
np.save(f"{prefix}_times.npy", ts[:n] - t_start)
for i, c in enumerate(color):
    cv2.imwrite(f"{prefix}_c{i:02d}.png", c)
print(n, "frames saved; color", len(color))
ipc.disconnect()
