"""首音符同步：逐帧跟踪第一个音符的前沿位置，拟合后外推到判定线。

音符到消失线的距离随时间指数增长（实机录像验证，整首谱面的音符都吻合）：
``y - y_h = (y_j - y_h)·exp(-(t_arr - t)/τ)``，即 ``t = a + τ·ln(y - y_h)``。
y_h 为运动消失线（见 ``motion_horizon_y``，实测与轨道消失线一致），τ 由游戏内流速决定
（见 ``SyncParams.tau_s``）。音符在画面下半部分仍要走相当一段时间，
所以可以一直跟踪到比较靠近判定线的位置再外推，外推距离短、对 τ 的误差不敏感：

1. 每帧在首音符的横向区间内找「与基线差异明显的最低一行」作为前沿 y，记录 (t, y)；
2. 样本足够后每帧试拟合（a、τ 两个参数的线性加权最小二乘，τ 带 ``tau_s`` 先验），
   预计剩余时间小于 ``solve_lead_s`` 时立即给出结果；
3. 拟合按每个样本的时间不确定度加权：y 量化误差折算到时间为 τ·σ_y/(y - y_h)，越靠下越可信；
4. ``fit_horizon=True`` 时额外搜索 y_h，``calibrate motion`` 用它跟踪完整轨迹来实测 y_h 与 τ。

截图有固定延迟、几何参数的系统误差，这些常量偏差都由 ``offset_ms`` 吸收。前沿比音符中间早到：
两者的距离随透视缩放，在 ln(y - y_h) 上是固定的（半个音符厚度），换算成时间与 τ 成正比，
流速 5.00 时约 33ms、10.00 时约 10ms。``offset_ms`` 是在流速 5.00 下得到的，已经含了这一项，
所以推算歌曲开始时刻（``t0``）时按拟合的 τ 与 ``offset_tau_s`` 之差补上（``center_lag``）。
不补的话流速 10.00 时整体晚约 23ms：拟人化故意打的 GREAT（偏 64~69ms）延后的全成了 GOOD、提前的成了 PERFECT。

游戏按 60fps 渲染，截图拿到的是最近渲染的那一帧，画面比截图时刻早 0~1 帧。截图帧率（约 57~60fps）与渲染帧率接近，
早多少只在一局里缓慢漂移：流速 5.00 时跟踪约 1.8s，平均下来每局只差 ±2ms；流速 10.00 时只跟踪约 0.45s，
平均不掉，每局差到 ±10ms（实机同样设置的两局整体差 17ms），拟人化的 GREAT 就会时而变 GOOD、时而变 PERFECT。
所以给出结果前把截图时刻对齐到渲染网格（``render_fps``）：网格的相位未知，相位只在越过某个截图时刻（取模后）时
才改变对齐结果，逐段试一遍取残差最小的（实机录像残差从 3~5ms 降到 1~2.5ms），拟合仍是渲染时刻下的到达时刻，
再把这一局实测的平均提前量（约半帧）加回作为主机侧时刻，与不对齐时的口径一致，``offset_ms`` 不用重新校准。
截图正好与渲染同步（提前量约 0）时，残差降不下来，就按原来的截图时刻拟合。

开了「小节线显示」（游戏设置 → 详情 → 节奏图示设置）时，小节线和音符一样从顶部落下。它是暗一些的灰白细线
（亮度一百二三十），只有一部分像素过得了偏白高亮的门槛，但足以让行平均差异超过 ``diff_threshold``。跟踪区右边
拼上首音符区间以外的一排轨道采样点（``_BAR_UNITS``），某几行在这些采样点上也大多变亮（不经过偏白高亮的门槛）、
又只有几像素高，就是横贯轨道的小节线，不算音符覆盖（首音符正好压在小节线上时，音符其余的行照常检测）。
整片变亮（转场、闪光）不是细线，照旧当成非音符变化；轨道淡入时轨道上是变暗，也不会当成小节线。

暂停菜单「重试」后歌曲立即从头开始（实测点确认后约 0.2s，演奏画面约 0.36s 后出现，再过 0.16s
100056 的首音符就进入跟踪区），首音符一直在动，等不到画面静止，会错过它、跟上后面的音符。
所以重试时直接沿用上一次静止时取的基线（``baseline``），不再等静止；歌曲开始时刻也几乎就是开始同步的时刻，
用更严格的 ``max_start_delay_s`` 检查有没有跟错音符。
"""

