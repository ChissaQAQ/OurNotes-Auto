import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from ournotes_auto import commands, runner, sources
from ournotes_auto.cli import build_parser
from ournotes_auto.config import INTL_PACKAGES, JP_PACKAGE, Config
from ournotes_auto.release import EXIT_RERUN, EXIT_UPDATE
from ournotes_auto.runner import NavigationError, RunStats


class Nav:
    def __init__(self, calls, fail=False):
        self.calls = calls
        self.fail = fail

    def ensure_in_game(self, timeout_s=180.0):
        self.calls.append("in_game")
        if self.fail:
            raise NavigationError("180s 内未能进入游戏")


def fake_run(monkeypatch, launched, fail=False, stats=None, cfg=None, **attrs):
    """把 ``run`` 命令用到的设备、导航、循环都换成假的，返回调用顺序。``stats`` 是循环的结果，
    ``attrs`` 设到命令参数上；传给 Runner 的参数记在 ``runner.Runner.args``。"""
    calls = []
    monkeypatch.setattr(commands, "_launch_game", lambda cfg: calls.append("launch") or launched)

    @contextmanager
    def open_context(cfg, stop, watch_combo=False, record=True):
        calls.append("open")
        yield SimpleNamespace(nav=Nav(calls, fail), session=None, client=None, catalog=None, store=None)

    class Runner:
        def __init__(self, *args):
            Runner.args = args
            self.stats = RunStats()

        def run(self):
            calls.append("run")
            return stats or RunStats(plays=1)

    monkeypatch.setattr(commands, "open_context", open_context)
    monkeypatch.setattr(sources, "make_source", lambda cfg, catalog, store=None: object())
    monkeypatch.setattr(runner, "Runner", Runner)
    args = build_parser().parse_args(["run"])
    args.stop = threading.Event()
    for key, value in attrs.items():
        setattr(args, key, value)
    return calls, commands.cmd_run(cfg or Config(), args)


def test_run_launches_game_first(monkeypatch):
    """游戏没在运行时 run 先启动它，等进了游戏再开始循环（#46）。"""
    calls, code = fake_run(monkeypatch, launched=True)
    assert calls == ["launch", "open", "in_game", "run"] and code == 0


def test_run_game_already_running(monkeypatch):
    calls, code = fake_run(monkeypatch, launched=False)
    assert calls == ["launch", "open", "run"] and code == 0


def test_run_launch_cannot_enter_game(monkeypatch, caplog):
    calls, code = fake_run(monkeypatch, launched=True, fail=True)
    assert calls == ["launch", "open", "in_game"] and code == 1
    assert "未能进入游戏" in caplog.text


def test_run_restart_for_update(monkeypatch):
    """挂机为换上新版本退出时返回 EXIT_UPDATE，Agent 看到后换完再用同样的参数重启它。"""
    calls, code = fake_run(monkeypatch, launched=False, stats=RunStats(restart=True))
    assert calls == ["launch", "open", "run"] and code == EXIT_UPDATE == 75


def test_run_rerun_tasks(monkeypatch):
    """挂机在游戏日期变更后退出时返回 EXIT_RERUN，Agent 看到后重跑前面的任务再用同样的参数重启它。"""
    calls, code = fake_run(monkeypatch, launched=False, stats=RunStats(plays=3, rerun=True))
    assert calls == ["launch", "open", "run"] and code == EXIT_RERUN == 76


@pytest.mark.parametrize("unmatched, code", [(True, 0), (False, 1)])
def test_run_arena_no_match_ends_normally(monkeypatch, unmatched, code):
    """#113：激奏舞台很久都没匹配到对局（这个时段没人）就结束，算正常结束，接着跑后面的任务（之前失败过一局也一样）。"""
    stats = RunStats(failures=1, no_match=12, unmatched=unmatched)
    assert fake_run(monkeypatch, launched=False, stats=stats)[1] == code


def test_run_passes_update_event(monkeypatch):
    """args.update 作为第 9 个参数、args.gate（「停止时打完当前这首」）作为第 11 个参数传给 Runner；
    没有它们时（不是从命令行入口来的）传 None。"""
    from ournotes_auto.songgate import SongGate

    update = threading.Event()
    gate = SongGate(threading.Event())
    fake_run(monkeypatch, launched=False, update=update, gate=gate)
    assert len(runner.Runner.args) == 11 and runner.Runner.args[8] is update and runner.Runner.args[10] is gate
    # 导航也知道「收到了停止请求、正等这首打完」：这时游戏不在了就不重启、不干等
    nav = runner.Runner.args[1]
    assert nav.stop_pending() is False
    gate.request()
    assert nav.stop_pending() is True
    assert not hasattr(build_parser().parse_args(["run"]), "update")
    fake_run(monkeypatch, launched=False)
    assert len(runner.Runner.args) == 11 and runner.Runner.args[8] is None and runner.Runner.args[10] is None


