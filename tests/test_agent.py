"""Agent：界面参数、控制器信息 → 演奏子进程参数；子进程日志转发与停止。"""

import io
import json
import logging
import sys
import threading
import time
from pathlib import Path

import pytest

from ournotes_auto.agent.logs import UiFormatter, set_ui_debug, setup_logging
from ournotes_auto.logfile import IMAGE_LOGGER
from ournotes_auto.agent.params import ParamError, agent_argv, device_from_controller, flag, global_args, run_settings, worker_args
from ournotes_auto.agent.worker import WORKER_LOGGER, parse_line, run_worker

ATTACH = {
    "song_mode": "current",
    "difficulty": "expert",
    "max_plays": 0,
    "lb_cost": "keep",
    "clear_lb_cost": 3,
    "lb_refill": False,
    "lb_refill_count": 0,
    "studio_claim": True,
    "studio_claim_hours": 4,
    "daily_claim": True,
    "daily_claim_time": "22:30",
    "touch": "minitouch",
    "watch_combo": False,
    "debug_log": False,
    "check_update": True,
    "auto_update": True,
    "ap_expert": True,
    "ap_hard": True,
    "ap_normal": True,
    "ap_easy": True,
    "ap_order": "hard_first",
    "ap_max_attempts": 3,
    "challenge_song_mode": "rotate",
    "challenge_cost": 200,
    "arena_band": "current",
    "arena_games_per_band": 1,
    "human_great": 0,
    "human_timing": 0,
    "human_position": False,
}
HUMAN_OFF = {
    "play.touch.great_ratio": "0.0",
    "play.touch.jitter_ms": "0",
    "play.touch.position_jitter": "0",
}
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def mumu_root(tmp_path):
    sdk = tmp_path / "MuMuPlayer" / "nx_main" / "sdk"
    sdk.mkdir(parents=True)
    (sdk / "external_renderer_ipc.dll").write_bytes(b"")
    return tmp_path / "MuMuPlayer"


def adb_info(serial="127.0.0.1:16416", adb_path="", config=None):
    return {"type": "adb", "adb_path": adb_path, "adb_serial": serial, "config": config or {}}


# ---------------------------------------------------------------- 控制器


def test_device_from_mumu_extras(mumu_root):
    extras = {"extras": {"mumu": {"enable": True, "path": str(mumu_root), "index": 2}}}
    dev = device_from_controller(adb_info("127.0.0.1:16448", r"C:\adb\adb.exe", extras))
    assert dev == {
        "device.backend": "mumu",
        "device.mumu_path": str(mumu_root),
        "device.instance": "2",
        "device.adb_path": r"C:\adb\adb.exe",
        "device.adb_serial": "127.0.0.1:16448",
    }
    # config 也可能是 JSON 字符串；enable 为 false 也照用（本工具总是需要 MuMu 接口）
    extras["extras"]["mumu"]["enable"] = False
    dev = device_from_controller(adb_info("127.0.0.1:16448", config=json.dumps(extras)))
    assert dev["device.instance"] == "2"


def test_device_inferred_from_adb_path(mumu_root):
    adb = mumu_root / "nx_main" / "adb.exe"
    dev = device_from_controller(adb_info("127.0.0.1:16416", str(adb)))
    assert dev["device.mumu_path"] == str(mumu_root)
    assert dev["device.instance"] == "1"
    assert device_from_controller(adb_info("127.0.0.1:16384", str(adb)))["device.instance"] == "0"


@pytest.mark.parametrize("serial", ["127.0.0.1:5555", "127.0.0.1:16400", "emulator-5554", "10.0.0.2:16416"])
def test_device_unknown_port(mumu_root, serial):
    with pytest.raises(ParamError, match="没有识别到 MuMu"):
        device_from_controller(adb_info(serial, str(mumu_root / "nx_main" / "adb.exe")))


def test_device_errors(tmp_path):
    with pytest.raises(ParamError, match="ADB"):
        device_from_controller({"type": "win32"})
    with pytest.raises(ParamError, match="没有连接"):
        device_from_controller(adb_info(""))
    with pytest.raises(ParamError, match="没有识别到 MuMu"):  # adb 不在 MuMu 目录里
        device_from_controller(adb_info(adb_path=r"C:\platform-tools\adb.exe"))
    extras = {"extras": {"mumu": {"path": str(tmp_path), "index": 0}}}
    with pytest.raises(ParamError, match="找不到截图接口"):
        device_from_controller(adb_info(config=extras))


# ---------------------------------------------------------------- 任务参数


def test_run_settings_repeat():
    assert run_settings("repeat", ATTACH) == {
        "device.touch": "minitouch",
        "game.difficulty": "expert",
        "loop.song_mode": "current",
        "loop.max_plays": "0",
        "loop.challenge": "false",
        "loop.arena": "false",
        "game.lb_cost": "null",
        "game.lb_auto": "false",
        "game.lb_refill": "false",
        "loop.until_lb_empty": "false",
        "loop.wait_lb": "false",
        "loop.use_skip": "false",
        "loop.studio_claim_hours": "0",
        "loop.daily_claim_time": "",
        **HUMAN_OFF,
    }
    sets = run_settings("repeat", {**ATTACH, "lb_cost": 3, "max_plays": "20", "difficulty": "HARD"})
    assert sets["game.lb_cost"] == "3"
    assert sets["loop.max_plays"] == "20"
    assert sets["game.difficulty"] == "hard"
    assert "loop.min_fps" not in sets


def test_run_settings_restart_low_fps():
    assert "loop.min_fps" not in run_settings("idle", {**ATTACH, "restart_low_fps": True})
    for task in ("idle", "ap", "challenge"):
        assert run_settings(task, {**ATTACH, "restart_low_fps": "No"})["loop.min_fps"] == "0"


def test_run_settings_clear_lb():
    sets = run_settings("clear_lb", {**ATTACH, "clear_lb_cost": 2, "song_mode": "random"})
    assert sets["game.lb_cost"] == "2"
    assert sets["loop.until_lb_empty"] == "true"
    assert sets["loop.song_mode"] == "random"
    sets = run_settings("clear_lb", {**ATTACH, "song_mode": "ap_first", "difficulty": "hard"})
    assert sets["loop.song_mode"] == "ap_first" and sets["game.difficulty"] == "hard"
    assert sets["loop.ap_first_difficulties"] == "hard"
    assert "loop.song_list" not in sets
    sets = run_settings("clear_lb", {**ATTACH, "song_mode": "list", "song_list": " 100010, 碧天伴走@hard "})
    assert sets["loop.song_mode"] == "list" and sets["loop.song_list"] == "100010, 碧天伴走@hard"
    assert sets["loop.wait_lb"] == "false"


def test_run_settings_idle():
    """挂机和清体力一样打到 LB 用完，只是之后等它恢复，并定时领取录音室练习、每天领取日常。"""
    attach = {**ATTACH, "clear_lb_cost": 2, "song_mode": "ap_first", "difficulty": "high_first"}
    sets = run_settings("idle", attach)
    assert sets == {
        **run_settings("clear_lb", attach),
        "loop.wait_lb": "true",
        "loop.studio_claim_hours": "4",
        "loop.daily_claim_time": "22:30",
    }
    assert sets["game.lb_cost"] == "2" and sets["loop.until_lb_empty"] == "true"
    # 挂机可以每局消耗 0：用不完 LB，一直打（清体力不行）
    sets = run_settings("idle", {**ATTACH, "clear_lb_cost": 0})
    assert sets["game.lb_cost"] == "0" and sets["loop.until_lb_empty"] == "false" and sets["loop.wait_lb"] == "true"
    # 消耗 0 用不着补充：照传，run 命令里不补充（#100），不报错
    assert run_settings("idle", {**ATTACH, "clear_lb_cost": 0, "lb_refill": True})["game.lb_refill"] == "true"
    with pytest.raises(ParamError, match="clear_lb_cost"):
        run_settings("idle", {**ATTACH, "clear_lb_cost": 6})