from __future__ import annotations

import logging
import math
import threading
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from ..charts.model import Span
from ..config import SyncParams
from ..device.base import FrameSource
from ..geometry import Geometry

logger = logging.getLogger(__name__)

_Y_SIGMA_PX = 0.35  # 前沿 y 的量化误差（像素，标准差）
_T_SIGMA_S = 0.004  # 截图时刻相对游戏内时间的抖动（秒，标准差）；MuMu 60fps 实测拟合残差约 5ms
# 检查小节线的采样位置（横向单位），去掉首音符区间两侧 _BAR_GAP 个单位以内的；剩下不到 _BAR_MIN_PROBES 个就不检查
_BAR_UNITS = np.arange(0.75, 23.5, 0.5)
_BAR_GAP = 1.5
_BAR_MIN_PROBES = 8
_BAR_MAX_H = 0.012  # 小节线最多这么高（屏高比例，720p 约 9 像素）；更高的整行变化是转场、闪光


class SyncTimeout(TimeoutError):
    pass


@dataclass
class SyncResult:
    arrival: float  # 首音符前沿到达判定线的主机时刻（perf_counter 秒）
    t0: float  # 谱面时间 0 对应的主机时刻（已按流速修正前沿与音符中间的时间差，见模块说明）
    samples: list[tuple[float, float]]  # 参与拟合的 (时刻, 前沿 y 像素)
    horizon_y: float  # 拟合用的运动消失线 y（像素）
    tau_s: float  # 拟合得到的逼近时间常数（秒）
    rms_ms: float  # 加权拟合残差均方根
    sigma_ms: float  # 外推结果的估计标准差
    lead_ms: float  # 给出结果时距到达判定线还有多久
    ok: bool
    frames: int = 0
    notes: list[str] = field(default_factory=list)
    fps: float = 0.0  # 跟踪首音符期间游戏的出帧率（音符一直在动，每帧都不一样）


@dataclass
class _Fit:
    arrival: float
    tau: float
    horizon_y: float
    resid: np.ndarray  # 秒
    weights: np.ndarray
    sigma: float  # 外推值的标准差（秒）

    @property
    def rms(self) -> float:
        wn = self.weights / self.weights.sum()
        return float(math.sqrt(float(wn @ (self.resid**2))))


def _wls(tt, lg, weights, lg_judge, tau_prior, tau_w):
    """``tt = a + τ·lg`` 的加权最小二乘；τ 的先验作为一行伪观测加入（tau_w=0 时无先验）。"""
    A = np.stack([np.ones_like(lg), lg], axis=1)
    AtA = (A * weights[:, None]).T @ A
    Atb = (A * weights[:, None]).T @ tt
    AtA[1, 1] += tau_w
    Atb[1] += tau_w * tau_prior
    cov = np.linalg.pinv(AtA)
    coef = cov @ Atb
    resid = tt - A @ coef
    x = np.array([1.0, lg_judge])
    return coef, resid, float(math.sqrt(max(float(x @ cov @ x), 0.0)))


def _tau_weight(tau_prior: float, tau_rel_sigma: float) -> float:
    if tau_prior > 0 and math.isfinite(tau_rel_sigma) and tau_rel_sigma > 0:
        return 1.0 / (tau_prior * tau_rel_sigma) ** 2
    return 0.0


def render_times(ts: np.ndarray, fps: float) -> np.ndarray:
    """截图时刻 ``ts`` 在各种渲染相位下对应的渲染时刻（每行一种，越早的相位越靠前）。

    渲染时刻落在周期 1/fps、相位未知的网格上，截图拿到的是不晚于截图时刻的最近一帧。相位只在越过某个截图时刻
    （取模后）时改变对齐结果，相邻两个越过点之间各帧的渲染时刻只整体平移，所以每段取中点代表。
    """
    period = 1.0 / fps
    cuts = np.unique(np.mod(ts, period))
    phases = (cuts + np.append(cuts[1:], cuts[0] + period)) / 2
    return phases[:, None] + np.floor((ts[None, :] - phases[:, None]) / period) * period


