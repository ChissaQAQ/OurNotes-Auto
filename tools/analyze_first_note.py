"""离线分析 record_play 录制：跟踪首音符前沿，拟合运动消失线。"""
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ournotes_auto.charts.model import Span
from ournotes_auto.config import load_config
from ournotes_auto.geometry import Geometry
from ournotes_auto.player.sync import fit_arrival

prefix = sys.argv[1]
lo_u, hi_u = float(sys.argv[2]), float(sys.argv[3])
fr = np.load(f"{prefix}_frames.npy").astype(np.int16)
ts = np.load(f"{prefix}_times.npy")
cfg = load_config("config.yaml")
geo = Geometry(1280, 720, cfg.geometry)
span = Span(lo_u, hi_u)
H = 720
mask = np.zeros((H, 320), bool)
for y in range(H):
    a, b = geo.row_range(span, y, shrink=0.15)
    mask[y, max(a // 4, 0):max(b // 4, 0)] = True
cnt = np.maximum(mask.sum(1), 1)
# 基线：首个音符出现前的静止帧
motion = np.array([np.abs(fr[i] - fr[i - 1])[mask].mean() if i else 0 for i in range(len(fr))])
start = int(sys.argv[4]) if len(sys.argv) > 4 else None
if start is None:
    # 找第一个大间隔（静止期）之后的帧
    gaps = np.flatnonzero(np.diff(ts) > 0.5)
    start = int(gaps[-1]) if gaps.size else 0
base = fr[start]
pts = []
for i in range(start + 1, len(fr)):
    diff = np.abs(fr[i] - base) * (fr[i] >= 150)
    score = (diff * mask).sum(1) / cnt
    hit = np.flatnonzero(score >= 30)
    if hit.size == 0:
        if pts:
            break
        continue
    lead = int(hit.max())
    if pts and lead < pts[-1][1] - 3:
        break
    pts.append((ts[i], lead))
    if lead >= geo.judge_y - 2:
        break
T = np.array([p[0] for p in pts]); Y = np.array([p[1] for p in pts], float)
print(f"start frame {start} t={ts[start]:.3f}; {len(pts)} samples, y {Y.min():.0f}->{Y.max():.0f}, t {T[0]:.3f}->{T[-1]:.3f}")
print("y samples:", Y.astype(int).tolist())
for top, bottom in [(0, 1), (0, 0.55), (0, 0.4), (0.1, 0.75), (0.2, 1)]:
    m = (Y >= top * H) & (Y <= bottom * H)
    if m.sum() < 6:
        continue
    f = fit_arrival(T[m], Y[m], geo.judge_y, -1e9, True)
    print(f"y∈[{top:.2f},{bottom:.2f}]H n={m.sum():3d}: horizon {f.horizon_y:8.1f}px ({f.horizon_y / H:+.4f}H) "
          f"rms {f.rms * 1000:5.2f}ms  arrival {f.arrival:.4f}s  σ {f.sigma * 1000:.2f}ms")
np.savez(f"{prefix}_track.npz", t=T, y=Y)
