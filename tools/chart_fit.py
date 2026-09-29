"""用整首谱面的全部音符在录制帧上做全局拟合，比较运动模型。

python tools/chart_fit.py prefix music_id diff first_arrival_s
对每帧、每个即将到达的音符按模型预测其前沿 y，取「前沿上方亮、下方暗」的边缘对比度求平均，
网格搜索时间平移与模型参数使其最大。
"""
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ournotes_auto.charts.bdon import BdonClient
from ournotes_auto.charts.model import SlideHead
from ournotes_auto.config import load_config
from ournotes_auto.geometry import Geometry

prefix, mid, diff, arr0 = sys.argv[1], int(sys.argv[2]), sys.argv[3], float(sys.argv[4])
cfg = load_config("config.yaml")
geo = Geometry(1280, 720, cfg.geometry)
chart = BdonClient(cfg.charts).chart(mid, diff)
fr = np.load(f"{prefix}_frames.npy", mmap_mode="r")
ts = np.load(f"{prefix}_times.npy")
ev = [(n.time_ms / 1000, n.span.center) for n in chart.notes]
ev += [(s.start_ms / 1000, s.nodes[0].span.center) for s in chart.slides if s.head is not SlideHead.NONE]
ev.sort()
nt = np.array([e[0] for e in ev]); nu = np.array([e[1] for e in ev])
first = nt.min()
J, yh = geo.judge_y, geo.horizon_y
t0 = arr0 - first
# 预先挑出帧 × 音符对（剩余 0.05~2.4s）
fi, ni = [], []
for i, t in enumerate(ts):
    dt = t0 + nt - t
    k = np.flatnonzero((dt > 0.05) & (dt < 2.4))
    fi += [i] * len(k); ni += k.tolist()
fi = np.array(fi); ni = np.array(ni)
print(f"{len(ev)} notes, {len(fi)} frame-note pairs")
F = np.asarray(fr)


def score(ypred):
    y = np.round(ypred).astype(int)
    ok = (y > 8) & (y < 700)
    xs = np.array([geo.x_at(u, yy) / 4 for u, yy in zip(nu[ni], ypred)]).round().astype(int).clip(0, 319)
    a = F[fi[ok], y[ok] - 3, xs[ok]].astype(float)
    b = F[fi[ok], y[ok] + 3, xs[ok]].astype(float)
    return float((a - b).mean())


def exp_model(shift, tau, h=yh):
    dt = t0 + shift + nt[ni] - ts[fi]
    return h + (J - h) * np.exp(-dt / tau)


def persp_model(shift, T, h):
    dt = t0 + shift + nt[ni] - ts[fi]
    uj, ut = 1 / (J - h), 1 / (0 - h)
    u = uj + (ut - uj) * dt / T
    return h + 1 / u


best = max((score(exp_model(s, tau)), s, tau) for s in np.arange(-0.03, 0.031, 0.005) for tau in np.arange(0.76, 0.90, 0.01))
print("exp coarse:", best)
_, s0, tau0 = best
best = max((score(exp_model(s, tau)), s, tau) for s in np.arange(s0 - 0.006, s0 + 0.0061, 0.001) for tau in np.arange(tau0 - 0.01, tau0 + 0.0101, 0.002))
print("exp fine: score %.2f shift %+.1fms tau %.3f" % (best[0], best[1] * 1000, best[2]))
for h in (-60.0, -50.0, -38.0, -30.0, -20.0):
    b = max((score(exp_model(s, tau, h)), s, tau) for s in np.arange(-0.02, 0.021, 0.002) for tau in np.arange(0.78, 0.90, 0.005))
    print(f"  exp yh={h:6.1f}: score {b[0]:.2f} shift {b[1]*1000:+.0f}ms tau {b[2]:.3f}")
bp = max((score(persp_model(s, T, h)), s, T, h) for s in np.arange(-0.03, 0.031, 0.01)
         for T in np.arange(2.0, 2.6, 0.05) for h in (-400.0, -300.0, -250.0, -200.0, -150.0))
print("persp best:", bp)
