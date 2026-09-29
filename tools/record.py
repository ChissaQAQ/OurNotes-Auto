"""录制一段画面（去重后的新帧）到 npz：python tools/record.py 秒数 输出.npz [x0 x1 y0 y1]"""
import sys, time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ournotes_auto.config import load_config
from ournotes_auto.device.mumu import MuMuFrameSource, open_mumu

secs, out = float(sys.argv[1]), sys.argv[2]
box = [int(v) for v in sys.argv[3:7]] if len(sys.argv) >= 7 else None
cfg = load_config("config.yaml")
ipc = open_mumu(cfg.device)
src = MuMuFrameSource(ipc, idle_s=1.0)
ts, frames = [], []
end = time.perf_counter() + secs
while time.perf_counter() < end:
    f, t = src.grab()
    if box:
        f = f[box[2]:box[3], box[0]:box[1]]
    ts.append(t); frames.append(f.copy())
np.savez_compressed(out, times=np.array(ts), frames=np.array(frames), box=np.array(box or [0, 0, 0, 0]))
d = np.diff(ts) * 1000
print(len(ts), "frames, interval ms mean %.2f max %.2f" % (d.mean(), d.max()))
ipc.disconnect()
