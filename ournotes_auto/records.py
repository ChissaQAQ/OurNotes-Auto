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
from contextlib import suppress
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from statistics import mean

from . import release
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


RESULT_FIELDS = frozenset(f.name for f in fields(PlayResult))


def parse_result(line: str) -> PlayResult:
    """``records.jsonl`` 的一行。不认识的字段（新版本加的，从新版本导入过来的记录里有）不管，其余照常读。"""
    r = PlayResult(**{k: v for k, v in json.loads(line).items() if k in RESULT_FIELDS})
    if isinstance(r.timestamp, bool) or not isinstance(r.timestamp, (int, float)):
        raise ValueError(f"时间不对：{r.timestamp!r}")
    return r


@dataclass
class ArenaResult:
    """激奏舞台的一局（``arena.jsonl``），读不到的项为 None。演奏了、读到了结算页的，判定数同时照常记在
    ``records.jsonl`` 里（修正 offset、统计用）。"""

    band: str | None = None  # 选的乐队（``nav.screens.ARENA_BANDS`` 之一）
    placement: int | None = None  # 名次（1～5）
    ap_gain: int | None = None  # 段位分增减
    ap_total: int | None = None  # 加减后的段位分
    lb_cost: int | None = None  # 消耗的 LB（-1 为全部消耗）
    title: str = ""  # 难度选择页上读到的曲名
    music_id: int | None = None  # 认出的曲目（认不出时为 None，这局不演奏）
    played: bool = False  # 演奏了（认出了曲目、拿到了谱面，同步没失败）
    score: int | None = None
    perfect: int | None = None
    great: int | None = None
    good: int | None = None
    bad: int | None = None
    miss: int | None = None
    timestamp: float = field(default_factory=time.time)


ARENA_FIELDS = frozenset(f.name for f in fields(ArenaResult))


def parse_arena(line: str) -> ArenaResult:
    """``arena.jsonl`` 的一行，读法同 :func:`parse_result`。"""
    r = ArenaResult(**{k: v for k, v in json.loads(line).items() if k in ARENA_FIELDS})
    if isinstance(r.timestamp, bool) or not isinstance(r.timestamp, (int, float)):
        raise ValueError(f"时间不对：{r.timestamp!r}")
    return r


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


def game_day(now: float | None = None) -> str:
    """游戏里的日期（YYYY-MM-DD）：每天北京时间 3:00（日本时间 4:00）变更（游戏 1.1.0 起，之前是 23:00）。"""
    t = time.time() if now is None else now
    return time.strftime("%Y-%m-%d", time.gmtime(t + 5 * 3600))


