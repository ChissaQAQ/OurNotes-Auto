"""offset 自动修正：用实测得到的判定模型检验收敛。"""

import random

from ournotes_auto.config import AutoTuneConfig
from ournotes_auto.records import PlayResult, RecordStore, suggest_offset_delta

# 实测（MuMu，迷星叫 EASY，91 个有时机的判定）：offset=0 时整体晚约 8ms，
# 按键离散度约 1.5ms，PERFECT 中心 ±2.4ms 内不标 FAST/SLOW
LATE_MS, SPREAD_MS, UNMARKED_MS, TIMED = 8.0, 1.5, 2.4, 91


def fake_result(offset, rng):
    fast = slow = 0
    for _ in range(TIMED):
        err = offset + LATE_MS + rng.gauss(0, SPREAD_MS)
        fast += err < -UNMARKED_MS
        slow += err > UNMARKED_MS
    return fast, slow


def test_measured_counts():
    cfg = AutoTuneConfig()
    assert suggest_offset_delta(0, 76, cfg) == -cfg.step_ms
    assert suggest_offset_delta(84, 2, cfg) > 0.9 * cfg.step_ms
    assert suggest_offset_delta(3, 4, cfg) == 0.0  # 太少：已在不标区附近
    assert suggest_offset_delta(None, 4, cfg) == 0.0


def test_autotune_converges_without_oscillation(tmp_path):
    rng = random.Random(1)
    cfg = AutoTuneConfig()
    store = RecordStore(tmp_path)
    offsets = []
    for _ in range(12):
        offset = store.learned_offset_ms
        offsets.append(offset)
        fast, slow = fake_result(offset, rng)
        store.autotune(PlayResult(100001, "easy", fast=fast, slow=slow, offset_ms=offset), cfg)
    assert all(abs(o + LATE_MS) <= cfg.step_ms for o in offsets[-5:]), offsets
    assert len(store.history()) == 12