def fit_arrival(
    ts: np.ndarray,
    ys: np.ndarray,
    judge_y: float,
    horizon_y: float,
    tau_prior: float = 0.0,
    tau_rel_sigma: float = math.inf,
    fit_horizon: bool = False,
) -> _Fit:
    """拟合 ``t = a + τ·ln(y - y_h)`` 并外推到判定线。

    ``tau_prior > 0`` 且 ``tau_rel_sigma`` 有限时给 τ 加高斯先验；``fit_horizon`` 时一维搜索 y_h。
    """
    t_ref = float(ts[0])
    tt = ts - t_ref  # 以首个样本为零点，避免大数相减的精度损失
    tau_w0 = _tau_weight(tau_prior, tau_rel_sigma)
    y_min = float(ys.min())

    def solve(h: float, weights: np.ndarray):
        return _wls(tt, np.log(ys - h), weights, math.log(judge_y - h), tau_prior, tau_w0)

    def reweight(h: float, coef) -> np.ndarray:
        tau = abs(float(coef[1]))
        sigma_t = np.sqrt((tau * _Y_SIGMA_PX / (ys - h)) ** 2 + _T_SIGMA_S**2)
        return 1.0 / sigma_t**2

    h = min(horizon_y, y_min - 1.0)
    weights = np.full_like(tt, 1.0 / _T_SIGMA_S**2)
    for _ in range(2):  # 先等权拟合，再按模型斜率折算的时间不确定度加权
        if fit_horizon and len(ts) >= 5:
            h = _search_horizon(lambda v: float(weights @ (solve(v, weights)[1] ** 2)), y_min, judge_y)
        coef, _, _ = solve(h, weights)
        weights = reweight(h, coef)
    coef, resid, sigma = solve(h, weights)
    arrival = t_ref + float(coef[0] + coef[1] * math.log(judge_y - h))
    return _Fit(arrival, float(coef[1]), h, resid, weights, sigma)


def _search_horizon(sse, y_min: float, judge_y: float) -> float:
    """在 (y_min - 5·y_j, y_min - 0.5) 内搜索使残差最小的 y_h：先对数网格粗搜，再黄金分割细化。"""
    span = max(judge_y, 1.0)
    gaps = np.geomspace(0.5, 5 * span, 160)  # y_min - y_h
    k = int(np.argmin([sse(y_min - g) for g in gaps]))
    lo, hi = gaps[max(k - 1, 0)], gaps[min(k + 1, len(gaps) - 1)]
    gr = (math.sqrt(5) - 1) / 2
    for _ in range(40):
        c1, c2 = hi - gr * (hi - lo), lo + gr * (hi - lo)
        if sse(y_min - c1) < sse(y_min - c2):
            hi = c2
        else:
            lo = c1
    return y_min - (lo + hi) / 2


