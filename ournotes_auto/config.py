"""配置：全部参数集中在这里，以 YAML 覆盖默认值。"""

from __future__ import annotations

import dataclasses
import logging
import re
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_type_hints

import yaml

from .geometry import GeometryParams

logger = logging.getLogger(__name__)


@dataclass
class DeviceConfig:
    # mumu：MuMu IPC 直连截图与触控（延迟最低，推荐）
    # adb：经 MaaFramework 控制器（MaaTouch/minitouch）操作，适用于其他模拟器或真机
    backend: str = "mumu"
    # 触控方式（backend 为 mumu 时）：minitouch 经 adb 在模拟器里直接写触摸屏设备（推荐，启动失败时自动改用 mumu）；
    # mumu 为 MuMu IPC（偶尔丢失/延后同一时刻的一批触控，约每 2400 个手势一次）
    touch: str = "minitouch"
    mumu_path: str = r"D:\MuMuPlayer"
    instance: int = 0  # MuMu 实例序号（多开器里的编号）
    adb_path: str = ""  # 留空则使用 MuMu 自带 adb 或 MaaFramework 自动发现
    adb_serial: str = ""  # 留空则按 MuMu 实例号推算 127.0.0.1:(16384 + 32 * instance)
    package: str = "com.bilibili.sirius.official"


@dataclass
class TouchParams:
    """谱面 → 触控动作的参数。时间单位毫秒，距离单位为屏高比例。"""

    tap_hold_ms: float = 35.0  # 点击按住时长
    flick_distance: float = 0.10  # 滑动距离
    flick_duration_ms: float = 30.0  # 滑动过程时长
    flick_steps: int = 3  # 滑动过程中的 move 次数
    flick_lead_ms: float = 0.0  # 滑动音符提前按下的时间
    # 横滑时同一时刻划去的那一侧还有别的音符：起点反向挪开滑动距离的这个比例（1 为终点正好落在音符中心，0 为关闭）
    flick_neighbor_shift: float = 1.0
    slide_sample_ms: float = 8.0  # 长条移动的采样间隔
    slide_release_ms: float = 0.0  # 长条终点后继续按住的时间（终点按松手时机判定时应为 0）
    handover_release_ms: float = 20.0  # 长条终点松手时另有手势同时按下：提前这么久松手（0 为关闭）
    head_flick_return_ms: float = 80.0  # 长条起点滑动划出后，用这么久逐渐回到路径上（0 为立即回去）
    trace_lead_ms: float = 25.0  # 追踪音符提前按下的时间
    trace_hold_ms: float = 50.0  # 追踪音符判定时刻之后继续按住的时间
    finger_gap_ms: float = 20.0  # 同一触点抬起到再次按下的最短间隔
    # 上划、追踪等手势的按下点会被之后 130ms 内同一位置的点击认领（判 BAD）：这时提前按下并按住，
    # 按下与前后的点击至少隔开认领范围再加这么久；0 为关闭
    claim_guard_ms: float = 25.0
    max_fingers: int = 10
    # —— 拟人化（默认都关闭）——
    # 时机随机偏移幅度（± 毫秒）：偏移随时间缓慢漂移（每 1.5s 取一个随机值，之间线性过渡），
    # 相近时刻的手势偏移几乎相同，不打乱手势之间的先后安排；另加最多 ±3ms 的独立抖动（同时按下的手势相同）。0 为关闭
    jitter_ms: float = 0.0
    # 故意打成 GREAT 的音符比例（按判定总数算）：挑前后没有别的音符挨着的普通点击，提前或延后 64~69ms。
    # 单局同步误差偏大时其中一部分会变成 PERFECT 或 GOOD（GOOD 不断连击）。
    # 选曲方式为 AP 补完 / 优先没 AP 的歌时不生效。0 为关闭
    great_ratio: float = 0.0
    # 触控点在音符区间内随机横移（两侧各留 1 个单位，不挪进附近点击类音符、同时按住的长条的判定区）并上下挪动，
    # 取值为可移动范围的比例（0~1），0 为关闭
    position_jitter: float = 0.0


