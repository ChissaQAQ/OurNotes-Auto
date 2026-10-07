"""offset 自动修正：用实测得到的判定模型检验收敛。"""

import json
import random
from statistics import mean

import pytest

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


class MemoryStore(RecordStore):
    """state.json 放在内存里（连续写几百次时 Windows 上替换文件偶尔被占用）。"""

    def __init__(self):
        super().__init__("unused")
        self.state = {}

    def _load_state(self):
        return json.loads(json.dumps(self.state))

    def _save_state(self, state):
        self.state = state

    def append(self, result):
        pass


def play_charts(cfg, bias, plays=600, seed=3, play_sd=1.0):
    """轮流随机打 ``bias`` 里的谱面（各自偏 bias[k] ms），返回 (store, 每张谱面后一半的 r)。"""
    rng = random.Random(seed)
    store = MemoryStore()
    rs = {k: [] for k in bias}
    for _ in range(plays):
        k = rng.choice(list(bias))
        offset = store.learned_offset_ms + store.chart_offset_ms(k, "expert", 5.0)
        fast, slow = fake_result(offset + bias[k] + rng.gauss(0, play_sd), rng)
        rs[k].append((slow - fast) / max(fast + slow, 1))
        store.autotune(PlayResult(k, "expert", fast=fast, slow=slow, offset_ms=offset), cfg, 5.0)
    return store, {k: v[len(v) // 2 :] for k, v in rs.items()}


def test_chart_offset_reduces_systematic_bias():
    """一直偏 SLOW / 偏 FAST 的谱面学到自己的 offset，偏差明显变小；对照谱面不受影响，全局不被拉走。"""
    bias = {100001: 0.0, 100002: 0.0, 100003: 0.0, 100004: 0.0, 100005: 4.0, 100006: -4.0}
    _, before = play_charts(AutoTuneConfig(per_chart=False), bias)
    store, after = play_charts(AutoTuneConfig(), bias)
    for k in (100005, 100006):
        assert abs(mean(before[k])) > 0.7 and abs(mean(after[k])) < 0.35, (k, mean(before[k]), mean(after[k]))
        assert store.chart_offset_ms(k, "expert", 5.0) == pytest.approx(-bias[k], abs=2.0)
    for k in (100001, 100002, 100003, 100004):
        assert abs(store.chart_offset_ms(k, "expert", 5.0)) <= 2.0
        assert abs(mean(after[k])) <= abs(mean(before[k])) + 0.2
    assert store.learned_offset_ms == pytest.approx(-LATE_MS, abs=1.5)


def seed_recent(store, cfg, n=10):
    """先打几局中性的谱面，攒够全局近况。"""
    for i in range(n):
        fast, slow = (12, 10) if i % 2 else (10, 12)
        store.autotune(PlayResult(100100 + i, "expert", fast=fast, slow=slow), cfg, 5.0)


def test_chart_offset_needs_consistent_plays_and_is_capped():
    cfg = AutoTuneConfig(chart_max_ms=2.0)
    store = MemoryStore()
    seed_recent(store, cfg)

    def total():
        return store.learned_offset_ms + store.chart_offset_ms(100001, "expert", 5.0)

    for fast, slow in ((0, 50), (50, 0), (0, 50)):
        store.autotune(PlayResult(100001, "expert", fast=fast, slow=slow), cfg, 5.0)
    assert store.chart_offset_ms(100001, "expert", 5.0) == 0.0  # 有正有负：都算在全局上
    totals = [total()]
    for _ in range(8):
        store.autotune(PlayResult(100001, "expert", fast=0, slow=50), cfg, 5.0)
        totals.append(total())
    chart = store.chart_offset_ms(100001, "expert", 5.0)
    assert -2.0 <= chart < -1.0  # 一直偏 SLOW：提前，不超过上限
    assert all(b <= a for a, b in zip(totals, totals[1:], strict=False)) and totals[-1] < totals[0] - 1.5
    # 各谱面 offset 的平均值保持为 0（平均的部分在全局里）
    assert mean(e["offset_ms"] for e in store.chart_offsets().values()) == pytest.approx(0, abs=0.05)
    # 换了流速重新学；清除后都是 0
    assert store.chart_offset_ms(100001, "expert", 10.0) == 0.0
    store.autotune(PlayResult(100001, "expert", fast=0, slow=50), cfg, 10.0)
    assert store.chart_offsets()["100001_expert"]["note_speed"] == 10.0
    assert store.chart_offset_ms(100001, "expert", 5.0) == 0.0
    assert store.clear_chart_offsets() >= 1 and store.chart_offsets() == {}


def test_chart_offset_disabled():
    cfg = AutoTuneConfig(per_chart=False)
    store = MemoryStore()
    seed_recent(store, cfg)
    for _ in range(5):
        store.autotune(PlayResult(100001, "expert", fast=0, slow=50), cfg, 5.0)
    assert store.chart_offsets() == {} and store.learned_offset_ms < -4