class NoteTracker:
    """状态机实现：``feed`` 逐帧喂入，便于用合成画面测试。"""

    def __init__(
        self,
        geometry: Geometry,
        params: SyncParams,
        first_ms: float,
        spans: list[Span],
        record_frames: int = 0,
        baseline: np.ndarray | None = None,
        max_start_delay_s: float | None = None,
    ):
        """``baseline``：上一次（同一谱面）静止时取的基线 ``clean_baseline``，给出时不等静止直接开始跟踪；
        ``max_start_delay_s``：代替 ``params`` 里的同名参数。"""
        self.geo = geometry
        self.spans = list(spans)
        self.p = params
        self.first_ms = first_ms
        h, w = geometry.height, geometry.width
        self.y_from = max(int(params.track_top * h), 0)
        self.y_to = min(int(params.track_bottom * h), int(geometry.judge_y) - 2)
        if self.y_to - self.y_from < 20:
            raise ValueError("跟踪区间太小，请检查 track_top/track_bottom")
        ranges = [
            [geometry.row_range(s, y, shrink=params.span_shrink) for s in spans]
            for y in range(self.y_from, self.y_to)
        ]
        self.x_from = max(min(a for row in ranges for a, _ in row), 0)
        self.x_to = min(max(b for row in ranges for _, b in row), w)
        mask = np.zeros((self.y_to - self.y_from, self.x_to - self.x_from), np.float32)
        for i, row in enumerate(ranges):
            for a, b in row:
                mask[i, max(a - self.x_from, 0) : max(b - self.x_from, 0)] = 1.0
        counts = mask.sum(axis=1, keepdims=True)
        # 每行按覆盖像素数归一化，得到「行平均差异」
        self._weights = mask / np.maximum(counts, 1.0)
        units = [u for u in _BAR_UNITS if all(u < s.left - _BAR_GAP or u > s.right + _BAR_GAP for s in spans)]
        if params.bar_line_ratio > 0 and len(units) >= _BAR_MIN_PROBES:
            ys = np.arange(self.y_from, self.y_to)
            self._probe_y = ys[:, None]
            self._probe_x = np.clip(
                np.rint([[geometry.x_at(u, y) for u in units] for y in ys]).astype(np.intp), 0, w - 1
            )
        else:
            self._probe_y = self._probe_x = None
        self._bar_h = max(2, round(_BAR_MAX_H * h))
        width = mask.shape[1] + (0 if self._probe_x is None else self._probe_x.shape[1])
        self.max_start_delay_s = params.max_start_delay_s if max_start_delay_s is None else max_start_delay_s
        if baseline is not None and baseline.shape != (mask.shape[0], width, 3):
            logger.debug("沿用的基线尺寸 %s 与跟踪区 %s 不符，改为等待静止", baseline.shape, mask.shape)
            baseline = None
        self._preset = None if baseline is None else baseline.astype(np.float32)
        self._baseline: np.ndarray | None = None if self._preset is None else self._preset.copy()
        self.clean_baseline: np.ndarray | None = None  # 画面静止时取的基线（重试时沿用）
        self._prev: np.ndarray | None = None
        self._stable = 0
        self._frames = 0
        self.samples: list[tuple[float, float]] = []
        self._first_seen: float | None = None
        self._seen_frames = 0  # 首音符出现以来的帧数（算出帧率）
        self._start_t: float | None = None
        self._begin_t: float | None = None  # 第一帧的时刻，用于检查推算的歌曲开始时刻
        # 调试用：保留最近若干帧的跟踪区截图，同步后可 dump 下来离线重放
        self._record: deque[tuple[float, np.ndarray]] | None = deque(maxlen=record_frames) if record_frames else None

    @property
    def armed(self) -> bool:
        return self._baseline is not None

    def _crop(self, frame: np.ndarray) -> np.ndarray:
        crop = frame[self.y_from : self.y_to, self.x_from : self.x_to]
        if self._probe_x is not None:
            crop = np.concatenate([crop, frame[self._probe_y, self._probe_x]], axis=1)
        return crop.astype(np.int16)

    def _row_scores(self, a: np.ndarray, b: np.ndarray, gate: bool = False) -> np.ndarray:
        diff = np.abs(a - b).max(axis=2).astype(np.float32)
        if gate:
            diff[a.max(axis=2) < self.p.min_brightness] = 0.0
            if self.p.min_whiteness > 0:
                diff[a.min(axis=2) < self.p.min_whiteness] = 0.0
        inner = self._weights.shape[1]
        scores = (diff[:, :inner] * self._weights).sum(axis=1)
        if self._probe_x is not None and gate:
            scores[self._bar_rows(a[:, inner:], b[:, inner:])] = 0.0
        return scores

    def _bar_rows(self, cur: np.ndarray, ref: np.ndarray) -> np.ndarray:
        """小节线所在的行：首音符区间以外的采样点大多变亮，且连成的一段不超过 ``_bar_h`` 行。"""
        brighter = (cur - ref).max(axis=2) >= self.p.diff_threshold
        lit = brighter.mean(axis=1) >= self.p.bar_line_ratio
        if not lit.any():
            return lit
        edges = np.flatnonzero(np.diff(np.concatenate([[0], lit.view(np.int8), [0]])))
        for lo, hi in zip(edges[::2], edges[1::2]):
            if hi - lo > self._bar_h:
                lit[lo:hi] = False
        return lit

    def _fit(self, ts: np.ndarray, ys: np.ndarray) -> _Fit:
        p = self.p
        return fit_arrival(ts, ys, self.geo.judge_y, self.geo.motion_horizon_y, p.tau_s, p.tau_rel_sigma, p.fit_horizon)

    def feed(self, frame: np.ndarray, t: float) -> SyncResult | None:
        return self.feed_crop(self._crop(frame), t)

    def feed_crop(self, crop: np.ndarray, t: float) -> SyncResult | None:
        """``crop`` 为跟踪区 [y_from:y_to, x_from:x_to] 的 int16 BGR 图像。"""
        self._frames += 1
        if self._begin_t is None:
            self._begin_t = t
        if self._record is not None:
            self._record.append((t, crop.astype(np.uint8)))
        if self._baseline is None:
            if self._start_t is None:
                self._start_t = t
            if self._prev is not None:
                # 与跟踪时一样只看偏白的高亮像素：轨道线的闪烁、彩色背景动画不影响检测，也就不必等它们停下
                motion = float(self._row_scores(crop, self._prev, gate=True).max())
                self._stable = self._stable + 1 if motion < self.p.stable_threshold else 0
            self._prev = crop
            if self._stable >= self.p.stable_frames:
                self._baseline = crop.astype(np.float32)
                self.clean_baseline = self._baseline.copy()
                logger.debug("跟踪区已就绪（静止 %d 帧）", self._stable)
            elif t - self._start_t > self.p.arm_timeout_s:
                self._baseline = crop.astype(np.float32)
                logger.warning("画面持续变化，%.1fs 后直接开始跟踪（建议关闭 MV / 调暗背景）", self.p.arm_timeout_s)
            return None

        scores = self._row_scores(crop, self._baseline.astype(np.int16), gate=True)
        hit = np.flatnonzero(scores >= self.p.diff_threshold)
        if hit.size == 0:
            # 音符出现前缓慢跟随背景变化
            self._baseline += (crop - self._baseline) * 0.05
            if self._first_seen is not None and t - self._first_seen > 0.5:
                return self._finish(crop, t, "音符消失")
            return None
        lead = int(hit.max())
        if self._first_seen is None:
            if lead > self.p.max_entry * (self.y_to - self.y_from):
                # 音符总是从顶部进入；一出现就在下方说明是转场/弹窗等整体变化
                return self._rearm(crop, t, f"首次出现在第 {lead} 行")
            self._first_seen = t
        self._seen_frames += 1
        y = self.y_from + lead + 0.5
        if self.samples and y > self.samples[-1][1] and (why := self._jump(t, y)):
            return self._rearm(crop, t, why)
        if lead >= self.y_to - self.y_from - 2:
            return self._finish(crop, t, "音符离开跟踪区")
        if y > self.y_from + 1.5 and (not self.samples or y > self.samples[-1][1]):
            self.samples.append((t, y))
            if len(self.samples) >= self.p.min_samples:
                ts = np.array([s[0] for s in self.samples])
                ys = np.array([s[1] for s in self.samples])
                fit = self._fit(ts, ys)
                if why := self._implausible(fit):
                    return self._rearm(crop, t, why)
                remain = fit.arrival - t
                # 跟踪时长太短时模型误差看不出来，除非快来不及了
                tracked = ts[-1] - ts[0] >= self.p.min_track_s
                if remain <= self.p.solve_lead_s and (tracked or remain <= self.p.solve_lead_s / 2):
                    return self._solve(t, "到达预定提前量")
        if t - self._first_seen > self.p.max_track_s:
            return self._solve(t, "跟踪超时")
        return None

    def _rearm(self, crop: np.ndarray, t: float, why: str) -> None:
        """放弃当前轨迹（转场、弹窗、介绍卡片等非音符变化），重新等待画面静止（沿用基线时换回那个基线）。"""
        logger.debug("跟踪区出现非音符变化（%s），%s", why, "重新等待静止" if self._preset is None else "换回沿用的基线")
        self._baseline = None if self._preset is None else self._preset.copy()
        self._prev = crop
        self._stable = 0
        self._start_t = t
        self._first_seen = None
        self._seen_frames = 0
        self.samples = []
        return None

    def _align_render(self, ts: np.ndarray, ys: np.ndarray, fit: _Fit) -> tuple[np.ndarray, float, _Fit] | None:
        """把截图时刻对齐到渲染网格再拟合（见模块说明），返回各帧的渲染时刻、截图相对渲染晚的平均时间与拟合；
        残差没有明显变小时返回 None。"""
        p = self.p
        cand = render_times(ts, p.render_fps)
        # 先按原拟合的权重与 y_h 比较各种相位的残差（同一个线性最小二乘，一次解完），再对最好的完整拟合一次
        lg = np.log(ys - fit.horizon_y)
        tt = (cand - cand[:, :1]).T
        _, resid, _ = _wls(tt, lg, fit.weights, 0.0, p.tau_s, _tau_weight(p.tau_s, p.tau_rel_sigma))
        wn = fit.weights / fit.weights.sum()
        rend = cand[int(np.argmin(wn @ resid**2))]
        best = self._fit(rend, ys)
        # 各帧的渲染时刻比截图时刻平均早多少（截图平均比渲染晚半帧；画面与截图同一时刻时约 0），
        # 拟合得到的是渲染时刻下的到达时刻，加回来才是主机的截图时刻，与不对齐时口径一致
        return None if best.rms > p.render_rms_ratio * fit.rms else (rend, float(np.mean(ts - rend)), best)

    def _jump(self, t: float, y: float) -> str | None:
        """前沿移动得比音符可能的速度快得多（留了 2 倍余量，流速略有改动也不会误判）。"""
        if self.p.tau_s <= 0:
            return None
        tp, yp = self.samples[-1]
        h = self.geo.motion_horizon_y
        need = self.p.tau_s * math.log((y - h) / (yp - h))
        if need > 2 * (t - tp) + 0.05:
            return f"前沿 {yp:.0f}→{y:.0f} 行只用了 {(t - tp) * 1000:.0f}ms"
        return None

    def _implausible(self, fit: _Fit) -> str | None:
        if fit.rms * 1000 > 3 * self.p.max_rms_ms:
            return f"轨迹不符合音符运动，残差 {fit.rms * 1000:.0f}ms"
        if self.p.tau_s > 0 and not 1 / self.p.tau_ratio_max <= fit.tau / self.p.tau_s <= self.p.tau_ratio_max:
            return f"轨迹不符合音符运动，τ={fit.tau:.3f}s"
        return None

    def _finish(self, crop: np.ndarray, t: float, reason: str) -> SyncResult | None:
        """音符离开跟踪区或消失：轨迹可信则给出结果，否则当作非音符变化重新等待。"""
        if len(self.samples) < self.p.min_samples:
            return self._rearm(crop, t, f"{reason}，只有 {len(self.samples)} 个样本")
        ts = np.array([s[0] for s in self.samples])
        ys = np.array([s[1] for s in self.samples])
        if why := self._implausible(self._fit(ts, ys)):
            return self._rearm(crop, t, f"{reason}，{why}")
        return self._solve(t, reason)

    def _solve(self, t_now: float, reason: str) -> SyncResult:
        notes = [reason]
        pts = self.samples
        if len(pts) < 2:
            raise SyncTimeout(f"首音符样本不足（{len(pts)} 个）：{reason}")
        ts = np.array([p[0] for p in pts])
        ys = np.array([p[1] for p in pts])
        fit = self._fit(ts, ys)
        tf, lag = ts, 0.0  # 拟合用的时刻（对齐到渲染帧后比截图时刻平均早 lag）
        if self.p.render_fps > 0 and len(ts) >= self.p.min_samples and (aligned := self._align_render(ts, ys, fit)):
            tf, lag, new = aligned
            notes.append(
                f"截图时刻对齐到 {self.p.render_fps:g}fps 渲染帧：残差 {fit.rms * 1000:.2f}→{new.rms * 1000:.2f}ms，"
                f"比截图早 {lag * 1000:.1f}ms，到达时刻 {(new.arrival + lag - fit.arrival) * 1000:+.1f}ms"
            )
            fit = new
        # 剔除离群点（如某帧截图时间戳异常）后重新拟合
        for _ in range(3):
            if len(ts) <= self.p.min_samples:
                break
            z = np.abs(fit.resid) * np.sqrt(fit.weights)
            worst = int(np.argmax(z))
            if z[worst] <= 4.0:
                break
            notes.append(f"剔除离群样本 y={ys[worst]:.0f}（{fit.resid[worst] * 1000:+.1f}ms）")
            ts, tf, ys = np.delete(ts, worst), np.delete(tf, worst), np.delete(ys, worst)
            fit = self._fit(tf, ys)
        arrival = fit.arrival + lag
        rms_ms = fit.rms * 1000
        sigma_ms = fit.sigma * 1000
        lead_ms = (arrival - t_now) * 1000
        ok = len(ts) >= self.p.min_samples and rms_ms <= self.p.max_rms_ms and sigma_ms <= self.p.max_sigma_ms
        if not ok:
            notes.append(f"拟合不可信：{len(ts)} 个样本，残差 {rms_ms:.2f}ms，外推误差 ±{sigma_ms:.2f}ms")
        t0 = arrival + self.p.center_lag * (fit.tau - self.p.offset_tau_s) - self.first_ms / 1000
        delay = t0 - self._begin_t
        if self.max_start_delay_s > 0 and delay > self.max_start_delay_s:
            ok = False
            notes.append(f"推算的歌曲开始时刻在开始同步 {delay:.1f}s 后，跟踪到的多半不是第一个音符")
        span = t_now - self._first_seen if self._first_seen is not None else 0.0
        fps = (self._seen_frames - 1) / span if span > 0 else 0.0
        if self.p.tau_s > 0 and abs(fit.tau / self.p.tau_s - 1) > 0.05:
            notes.append(f"τ={fit.tau:.3f}s 与配置的 {self.p.tau_s:.3f}s 相差较大，游戏流速可能已修改，建议重新校准")
        result = SyncResult(
            arrival=arrival,
            t0=t0,
            samples=list(zip(ts.tolist(), ys.tolist())),
            horizon_y=fit.horizon_y,
            tau_s=fit.tau,
            rms_ms=rms_ms,
            sigma_ms=sigma_ms,
            lead_ms=lead_ms,
            ok=ok,
            frames=self._frames,
            notes=notes,
            fps=fps,
        )
        logger.debug(
            "同步%s：%d 个样本，%.0ffps，τ=%.3fs，残差 %.2fms，外推误差 ±%.2fms，距到达 %.0fms，歌曲开始于 %+.1fs（%s）",
            "成功" if ok else "存疑",
            len(ts),
            fps,
            fit.tau,
            rms_ms,
            sigma_ms,
            lead_ms,
            delay,
            "；".join(notes),
        )
        return result

    def dump(self, path: str | Path, result: SyncResult | None = None) -> Path:
        """保存记录的帧与参数（npz），供 ``replay_dump`` 离线分析。"""
        if not self._record:
            raise ValueError("没有记录任何帧（record_frames=0）")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path,
            times=np.array([t for t, _ in self._record]),
            crops=np.stack([c for _, c in self._record]),
            box=np.array([self.y_from, self.y_to, self.x_from, self.x_to]),
            size=np.array([self.geo.width, self.geo.height]),
            first_ms=self.first_ms,
            spans=np.array([[sp.left, sp.right] for sp in self.spans]),
            arrival=np.nan if result is None else result.arrival,
        )
        return path

    def wait(self, source: FrameSource, stop: threading.Event | None = None) -> SyncResult:
        from .clock import now

        deadline = now() + self.p.timeout_s
        while now() < deadline:
            if stop is not None and stop.is_set():
                raise InterruptedError("同步被中止")
            frame, t = source.grab()
            result = self.feed(frame, t)
            if result is not None:
                return result
        raise SyncTimeout(f"{self.p.timeout_s}s 内未检测到首音符（已处理 {self._frames} 帧）")