@dataclass
class SyncParams:
    """首音符同步参数：跟踪第一个音符的下落轨迹并外推到判定线（运动模型见 player/sync.py）。"""

    track_top: float = 0.01  # 跟踪区上沿 / 屏高
    track_bottom: float = 0.72  # 跟踪区下沿 / 屏高（跟踪越靠下，外推距离越短）
    span_shrink: float = 0.15  # 首音符横向区间两侧各收缩的比例（避开相邻音符与轨道线）
    diff_threshold: float = 30.0  # 行平均差异（0~255）超过该值视为音符覆盖
    min_brightness: int = 150  # 只统计亮度（最大通道）不低于该值的像素：音符很亮，半透明轨道下的背景较暗
    # 同时要求最小通道不低于该值（音符是偏白的淡蓝色，最小通道 ≥150）：排除彩色特效，
    # 如 Mas?uerade Rhapsody Re?uest 里走在音符前面的紫色光锥（最小通道 ≤115）
    min_whiteness: int = 130
    # 开始跟踪前要求画面保持静止的帧数。首音符很早的歌（如 Ave Mujica 2.16s）转场结束约 0.2s 后
    # 首音符就进入跟踪区，要求太多会错过它
    stable_frames: int = 8
    max_entry: float = 0.5  # 首次检测到的前沿超过跟踪区该比例时视为转场而非音符
    # 判定「静止」的帧间差异阈值（与跟踪一样只统计偏白的高亮像素）。轨道出现动画的尾声里轨道线还在闪，
    # 约 6~11；阈值 8 时 Ave Mujica 在首音符出现前约 1s 就绪（只统计亮度、阈值 6 时仅约 0.1s，偶尔错过首音符）
    stable_threshold: float = 8.0
    arm_timeout_s: float = 4.0  # 背景一直在动（如 MV）时，最多等这么久就直接开始跟踪
    # 音符逼近的时间常数：y - y_h ∝ exp(t/τ)，由游戏内流速决定（流速 5.00 实测 0.835s），用 calibrate motion 测量
    tau_s: float = 0.835
    tau_rel_sigma: float = 0.03  # τ 先验的相对不确定度（样本足够时以实测为准；inf 表示不用先验）
    # 拟合的 τ 与 tau_s 之比超出 [1/x, x] 时认为跟踪到的不是音符（介绍卡淡出实测 τ≈0.14s）；
    # 流速改动一般仍在范围内，照常同步并提示重新校准
    tau_ratio_max: float = 2.5
    fit_horizon: bool = False  # 同时拟合运动消失线（仅校准时使用）
    solve_lead_s: float = 0.20  # 预计距到达判定线还剩这么久时给出同步结果
    min_track_s: float = 0.12  # 提前给出结果前至少跟踪这么久（太短则看不出模型误差）
    min_samples: int = 5  # 至少需要的样本帧数
    max_rms_ms: float = 8.0  # 拟合残差上限（60fps 截图实测约 5ms）
    max_sigma_ms: float = 5.0  # 外推误差估计上限，超过视为同步失败
    max_track_s: float = 8.0  # 首次发现音符后最多跟踪的时长（流速很慢时音符在画面上停留更久）
    timeout_s: float = 40.0
    # 推算出的歌曲开始时刻最多比开始同步晚这么久（MuMu 实测加载约 9s）；更晚多半是错过了首音符、
    # 跟踪到了后面的音符，按它演奏会整体错开，视为同步失败。0 表示不检查
    max_start_delay_s: float = 20.0
    # 暂停重试后歌曲立即从头开始（实测推算的开始时刻在开始同步前 0.3s 左右），同一个检查改用这个上限
    retry_start_delay_s: float = 1.5
    record_frames: int = 0  # 调试：保存最近多少帧跟踪区截图到 debug/sync/（演奏结束后写盘）


@dataclass
class PlayConfig:
    # 全局时间偏移：正值表示整体更晚按下。用于补偿截图/输入/渲染延迟
    offset_ms: float = 0.0
    # 演奏中连续这么久（秒）看不到演奏画面（被暂停、闪退）就停止触控；0 为不检查
    guard_lost_s: float = 2.0
    # 演奏中生命值连续这么久（秒）为 0（整体对不上了）就停止触控、暂停重试（次数算在 loop.sync_retries 里）；
    # 0 为不检查。需要 guard_lost_s 不为 0
    guard_life_zero_s: float = 1.0
    touch: TouchParams = field(default_factory=TouchParams)
    sync: SyncParams = field(default_factory=SyncParams)


