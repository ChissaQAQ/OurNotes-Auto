import importlib.util
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ui_config", ROOT / "tools" / "ui_config.py")
ui_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ui_config)

IFACE = {"controller": [{"name": "MuMu"}], "resource": [{"name": "国际服"}]}
TASKS = {"启动游戏": "OurNotes_Start", "领取日常": "OurNotes_Daily", "重复刷歌": "OurNotes_Repeat"}


def _args(**kw):
    return SimpleNamespace(**{"serial": "127.0.0.1:16416", "name": "MuMu", "tasks": None, **kw})


def _config():
    return {
        "version": "1.0",
        "instances": [
            {
                "id": "ournotes",
                "name": "MuMu",
                "controllerName": "旧控制器",
                "savedDevice": {"adbDeviceName": "MuMu安卓设备-1-MuMuPlayer v5+", "adbDeviceAddress": "127.0.0.1:5555"},
                "tasks": [
                    {"id": "t2", "taskName": "重复刷歌", "enabled": True, "optionValues": {"Difficulty": {"type": "select", "caseName": "hard"}}},
                    {"id": "t0", "taskName": "启动游戏", "enabled": True, "optionValues": {}},
                    {"id": "t1", "taskName": "已删除的任务", "enabled": True, "optionValues": {}},
                ],
            }
        ],
        "settings": {"theme": "dark", "webServerEnabled": True},
        "interfaceTaskSnapshot": ["启动游戏", "重复刷歌"],
        "newTaskNames": ["领取日常"],
    }


def test_mxu_update_keeps_user_choices_and_adds_new_tasks():
    config = ui_config.mxu_update(_config(), IFACE, TASKS, _args())
    inst = config["instances"][0]
    assert [(t["id"], t["taskName"], t["enabled"]) for t in inst["tasks"]] == [
        ("t2", "重复刷歌", True),
        ("t0", "启动游戏", True),
        ("t3", "领取日常", False),
    ]
    assert inst["tasks"][0]["optionValues"]["Difficulty"]["caseName"] == "hard"
    # 地址重新固定，界面改过的设备名保留
    assert inst["savedDevice"] == {"adbDeviceName": "MuMu安卓设备-1-MuMuPlayer v5+", "adbDeviceAddress": "127.0.0.1:16416"}
    assert (inst["controllerName"], inst["resourceName"]) == ("MuMu", "国际服")
    assert config["settings"]["webServerEnabled"] is False
    assert config["settings"]["theme"] == "dark"
    assert config["interfaceTaskSnapshot"] == list(TASKS)
    assert "newTaskNames" not in config


def test_mxu_update_sets_checks_when_given():
    config = ui_config.mxu_update(_config(), IFACE, TASKS, _args(tasks="领取日常"))
    assert {t["taskName"]: t["enabled"] for t in config["instances"][0]["tasks"]} == {
        "重复刷歌": False,
        "启动游戏": False,
        "领取日常": True,
    }


def test_mxu_update_fills_empty_device_name():
    config = _config()
    del config["instances"][0]["savedDevice"]
    inst = ui_config.mxu_update(config, IFACE, TASKS, _args())["instances"][0]
    assert inst["savedDevice"] == {"adbDeviceName": "MuMu", "adbDeviceAddress": "127.0.0.1:16416"}


def test_mfaa_options_follow_default_case():
    tasks = [{"name": "领取日常", "option": ["DailyStudio", "DailyStory"]}, {"name": "重复刷歌", "option": ["MaxPlays"]}, {"name": "启动游戏"}]
    specs = {
        "DailyStudio": {"type": "switch", "cases": [{"name": "Yes"}, {"name": "No"}], "default_case": "Yes"},
        "DailyStory": {"type": "switch", "cases": [{"name": "Yes"}, {"name": "No"}], "default_case": "No"},
        "MaxPlays": {"type": "input", "inputs": [{"name": "count", "default": "0"}]},
    }
    assert ui_config.mfaa_options(tasks, specs) == {
        "领取日常": [{"name": "DailyStudio", "index": 0}, {"name": "DailyStory", "index": 1}],
        "重复刷歌": [{"name": "MaxPlays", "index": 0, "data": {"count": "0"}}],
        "启动游戏": [],
    }


def test_mfaa_options_cover_real_interface():
    _, tasks, specs = ui_config._load(ROOT)
    story = {o["name"]: o for o in ui_config.mfaa_options(tasks, specs)["领取日常"]}["DailyStory"]
    assert specs["DailyStory"]["cases"][story["index"]]["name"] == "No"