class RecordStore:
    def __init__(self, data_dir: str | Path = "data"):
        self.dir = Path(data_dir)
        self.records_path = self.dir / "records.jsonl"
        self.arena_path = self.dir / "arena.jsonl"
        self.state_path = self.dir / "state.json"

    def _load_state(self) -> dict:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                logger.warning("state.json 损坏，已重置")
        return {}

    def _save_state(self, state: dict) -> None:
        release.write_text(self.state_path, json.dumps(state, ensure_ascii=False, indent=2))

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

    def ap_done(self, package: str, difficulty: str) -> dict | None:
        """上次确认这个服的这个难度没有要补的歌时记下的 ``{"time", "catalog"}``（见 :class:`~ournotes_auto.sources.ApDoneCache`）。"""
        return self._load_state().get("ap_done", {}).get(package, {}).get(difficulty)

    def set_ap_done(self, package: str, difficulty: str, entry: dict | None) -> None:
        """记下（``entry`` 为 None 时删掉）这个服的这个难度没有要补的歌。"""
        state = self._load_state()
        done = state.setdefault("ap_done", {})
        if entry is not None:
            done.setdefault(package, {})[difficulty] = entry
        elif done.get(package, {}).pop(difficulty, None) is None:
            return
        self._save_state(state)

    def ap_give_up(self, package: str) -> dict[str, dict]:
        """这个服 AP 时反复失败、暂时不再打的谱面：``{"<musicId>_<难度>": {"time"}}``。"""
        return dict(self._load_state().get("ap_give_up", {}).get(package, {}))

    def set_ap_give_up(self, package: str, key: str, entry: dict | None) -> None:
        """记下（``entry`` 为 None 时删掉）这个服的这张谱面 AP 时反复失败。"""
        state = self._load_state()
        given = state.setdefault("ap_give_up", {})
        if entry is not None:
            given.setdefault(package, {})[key] = entry
        elif given.get(package, {}).pop(key, None) is None:
            return
        self._save_state(state)

    def clear_ap_done(self) -> int:
        """清除所有「没有要补的歌」「反复失败暂时不打」的记录（切换账号后、手动刷新时），返回清掉了几条。"""
        state = self._load_state()
        count = sum(len(v) for v in state.pop("ap_done", {}).values())
        count += sum(len(v) for v in state.pop("ap_give_up", {}).values())
        if count:
            self._save_state(state)
        return count

    def account(self, package: str) -> str:
        """这个服上次用「切换账号」切到的账号（:func:`~ournotes_auto.nav.screens.account_key` 之后），没切过为空。"""
        return self._load_state().get("account", {}).get(package, "")

    def set_account(self, package: str, account: str) -> None:
        state = self._load_state()
        if state.setdefault("account", {}).get(package) != account:
            state["account"][package] = account
            self._save_state(state)

    def lb_all_used(self, package: str) -> str | None:
        """这个服的当前账号（:meth:`account`）上次用完「全部消耗」当天的次数是哪个游戏日（:func:`game_day`），
        没记过为 None。"""
        state = self._load_state()
        used = state.get("lb_all_used", {}).get(package)
        return used.get(state.get("account", {}).get(package, "")) if isinstance(used, dict) else None

    def lb_all_uses(self, package: str, day: str) -> int:
        """这个服的当前账号在游戏日 ``day`` 用了几次「全部消耗」。"""
        state = self._load_state()
        uses = state.get("lb_all_uses", {}).get(package)
        got = uses.get(state.get("account", {}).get(package, "")) if isinstance(uses, dict) else None
        return got[1] if isinstance(got, list) and len(got) == 2 and got[0] == day else 0

    def add_lb_all_use(self, package: str, day: str) -> int:
        """记下这个服的当前账号在游戏日 ``day`` 又用了一次「全部消耗」，返回当天一共用了几次。"""
        n = self.lb_all_uses(package, day) + 1
        state = self._load_state()
        uses = state.setdefault("lb_all_uses", {})
        if not isinstance(uses.get(package), dict):
            uses[package] = {}
        uses[package][state.get("account", {}).get(package, "")] = [day, n]
        self._save_state(state)
        return n

    def set_lb_all_used(self, package: str, day: str | None) -> None:
        """记下（``day`` 为 None 时删掉）这个服的当前账号在游戏日 ``day`` 用完了当天「全部消耗」的次数。"""
        state = self._load_state()
        account = state.get("account", {}).get(package, "")
        used = state.setdefault("lb_all_used", {})
        if not isinstance(used.get(package), dict):
            used[package] = {}  # 开发版里曾经不分账号
        if day is not None:
            used[package][account] = day
        elif used[package].pop(account, None) is None:
            return
        self._save_state(state)

    def append(self, result: PlayResult) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.records_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")

    def history(self, limit: int | None = 20) -> list[PlayResult]:
        """最近 ``limit`` 局（None 为全部），从旧到新。按时间排：导入的旧记录接在文件末尾。"""
        return _load(self.records_path, parse_result, limit)

    def append_arena(self, result: ArenaResult) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.arena_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")

    def arena_history(self, limit: int | None = 20) -> list[ArenaResult]:
        """激奏舞台最近 ``limit`` 局（None 为全部），从旧到新。"""
        return _load(self.arena_path, parse_arena, limit)

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


def _load(path: Path, parse, limit: int | None) -> list:
    """按时间排好的记录（读不了的行不要）的最后 ``limit`` 条（None 为全部）。"""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        with suppress(TypeError, ValueError, AttributeError):
            out.append(parse(line))
    out.sort(key=lambda r: r.timestamp)
    return out if limit is None else out[-limit:] if limit > 0 else []


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