@dataclass
class AutoTuneConfig:
    """根据结算画面的 FAST/SLOW 数自动修正 offset_ms。

    结算画面可切换为按判定分列的 FAST/SLOW（含 PERFECT），即使 ALL PERFECT 也有数据。
    实测（MuMu，迷星叫 EASY）PERFECT 中心约 ±2.5ms 内不标 FAST/SLOW，而本工具的按键离散度只有
    1~2ms，所以 FAST/SLOW 比例在中心附近几毫秒内就从全 SLOW 跳到全 FAST，不适合用正态模型反推偏差。
    改用 r = (SLOW-FAST)/(SLOW+FAST) ∈ [-1, 1] 做固定步长修正：delta = -step_ms·r，
    偏差落进不标区后 FAST+SLOW 变少，不足 min_samples 时不再修正。
    """

    enabled: bool = True
    # |r|=1 时的单次修正量。每局的整体偏差本身有约 ±3.5ms 的随机波动（模拟器加载时刻），
    # 步长大了学习值会跟着噪声来回跳；1ms 时稳态抖动约 1.5ms，约 5 局收敛
    step_ms: float = 1.0
    min_samples: int = 10  # FAST+SLOW 少于该值不修正


@dataclass
class GameConfig:
    difficulty: str = "expert"  # easy / normal / hard / expert
    # 游戏内「节奏图示速度」。play.sync.tau_s 与它一一对应，改了流速要重新 calibrate motion；
    # 开始演出前会从演出前选项设置弹窗读取并核对
    note_speed: float = 5.0
    # 每局消耗的 LIVE BOOST（0~3，越多奖励越多）。开始第一局前在乐队确认页「消耗LB」弹窗里选好并核对；
    # null 表示不改游戏里的设置。持有数量不足时游戏会消耗剩余的全部 LB；
    # 用完时弹出「恢复LIVE BOOST」（道具/星钻/广告）只点取消，之后 30 分钟内（或到玩家升级）改为 0 继续
    lb_cost: int | None = None
    # 每局开始前 LB 持有少于 lb_cost（需要为 1~3）时，用道具里的 LIVE BOOST饮料补充（从「消耗LB」→「恢复」进去，
    # 只在「道具」页选；绝不用星钻、不看广告）。道具用完或补够 lb_refill_limit 个后按上面的方式处理
    lb_refill: bool = False
    lb_refill_limit: int = 0  # 本次运行最多补充多少 LB，0 为不限（直到道具用完）
    # 挑战演出（loop.challenge）每局消耗的挑战pt：200 / 400 / 800 / 1600（越多奖励越多，200 能打的局数最多）；
    # null 表示不改游戏里的设置。CP 不够一局时结束
    challenge_cost: int | None = 200


@dataclass
class ChartsConfig:
    base_url: str = "https://assets.bdon.moe/chart-site/"
    # 国际服曲名（多语言），仅用于把 OCR 识别到的曲名匹配到 musicId；留空则只用日文曲名
    titles_url: str = "https://haneoka.org/api/v1/servers/intl/songs"
    cache_dir: str = "cache/charts"
    index_ttl_hours: float = 12.0


@dataclass
class LoopConfig:
    """全自动循环（界面导航）参数。"""

    # current：打当前选中曲目；random：每次随机选曲；ap：全曲 AP 补完；
    # ap_first：在 game.difficulty（或 ap_first_difficulties）里优先打没 AP 的歌，没有了再随机（挑战演出时轮流打）；
    # list：按 song_list 依次打；rotate：挑战演出的几首歌轮流打（只用于挑战演出，挑战演出只能用 current / rotate / ap_first）
    song_mode: str = "current"
    # 歌单：曲目 ID 或曲名，逗号或换行分隔，后面可加 @难度（默认 game.difficulty），如 "100010, 碧天伴走@hard"
    song_list: str = ""
    max_plays: int = 0  # 0 表示不限次数
    # 打挑战演出（部分活动期间开放，消耗挑战pt，见 game.challenge_cost）而不是自由演出，打到 CP 不够一局为止
    challenge: bool = False
    until_lb_empty: bool = False  # 打到 LB 用完为止（需要 game.lb_cost 为 1~3；用完后不再改为消耗 0 继续）
    # 挂机：LB 用完后停在乐队确认页，等它恢复到 game.lb_cost 个再接着打，一直运行（需要 game.lb_cost 为 0~3；
    # 0 时不等：持有 LB 时每局消耗 1 个，用完了消耗 0 接着打）
    wait_lb: bool = False
    # 每隔这么多小时回主界面领一次录音室练习（收获），开始时先领一次；0 为不领。
    # 录音室练习最多累计 12 小时，超过后效率降到 30%，挂机等长时间运行时用
    studio_claim_hours: float = 0.0
    # 每天到这个时间（电脑的本地时间，时:分，如 "22:30"）回主界面领一次日常（任务、任务通行证、限定任务、新手任务、
    # T.G.W CARD、礼物盒），开始后第一次到点时领；留空不领。游戏每天 23:00 日期变更，挂机等跨天运行时用
    daily_claim_time: str = ""
    max_failures: int = 5  # 连续失败次数上限
    # 首音符同步失败、演奏中生命值归零时暂停、点「重试」让这首歌从头开始的次数（每局）；用完了就等歌曲放完。0 为不重试
    sync_retries: int = 2
    ap_difficulties: str = "expert,hard,normal,easy"  # AP 补完依次处理的难度（逗号分隔）
    # ap_first 依次补的难度（逗号分隔，如 "expert,hard,normal,easy"），都补完了按 game.difficulty 随机；
    # 留空只补 game.difficulty
    ap_first_difficulties: str = ""
    ap_max_attempts: int = 3  # AP 补完 / ap_first 时同一首歌最多打几次
    ocr_model: str = ""  # 留空使用 resource/model/ocr 下的默认模型