def test_run_settings_lb_auto_and_skip():
    """每局消耗 LB 选「自动」：按持有数选；4、5 也能选。「使用跳过」只有清体力、挂机有。"""
    for task in ("repeat", "clear_lb", "idle", "ap"):
        key = "lb_cost" if task in ("repeat", "ap") else "clear_lb_cost"
        sets = run_settings(task, {**ATTACH, key: "auto", "lb_refill": True})
        assert sets["game.lb_auto"] == "true" and sets["game.lb_cost"] == "null", task
        assert sets["game.lb_refill"] == "true", task
        assert run_settings(task, {**ATTACH, key: 5})["game.lb_cost"] == "5", task
    assert run_settings("clear_lb", {**ATTACH, "clear_lb_cost": "auto"})["loop.until_lb_empty"] == "true"
    sets = run_settings("idle", {**ATTACH, "clear_lb_cost": "auto"})
    assert sets["loop.until_lb_empty"] == "true" and sets["loop.wait_lb"] == "true"
    for task in ("clear_lb", "idle"):
        assert run_settings(task, {**ATTACH, "use_skip": "Yes"})["loop.use_skip"] == "true", task
        assert run_settings(task, ATTACH)["loop.use_skip"] == "false", task
    for task in ("repeat", "ap", "challenge"):
        assert run_settings(task, {**ATTACH, "use_skip": True})["loop.use_skip"] == "false", task


def test_run_settings_daily_claim():
    """「每天领取日常」只有挂机有；关掉或旧资源没有这两项时不领，时间统一成 HH:MM。"""
    assert run_settings("idle", {**ATTACH, "daily_claim_time": "7:05"})["loop.daily_claim_time"] == "07:05"
    assert run_settings("idle", {**ATTACH, "daily_claim_time": " 23：59 "})["loop.daily_claim_time"] == "23:59"
    assert run_settings("idle", {**ATTACH, "daily_claim": "No"})["loop.daily_claim_time"] == ""
    old = {k: v for k, v in ATTACH.items() if not k.startswith("daily_claim")}
    assert run_settings("idle", old)["loop.daily_claim_time"] == ""
    for task in ("repeat", "clear_lb", "ap", "challenge"):
        assert run_settings(task, ATTACH)["loop.daily_claim_time"] == "", task
    for text in ("24:00", "22:60", "2230", "", "{time}"):
        with pytest.raises(ParamError, match="daily_claim_time"):
            run_settings("idle", {**ATTACH, "daily_claim_time": text})


def test_run_settings_studio_claim():
    """「定时收获」只有挂机有；关掉或旧资源没有这两项时不领，间隔 1~12 小时。"""
    assert run_settings("idle", {**ATTACH, "studio_claim_hours": "11"})["loop.studio_claim_hours"] == "11"
    assert run_settings("idle", {**ATTACH, "studio_claim": "No"})["loop.studio_claim_hours"] == "0"
    old = {k: v for k, v in ATTACH.items() if not k.startswith("studio_claim")}
    assert run_settings("idle", old)["loop.studio_claim_hours"] == "0"
    for task in ("repeat", "clear_lb", "ap"):
        assert run_settings(task, ATTACH)["loop.studio_claim_hours"] == "0", task
    for hours in (0, 13, "{hours}"):
        with pytest.raises(ParamError, match="studio_claim_hours"):
            run_settings("idle", {**ATTACH, "studio_claim_hours": hours})


def test_run_settings_high_first():
    sets = run_settings("clear_lb", {**ATTACH, "song_mode": "ap_first", "difficulty": "high_first"})
    assert sets["game.difficulty"] == "expert"  # 都补完了按 EXPERT 随机
    assert sets["loop.ap_first_difficulties"] == "expert,hard,normal,easy"
    sets = run_settings("repeat", {**ATTACH, "song_mode": "random", "difficulty": "high_first"})
    assert sets["game.difficulty"] == "expert" and "loop.ap_first_difficulties" not in sets


def test_run_settings_ap():
    assert run_settings("ap", ATTACH) == {
        "device.touch": "minitouch",
        "loop.max_plays": "0",
        "loop.challenge": "false",
        "loop.arena": "false",
        "loop.song_mode": "ap",
        "loop.ap_difficulties": "expert,hard,normal,easy",
        "loop.ap_max_attempts": "3",
        "game.lb_cost": "null",
        "game.lb_auto": "false",
        "game.lb_refill": "false",
        "loop.until_lb_empty": "false",
        "loop.wait_lb": "false",
        "loop.use_skip": "false",
        "loop.studio_claim_hours": "0",
        "loop.daily_claim_time": "",
        **HUMAN_OFF,
    }
    sets = run_settings(
        "ap", {**ATTACH, "ap_hard": "No", "ap_normal": False, "ap_order": "easy_first", "lb_cost": 0}
    )
    assert sets["loop.ap_difficulties"] == "easy,expert"
    assert sets["game.lb_cost"] == "0"


def test_run_settings_challenge():
    """挑战演出：轮流打 / 打当前的歌，默认每局消耗 200 CP；不用 LB 相关的选项。"""
    assert run_settings("challenge", ATTACH) == {
        "device.touch": "minitouch",
        "loop.max_plays": "0",
        "loop.challenge": "true",
        "loop.arena": "false",
        "loop.song_mode": "rotate",
        "game.difficulty": "expert",
        "game.challenge_cost": "200",
        "game.lb_refill": "false",
        "loop.until_lb_empty": "false",
        "loop.wait_lb": "false",
        "loop.use_skip": "false",
        "loop.studio_claim_hours": "0",
        "loop.daily_claim_time": "",
        **HUMAN_OFF,
    }
    attach = {**ATTACH, "challenge_song_mode": "current", "challenge_cost": "1600", "difficulty": "high_first"}
    sets = run_settings("challenge", {**attach, "lb_refill": True, "lb_cost": 0})
    assert sets["loop.song_mode"] == "current" and sets["game.challenge_cost"] == "1600"
    assert sets["game.difficulty"] == "expert" and sets["game.lb_refill"] == "false"
    assert run_settings("challenge", {**ATTACH, "challenge_cost": "keep"})["game.challenge_cost"] == "null"
    assert "loop.ap_first_difficulties" not in sets
    # 优先打没 AP 的歌：难度选「优先高难度」时从 EXPERT 往下补
    sets = run_settings("challenge", {**attach, "challenge_song_mode": "ap_first"})
    assert sets["loop.song_mode"] == "ap_first" and sets["loop.ap_first_difficulties"] == "expert,hard,normal,easy"
    sets = run_settings("challenge", {**ATTACH, "challenge_song_mode": "ap_first", "difficulty": "hard"})
    assert sets["loop.ap_first_difficulties"] == "hard" and sets["game.difficulty"] == "hard"
    for task in ("repeat", "clear_lb", "idle", "ap", "arena", "arena_idle"):
        assert run_settings(task, ATTACH)["loop.challenge"] == "false", task


