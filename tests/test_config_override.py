"""命令行 / 界面传入的配置覆盖与校验。"""

import json
import logging

import pytest

from ournotes_auto.cli import JsonLineFormatter
from ournotes_auto.commands import check_run_config, drop_great_for_ap
from ournotes_auto.config import Config, apply_override
from ournotes_auto.context import SetupError


def test_apply_override_types():
    cfg = Config()
    apply_override(cfg, "game.difficulty", "hard")
    apply_override(cfg, "loop.max_plays", " 12 ")
    apply_override(cfg, "play.offset_ms", "-1.5")
    apply_override(cfg, "loop.until_lb_empty", "Yes")
    apply_override(cfg, "play.touch.jitter_ms", "3")  # 多层嵌套
    apply_override(cfg, "device.mumu_path", r"E:\MuMu Player")
    assert cfg.game.difficulty == "hard"
    assert cfg.loop.max_plays == 12
    assert cfg.play.offset_ms == -1.5
    assert cfg.loop.until_lb_empty is True
    assert cfg.play.touch.jitter_ms == 3.0
    assert cfg.device.mumu_path == r"E:\MuMu Player"


def test_apply_override_optional():
    cfg = Config()
    apply_override(cfg, "game.lb_cost", "2")
    assert cfg.game.lb_cost == 2
    apply_override(cfg, "game.lb_cost", "null")
    assert cfg.game.lb_cost is None


@pytest.mark.parametrize(
    "key, raw, msg",
    [
        ("game.nope", "1", "未知配置项"),
        ("nope", "1", "未知配置项"),
        ("game.difficulty.x", "1", "未知配置项"),
        ("play.touch", "1", "是一个节"),
        ("loop.max_plays", "abc", "值无效"),
        ("loop.until_lb_empty", "maybe", "值无效"),
        ("game.lb_cost", "1.5", "值无效"),
    ],
)
def test_apply_override_errors(key, raw, msg):
    with pytest.raises(ValueError, match=msg):
        apply_override(Config(), key, raw)


def test_check_run_config():
    check_run_config(Config())
    cfg = Config()
    cfg.game.difficulty = "master"
    with pytest.raises(SetupError, match="难度"):
        check_run_config(cfg)
    cfg = Config()
    cfg.loop.song_mode = "all"
    with pytest.raises(SetupError, match="选曲方式"):
        check_run_config(cfg)
    cfg = Config()
    cfg.loop.song_mode = "ap_first"
    cfg.loop.ap_first_difficulties = "expert,master"
    with pytest.raises(SetupError, match="优先没 AP"):
        check_run_config(cfg)
    cfg.loop.ap_first_difficulties = ""
    check_run_config(cfg)
    cfg = Config()
    cfg.game.lb_cost = 4
    with pytest.raises(SetupError, match="0~3"):
        check_run_config(cfg)
    cfg = Config()
    cfg.loop.until_lb_empty = True
    with pytest.raises(SetupError, match="LB 用完"):
        check_run_config(cfg)
    cfg.game.lb_cost = 0
    with pytest.raises(SetupError, match="LB 用完"):
        check_run_config(cfg)
    cfg.game.lb_cost = 3
    check_run_config(cfg)


@pytest.mark.parametrize(
    "key, value, msg",
    [
        ("great_ratio", 0.5, "GREAT"),
        ("great_ratio", -0.01, "GREAT"),
        ("jitter_ms", 25.0, "时机随机偏移"),
        ("position_jitter", 1.5, "触控位置"),
    ],
)
def test_check_run_config_humanize(key, value, msg):
    cfg = Config()
    setattr(cfg.play.touch, key, value)
    with pytest.raises(SetupError, match=msg):
        check_run_config(cfg)


@pytest.mark.parametrize("mode, kept", [("current", True), ("random", True), ("list", True), ("ap", False), ("ap_first", False)])
def test_ap_modes_drop_great(mode, kept):
    cfg = Config()
    cfg.loop.song_mode = mode
    cfg.play.touch.great_ratio = 0.03
    cfg.play.touch.jitter_ms = 8
    drop_great_for_ap(cfg)
    assert cfg.play.touch.great_ratio == (0.03 if kept else 0.0)
    assert cfg.play.touch.jitter_ms == 8  # 时机偏移不影响 PERFECT，照留


def test_json_line_formatter():
    record = logging.LogRecord("ournotes_auto.runner", logging.WARNING, __file__, 1, "曲目：%s", ("春日影",), None)
    line = JsonLineFormatter().format(record)
    assert line.isascii() and "\n" not in line
    assert json.loads(line) == {"level": "WARNING", "name": "ournotes_auto.runner", "msg": "曲目：春日影"}
