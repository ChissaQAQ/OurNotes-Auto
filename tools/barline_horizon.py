"""从录制的画面中跟踪下落的小节线（整行白线），拟合 t = a + b/(y - y_h) 求运动消失线。"""
import sys
import numpy as np
sys.path.insert(0, ".")
from ournotes_auto.player.sync import fit_arrival

d = np.load(sys.argv[1]); fr = d["frames"]; ts = d["times"]
judge = float(sys.argv[2]) if len(sys.argv) > 2 else 570.7
H = 720
masks = [(f.min(axis=2) > 170).mean(axis=1) > 0.9 for f in fr]
static = np.mean(masks, axis=0) > 0.3  # 静止的白线（边框、判定线）
pts = []
for m, t in zip(masks, ts):
    rows = np.flatnonzero(m & ~static)
    if rows.size:
        # 取每段连续行的中心
        segs = np.split(rows, np.flatnonzero(np.diff(rows) > 1) + 1)
        pts.append((t, [float(s.mean()) for s in segs]))
    else:
        pts.append((t, []))
# 把检测到的线串成轨迹：每条线 y 单调增加
tracks, cur = [], []
last_y = None
for t, ys in pts:
    ys = [y for y in ys if 3 < y < judge + 5]
    if not ys:
        continue
    y = min(ys, key=lambda v: abs(v - last_y) if last_y is not None else v)
    if last_y is not None and (y < last_y - 2 or (cur and t - cur[-1][0] > 0.15)):
        if len(cur) > 8: tracks.append(cur)
        cur = []
    cur.append((t, y)); last_y = y
if len(cur) > 8: tracks.append(cur)
for tr in tracks:
    tt = np.array([p[0] for p in tr]); yy = np.array([p[1] for p in tr])
    print(f"track: {len(tr)} pts, y {yy.min():.1f}->{yy.max():.1f}, dur {(tt[-1]-tt[0])*1000:.0f}ms")
    for top in (0.0, 0.05):
        m = yy >= top * H
        fit = fit_arrival(tt[m], yy[m], judge, -1e9, True)
        print(f"   y>={top:.2f}H: horizon {fit.horizon_y:.2f}px = {fit.horizon_y/H:.4f}H, rms {fit.rms*1000:.2f}ms, "
              f"arrival-last {(fit.arrival-tt[m][-1])*1000:.1f}ms")
