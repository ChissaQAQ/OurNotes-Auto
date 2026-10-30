"""命令行 / 界面传入的配置覆盖与校验。"""

import json
import logging

import pytest

from ournotes_auto.cli import JsonLineFormatter, _masked
from ournotes_auto.commands import arena_defaults, check_run_config, drop_great_for_ap, drop_refill_without_cost
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
    cfg.game.lb_cost = 6
    with pytest.raises(SetupError, match="0~5"):
        check_run_config(cfg)
    cfg = Config()
    cfg.loop.until_lb_empty = True
    with pytest.raises(SetupError, match="LB 用完"):
        check_run_config(cfg)
    cfg.game.lb_cost = 0
    with pytest.raises(SetupError, match="LB 用完"):
        check_run_config(cfg)
    cfg.game.lb_auto = True  # 自动选消耗：不看 lb_cost
    check_run_config(cfg)
    cfg.game.lb_auto = False
    cfg.game.lb_cost = 3
    check_run_config(cfg)
    cfg = Config()
    cfg.loop.wait_lb = True
    with pytest.raises(SetupError, match="挂机"):
        check_run_config(cfg)
    cfg.game.lb_auto = True
    check_run_config(cfg)
    cfg.game.lb_auto = False
    cfg.game.lb_cost = 0  # 挂机消耗 0：一直打
    check_run_config(cfg)
    cfg.game.lb_cost = 1
    check_run_config(cfg)
    cfg.loop.studio_claim_hours = -1
    with pytest.raises(SetupError, match="录音室练习"):
        check_run_config(cfg)
    cfg.loop.studio_claim_hours = 0.5
    check_run_config(cfg)
    for text in ("22:30", "7:05", " 23：59 ", ""):
        cfg.loop.daily_claim_time = text
        check_run_config(cfg)
    for text in ("24:00", "22:60", "2230", "22:3"):
        cfg.loop.daily_claim_time = text
        with pytest.raises(SetupError, match="领取日常"):
            check_run_config(cfg)
    cfg.loop.daily_claim_time = 1350  # 配置文件里不加引号的 22:30
    with pytest.raises(SetupError, match="引号"):
        check_run_config(cfg)


def test_check_run_config_challenge():
    cfg = Config()
    cfg.loop.challenge = True
    check_run_config(cfg)
    cfg.loop.song_mode = "rotate"
    cfg.game.challenge_cost = 1600
    check_run_config(cfg)
    cfg.game.challenge_cost = None  # 不改游戏里的设置
    check_run_config(cfg)
    cfg.game.challenge_cost = 300
    with pytest.raises(SetupError, match="挑战pt"):
        check_run_config(cfg)
    cfg.game.challenge_cost = 200
    cfg.loop.song_mode = "ap_first"
    cfg.loop.ap_first_difficulties = "expert,hard"
    check_run_config(cfg)
    cfg.loop.ap_first_difficulties = "expert,master"
    with pytest.raises(SetupError):
        check_run_config(cfg)
    cfg.loop.song_mode = "random"
    with pytest.raises(SetupError, match="挑战演出的选曲方式"):
        check_run_config(cfg)
    cfg.loop.song_mode = "current"
    cfg.loop.until_lb_empty, cfg.game.lb_cost = True, 3
    with pytest.raises(SetupError, match="不消耗 LB"):
        check_run_config(cfg)
    cfg.loop.until_lb_empty = False
    cfg.game.lb_refill = True
    with pytest.raises(SetupError, match="道具补充"):
        check_run_config(cfg)
    cfg = Config()
    cfg.loop.song_mode = "rotate"
    with pytest.raises(SetupError, match="rotate"):
        check_run_config(cfg)


def test_check_run_config_arena():
    cfg = Config()
    cfg.loop.arena = True
    check_run_config(cfg)
    cfg.loop.arena_band, cfg.loop.arena_games_per_band = "rotate", 3
    cfg.game.lb_cost, cfg.loop.until_lb_empty, cfg.loop.wait_lb = 2, True, True  # LB 同挂机
    check_run_config(cfg)
    cfg.loop.arena_band = "poppin"
    with pytest.raises(SetupError, match="激奏舞台的乐队"):
        check_run_config(cfg)
    cfg.loop.arena_band, cfg.loop.arena_games_per_band = "mygo", 0
    with pytest.raises(SetupError, match="至少为 1"):
        check_run_config(cfg)
    cfg.loop.arena_games_per_band = 1
    cfg.loop.challenge = True
    with pytest.raises(SetupError, match="不能同时打开"):
        check_run_config(cfg)


def test_arena_defaults():
    """激奏舞台只打 EXPERT、没有选曲和跳过；GREAT 至少 1%，拟人化设得更多时按设置。别的任务不动。"""
    cfg = Config()
    cfg.game.difficulty, cfg.loop.song_mode, cfg.loop.use_skip = "hard", "random", True
    arena_defaults(cfg)
    assert (cfg.game.difficulty, cfg.loop.song_mode, cfg.loop.use_skip, cfg.play.touch.great_ratio) == ("hard", "random", True, 0.0)
    cfg.loop.arena = True
    arena_defaults(cfg)
    assert (cfg.game.difficulty, cfg.loop.song_mode, cfg.loop.use_skip, cfg.play.touch.great_ratio) == ("expert", "current", False, 0.01)
    cfg.play.touch.great_ratio = 0.05
    arena_defaults(cfg)
    drop_great_for_ap(cfg)
    assert cfg.play.touch.great_ratio == 0.05


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


@pytest.mark.parametrize(
    "lb_cost, lb_auto, kept",
    [(0, False, False), (None, False, False), (1, False, True), (5, False, True), (None, True, True), (0, True, True)],
)
def test_drop_refill_without_cost(lb_cost, lb_auto, kept):
    """#100：开了用道具补充 LB、每局消耗 0（或不改游戏里的设置）时不报错，不补充照常打；消耗 1~5、自动时照留。"""
    cfg = Config()
    cfg.game.lb_cost, cfg.game.lb_auto = lb_cost, lb_auto
    cfg.game.lb_refill = True
    check_run_config(cfg)
    drop_refill_without_cost(cfg)
    assert cfg.game.lb_refill is kept


def test_json_line_formatter():
    record = logging.LogRecord("ournotes_auto.runner", logging.WARNING, __file__, 1, "曲目：%s", ("春日影",), None)
    line = JsonLineFormatter().format(record)
    assert line.isascii() and "\n" not in line
    assert json.loads(line) == {"level": "WARNING", "name": "ournotes_auto.runner", "msg": "曲目：春日影"}


def test_masked_notify_target():
    """启动参数记日志时遮住推送地址：--set 的键前后多了空格（照样能设上）、写成 --set=… 也遮。"""
    argv = ["run", "--set", "notify.target=SCT1", "--set", " notify.target =SCT2", "--set=notify.target=SCT3", "--set", "game.difficulty=hard"]
    shown = " ".join(_masked(argv))
    assert "SCT" not in shown and shown.count("***") == 3 and "game.difficulty=hard" in shown