def test_run_settings_arena():
    """激奏舞台：选乐队（轮流时另有每个乐队的局数）；LB 同重复刷歌，挂机（激奏舞台）同挂机、定时领取。"""
    assert run_settings("arena", ATTACH) == {
        "device.touch": "minitouch",
        "loop.max_plays": "0",
        "loop.challenge": "false",
        "loop.arena": "true",
        "loop.arena_band": "current",
        "game.lb_cost": "null",
        "game.lb_auto": "false",
        "game.lb_refill": "false",
        "loop.until_lb_empty": "false",
        "loop.wait_lb": "false",
        "loop.studio_claim_hours": "0",
        "loop.daily_claim_time": "",
        **HUMAN_OFF,
    }
    sets = run_settings("arena", {**ATTACH, "arena_band": " MyGO ", "lb_cost": 3, "max_plays": "5"})
    assert sets["loop.arena_band"] == "mygo" and sets["game.lb_cost"] == "3" and sets["loop.max_plays"] == "5"
    assert "loop.arena_games_per_band" not in sets
    sets = run_settings("arena", {**ATTACH, "arena_band": "rotate", "arena_games_per_band": "3"})
    assert sets["loop.arena_band"] == "rotate" and sets["loop.arena_games_per_band"] == "3"
    # 挂机（激奏舞台）：打到 LB 用完再等它恢复，消耗 0 时不等；定时收获、领日常同挂机
    sets = run_settings("arena_idle", {**ATTACH, "clear_lb_cost": 2})
    assert sets["loop.arena"] == "true" and sets["game.lb_cost"] == "2"
    assert sets["loop.until_lb_empty"] == "true" and sets["loop.wait_lb"] == "true"
    assert sets["loop.studio_claim_hours"] == "4" and sets["loop.daily_claim_time"] == "22:30"
    sets = run_settings("arena_idle", {**ATTACH, "clear_lb_cost": 0})
    assert sets["game.lb_cost"] == "0" and sets["loop.until_lb_empty"] == "false" and sets["loop.wait_lb"] == "true"
    sets = run_settings("arena_idle", {**ATTACH, "clear_lb_cost": "auto"})
    assert sets["game.lb_auto"] == "true" and sets["loop.until_lb_empty"] == "true"
    # 没有选曲、难度：这些选项不传（只打 EXPERT、不跳过由 run 命令统一设）
    for task in ("arena", "arena_idle"):
        sets = run_settings(task, {**ATTACH, "song_mode": "random", "difficulty": "hard", "use_skip": True})
        assert not {"loop.song_mode", "game.difficulty", "loop.use_skip"} & set(sets), task
    for task in ("repeat", "clear_lb", "idle", "ap", "challenge"):
        assert run_settings(task, {**ATTACH, "arena_band": "mygo"})["loop.arena"] == "false", task


def test_run_settings_lb_refill():
    """用 LB 的演奏任务都能开「LB 不足时用道具补充」，旧资源没有这两项时不补充；每局消耗 0、不改游戏里的设置时
    也不报错（#100，run 命令里不补充）。"""
    on = {**ATTACH, "lb_refill": "Yes", "lb_refill_count": "5", "lb_cost": 2}
    for task in ("repeat", "clear_lb", "idle", "ap", "arena", "arena_idle"):
        sets = run_settings(task, on)
        assert sets["game.lb_refill"] == "true" and sets["game.lb_refill_limit"] == "5", task
    assert run_settings("clear_lb", {**on, "lb_refill_count": 0})["game.lb_refill_limit"] == "0"
    old = {k: v for k, v in ATTACH.items() if not k.startswith("lb_refill")}
    assert run_settings("repeat", old)["game.lb_refill"] == "false"
    for task in ("repeat", "ap", "arena"):
        for cost in ("keep", 0):
            assert run_settings(task, {**on, "lb_cost": cost})["game.lb_refill"] == "true", (task, cost)
    for task in ("idle", "arena_idle"):
        assert run_settings(task, {**on, "clear_lb_cost": 0})["game.lb_refill"] == "true", task
    with pytest.raises(ParamError, match="lb_refill_count"):
        run_settings("clear_lb", {**on, "lb_refill_count": -1})
    with pytest.raises(ParamError, match="lb_refill"):
        run_settings("clear_lb", {**on, "lb_refill": "maybe"})


def test_run_settings_humanize():
    sets = run_settings("clear_lb", {**ATTACH, "human_great": "3", "human_timing": 16, "human_position": "Yes"})
    assert sets["play.touch.great_ratio"] == "0.03"
    assert sets["play.touch.jitter_ms"] == "16"
    assert sets["play.touch.position_jitter"] == "1"


@pytest.mark.parametrize(
    "task, change, msg",
    [
        ("sweep", {}, "未知任务"),
        ("repeat", {"difficulty": "master"}, "difficulty"),
        ("repeat", {"song_mode": "list"}, "song_list"),
        ("repeat", {"song_mode": "list", "song_list": "  "}, "歌单"),
        ("repeat", {"song_mode": "playlist"}, "song_mode"),
        ("repeat", {"song_mode": "ap"}, "song_mode"),
        ("ap", {"ap_expert": False, "ap_hard": False, "ap_normal": False, "ap_easy": False}, "至少要选一个难度"),
        ("ap", {"ap_order": "random"}, "ap_order"),
        ("ap", {"ap_max_attempts": 0}, "ap_max_attempts"),
        ("ap", {"ap_easy": "maybe"}, "ap_easy"),
        ("repeat", {"touch": "adb"}, "touch"),
        ("repeat", {"max_plays": -1}, "max_plays"),
        ("repeat", {"max_plays": "{count}"}, "max_plays"),
        ("repeat", {"max_plays": True}, "max_plays"),
        ("repeat", {"lb_cost": 6}, "lb_cost"),
        ("clear_lb", {"clear_lb_cost": "keep"}, "clear_lb_cost"),
        ("clear_lb", {"clear_lb_cost": 0}, "clear_lb_cost"),
        ("idle", {"clear_lb_cost": -1}, "clear_lb_cost"),
        ("repeat", {"human_great": 30}, "human_great"),
        ("repeat", {"human_timing": 25}, "human_timing"),
        ("ap", {"human_position": "maybe"}, "human_position"),
        ("challenge", {"challenge_cost": 300}, "challenge_cost"),
        ("challenge", {"challenge_cost": "{cost}"}, "challenge_cost"),
        ("challenge", {"challenge_song_mode": "random"}, "challenge_song_mode"),
        ("challenge", {"difficulty": "master"}, "difficulty"),
        ("arena", {"arena_band": "poppin"}, "arena_band"),
        ("arena", {"arena_band": "rotate", "arena_games_per_band": 0}, "arena_games_per_band"),
        ("arena", {"arena_band": "rotate", "arena_games_per_band": "{count}"}, "arena_games_per_band"),
        ("arena", {"lb_cost": 6}, "lb_cost"),
        ("arena_idle", {"clear_lb_cost": -1}, "clear_lb_cost"),
    ],
)
def test_run_settings_errors(task, change, msg):
    with pytest.raises(ParamError, match=msg):
        run_settings(task, {**ATTACH, **change})


def test_run_settings_missing_key():
    attach = dict(ATTACH)
    del attach["difficulty"]
    with pytest.raises(ParamError, match="缺少参数 difficulty"):
        run_settings("repeat", attach)


