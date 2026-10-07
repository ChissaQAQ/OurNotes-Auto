import threading
from contextlib import contextmanager
from types import SimpleNamespace

from ournotes_auto import commands, runner, sources
from ournotes_auto.cli import build_parser
from ournotes_auto.config import Config
from ournotes_auto.runner import NavigationError, RunStats


class Nav:
    def __init__(self, calls, fail=False):
        self.calls = calls
        self.fail = fail

    def ensure_in_game(self, timeout_s=180.0):
        self.calls.append("in_game")
        if self.fail:
            raise NavigationError("180s 内未能进入游戏")


def fake_run(monkeypatch, launched, fail=False):
    """把 ``run`` 命令用到的设备、导航、循环都换成假的，返回调用顺序。"""
    calls = []
    monkeypatch.setattr(commands, "_launch_game", lambda cfg: calls.append("launch") or launched)

    @contextmanager
    def open_context(cfg, stop, watch_combo=False, record=True):
        calls.append("open")
        yield SimpleNamespace(nav=Nav(calls, fail), session=None, client=None, catalog=None, store=None)

    class Runner:
        def __init__(self, *args):
            self.stats = RunStats()

        def run(self):
            calls.append("run")
            return RunStats(plays=1)

    monkeypatch.setattr(commands, "open_context", open_context)
    monkeypatch.setattr(sources, "make_source", lambda cfg, catalog: object())
    monkeypatch.setattr(runner, "Runner", Runner)
    args = build_parser().parse_args(["run"])
    args.stop = threading.Event()
    return calls, commands.cmd_run(Config(), args)


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