def parse_daily_time(text: str) -> tuple[int, int]:
    """解析 ``loop.daily_claim_time`` 这样的「时:分」（24 小时制，如 22:30）→ (时, 分)，格式不对时抛 ValueError。"""
    m = re.fullmatch(r"\s*(\d{1,2})\s*[:：]\s*(\d{2})\s*", str(text))
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        raise ValueError(f"时间应为 时:分（如 22:30）：{text!r}")
    return int(m[1]), int(m[2])


@dataclass
class Config:
    device: DeviceConfig = field(default_factory=DeviceConfig)
    game: GameConfig = field(default_factory=GameConfig)
    geometry: GeometryParams = field(default_factory=GeometryParams)
    play: PlayConfig = field(default_factory=PlayConfig)
    autotune: AutoTuneConfig = field(default_factory=AutoTuneConfig)
    charts: ChartsConfig = field(default_factory=ChartsConfig)
    loop: LoopConfig = field(default_factory=LoopConfig)


def _build(cls: type, data: dict[str, Any], path: str) -> Any:
    hints = get_type_hints(cls)
    kwargs = {}
    for key, value in data.items():
        f = next((f for f in dataclasses.fields(cls) if f.name == key), None)
        if f is None:
            logger.warning("未知配置项 %s.%s，已忽略", path, key)
            continue
        hint = hints[key]
        if dataclasses.is_dataclass(hint):
            if not isinstance(value, dict):
                raise ValueError(f"配置项 {path}.{key} 应为映射")
            kwargs[key] = _build(hint, value, f"{path}.{key}")
        elif hint is float and isinstance(value, int):
            kwargs[key] = float(value)
        else:
            kwargs[key] = value
    return cls(**kwargs)


_TRUE = ("true", "yes", "on", "1")
_FALSE = ("false", "no", "off", "0")


def _coerce(hint: Any, raw: str, key: str) -> Any:
    """命令行字符串 → 字段类型（str / int / float / bool，及其 ``| None``）。"""
    args = typing.get_args(hint)
    if type(None) in args:
        if raw.strip().lower() in ("", "null", "none"):
            return None
        (hint,) = [a for a in args if a is not type(None)]
    try:
        if hint is bool:
            low = raw.strip().lower()
            if low in _TRUE or low in _FALSE:
                return low in _TRUE
            raise ValueError(raw)
        if hint in (int, float, str):
            return hint(raw.strip() if hint is not str else raw)
    except ValueError:
        raise ValueError(f"配置项 {key} 的值无效：{raw!r}（应为 {hint.__name__}）") from None
    raise ValueError(f"配置项 {key} 不能从命令行修改")


def apply_override(config: Config, key: str, raw: str) -> None:
    """按「节.字段」路径修改一项配置，如 ``apply_override(cfg, "game.difficulty", "hard")``。"""
    obj: Any = config
    parts = key.split(".")
    for i, name in enumerate(parts):
        if not dataclasses.is_dataclass(obj) or name not in {f.name for f in dataclasses.fields(obj)}:
            raise ValueError(f"未知配置项：{key}")
        if i < len(parts) - 1:
            obj = getattr(obj, name)
    hint = get_type_hints(type(obj))[parts[-1]]
    if dataclasses.is_dataclass(hint):
        raise ValueError(f"配置项 {key} 是一个节，要指定到具体字段")
    setattr(obj, parts[-1], _coerce(hint, raw, key))


def load_config(path: str | Path | None) -> Config:
    if path is None or not Path(path).exists():
        if path is not None:
            logger.info("配置文件 %s 不存在，使用默认配置", path)
        return Config()
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return _build(Config, data, "config")


def config_to_dict(config: Config) -> dict[str, Any]:
    return dataclasses.asdict(config)


def save_config(config: Config, path: str | Path) -> None:
    Path(path).write_text(
        yaml.safe_dump(config_to_dict(config), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