def test_worker_args():
    device = {"device.backend": "mumu", "device.adb_serial": "127.0.0.1:16416"}
    args = worker_args("repeat", ATTACH, device)
    assert args[:4] == ["--set", "device.backend=mumu", "--set", "device.adb_serial=127.0.0.1:16416"]
    assert "--set" in args and "game.lb_cost=null" in args
    assert args[-1] == "run"
    args = worker_args("repeat", {**ATTACH, "watch_combo": "true"}, device)
    assert args[-2:] == ["run", "--watch-combo"]


def test_worker_args_start():
    """启动游戏只需要设备和触控方式，其余选项缺了也不报错。"""
    device = {"device.backend": "mumu", "device.adb_serial": "127.0.0.1:16416"}
    args = worker_args("start", {"touch": "mumu"}, device)
    assert args == [
        "--set", "device.backend=mumu", "--set", "device.adb_serial=127.0.0.1:16416",
        "--set", "device.touch=mumu", "start",
    ]
    with pytest.raises(ParamError, match="未知任务"):
        worker_args("nope", ATTACH, device)
    assert worker_args("records", {}, {}) == ["records"]  # 不需要设备和选项
    with pytest.raises(ParamError, match="未知任务"):
        worker_args("backup", {}, {})  # 导出、导入在统计窗口里做，不是任务


def test_worker_args_game_language():
    """游戏语言选한국어时用 ko_kr 识别模型（每个任务都要）；默认和旧版资源没有这一项时不改模型。"""
    args = worker_args("start", {"touch": "mumu", "game_language": "ko"}, {})
    assert args == ["--set", "device.touch=mumu", "--set", "loop.ocr_model=ko_kr", "start"]
    assert "loop.ocr_model=ko_kr" in worker_args("repeat", {**ATTACH, "game_language": "ko"}, {})
    assert not any("ocr_model" in a for a in worker_args("repeat", {**ATTACH, "game_language": "default"}, {}))
    with pytest.raises(ParamError, match="game_language"):
        worker_args("start", {"touch": "mumu", "game_language": "zh"}, {})


def test_worker_args_jp():
    """日本語（日服）：换成日服的包名（覆盖控制器给的），识别模型不变；日服不能切换账号。"""
    args = worker_args("start", {"touch": "mumu", "game_language": "ja"}, {"device.package": "x"})
    assert args == ["--set", "device.package=com.bushiroad.sirius", "--set", "device.touch=mumu", "start"]
    args = worker_args("repeat", {**ATTACH, "game_language": "ja"}, {})
    assert "device.package=com.bushiroad.sirius" in args
    assert not any("ocr_model" in a for a in args)
    assert not any("package" in a for a in worker_args("repeat", {**ATTACH, "game_language": "ko"}, {}))
    with pytest.raises(ParamError, match="日服"):
        worker_args("switch_account", {"touch": "mumu", "account": "user_12", "game_language": "ja"}, {})


def test_worker_args_switch_account():
    """切换账号：账号名去掉首尾空白传给 switch-account，没填时报错。"""
    from ournotes_auto.cli import build_parser

    args = worker_args("switch_account", {"touch": "mumu", "account": " user_12 "}, {"device.instance": "1"})
    assert args == ["--set", "device.instance=1", "--set", "device.touch=mumu", "switch-account", "user_12"]
    assert build_parser().parse_args(args).account == "user_12"
    with pytest.raises(ParamError, match="账号名"):
        worker_args("switch_account", {"touch": "mumu", "account": "  "}, {})


def test_worker_args_daily():
    from ournotes_auto.agent.params import DAILY_JOBS
    from ournotes_auto.cli import build_parser
    from ournotes_auto.nav import daily

    assert DAILY_JOBS == tuple(daily.DAILY_JOBS)
    attach = {"touch": "mumu", **{f"daily_{j}": True for j in DAILY_JOBS}, "daily_limited": "No", "daily_event": "No"}
    args = worker_args("daily", attach, {"device.instance": "1"})
    assert args == [
        "--set", "device.instance=1", "--set", "device.touch=mumu",
        "daily", "--jobs", "studio,story,missions,pass,beginner,tgw,recruit,gifts",
    ]
    jobs = ["studio", "story", "missions", "pass", "beginner", "tgw", "recruit", "gifts"]
    assert build_parser().parse_args(args).jobs == jobs
    # 旧资源的 attach 里没有后加的项目：不做
    old = {k: v for k, v in attach.items() if k not in ("daily_event", "daily_recruit")}
    assert worker_args("daily", old, {})[-1] == "studio,story,missions,pass,beginner,tgw,gifts"
    with pytest.raises(ParamError, match="至少要选一项"):
        worker_args("daily", {"touch": "mumu", **{f"daily_{j}": False for j in DAILY_JOBS}}, {})
    with pytest.raises(SystemExit):
        build_parser().parse_args(["daily", "--jobs", "studio,shop"])


def test_worker_args_notify():
    """「推送到手机」：每个任务都带上推送设置；选不推送时清掉 config.yaml 里的；旧资源没有这几项时不改。"""
    from ournotes_auto.cli import build_parser
    from ournotes_auto.config import Config, apply_override

    attach = {"touch": "mumu", "notify_channel": "ServerChan", "notify_target": " SCT1 ", "notify_events": "problems"}
    args = worker_args("start", attach, {})
    assert args == [
        "--set", "device.touch=mumu", "--set", "notify.channel=serverchan", "--set", "notify.target=SCT1",
        "--set", "notify.events=error,maintenance", "start",
    ]
    assert "notify.events=done,error,maintenance" in worker_args("repeat", {**ATTACH, **attach, "notify_events": "all"}, {})
    assert worker_args("start", {**attach, "notify_channel": "off"}, {})[-3:] == ["--set", "notify.channel=", "start"]
    assert worker_args("notify_test", attach, {})[-1] == "notify-test"
    assert worker_args("notify_test", {}, {}) == ["notify-test"]
    cfg = Config()
    for item in build_parser().parse_args(worker_args("notify_test", attach, {})).set:
        apply_override(cfg, *item.split("=", 1))
    assert (cfg.notify.channel, cfg.notify.target, cfg.notify.events) == ("serverchan", "SCT1", "error,maintenance")
    assert "notify.channel=bark" in worker_args("start", {**attach, "notify_channel": "Bark"}, {})
    with pytest.raises(ParamError, match="notify_channel"):
        worker_args("start", {**attach, "notify_channel": "telegram"}, {})


def test_global_args():
    """「调试日志」开着时子进程加 -v；旧资源的 attach 里没有这一项时按关闭。"""
    from ournotes_auto.cli import build_parser

    assert global_args(ATTACH) == global_args({}) == ["--json-log", "--stdin-stop"]
    args = build_parser().parse_args([*global_args({**ATTACH, "debug_log": "Yes"}), *worker_args("records", {}, {})])
    assert (args.verbose, args.json_log, args.stdin_stop, args.command) == (1, True, True, "records")
    with pytest.raises(ParamError, match="debug_log"):
        global_args({"debug_log": "maybe"})
    assert flag({}, "check_update", default=True) and not flag({"check_update": "No"}, "check_update", default=True)


