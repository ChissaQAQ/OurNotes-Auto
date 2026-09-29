"""比较首音符运动模型：透视 t=a+b/(y-yh) 与指数 t=a+b·ln(y-yh)。"""
import sys
import numpy as np

d = np.load(sys.argv[1]); T = d["t"]; Y = d["y"]
lo = float(sys.argv[2]) if len(sys.argv) > 2 else 0
m = Y >= lo
T, Y = T[m], Y[m]
J = 570.7
def fit(g):
    A = np.stack([np.ones_like(g), g], 1)
    c, *_ = np.linalg.lstsq(A, T - T[0], rcond=None)
    r = T - T[0] - A @ c
    return c, r
for name, basis, grid in [
    ("persp", lambda yh: 1 / (Y - yh), np.arange(-2000, -1, 1.0)),
    ("exp", lambda yh: np.log(Y - yh), np.arange(-2000, -0.5, 0.5)),
]:
    best = min(((np.sqrt((fit(basis(yh))[1] ** 2).mean()), yh) for yh in grid))
    s, yh = best
    c, r = fit(basis(yh))
    g = {"persp": 1 / (J - yh), "exp": np.log(J - yh)}[name]
    arr = T[0] + c[0] + c[1] * g
    print(f"{name:5s}: yh={yh:7.1f}px ({yh/720:+.4f}H) rms={s*1000:5.2f}ms arrival={arr:.4f}")
    for fixed in (-38.0,):
        c2, r2 = fit(basis(fixed))
        print(f"        yh fixed {fixed}: rms={np.sqrt((r2**2).mean())*1000:6.2f}ms")
    if "-v" in sys.argv:
        print("   resid ms:", np.round(r * 1000, 1).tolist())
