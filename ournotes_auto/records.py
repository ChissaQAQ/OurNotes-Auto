"""演奏记录与 offset 自动修正。

结算画面点击切换按钮后按判定分列 FAST/SLOW（含 PERFECT）。FAST 多说明按早了，应增大 offset
（整体推迟），SLOW 多则减小。学到的 offset 保存在 ``data/state.json``，与配置文件里的
``play.offset_ms`` 相加使用。

一直偏 FAST 或偏 SLOW 的谱面另有自己的 offset（``chart_offsets``，见 :class:`AutoTuneConfig`），
总 offset = 配置 + 全局学习值 + 谱面学习值。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import mean

from .config import AutoTuneConfig

logger = logging.getLogger(__name__)

# 全局近况：最近这么多局（算在全局上的）的 r 的平均；不到 RECENT_MIN 局时不按谱面修正
RECENT_WINDOW = 10
RECENT_MIN = 5


@dataclass
class PlayResult:
    music_id: int
    difficulty: str
    perfect: int | None = None
    great: int | None = None
    good: int | None = None
    bad: int | None = None
    miss: int | None = None
    fast: int | None = None  # 各判定 FAST 数之和（含 PERFECT；故意打 GREAT 时不含 GREAT）
    slow: int | None = None
    max_combo: int | None = None
    score: int | None = None
    offset_ms: float = 0.0  # 本次使用的总 offset
    sync_error_ms: float | None = None  # 同步拟合残差
    max_late_ms: float | None = None  # 执行器最大迟到
    stalls: list[list[float]] | None = None  # 演奏中截图卡住（模拟器卡顿）的谱面时段 [开始, 结束] ms
    timestamp: float = field(default_factory=time.time)

    @property
    def full_combo(self) -> bool | None:
        if None in (self.good, self.bad, self.miss):
            return None
        return self.good == 0 and self.bad == 0 and self.miss == 0

    @property
    def all_perfect(self) -> bool | None:
        if self.full_combo is None or self.great is None:
            return None
        return self.full_combo and self.great == 0


def timing_ratio(fast: int | None, slow: int | None, cfg: AutoTuneConfig) -> float | None:
    """r = (SLOW-FAST)/(SLOW+FAST)；FAST+SLOW 不到 ``min_samples``（或没读到）时为 None。"""
    if fast is None or slow is None:
        return None
    n = fast + slow
    if n < max(cfg.min_samples, 1):
        return None
    return (slow - fast) / n


def suggest_offset_delta(fast: int | None, slow: int | None, cfg: AutoTuneConfig) -> float:
    """根据 FAST/SLOW 计数给出 offset 修正量（毫秒，正值 = 推迟）。"""
    r = timing_ratio(fast, slow, cfg)
    return 0.0 if r is None else -cfg.step_ms * r


def chart_key(music_id: int, difficulty: str) -> str:
    return f"{music_id}_{difficulty}"


class RecordStore:
    def __init__(self, data_dir: str | Path = "data"):
        self.dir = Path(data_dir)
        self.records_path = self.dir / "records.jsonl"
        self.state_path = self.dir / "state.json"

    def _load_state(self) -> dict:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                logger.warning("state.json 损坏，已重置")
        return {}

    def _save_state(self, state: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    @property
    def learned_offset_ms(self) -> float:
        return float(self._load_state().get("learned_offset_ms", 0.0))

    def set_learned_offset(self, value: float) -> None:
        state = self._load_state()
        state["learned_offset_ms"] = round(value, 2)
        self._save_state(state)

    def chart_offset_ms(self, music_id: int, difficulty: str, note_speed: float) -> float:
        """这张谱面在流速 ``note_speed`` 下学到的 offset（没学过为 0）。"""
        entry = self._load_state().get("chart_offsets", {}).get(chart_key(music_id, difficulty))
        if not entry or entry.get("note_speed") != note_speed:
            return 0.0
        return float(entry.get("offset_ms", 0.0))

    def chart_offsets(self) -> dict[str, dict]:
        """``{"<musicId>_<难度>": {"offset_ms", "note_speed", "residuals", "updates"}}``。"""
        return dict(self._load_state().get("chart_offsets", {}))

    def clear_chart_offsets(self) -> int:
        """清除所有谱面的 offset（全局学习值不变），返回清掉了几张不为 0 的。"""
        state = self._load_state()
        count = sum(1 for e in state.pop("chart_offsets", {}).values() if e.get("offset_ms"))
        state.pop("recent_residuals", None)
        self._save_state(state)
        return count

    def append(self, result: PlayResult) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.records_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")

    def history(self, limit: int | None = 20) -> list[PlayResult]:
        """最近 ``limit`` 局（None 为全部），从旧到新。"""
        if not self.records_path.exists():
            return []
        lines = self.records_path.read_text(encoding="utf-8").splitlines()
        if limit is not None:
            lines = lines[-limit:]
        out = []
        for line in lines:
            try:
                out.append(PlayResult(**json.loads(line)))
            except (TypeError, ValueError):
                continue
        return out

    def autotune(self, result: PlayResult, cfg: AutoTuneConfig, note_speed: float = 5.0, label: str = "") -> float:
        """记录结果并按 FAST/SLOW 更新学到的 offset（全局或这张谱面的），返回修正量。"""
        self.append(result)
        if not cfg.enabled:
            return 0.0
        r = timing_ratio(result.fast, result.slow, cfg)
        if r is None:
            return 0.0
        state = self._load_state()
        recent = state.get("recent_residuals", [])
        label = label or chart_key(result.music_id, result.difficulty)
        delta = self._tune_chart(state, result, r, recent, cfg, note_speed, label) if cfg.per_chart else None
        if delta is None:  # 偏差算在全局上
            delta = -cfg.step_ms * r
            new = float(state.get("learned_offset_ms", 0.0)) + delta
            state["learned_offset_ms"] = round(new, 2)
            state["recent_residuals"] = [*recent, round(r, 3)][-RECENT_WINDOW:]
            logger.debug("FAST=%s SLOW=%s → offset 修正 %+.1fms（学习值 %.1fms）", result.fast, result.slow, delta, new)
        self._save_state(state)
        return delta

    def _tune_chart(
        self,
        state: dict,
        result: PlayResult,
        r: float,
        recent: list[float],
        cfg: AutoTuneConfig,
        note_speed: float,
        label: str,
    ) -> float | None:
        """记下这张谱面相对全局近况的偏差；最近几局一直往同一边偏时修正它的 offset、返回修正量，否则返回 None。"""
        if len(recent) < RECENT_MIN:
            logger.debug("%s：全局只有 %d 局近况，先不按谱面修正", label, len(recent))
            return None
        charts = state.setdefault("chart_offsets", {})
        key = chart_key(result.music_id, result.difficulty)
        entry = charts.get(key)
        if entry is None or entry.get("note_speed") != note_speed:
            if entry is not None:
                logger.info("%s：流速从 %s 改成了 %s，谱面 offset 重新学", label, entry.get("note_speed"), note_speed)
            entry = charts[key] = {"offset_ms": 0.0, "note_speed": note_speed, "residuals": [], "updates": 0}
        base = mean(recent)
        residuals = entry["residuals"] = [*entry["residuals"], round(r - base, 3)][-cfg.chart_plays :]
        avg = mean(residuals)
        if len(residuals) < cfg.chart_plays:
            why = f"只有 {len(residuals)} 局"
        elif not (all(e > 0 for e in residuals) or all(e < 0 for e in residuals)):
            why = "有正有负"
        elif abs(avg) < cfg.chart_threshold:
            why = f"平均 {avg:+.2f} 偏得不多"
        else:
            why = None
        detail = f"FAST/SLOW {result.fast}/{result.slow}，相对全局近况（{base:+.2f}）最近 {len(residuals)} 局 " + "/".join(
            f"{e:+.2f}" for e in residuals
        )
        if why is not None:
            logger.debug("%s：%s，%s，不按谱面修正", label, detail, why)
            return None
        old = float(entry["offset_ms"])
        limit = cfg.chart_max_ms
        new = min(max(old - cfg.step_ms * residuals[-1], -limit), limit)
        entry["offset_ms"] = round(new, 2)
        entry["updates"] = entry.get("updates", 0) + 1
        shift = _recenter(state, note_speed, limit)
        logger.info(
            "%s：%s，一直偏 %s → 谱面 offset %+.1f → %+.1fms%s（这局不修正全局%s）",
            label,
            detail,
            "SLOW" if avg > 0 else "FAST",
            old,
            new,
            "（已到上限）" if abs(new) >= limit else "",
            f"；各谱面的平均 {shift:+.2f}ms 移到全局" if abs(shift) >= 0.01 else "",
        )
        return new - old


def _recenter(state: dict, note_speed: float, limit: float) -> float:
    """把这个流速下各谱面 offset 的平均值移到全局学习值里（每张谱面的总 offset 不变），返回移了多少。

    谱面 offset 只该是各谱面相对平均的偏差；不这样做的话，全局一时偏了被谱面各自吸收掉，两者会慢慢一起漂走。
    """
    entries = [e for e in state.get("chart_offsets", {}).values() if e.get("note_speed") == note_speed]
    if not entries:
        return 0.0
    shift = mean(float(e["offset_ms"]) for e in entries)
    if abs(shift) < 0.01:
        return 0.0
    for e in entries:
        e["offset_ms"] = round(min(max(float(e["offset_ms"]) - shift, -limit), limit), 2)
    state["learned_offset_ms"] = round(float(state.get("learned_offset_ms", 0.0)) + shift, 2)
    return shift