def test_run_passes_tracker(monkeypatch):
    """统计页的运行记录：任务名用界面上的任务名（命令行里是「命令行」），挂机看 --wait-lb；
    loop.continues（重跑的任务、重新启动的挂机）记上接着前面的运行。"""
    from ournotes_auto.stats import Tracker

    fake_run(monkeypatch, launched=False, label="挂机", wait_lb=True, lb_auto=True)
    tracker = runner.Runner.args[9]
    assert isinstance(tracker, Tracker) and (tracker.session.task, tracker.session.idle) == ("挂机", True)
    assert tracker.session.continues is False
    fake_run(monkeypatch, launched=False)
    assert (runner.Runner.args[9].session.task, runner.Runner.args[9].session.idle) == ("命令行", False)
    cfg = Config()
    cfg.loop.continues = True
    fake_run(monkeypatch, launched=False, cfg=cfg)
    assert runner.Runner.args[9].session.continues is True


def test_run_arena_args(monkeypatch):
    """--arena：激奏舞台打 -d 的难度（#134）、不跳过、至少 1% GREAT、时机偏移至少 ±16ms；--arena-band、--arena-games 选乐队和每个乐队的局数。"""
    args = build_parser().parse_args(["run", "--arena", "--arena-band", "rotate", "--arena-games", "2"])
    assert (args.arena, args.arena_band, args.arena_games) == (True, "rotate", 2)
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--arena-band", "poppin"])
    fake_run(monkeypatch, launched=False, arena=True, arena_band="rotate", arena_games=2, difficulty="hard", use_skip=True)
    cfg = runner.Runner.args[0]
    assert cfg.loop.arena and (cfg.loop.arena_band, cfg.loop.arena_games_per_band) == ("rotate", 2)
    assert (cfg.game.difficulty, cfg.loop.song_mode, cfg.loop.use_skip) == ("hard", "current", False)
    assert cfg.play.touch.great_ratio == 0.01 and cfg.play.touch.jitter_ms == 16.0
    with pytest.raises(commands.SetupError, match="不能同时打开"):
        fake_run(monkeypatch, launched=False, arena=True, challenge=True)


def test_switch_account_intl_packages(monkeypatch, caplog):
    """切换账号只限国际服，Google Play 版的包名也算（#54）；日服不启动游戏直接报错。"""
    launched = []
    monkeypatch.setattr(commands, "_launch_game", lambda cfg: launched.append(cfg.device.package))

    class Opened(Exception):
        pass

    def open_device(cfg):
        raise Opened

    monkeypatch.setattr(commands, "_device", open_device)
    args = build_parser().parse_args(["switch-account", "user_12"])
    args.stop = threading.Event()
    cfg = Config()
    for package in INTL_PACKAGES:
        cfg.device.package = package
        with pytest.raises(Opened):
            commands.cmd_switch_account(cfg, args)
    cfg.device.package = JP_PACKAGE
    assert commands.cmd_switch_account(cfg, args) == 2
    assert launched == list(INTL_PACKAGES) and "只有国际服能切换账号" in caplog.text


def test_run_challenge_auto_arg(monkeypatch):
    """--challenge-auto：挑战演出每局按持有的挑战pt 自动选消耗；不加时照 game.challenge_cost。"""
    assert build_parser().parse_args(["run", "--challenge", "--challenge-auto"]).challenge_auto is True
    fake_run(monkeypatch, launched=False, challenge=True, challenge_auto=True, song_mode="rotate")
    cfg = runner.Runner.args[0]
    assert cfg.loop.challenge and cfg.game.challenge_auto is True
    fake_run(monkeypatch, launched=False, challenge=True, song_mode="rotate")
    assert runner.Runner.args[0].game.challenge_auto is False


def quit_game(monkeypatch, stop_app):
    """运行 ``quit`` 命令；``stop_app`` 代替 adb.stop_app。返回 (退出码, 用到的包名)。"""
    from ournotes_auto.config import INTL_PACKAGE, INTL_PLAY_PACKAGE
    from ournotes_auto.device import adb

    monkeypatch.setattr(adb, "resolve_package", lambda device: setattr(device, "package", INTL_PLAY_PACKAGE))
    seen = []

    def stop(device):
        seen.append(device.package)
        return stop_app()

    monkeypatch.setattr(adb, "stop_app", stop)
    cfg = Config()
    cfg.device.package = INTL_PACKAGE
    args = build_parser().parse_args(["quit"])
    args.stop = threading.Event()
    return args.func(cfg, args), seen


def test_quit_command(monkeypatch, caplog):
    """#144 退出游戏：结束模拟器里装的那个版本的游戏；没在运行也算成功；adb 出错时报错、退出码 1（不是意外错误）。"""
    import subprocess

    from ournotes_auto.config import INTL_PLAY_PACKAGE

    with caplog.at_level("INFO", logger="ournotes_auto"):
        assert quit_game(monkeypatch, lambda: True) == (0, [INTL_PLAY_PACKAGE])
        assert quit_game(monkeypatch, lambda: False) == (0, [INTL_PLAY_PACKAGE])
    assert f"已退出游戏（{INTL_PLAY_PACKAGE}）" in caplog.text and f"游戏没有在运行（{INTL_PLAY_PACKAGE}）" in caplog.text

    def hung():
        raise subprocess.TimeoutExpired(["adb", "shell", "am", "force-stop"], 30)

    assert quit_game(monkeypatch, hung)[0] == 1
    assert "退出游戏失败" in caplog.text
