"""「统计与成就」窗口：找浏览器、打开或切到前面、包目录里的快捷方式。"""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ournotes_auto import release, stats_window, updater

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="只在 Windows 上用应用模式窗口")
URL = "http://127.0.0.1:5555/stats.html?key=abc"


def test_find_browser_fallback_paths(tmp_path, monkeypatch):
    """注册表里没有时按常见的安装位置找：先 Edge 后 Chrome。"""
    import winreg

    def missing(*args):
        raise OSError("no key")

    monkeypatch.setattr(winreg, "OpenKey", missing)
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        monkeypatch.setenv(env, str(tmp_path / env))
    assert stats_window.find_browser() is None
    chrome = tmp_path / "LOCALAPPDATA" / "Google" / "Chrome" / "Application" / "chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.write_bytes(b"")
    assert stats_window.find_browser() == chrome
    edge = tmp_path / "ProgramFiles" / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_bytes(b"")
    assert stats_window.find_browser() == edge


def test_is_stats_window():
    """只认应用模式的窗口；浏览器普通窗口碰巧在看统计页时不算（标题后面带浏览器名），不然不会开窗口。"""
    title = stats_window.TITLE
    assert stats_window.is_stats_window("Chrome_WidgetWin_1", title)
    assert not stats_window.is_stats_window("Chrome_WidgetWin_1", f"{title} - Google Chrome")
    assert not stats_window.is_stats_window("Chrome_WidgetWin_1", f"{title} 和另外 2 个页面 - 个人 - Microsoft Edge")
    assert not stats_window.is_stats_window("MozillaWindowClass", title)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """窗口相关的系统调用都换成记录：``windows`` 是 find_window 依次返回的句柄，``url`` 是服务给的地址。"""
    state = SimpleNamespace(
        windows=[], focused=[], launched=[], started=[], served=[], browser=tmp_path / "msedge.exe", launch_ok=True, url=URL
    )

    def find_window():
        return state.windows.pop(0) if state.windows else 0

    def launch(browser, url, root):
        state.launched.append((browser, url, root))
        return state.launch_ok

    def start_server(page, root):
        state.served.append((page, root))
        return state.url

    monkeypatch.setattr(stats_window, "find_window", find_window)
    monkeypatch.setattr(stats_window, "focus", state.focused.append)
    monkeypatch.setattr(stats_window, "find_browser", lambda: state.browser)
    monkeypatch.setattr(stats_window, "launch", launch)
    monkeypatch.setattr(stats_window, "start_server", start_server)
    monkeypatch.setattr(stats_window.os, "startfile", state.started.append, raising=False)
    monkeypatch.setattr(stats_window.time, "sleep", lambda s: None)
    return state


def test_open_window_focuses_existing(tmp_path, fake):
    fake.windows = [42]
    assert stats_window.open_window(tmp_path / "stats.html", tmp_path) == "focused"
    assert fake.focused == [42] and fake.launched == []


def test_open_window_launches_and_focuses(tmp_path, fake):
    """新开窗口：打开服务给的地址，等窗口出来再切到前面；一直没出来也算开了（不再用浏览器另开一个）。
    服务起不来时直接打开页面文件。"""
    fake.windows = [0, 0, 0, 7]
    page, root = tmp_path / "data" / "stats.html", tmp_path.resolve()
    assert stats_window.open_window(page, tmp_path) == "window"
    assert fake.served == [(page.resolve(), root)] and fake.launched == [(fake.browser, URL, root)] and fake.focused == [7]
    fake.url = None
    assert stats_window.open_window(page, tmp_path, wait_s=0) == "window" and fake.focused == [7]
    assert fake.launched[-1] == (fake.browser, page.resolve().as_uri(), root)
    assert fake.started == []