def replay_dump(path: str | Path, geometry: Geometry, params: SyncParams) -> SyncResult | None:
    """用新的参数重放 ``dump`` 保存的帧，返回同步结果（未得出结果时为 None）。"""
    d = np.load(path)
    w, h = (int(v) for v in d["size"])
    if (w, h) != (geometry.width, geometry.height):
        raise ValueError(f"记录的分辨率 {w}x{h} 与几何参数 {geometry.width}x{geometry.height} 不符")
    spans = [Span(float(a), float(b)) for a, b in d["spans"]]
    y0, y1, x0, x1 = (int(v) for v in d["box"])
    if d["crops"].shape[2] == x1 - x0:
        params = replace(params, bar_line_ratio=0)  # 加上小节线检查之前的记录，没有轨道采样点
    tracker = NoteTracker(geometry, params, float(d["first_ms"]), spans)
    # 不检查小节线（bar_line_ratio=0）时去掉记录里的轨道采样点
    width = (x1 - x0) + (0 if tracker._probe_x is None else tracker._probe_x.shape[1])
    recorded = d["crops"].shape[2]
    if (tracker.y_from, tracker.y_to, tracker.x_from, tracker.x_to) != (y0, y1, x0, x1) or (
        recorded != width and tracker._probe_x is not None
    ):
        raise ValueError("跟踪区与记录时不同，无法重放（track_top/track_bottom/几何参数已修改）")
    for t, crop in zip(d["times"], d["crops"]):
        result = tracker.feed_crop(crop[:, :width].astype(np.int16), float(t))
        if result is not None:
            return result
    return None
