"""Agent：界面参数、控制器信息 → 演奏子进程参数；子进程日志转发与停止。"""

import json
import logging
import sys
import time
from pathlib import Path

import pytest

from ournotes_auto.agent.logs import UiFormatter, set_ui_debug, setup_logging
from ournotes_auto.agent.params import ParamError, device_from_controller, flag, global_args, run_settings, worker_args
from ournotes_auto.agent.worker import WORKER_LOGGER, parse_line, run_worker

ATTACH = {
    "song_mode": "current",
    "difficulty": "expert",
    "max_plays": 0,
    "lb_cost": "keep",
    "clear_lb_cost": 3,
    "touch": "minitouch",
    "watch_combo": False,
    "debug_log": False,
    "check_update": True,
    "ap_expert": True,
    "ap_hard": True,
    "ap_normal": True,
    "ap_easy": True,
    "ap_order": "hard_first",
    "ap_max_attempts": 3,
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
        "game.lb_cost": "null",
        "loop.until_lb_empty": "false",
        **HUMAN_OFF,
    }
    sets = run_settings("repeat", {**ATTACH, "lb_cost": 3, "max_plays": "20", "difficulty": "HARD"})
    assert sets["game.lb_cost"] == "3"
    assert sets["loop.max_plays"] == "20"
    assert sets["game.difficulty"] == "hard"


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
        "loop.song_mode": "ap",
        "loop.ap_difficulties": "expert,hard,normal,easy",
        "loop.ap_max_attempts": "3",
        "game.lb_cost": "null",
        "loop.until_lb_empty": "false",
        **HUMAN_OFF,
    }
    sets = run_settings(
        "ap", {**ATTACH, "ap_hard": "No", "ap_normal": False, "ap_order": "easy_first", "lb_cost": 0}
    )
    assert sets["loop.ap_difficulties"] == "easy,expert"
    assert sets["game.lb_cost"] == "0"


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
        ("repeat", {"lb_cost": 4}, "lb_cost"),
        ("clear_lb", {"clear_lb_cost": 0}, "clear_lb_cost"),
        ("repeat", {"human_great": 30}, "human_great"),
        ("repeat", {"human_timing": 25}, "human_timing"),
        ("ap", {"human_position": "maybe"}, "human_position"),
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


def test_worker_args_daily():
    from ournotes_auto.agent.params import DAILY_JOBS
    from ournotes_auto.cli import build_parser
    from ournotes_auto.nav import daily

    assert DAILY_JOBS == tuple(daily.DAILY_JOBS)
    attach = {"touch": "mumu", **{f"daily_{j}": True for j in DAILY_JOBS}, "daily_limited": "No"}
    args = worker_args("daily", attach, {"device.instance": "1"})
    assert args == [
        "--set", "device.instance=1", "--set", "device.touch=mumu",
        "daily", "--jobs", "studio,story,missions,pass,beginner,tgw,gifts",
    ]
    assert build_parser().parse_args(args).jobs == ["studio", "story", "missions", "pass", "beginner", "tgw", "gifts"]
    with pytest.raises(ParamError, match="至少要选一项"):
        worker_args("daily", {"touch": "mumu", **{f"daily_{j}": False for j in DAILY_JOBS}}, {})
    with pytest.raises(SystemExit):
        build_parser().parse_args(["daily", "--jobs", "studio,shop"])


def test_global_args():
    """「调试日志」开着时子进程加 -v；旧资源的 attach 里没有这一项时按关闭。"""
    from ournotes_auto.cli import build_parser

    assert global_args(ATTACH) == global_args({}) == ["--json-log", "--stdin-stop"]
    args = build_parser().parse_args([*global_args({**ATTACH, "debug_log": "Yes"}), *worker_args("records", {}, {})])
    assert (args.verbose, args.json_log, args.stdin_stop, args.command) == (1, True, True, "records")
    with pytest.raises(ParamError, match="debug_log"):
        global_args({"debug_log": "maybe"})
    assert flag({}, "check_update", default=True) and not flag({"check_update": "No"}, "check_update", default=True)


def test_worker_args_parse_back():
    """生成的 --set 能被命令行原样解析、应用。"""
    from ournotes_auto.cli import build_parser
    from ournotes_auto.commands import check_run_config
    from ournotes_auto.config import Config, apply_override

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
    assert cfg.game.lb_cost == 3 and cfg.loop.until_lb_empty

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
        assert TASKS[agent_task] == task["name"]
        worker_args(agent_task, defaults, {})
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


# ---------------------------------------------------------------- 子进程

CHILD = r"""
import json, sys, time
print(json.dumps({"level": "INFO", "name": "child", "msg": "开始"}), flush=True)
print("not json", flush=True)
if "wait" in sys.argv:
    line = sys.stdin.readline()
    print(json.dumps({"level": "INFO", "name": "child", "msg": "got " + line.strip()}), flush=True)
if "hang" in sys.argv:
    time.sleep(60)
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
