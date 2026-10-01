"""界面选项 / 控制器信息 → 演奏子进程的命令行参数。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..device.mumu_ipc import find_dll
from ..sources import DIFFICULTIES

PARAM_NODE = "OurNotesParam"  # 界面选项都覆盖到这个节点的 attach 上
TASKS = {
    "start": "启动游戏",
    "daily": "领取日常",
    "repeat": "重复刷歌",
    "clear_lb": "清体力",
    "idle": "挂机",
    "ap": "AP补完",
    "records": "记录汇总",
}
LOCAL_TASKS = {"records"}  # 不需要连接模拟器
# 领取日常的各项（与 ``daily --jobs`` 一致），界面上的开关是 attach 里的 ``daily_<项目>``
DAILY_JOBS = ("studio", "story", "missions", "pass", "limited", "beginner", "tgw", "gifts")
LOOP_SONG_MODES = ("current", "random", "ap_first", "list")  # 重复刷歌 / 清体力 / 挂机可选的选曲方式
# 重复刷歌 / 清体力 / 挂机的难度：优先高难度（high_first）在「优先没 AP 的歌」时从高到低依次补，其他选曲方式按 EXPERT
LOOP_DIFFICULTIES = (*DIFFICULTIES, "high_first")
AP_ORDERS = ("hard_first", "easy_first")
TOUCH_MODES = ("minitouch", "mumu")

# MuMu 12 第 n 个实例的 adb 端口
MUMU_BASE_PORT = 16384
MUMU_PORT_STEP = 32


class ParamError(ValueError):
    """界面传来的参数有问题，消息直接显示给用户。"""


def _mumu_config(info: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """控制器 config 里的 ``extras.mumu``（界面识别到 MuMu 时会填写）；config 可能是 JSON 字符串。"""
    config = info.get("config")
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except ValueError:
            return None
    if not isinstance(config, Mapping):
        return None
    mumu = (config.get("extras") or {}).get("mumu")
    return mumu if isinstance(mumu, Mapping) else None


def _mumu_root(adb_path: str) -> str | None:
    """adb 在 MuMu 安装目录里（如 ``D:\\MuMuPlayer\\nx_main\\adb.exe``）时推出安装根目录。"""
    if not adb_path:
        return None
    for parent in Path(adb_path).parents:
        try:
            find_dll(str(parent))
        except FileNotFoundError:
            continue
        return str(parent)
    return None


def _mumu_index(serial: str) -> int | None:
    """按 MuMu 12 的端口规则从 ``127.0.0.1:16416`` 这样的地址推出实例序号。"""
    host, sep, port = serial.rpartition(":")
    if not sep or not port.isdigit() or host not in ("127.0.0.1", "localhost"):
        return None
    offset = int(port) - MUMU_BASE_PORT
    if offset < 0 or offset % MUMU_PORT_STEP:
        return None
    return offset // MUMU_PORT_STEP


def device_from_controller(info: Mapping[str, Any]) -> dict[str, str]:
    """界面连接的控制器（``Controller.info``）→ ``device.*`` 配置。"""
    if str(info.get("type", "")).lower() != "adb":
        raise ParamError("只支持模拟器（ADB 控制器）")
    adb_path = str(info.get("adb_path") or "")
    serial = str(info.get("adb_serial") or "")
    if not serial:
        raise ParamError("没有连接模拟器，请先在界面里选择并连接 MuMu 模拟器")

    # 不看 extras.mumu.enable：那是 MaaFramework 自己是否走 MuMu 接口，本工具总是需要它
    mumu = _mumu_config(info) or {}
    root = str(mumu["path"]) if mumu.get("path") else _mumu_root(adb_path)
    index = mumu.get("index")
    index = _mumu_index(serial) if index is None or index == "" else int(index)
    if root is None or index is None:
        raise ParamError(f"没有识别到 MuMu 模拟器（{serial}）：目前只支持 MuMu 12，截图与触控需要 MuMu 的接口")
    try:
        find_dll(root)
    except FileNotFoundError:
        raise ParamError(f"MuMu 安装目录 {root} 里找不到截图接口 external_renderer_ipc.dll") from None
    return {
        "device.backend": "mumu",
        "device.mumu_path": root,
        "device.instance": str(index),
        "device.adb_path": adb_path,
        "device.adb_serial": serial,
    }


def _get(attach: Mapping[str, Any], key: str) -> Any:
    if key not in attach:
        raise ParamError(f"缺少参数 {key}（{PARAM_NODE}.attach）")
    return attach[key]


def _choice(attach: Mapping[str, Any], key: str, choices: tuple[str, ...]) -> str:
    value = str(_get(attach, key)).strip().lower()
    if value not in choices:
        raise ParamError(f"参数 {key} 应为 {'/'.join(choices)}：{value}")
    return value


def _int(attach: Mapping[str, Any], key: str, lo: int, hi: int | None = None) -> int:
    raw = _get(attach, key)
    try:
        if isinstance(raw, bool):
            raise ValueError
        value = int(str(raw).strip())
    except ValueError:
        raise ParamError(f"参数 {key} 应为整数：{raw!r}") from None
    if value < lo or (hi is not None and value > hi):
        raise ParamError(f"参数 {key} 应在 {lo}~{hi if hi is not None else '∞'}：{value}")
    return value


def _bool(attach: Mapping[str, Any], key: str) -> bool:
    raw = _get(attach, key)
    if isinstance(raw, bool):
        return raw
    low = str(raw).strip().lower()
    if low in ("true", "yes", "1"):
        return True
    if low in ("false", "no", "0"):
        return False
    raise ParamError(f"参数 {key} 应为 true/false：{raw!r}")


def flag(attach: Mapping[str, Any], key: str, default: bool = False) -> bool:
    """可选开关（``debug_log`` ``check_update`` 等）：资源是旧版、attach 里没有这一项时用默认值。"""
    return _bool(attach, key) if key in attach else default


def _lb_cost(attach: Mapping[str, Any]) -> str:
    """``lb_cost``：``keep``（不改游戏里的设置）或 0~3。"""
    if str(_get(attach, "lb_cost")).strip().lower() == "keep":
        return "null"
    return str(_int(attach, "lb_cost", 0, 3))


def _lb_refill(attach: Mapping[str, Any], lb_cost: str) -> dict[str, str]:
    """``lb_refill``（开关）、``lb_refill_count``（最多补充多少 LB，0 为直到道具用完）；资源是旧版没有这两项时不补充。"""
    if not flag(attach, "lb_refill"):
        return {"game.lb_refill": "false"}
    if lb_cost in ("null", "0"):
        raise ParamError("LB 不足时用道具补充，需要把「每局消耗 LB」选为 1~3")
    return {"game.lb_refill": "true", "game.lb_refill_limit": str(_int(attach, "lb_refill_count", 0))}


def _ap_settings(attach: Mapping[str, Any]) -> dict[str, str]:
    chosen = [d for d in DIFFICULTIES if _bool(attach, f"ap_{d}")]  # DIFFICULTIES 从低到高
    if not chosen:
        raise ParamError("AP补完至少要选一个难度")
    if _choice(attach, "ap_order", AP_ORDERS) == "hard_first":
        chosen.reverse()
    lb_cost = _lb_cost(attach)
    return {
        "loop.song_mode": "ap",
        "loop.ap_difficulties": ",".join(chosen),
        "loop.ap_max_attempts": str(_int(attach, "ap_max_attempts", 1, 99)),
        "game.lb_cost": lb_cost,
        **_lb_refill(attach, lb_cost),
        "loop.until_lb_empty": "false",
        "loop.wait_lb": "false",
    }


def _humanize(attach: Mapping[str, Any]) -> dict[str, str]:
    """拟人化：``human_great``（GREAT 比例，百分数）、``human_timing``（时机偏移幅度 ms）、``human_position``（开关）。"""
    return {
        "play.touch.great_ratio": str(_int(attach, "human_great", 0, 20) / 100),
        "play.touch.jitter_ms": str(_int(attach, "human_timing", 0, 20)),
        "play.touch.position_jitter": "1" if _bool(attach, "human_position") else "0",
    }


def run_settings(task: str, attach: Mapping[str, Any]) -> dict[str, str]:
    """任务名 + 界面选项 → ``--set`` 配置项。

    共用 attach 键：``max_plays`` ``touch`` 和拟人化的 ``human_great`` ``human_timing`` ``human_position``。
    重复刷歌 / 清体力 / 挂机另有 ``song_mode`` ``difficulty``（四个难度或 ``high_first``；``song_mode`` 为 list 时还有
    ``song_list``）；重复刷歌另有 ``lb_cost``（``keep`` 或 0~3），
    清体力 / 挂机另有 ``clear_lb_cost``（1~3）；AP补完另有 ``ap_expert`` 等四个难度开关、``ap_order``、
    ``ap_max_attempts`` 和 ``lb_cost``。四个任务都有 ``lb_refill`` ``lb_refill_count``（LB 不足时用道具补充）。
    """
    if task not in ("repeat", "clear_lb", "idle", "ap"):
        raise ParamError(f"未知任务：{task}")
    sets = {
        "device.touch": _choice(attach, "touch", TOUCH_MODES),
        "loop.max_plays": str(_int(attach, "max_plays", 0)),
        **_humanize(attach),
    }
    if task == "ap":
        return {**sets, **_ap_settings(attach)}
    difficulty = _choice(attach, "difficulty", LOOP_DIFFICULTIES)
    sets["loop.song_mode"] = mode = _choice(attach, "song_mode", LOOP_SONG_MODES)
    high_first = difficulty == "high_first"
    sets["game.difficulty"] = DIFFICULTIES[-1] if high_first else difficulty
    if mode == "ap_first":
        sets["loop.ap_first_difficulties"] = ",".join(reversed(DIFFICULTIES)) if high_first else difficulty
    if mode == "list":
        songs = str(_get(attach, "song_list")).strip()
        if not songs:
            raise ParamError("选曲方式为歌单时要填写歌单")
        sets["loop.song_list"] = songs
    if task == "repeat":
        sets["game.lb_cost"] = _lb_cost(attach)
        sets["loop.until_lb_empty"] = "false"
    else:
        sets["game.lb_cost"] = str(_int(attach, "clear_lb_cost", 1, 3))
        sets["loop.until_lb_empty"] = "true"
    sets.update(_lb_refill(attach, sets["game.lb_cost"]))
    sets["loop.wait_lb"] = "true" if task == "idle" else "false"
    return sets


def _daily_jobs(attach: Mapping[str, Any]) -> str:
    jobs = [job for job in DAILY_JOBS if _bool(attach, f"daily_{job}")]
    if not jobs:
        raise ParamError("领取日常至少要选一项")
    return ",".join(jobs)


def global_args(attach: Mapping[str, Any]) -> list[str]:
    """子进程的全局参数：日志按 JSON 行输出、从标准输入接收停止；「调试日志」开着时加 ``-v``。"""
    return ["--json-log", "--stdin-stop", *(["-v"] if flag(attach, "debug_log") else [])]


def worker_args(task: str, attach: Mapping[str, Any], device: Mapping[str, str]) -> list[str]:
    """``python -m ournotes_auto`` 之后的参数（不含日志 / 停止相关的全局参数，见 ``global_args``）。"""
    if task == "records":
        return ["records"]
    if task == "start":
        sets = {**device, "device.touch": _choice(attach, "touch", TOUCH_MODES)}
        tail = ["start"]
    elif task == "daily":
        sets = {**device, "device.touch": _choice(attach, "touch", TOUCH_MODES)}
        tail = ["daily", "--jobs", _daily_jobs(attach)]
    else:
        sets = {**device, **run_settings(task, attach)}
        tail = ["run", *(["--watch-combo"] if _bool(attach, "watch_combo") else [])]
    args: list[str] = []
    for key, value in sets.items():
        args += ["--set", f"{key}={value}"]
    return args + tail