def test_agent_argv(mumu_root):
    """Agent 交来的 {task, attach, controller} → 全局参数 + 任务参数，控制器换成 device.* 配置。"""
    from ournotes_auto.cli import build_parser

    controller = adb_info("127.0.0.1:16416", str(mumu_root / "nx_main" / "adb.exe"))
    device = device_from_controller(controller)
    for task, attach in (("idle", ATTACH), ("start", {"touch": "mumu", "debug_log": True})):
        payload = {"task": task, "attach": attach, "controller": controller}
        assert agent_argv(payload) == (task, [*global_args(attach), *worker_args(task, attach, device)])
    task, argv = agent_argv({"task": "start", "attach": {"touch": "mumu"}, "controller": controller})
    args = build_parser().parse_args(argv)
    assert (args.command, args.json_log, args.stdin_stop) == ("start", True, True)
    assert "device.instance=1" in args.set
    # 本地任务不需要控制器：没连模拟器、连的不是 ADB 控制器都照样执行
    assert agent_argv({"task": "records"}) == ("records", ["--json-log", "--stdin-stop", "records"])
    assert agent_argv({"task": "records", "attach": {"debug_log": "Yes"}, "controller": {"type": "win32"}}) == (
        "records", ["--json-log", "--stdin-stop", "-v", "records"],
    )
    with pytest.raises(ParamError, match="difficulty"):  # 界面选项照样校验
        agent_argv({"task": "repeat", "attach": {**ATTACH, "difficulty": "master"}, "controller": controller})


@pytest.mark.parametrize(
    "payload, msg",
    [
        (None, "格式不对"),
        ([{"task": "records"}], "格式不对"),
        ("records", "格式不对"),
        ({}, "未知任务"),
        ({"task": "sweep"}, "未知任务"),
        ({"task": "records", "attach": ["debug_log"]}, "格式不对"),
        ({"task": "records", "attach": "{}"}, "格式不对"),
        ({"task": "records", "controller": "adb"}, "格式不对"),
        ({"task": "records", "controller": [1]}, "格式不对"),
        ({"task": "start", "attach": {"touch": "mumu"}}, "ADB"),  # 要连模拟器的任务没有控制器
        ({"task": "repeat", "attach": ATTACH, "controller": adb_info()}, "没有识别到 MuMu"),
    ],
)
def test_agent_argv_errors(payload, msg):
    with pytest.raises(ParamError, match=msg):
        agent_argv(payload)


def test_worker_args_parse_back():
    """生成的 --set 能被命令行原样解析、应用。"""
    from ournotes_auto.cli import build_parser
    from ournotes_auto.commands import check_run_config
    from ournotes_auto.config import Config, apply_override
    from ournotes_auto.context import SetupError

    device = {"device.backend": "mumu", "device.mumu_path": r"D:\MuMu Player", "device.instance": "1"}
    args = build_parser().parse_args(worker_args("clear_lb", ATTACH, device))
    assert args.command == "run" and not args.watch_combo
    cfg = Config()
    for item in args.set:
        key, _, value = item.partition("=")
        apply_override(cfg, key, value)
    check_run_config(cfg)
    assert cfg.device.mumu_path == r"D:\MuMu Player"
    assert cfg.device.instance == 1
    assert cfg.game.lb_cost == 3 and cfg.loop.until_lb_empty and not cfg.loop.wait_lb

    args = build_parser().parse_args(worker_args("idle", ATTACH, device))
    cfg = Config()
    for item in args.set:
        key, _, value = item.partition("=")
        apply_override(cfg, key, value)
    check_run_config(cfg)
    assert cfg.game.lb_cost == 3 and cfg.loop.until_lb_empty and cfg.loop.wait_lb
    assert cfg.loop.studio_claim_hours == 4.0 and cfg.loop.daily_claim_time == "22:30"
    assert build_parser().parse_args(["run", "--claim-studio", "2.5"]).claim_studio == 2.5
    assert build_parser().parse_args(["run", "--claim-daily", "6:00"]).claim_daily == "6:00"

    # 挂机每局消耗 0：不打到 LB 用完，一直打；不领日常时传空字符串
    args = build_parser().parse_args(worker_args("idle", {**ATTACH, "clear_lb_cost": 0, "daily_claim": False}, device))
    cfg = Config()
    for item in args.set:
        key, _, value = item.partition("=")
        apply_override(cfg, key, value)
    check_run_config(cfg)
    assert cfg.game.lb_cost == 0 and not cfg.loop.until_lb_empty and cfg.loop.wait_lb
    assert cfg.loop.daily_claim_time == ""

    args = build_parser().parse_args(worker_args("ap", {**ATTACH, "ap_normal": False}, device))
    cfg = Config()
    for item in args.set:
        key, _, value = item.partition("=")
        apply_override(cfg, key, value)
    check_run_config(cfg)
    assert cfg.loop.song_mode == "ap" and cfg.loop.ap_difficulties == "expert,hard,easy"
    assert cfg.loop.ap_max_attempts == 3 and cfg.game.lb_cost is None

    attach = {**ATTACH, "song_mode": "ap_first", "difficulty": "high_first"}
    args = build_parser().parse_args(worker_args("clear_lb", attach, device))
    cfg = Config()
    for item in args.set:
        key, _, value = item.partition("=")
        apply_override(cfg, key, value)
    check_run_config(cfg)
    assert cfg.game.difficulty == "expert" and cfg.loop.ap_first_difficulties == "expert,hard,normal,easy"

    # 拟人化选项开到最大也能通过校验
    human = {"human_great": 20, "human_timing": 20, "human_position": True}
    args = build_parser().parse_args(worker_args("repeat", {**ATTACH, **human}, device))
    cfg = Config()
    for item in args.set:
        key, _, value = item.partition("=")
        apply_override(cfg, key, value)
    check_run_config(cfg)
    touch = cfg.play.touch
    assert (touch.great_ratio, touch.jitter_ms, touch.position_jitter) == (0.2, 20.0, 1.0)

    args = build_parser().parse_args(worker_args("idle", {**ATTACH, "lb_refill": True, "lb_refill_count": 7}, device))
    cfg = Config()
    for item in args.set:
        key, _, value = item.partition("=")
        apply_override(cfg, key, value)
    check_run_config(cfg)
    assert cfg.game.lb_refill is True and cfg.game.lb_refill_limit == 7
    cfg.game.lb_refill_limit = -1
    with pytest.raises(SetupError, match="上限"):
        check_run_config(cfg)
    cfg.game.lb_refill_limit, cfg.game.lb_cost = 0, 0
    cfg.loop.until_lb_empty = cfg.loop.wait_lb = False
    check_run_config(cfg)  # 消耗 0 时不报错，run 命令里不补充（#100）

    # 激奏舞台：run 命令统一改成只打 EXPERT、不跳过、至少 1% GREAT（拟人化设得更多时按设置）
    from ournotes_auto.commands import arena_defaults

    attach = {**ATTACH, "arena_band": "rotate", "arena_games_per_band": 2, "clear_lb_cost": 1, "difficulty": "hard"}
    args = build_parser().parse_args(worker_args("arena_idle", attach, device))
    cfg = Config()
    for item in args.set:
        key, _, value = item.partition("=")
        apply_override(cfg, key, value)
    arena_defaults(cfg)
    check_run_config(cfg)
    assert cfg.loop.arena and cfg.loop.arena_band == "rotate" and cfg.loop.arena_games_per_band == 2
    assert cfg.loop.until_lb_empty and cfg.loop.wait_lb and cfg.game.lb_cost == 1
    assert cfg.game.difficulty == "expert" and cfg.loop.song_mode == "current" and not cfg.loop.use_skip
    assert cfg.play.touch.great_ratio == 0.01


