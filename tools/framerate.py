"""测 MuMu IPC 截图：连续轮询时新帧出现的间隔。"""
import sys, time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ournotes_auto.device.mumu_ipc import MuMuIpc

ipc = MuMuIpc(r"D:\MuMuPlayer", 1, package="com.bilibili.sirius.official")
ipc.connect()
prev = None; changes = []; caps = []
t_end = time.perf_counter() + float(sys.argv[1] if len(sys.argv) > 1 else 3)
while time.perf_counter() < t_end:
    t0 = time.perf_counter()
    raw = ipc.capture_raw()
    t1 = time.perf_counter()
    caps.append(t1 - t0)
    sub = raw[::4, ::4].copy()
    if prev is None or not np.array_equal(sub, prev):
        changes.append(t1)
    prev = sub
d = np.diff(changes) * 1000
caps = np.array(caps) * 1000
print(f"captures {len(caps)}  cap ms mean {caps.mean():.2f} p50 {np.median(caps):.2f} p99 {np.percentile(caps,99):.2f} max {caps.max():.2f}")
if len(d):
    print(f"new frames {len(changes)}  interval ms mean {d.mean():.2f} p10 {np.percentile(d,10):.2f} p50 {np.median(d):.2f} p90 {np.percentile(d,90):.2f} max {d.max():.2f}")
    print(np.round(d[:40], 1))
ipc.disconnect()