def test_open_window_falls_back_to_browser(tmp_path, fake):
    """没有 Edge / Chrome，或者启动失败：用默认浏览器打开页面。"""
    page = tmp_path / "stats.html"
    fake.browser = None
    assert stats_window.open_window(page, tmp_path) == "browser" and fake.served == []  # 没有浏览器时不起服务
    fake.browser, fake.launch_ok = tmp_path / "msedge.exe", False
    assert stats_window.open_window(page, tmp_path) == "browser"
    assert fake.started == [str(page.resolve())] * 2


@pytest.fixture
def popen(monkeypatch):
    calls = []

    def fake(args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1 and kwargs["creationflags"] & stats_window._BREAKAWAY and fake.fail_breakaway:
            raise OSError("access denied")
        return SimpleNamespace(pid=1)

    fake.fail_breakaway = False
    monkeypatch.setattr(stats_window.subprocess, "Popen", fake)
    return calls, fake


def test_launch_app_mode(tmp_path, monkeypatch, popen):
    """应用模式、专用的配置目录；第一次打开成功后才不再给窗口大小（没打开时下次还给）；不在作业对象里时不加脱离标志。"""
    calls, _ = popen
    monkeypatch.setattr(updater, "in_job", lambda: False)
    browser, profile, sized = tmp_path / "msedge.exe", tmp_path / stats_window.PROFILE, tmp_path / stats_window.SIZED
    spawn = stats_window._spawn
    monkeypatch.setattr(stats_window, "_spawn", lambda args, cwd: False)
    assert not stats_window.launch(browser, URL, tmp_path) and not sized.exists()
    monkeypatch.setattr(stats_window, "_spawn", spawn)
    assert stats_window.launch(browser, URL, tmp_path)
    args, kwargs = calls[-1]
    assert args[:3] == [str(browser), f"--app={URL}", f"--user-data-dir={profile}"]
    assert f"--window-size={stats_window.WINDOW_SIZE}" in args
    assert kwargs["creationflags"] == stats_window._DETACHED and kwargs["cwd"] == browser.parent
    assert sized.read_text(encoding="utf-8") == URL.partition("?")[0]  # 不记密钥
    assert stats_window.launch(browser, URL, tmp_path)
    assert len(calls) == 2 and not any(a.startswith("--window-size") for a in calls[-1][0])


def test_launch_escapes_job(tmp_path, monkeypatch, popen):
    """在界面的作业对象里：加脱离标志；不允许脱离时通过 WMI 启动（关界面时窗口不跟着关）。"""
    calls, fake = popen
    monkeypatch.setattr(updater, "in_job", lambda: True)
    wmi = []
    monkeypatch.setattr(stats_window, "_wmi_start", lambda cmd, cwd: wmi.append((cmd, cwd)) or True)
    browser = tmp_path / "msedge.exe"
    assert stats_window.launch(browser, URL, tmp_path)
    assert calls[-1][1]["creationflags"] == stats_window._DETACHED | stats_window._BREAKAWAY and wmi == []
    calls.clear()
    fake.fail_breakaway = True
    assert stats_window.launch(browser, URL, tmp_path)
    assert len(calls) == 1 and len(wmi) == 1
    assert wmi[0][0] == subprocess.list2cmdline(calls[0][0]) and wmi[0][1] == browser.parent


@pytest.fixture
def serve_proc(tmp_path, monkeypatch):
    """起服务的进程换成记录：``ready`` 是第几次 sleep 时写进 ready 文件的内容，``exit`` 是进程的退出码。"""
    state = SimpleNamespace(spawned=[], sleeps=0, ready={}, exit=None, proc=None)

    class Proc(subprocess.Popen):
        def __init__(self):  # 不真的起进程
            self.returncode, self._child_created = None, False

        def poll(self):
            self.returncode = state.exit
            return state.exit

    def spawn(args, cwd):
        state.spawned.append((args, cwd))
        return Proc() if state.proc is None else state.proc

    def sleep(s):
        state.sleeps += 1
        if state.sleeps in state.ready:
            release.write_json(Path(state.spawned[-1][0][-1]), state.ready[state.sleeps])

    monkeypatch.setattr(stats_window, "_spawn", spawn)
    monkeypatch.setattr(stats_window.time, "sleep", sleep)
    return state


def test_start_server(tmp_path, serve_proc):
    """起 stats-serve（有 pythonw 时用它，不弹控制台），读到 ready 里的端口和密钥就删掉它、返回窗口要打开的地址；
    ready 里的内容不对时接着等。"""
    serve_proc.ready = {2: {"port": "5555", "key": "abc"}, 3: {"port": 5555, "key": "abc"}}
    page = tmp_path / "data" / "stats.html"
    assert stats_window.start_server(page, tmp_path) == URL and serve_proc.sleeps == 3
    [(args, cwd)] = serve_proc.spawned
    exe = Path(sys.executable)
    assert args[0] in (str(exe.with_name("pythonw.exe")), str(exe)) and cwd == tmp_path
    assert args[1:5] == ["-m", "ournotes_auto", "stats-serve", "--ready"]
    ready = Path(args[5])
    assert ready.parent == tmp_path / "cache" and not ready.exists()


def test_start_server_fails(tmp_path, serve_proc, monkeypatch):
    """起不来、进程退出了、等太久：返回 None（直接打开页面文件），不一直等。"""
    page = tmp_path / "stats.html"
    serve_proc.proc = False
    assert stats_window.start_server(page, tmp_path) is None and serve_proc.sleeps == 0
    serve_proc.proc, serve_proc.exit = None, 1
    assert stats_window.start_server(page, tmp_path) is None and serve_proc.sleeps == 1
    serve_proc.proc = True  # 通过 WMI 启动：看不到进程，只能等到时间
    monkeypatch.setattr(stats_window, "SERVE_WAIT_S", 0)
    assert stats_window.start_server(page, tmp_path) is None and serve_proc.sleeps == 1


@pytest.fixture
def package(tmp_path):
    release.write_json(tmp_path / release.RELEASE_FILE, {"ui": "mxu", "files": {}})
    (tmp_path / "python").mkdir()
    (tmp_path / "python" / "pythonw.exe").write_bytes(b"")
    return tmp_path


def test_ensure_shortcut(package, monkeypatch):
    """发布包里建一次；已经有了不再建；包挪了位置（记下的目录不一样）重建；建失败报错、不记下。"""
    runs = []

    def run(cmd, env, **kwargs):
        runs.append(env)
        open(env["OURNOTES_LNK"], "wb").close()
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(stats_window.subprocess, "run", run)
    root = package.resolve()
    lnk, mark = root / stats_window.SHORTCUT, root / stats_window.SHORTCUT_MARK
    assert stats_window.ensure_shortcut(package) is True
    env = runs[0]
    assert env["OURNOTES_EXE"] == str(root / "python" / "pythonw.exe") and env["OURNOTES_ROOT"] == str(root)
    assert env["OURNOTES_ICON"].endswith("logo.ico,0") and lnk.exists()
    assert mark.read_text(encoding="utf-8") == str(root)
    assert stats_window.ensure_shortcut(package) is False and len(runs) == 1
    mark.write_text("D:/old", encoding="utf-8")
    assert stats_window.ensure_shortcut(package) is True and len(runs) == 2

    lnk.unlink()
    mark.unlink()
    monkeypatch.setattr(stats_window.subprocess, "run", lambda cmd, env, **kw: SimpleNamespace(returncode=1, stderr="拒绝访问".encode("mbcs")))
    with pytest.raises(OSError, match="拒绝访问"):
        stats_window.ensure_shortcut(package)
    assert not mark.exists()


def test_ensure_shortcut_skips_dev_and_broken(tmp_path, monkeypatch):
    """开发目录（没有 release.json）、包里没有 pythonw.exe：不建。"""
    monkeypatch.setattr(stats_window.subprocess, "run", lambda *a, **k: pytest.fail("不该建快捷方式"))
    assert stats_window.ensure_shortcut(tmp_path) is False
    release.write_json(tmp_path / release.RELEASE_FILE, {"ui": "mfaa", "files": {}})
    assert stats_window.ensure_shortcut(tmp_path) is False
