"""演奏记录与 offset 自动修正。

结算画面点击切换按钮后按判定分列 FAST/SLOW（含 PERFECT）。FAST 多说明按早了，应增大 offset
（整体推迟），SLOW 多则减小。学到的 offset 保存在 ``data/state.json``，与配置文件里的
``play.offset_ms`` 相加使用。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import AutoTuneConfig

logger = logging.getLogger(__name__)


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


def suggest_offset_delta(fast: int | None, slow: int | None, cfg: AutoTuneConfig) -> float:
    """根据 FAST/SLOW 计数给出 offset 修正量（毫秒，正值 = 推迟）。"""
    if fast is None or slow is None:
        return 0.0
    n = fast + slow
    if n < max(cfg.min_samples, 1):
        return 0.0
    return -cfg.step_ms * (slow - fast) / n


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

    def autotune(self, result: PlayResult, cfg: AutoTuneConfig) -> float:
        """记录结果并按 FAST/SLOW 更新学到的 offset，返回修正量。"""
        self.append(result)
        if not cfg.enabled:
            return 0.0
        delta = suggest_offset_delta(result.fast, result.slow, cfg)
        if delta:
            new = self.learned_offset_ms + delta
            self.set_learned_offset(new)
            logger.debug("FAST=%s SLOW=%s → offset 修正 %+.1fms（学习值 %.1fms）", result.fast, result.slow, delta, new)
        return delta