def _load(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def test_interface_tasks_match_agent():
    """界面任务 → pipeline 入口 → agent 任务名 → 默认参数能生成命令行，选项只写 OurNotesParam 里已有的键。"""
    from ournotes_auto.agent.params import PARAM_NODE, TASKS

    interface = _load("interface.json")
    tasks, options = [], {}
    for rel in interface["import"]:
        part = _load(rel)
        tasks += part.get("task", [])
        options.update(part.get("option", {}))
    pipeline = {}
    for path in sorted((ROOT / "resource" / "pipeline").glob("*.json")):
        pipeline.update(json.loads(path.read_text(encoding="utf-8")))
    defaults = pipeline[PARAM_NODE]["attach"]
    zh = json.loads((ROOT / "locales" / "zh_cn.json").read_text(encoding="utf-8"))
    def overrides(option):
        cases = option.get("cases", [])
        return [c["pipeline_override"] for c in cases] or [option["pipeline_override"]]

    for name, option in options.items():
        for override in overrides(option):
            assert set(override) == {PARAM_NODE}, name
            assert set(override[PARAM_NODE]["attach"]) <= set(defaults), name
        for case in option.get("cases", []):
            for sub in case.get("option", []):
                assert sub in options, (name, case["name"], sub)
    for name in interface["global_option"]:
        assert name in options
    seen = set()
    for task in tasks:
        for name in task.get("option", []):
            assert name in options, (task["name"], name)
        param = pipeline[task["entry"]]["action"]["param"]
        assert param["custom_action"] == "OurNotesRun"
        agent_task = param["custom_action_param"]["task"]
        # 日志里的任务名 = 界面上显示的中文名；name 是界面保存任务列表用的，改名时不动（「打开统计窗口」原名「记录汇总」）
        assert TASKS[agent_task] == zh[task["label"].removeprefix("$")], task["name"]
        worker_args(agent_task, {**defaults, "account": "user_1"}, {})  # 切换账号的账号名没有默认值
        seen.add(agent_task)
    assert seen == set(TASKS)


# ---------------------------------------------------------------- 日志


def _record(level, msg):
    return logging.LogRecord("x", level, __file__, 1, msg, (), None)


def test_ui_formatter():
    assert UiFormatter("MFAAVALONIA").format(_record(logging.WARNING, "a\nb")) == "warn:a\nwarn:b"
    assert UiFormatter("MFAAVALONIA").format(_record(logging.ERROR, "x")) == "err:x"
    assert UiFormatter("MXU").format(_record(logging.INFO, "<1>")) == "<span>&lt;1&gt;</span>"
    assert UiFormatter("MXU").format(_record(logging.ERROR, "x")) == '<span style="color:crimson;">x</span>'
    assert UiFormatter("").format(_record(logging.INFO, "hi")).endswith(" I hi")


def test_ui_image_line():
    """截图日志：MXU 显示成图片（路径相对于程序目录），别的界面不显示。"""
    record = logging.LogRecord(IMAGE_LOGGER, logging.INFO, __file__, 1, "%s", ("data/recruit/a&b.jpg",), None)
    assert UiFormatter("MXU").format(record) == '<img src="data/recruit/a&amp;b.jpg" style="max-width:480px;width:100%;" />'
    for client, shown in (("MXU", True), ("MFAAVALONIA", False), ("", False)):
        ui = setup_logging(None, client=client)
        try:
            assert bool(ui.filter(record)) == shown, client
            assert ui.filter(_record(logging.INFO, "x")), client
        finally:
            logging.getLogger().removeHandler(ui)


def test_ui_debug_switch():
    ui = setup_logging(None, client="")
    try:
        assert ui.level == logging.INFO  # 默认不显示调试信息
        set_ui_debug(True)
        assert ui.level == logging.DEBUG
        set_ui_debug(False)
        assert ui.level == logging.INFO
    finally:
        logging.getLogger().removeHandler(ui)


def test_parse_line():
    line = json.dumps({"level": "WARNING", "name": "ournotes_auto.runner", "msg": "曲目"}).encode() + b"\r\n"
    assert parse_line(line) == (logging.WARNING, "ournotes_auto.runner", "曲目")
    assert parse_line("Traceback：出错".encode()) == (logging.WARNING, WORKER_LOGGER, "Traceback：出错")
    assert parse_line(b"[1, 2]") == (logging.WARNING, WORKER_LOGGER, "[1, 2]")
    assert parse_line(b"  \n") is None


# ---------------------------------------------------------------- agent-run


@pytest.fixture
def cli_main(tmp_path, monkeypatch):
    """在临时目录里调 cli.main：不读仓库的 config.yaml，日志文件写到临时目录；不接管本进程的标准输入
    （记下 start_stdin_watch 收到的事件），跑完撤掉加上的日志处理器。"""
    from ournotes_auto import cli

    monkeypatch.chdir(tmp_path)
    watches = []
    monkeypatch.setattr(cli, "start_stdin_watch", lambda stop, update=None: watches.append((stop, update)))
    root, added = logging.getLogger(), []
    level, setup = root.level, cli._setup_logging

    def setup_logging(*args, **kw):
        before = list(root.handlers)
        setup(*args, **kw)
        added.extend(h for h in root.handlers if h not in before)

    monkeypatch.setattr(cli, "_setup_logging", setup_logging)
    yield cli.main, watches
    for handler in added:
        root.removeHandler(handler)
        handler.close()
    root.setLevel(level)


def _ui_lines(capsys, level=None):
    """演奏进程输出到标准输出的 JSON 日志（Agent 转发到界面的内容）→ [(级别, 消息)]；给了 level 时只留这一级的消息。"""
    lines = [x for x in map(parse_line, capsys.readouterr().out.encode().splitlines()) if x is not None]
    return [(lv, msg) for lv, _, msg in lines] if level is None else [msg for lv, _, msg in lines if lv == level]


def test_agent_run_bad_payload(cli_main, capsys):
    """agent-run：参数不是有效的 JSON、任务名不认识、要连模拟器却没有控制器时返回 2，错误显示在界面上。"""
    main, watches = cli_main
    assert main(["agent-run", "{task"]) == 2
    [(level, msg)] = _ui_lines(capsys)
    assert level == logging.ERROR and msg.startswith("任务参数不是有效的 JSON：")
    # 每次调用都加一个输出处理器，后面的同一行会重复，只看最后一行
    assert main(["agent-run", json.dumps({"task": "sweep"})]) == 2
    assert _ui_lines(capsys)[-1] == (logging.ERROR, "未知任务：sweep")
    assert main(["agent-run"]) == 2
    assert _ui_lines(capsys)[-1] == (logging.ERROR, "任务参数格式不对")
    assert main(["agent-run", json.dumps({"task": "start", "attach": {"touch": "mumu"}})]) == 2
    assert _ui_lines(capsys)[-1] == (logging.ERROR, "只支持模拟器（ADB 控制器）")
    assert watches == []  # 都没有开始执行


def test_agent_run_local_task(cli_main, capsys, monkeypatch):
    """本地任务（打开统计窗口）不需要控制器；开始、完成各在界面上显示一行，命令拿到停止 / 更新两个事件和任务名。"""
    from ournotes_auto import cli

    main, watches = cli_main
    seen = []
    monkeypatch.setattr(cli, "cmd_records", lambda cfg, args: seen.append(args) or 0)
    assert main(["agent-run", json.dumps({"task": "records", "attach": {}, "controller": {}})]) == 0
    [args] = seen
    assert (args.command, args.json_log, args.stdin_stop, args.verbose) == ("records", True, True, 0)
    assert args.label == "打开统计窗口"
    assert watches == [(args.stop, args.update)]
    assert not args.stop.is_set() and not args.update.is_set()
    msgs = _ui_lines(capsys, logging.INFO)
    assert msgs[0] == "开始打开统计窗口" and msgs[-1] == "打开统计窗口完成"


def test_agent_run_device_task(cli_main, capsys, monkeypatch, mumu_root):
    """要连模拟器的任务：控制器信息换成 device.* 配置交给命令；停止后结束的、为了更新退出的都不显示「完成」。"""
    from ournotes_auto import commands
    from ournotes_auto.release import EXIT_UPDATE

    main, watches = cli_main
    controller = adb_info("127.0.0.1:16416", str(mumu_root / "nx_main" / "adb.exe"))
    payload = json.dumps({"task": "start", "attach": {"touch": "mumu", "debug_log": True}, "controller": controller})
    seen = []
    monkeypatch.setattr(commands, "cmd_start", lambda cfg, args: seen.append((cfg, args)) or 0)
    assert main(["agent-run", payload]) == 0
    [(cfg, args)] = seen
    assert (args.command, args.verbose) == ("start", 1)
    assert (cfg.device.backend, cfg.device.mumu_path, cfg.device.instance) == ("mumu", str(mumu_root), 1)
    assert (cfg.device.adb_serial, cfg.device.touch) == ("127.0.0.1:16416", "mumu")
    assert watches == [(args.stop, args.update)]
    msgs = _ui_lines(capsys, logging.INFO)
    assert msgs[0] == "开始启动游戏" and msgs[-1] == "启动游戏完成"

    def stopped(cfg, args):
        args.stop.set()
        return 0

    monkeypatch.setattr(commands, "cmd_start", stopped)
    assert main(["agent-run", payload]) == 0
    msgs = _ui_lines(capsys, logging.INFO)
    assert "开始启动游戏" in msgs and "启动游戏完成" not in msgs
    monkeypatch.setattr(commands, "cmd_start", lambda cfg, args: EXIT_UPDATE)
    assert main(["agent-run", payload]) == EXIT_UPDATE
    msgs = _ui_lines(capsys, logging.INFO)
    assert "开始启动游戏" in msgs and "启动游戏完成" not in msgs


NOTIFY = {"notify_channel": "ntfy", "notify_target": "secret-topic_42", "notify_events": "all"}


def test_agent_run_notify_test(cli_main, capsys, monkeypatch, tmp_path):
    """推送测试：按界面上的设置发一条；推送地址不进日志文件和界面。没选推送方式、发送失败时报错。"""
    from ournotes_auto import notify

    main, watches = cli_main
    posted = []
    monkeypatch.setattr(notify, "_post", lambda url, body, ctype: posted.append((url, json.loads(body))) or b"{}")
    payload = {"task": "notify_test", "attach": {**NOTIFY, "debug_log": True}, "controller": {}}
    assert main(["agent-run", json.dumps(payload)]) == 0
    [(url, body)] = posted
    assert url == "https://ntfy.sh/" and body["topic"] == "secret-topic_42" and body["title"] == "OurNotes-Auto：推送测试"
    out = capsys.readouterr().out
    log = (tmp_path / "data" / "ournotes.log").read_text(encoding="utf-8")
    assert "notify.target=***" in log  # 记了参数，只是没记推送地址
    assert "secret-topic_42" not in log and "secret-topic_42" not in out

    payload["attach"]["notify_channel"] = "off"
    assert main(["agent-run", json.dumps(payload)]) == 1
    assert _ui_lines(capsys, logging.ERROR)[0].startswith("没有设置推送方式")

    def unreachable(url, body, ctype):
        raise notify.NotifyError("连不上推送服务器：timed out")

    monkeypatch.setattr(notify, "_post", unreachable)
    payload["attach"]["notify_channel"] = "ntfy"
    assert main(["agent-run", json.dumps(payload)]) == 1
    assert set(_ui_lines(capsys, logging.ERROR)) == {"推送失败：连不上推送服务器：timed out"}  # 每次 main 都加一个输出，会重复
    assert len(posted) == 1  # 测试命令自己不触发任务结束的推送


def test_agent_run_notify_task_end(cli_main, capsys, monkeypatch, mumu_root):
    """任务出错结束时推送最后一条错误日志；完成了的启动游戏、手动停止的、为更新退出的不推送。"""
    from ournotes_auto import commands, notify
    from ournotes_auto.release import EXIT_UPDATE

    main, watches = cli_main
    pushed = []
    monkeypatch.setattr(notify, "send", lambda cfg, event, title, text: pushed.append((cfg.target, event, title, text)))
    controller = adb_info("127.0.0.1:16416", str(mumu_root / "nx_main" / "adb.exe"))
    payload = json.dumps({"task": "start", "attach": {"touch": "mumu", **NOTIFY}, "controller": controller})

    def failing(cfg, args):
        logging.getLogger("ournotes_auto.nav").error("画面 60s 没有变化")
        return 1

    def stopped(cfg, args):
        args.stop.set()
        return failing(cfg, args)

    for cmd, code in ((failing, 1), (lambda cfg, args: 0, 0), (stopped, 1), (lambda cfg, args: EXIT_UPDATE, EXIT_UPDATE)):
        monkeypatch.setattr(commands, "cmd_start", cmd)
        assert main(["agent-run", payload]) == code
    [(target, event, title, text)] = pushed
    assert (target, event, title) == ("secret-topic_42", "error", "启动游戏出错")
    assert text.splitlines()[0] == "画面 60s 没有变化" and text.splitlines()[-1] == notify.LOG_HINT
    assert not any(isinstance(h, notify.LastError) for h in logging.getLogger().handlers)


# ---------------------------------------------------------------- 子进程

CHILD = r"""
import json, sys, time
print(json.dumps({"level": "INFO", "name": "child", "msg": "开始"}), flush=True)
print("not json", flush=True)
if "wait" in sys.argv:
    line = sys.stdin.readline()
    print(json.dumps({"level": "INFO", "name": "child", "msg": "got " + line.strip()}), flush=True)
if "lines" in sys.argv:  # 每行都回显，读到 stop 或输入结束为止；带 quit 时读到 update 也退出
    for line in iter(sys.stdin.readline, ""):
        cmd = line.strip()
        print(json.dumps({"level": "INFO", "name": "child", "msg": "got " + cmd}), flush=True)
        if cmd == "stop" or (cmd == "update" and "quit" in sys.argv):
            break
if "hang" in sys.argv:
    time.sleep(60)
if "openblas" in sys.argv:  # OpenBLAS 分配不到内存时直接 exit(1)
    print("OpenBLAS error: Memory allocation still failed after 10 retries, giving up.", flush=True)
if "memerr" in sys.argv:
    raise MemoryError
sys.exit(int(sys.argv[1]))
"""


def _run(tmp_path, *args, should_stop=lambda: False, **kw):
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    lines = []
    result = run_worker(
        [sys.executable, str(script), *args],
        cwd=tmp_path,
        should_stop=should_stop,
        on_line=lambda raw: lines.append(parse_line(raw)),
        poll_s=0.05,
        **kw,
    )
    return result, [x for x in lines if x is not None]


def test_run_worker_relays_output(tmp_path):
    result, lines = _run(tmp_path, "3")
    assert (result.returncode, result.stopped, result.killed) == (3, False, False)
    assert lines == [(logging.INFO, "child", "开始"), (logging.WARNING, WORKER_LOGGER, "not json")]


@pytest.mark.parametrize("arg", ["openblas", "memerr"])
def test_run_worker_out_of_memory(tmp_path, arg):
    """输出里有分配不到内存的报错（#94）：标出来，Agent 好提示电脑内存不足。"""
    result, _ = _run(tmp_path, "1", arg)
    assert result.returncode == 1 and result.out_of_memory
    assert not _run(tmp_path, "1")[0].out_of_memory


def test_run_worker_stop(tmp_path):
    t0 = time.monotonic()
    result, lines = _run(tmp_path, "0", "wait", should_stop=lambda: time.monotonic() - t0 > 0.3)
    assert (result.returncode, result.stopped, result.killed) == (0, True, False)
    assert lines[-1] == (logging.INFO, "child", "got stop")


def test_run_worker_kill_after_grace(tmp_path):
    t0 = time.monotonic()
    result, _ = _run(tmp_path, "0", "hang", should_stop=lambda: True, grace_s=0.3)
    assert result.stopped and result.killed and result.returncode != 0
    assert time.monotonic() - t0 < 20


def test_run_worker_update_once(tmp_path):
    """should_update 返回真后只发一次 update，之后不再问；停止照常发。"""
    calls, t0 = [], time.monotonic()
    result, lines = _run(
        tmp_path, "0", "lines",
        should_stop=lambda: len(calls) >= 3 and time.monotonic() - t0 > 0.5,
        should_update=lambda: calls.append(1) or len(calls) >= 3,
    )
    assert (result.returncode, result.stopped, result.killed) == (0, True, False)
    assert [msg for _, name, msg in lines if name == "child"] == ["开始", "got update", "got stop"]
    assert len(calls) == 3


def test_run_worker_update_exit(tmp_path):
    """演奏进程收到 update 后以 EXIT_UPDATE 退出：不算停止，退出码原样返回给 Agent。"""
    from ournotes_auto.release import EXIT_UPDATE

    result, lines = _run(tmp_path, str(EXIT_UPDATE), "lines", "quit", should_update=lambda: True)
    assert (result.returncode, result.stopped, result.killed) == (EXIT_UPDATE, False, False)
    assert lines[-1] == (logging.INFO, "child", "got update")


def test_run_worker_stop_before_update(tmp_path):
    """已经在停止了就不再发 update。"""
    calls = []
    result, lines = _run(
        tmp_path, "0", "lines", should_stop=lambda: True, should_update=lambda: calls.append(1) or True
    )
    assert (result.returncode, result.stopped, result.killed) == (0, True, False)
    assert [msg for _, name, msg in lines if name == "child"] == ["开始", "got stop"]
    assert calls == []


# ---------------------------------------------------------------- 标准输入


def _feed(stop, update, *lines):
    """依次给出 lines，每给出一行之后记下 (update, stop) 是否置位。"""
    seen = []

    def src():
        for line in lines:
            yield line
            seen.append((update is not None and update.is_set(), stop.is_set()))

    return src(), seen


def test_watch_stdin_update():
    """读到 update 只置位 update，不停止；读到 stop 才停止，后面的行不再读。"""
    from ournotes_auto.cli import _watch_stdin

    stop, update = threading.Event(), threading.Event()
    src, seen = _feed(stop, update, "hello\n", " update\r\n", "update\n", "stop\n", "more\n")
    _watch_stdin(stop, src, update)
    assert seen == [(False, False), (True, False), (True, False)]
    assert stop.is_set() and update.is_set()


def test_watch_stdin_eof():
    """输入结束也停止；没收到 update 时不置位。"""
    from ournotes_auto.cli import _watch_stdin

    stop, update = threading.Event(), threading.Event()
    src, seen = _feed(stop, update, "update\n")
    _watch_stdin(stop, src, update)
    assert seen == [(True, False)] and stop.is_set()
    stop, update = threading.Event(), threading.Event()
    _watch_stdin(stop, io.StringIO(" stop \n"), update)
    assert stop.is_set() and not update.is_set()
    stop = threading.Event()
    _watch_stdin(stop, io.StringIO(""), update)
    assert stop.is_set() and not update.is_set()


def test_watch_stdin_without_update():
    """不接收更新（update 为 None）时 update 行当普通行忽略，不会停止。"""
    from ournotes_auto.cli import _watch_stdin

    stop = threading.Event()
    src, seen = _feed(stop, None, "update\n", "update\n")
    _watch_stdin(stop, src)
    assert seen == [(False, False), (False, False)] and stop.is_set()
    stop = threading.Event()
    src, seen = _feed(stop, None, "update\n", "stop\n", "update\n")
    _watch_stdin(stop, src, None)
    assert seen == [(False, False)] and stop.is_set()


WATCH_CHILD = r"""
import sys, threading, time
sys.path.insert(0, {root!r})
from ournotes_auto.cli import start_stdin_watch
assert "numpy" not in sys.modules
stop = threading.Event()
start_stdin_watch(stop)
time.sleep(0.3)  # 等监听线程挂上读
import numpy  # Windows：监听线程挂着读管道时，扩展 DLL 初始化曾在这里卡死
print("imported", flush=True)
print("stopped" if stop.wait(10) else "timeout", flush=True)
"""


def test_stdin_watch_does_not_block_imports(tmp_path):
    script = tmp_path / "watch.py"
    script.write_text(WATCH_CHILD.format(root=str(Path(__file__).resolve().parents[1])), encoding="utf-8")
    lines, t0 = [], time.monotonic()
    result = run_worker(
        [sys.executable, str(script)],
        cwd=tmp_path,
        # 卡住时发出的 stop 也会解开挂起的读，所以要看 imported 是不是在超时之前到的
        should_stop=lambda: any(text == "imported" for _, text in lines) or time.monotonic() - t0 > 8,
        on_line=lambda raw: lines.append((time.monotonic() - t0, raw.decode().strip())),
        poll_s=0.05,
        grace_s=5,
    )
    assert [text for _, text in lines] == ["imported", "stopped"]
    assert lines[0][0] < 6
    assert (result.returncode, result.killed) == (0, False)


def test_apply_proxy():
    from ournotes_auto.agent.worker import PROXY_ENV, apply_proxy

    env = {"PATH": "x"}
    assert apply_proxy(" 127.0.0.1:7890 ", env, {}) == "http://127.0.0.1:7890"
    assert {k: env[k] for k in PROXY_ENV} == dict.fromkeys(PROXY_ENV, "http://127.0.0.1:7890")
    assert apply_proxy("https://proxy.lan:443", env, {}) == "https://proxy.lan:443"
    # 清空后恢复成 Agent 启动时的样子：原来有的还原，原来没有的删掉
    assert apply_proxy("", env, {"HTTPS_PROXY": "http://corp:8080"}) == ""
    assert env == {"PATH": "x", "HTTPS_PROXY": "http://corp:8080"}
    assert apply_proxy(None, env, {}) == "" and env == {"PATH": "x"}
