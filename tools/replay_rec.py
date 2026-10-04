"""把 record_play 的录制（灰度、x/4 下采样）喂给 NoteTracker，检查实机数据上的同步结果。

python tools/replay_rec.py prefix music_id diff [key=value ...]   # 额外参数覆盖 SyncParams
"""
import dataclasses
import logging
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ournotes_auto.charts.bdon import BdonClient
from ournotes_auto.config import load_config
from ournotes_auto.geometry import Geometry
from ournotes_auto.player.sync import NoteTracker

logging.basicConfig(level=logging.INFO, format="%(message)s")
prefix, mid, diff = sys.argv[1], int(sys.argv[2]), sys.argv[3]
cfg = load_config("config.yaml")
over = {}
for kv in sys.argv[4:]:
    k, v = kv.split("=")
    over[k] = type(getattr(cfg.play.sync, k))(v) if not isinstance(getattr(cfg.play.sync, k), bool) else v == "1"
sp = dataclasses.replace(cfg.play.sync, **over)
geo = Geometry(1280, 720, cfg.geometry)
chart = BdonClient(cfg.charts).chart(mid, diff)
first_ms, spans = chart.first_hits()
fr = np.load(f"{prefix}_frames.npy", mmap_mode="r")
ts = np.load(f"{prefix}_times.npy")
tr = NoteTracker(geo, sp, first_ms, spans)
for g, t in zip(fr, ts):
    frame = np.repeat(np.asarray(g), 4, axis=1)
    r = tr.feed(np.repeat(frame[:, :, None], 3, axis=2), float(t))
    if r is not None:
        ys = np.array([s[1] for s in r.samples])
        print(f"arrival {r.arrival:.4f}  tau {r.tau_s:.4f}  horizon {r.horizon_y:.1f}  rms {r.rms_ms:.2f}ms  "
              f"σ {r.sigma_ms:.2f}ms  lead {r.lead_ms:.0f}ms  n={len(ys)} y {ys.min():.0f}->{ys.max():.0f}  ok={r.ok}")
        break
else:
    print("no result; armed", tr.armed, "samples", len(tr.samples))
