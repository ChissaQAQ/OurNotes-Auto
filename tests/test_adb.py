import subprocess

import pytest

from ournotes_auto.config import INTL_PACKAGE, INTL_PLAY_PACKAGE, JP_PACKAGE, DeviceConfig
from ournotes_auto.device import adb


def fake_adb(monkeypatch, installed=(), running=(), error=None, focus=None):
    """假的 adb：``pm list packages <过滤>`` 列出 ``installed`` 里含过滤串的，``pidof`` 只对 ``running`` 里的有输出；
    ``dumpsys window`` 里获得焦点的窗口属于 ``focus``（None 时没有这一行）；
    ``error`` 不为空时连接就抛出它。返回执行过的 shell 命令。"""
    calls = []

    def runner(cfg, timeout_s):
        if error is not None:
            raise error

        def run(*args, check=True):
            cmd = args[3:]  # 去掉 -s <serial> shell
            calls.append(cmd)
            if cmd[:3] == ("pm", "list", "packages"):
                out = "".join(f"package:{p}\n" for p in installed if cmd[3] in p)
            elif cmd[0] == "pidof":
                out = "4321\n" if cmd[1] in running else ""
            elif cmd == ("dumpsys", "window"):
                out = f"  mCurrentFocus=Window{{f1250d0 u0 {focus}/{focus}.MainActivity}}\n" if focus else ""
            elif cmd[0] == "monkey":
                if cmd[2] not in installed:
                    raise subprocess.CalledProcessError(adb.MONKEY_NO_APP, args)
                out = ""
            else:
                raise AssertionError(cmd)
            return subprocess.CompletedProcess(args, 0, stdout=out.encode(), stderr=b"")

        return "127.0.0.1:16416", run

    monkeypatch.setattr(adb, "_runner", runner)
    return calls


def resolved(monkeypatch, package, **kw) -> str:
    cfg = DeviceConfig(package=package)
    fake_adb(monkeypatch, **kw)
    adb.resolve_package(cfg)
    return cfg.package


def test_resolve_package_installed_one(monkeypatch):
    """国际服只装了其中一个版本：用装了的那个（#55 装的是 Google Play 版）。日服也装着不影响。"""
    assert resolved(monkeypatch, INTL_PACKAGE, installed=(INTL_PLAY_PACKAGE, JP_PACKAGE)) == INTL_PLAY_PACKAGE
    assert resolved(monkeypatch, INTL_PLAY_PACKAGE, installed=(INTL_PACKAGE,)) == INTL_PACKAGE
    assert resolved(monkeypatch, INTL_PACKAGE, installed=(INTL_PACKAGE, JP_PACKAGE)) == INTL_PACKAGE
    assert resolved(monkeypatch, INTL_PACKAGE) == INTL_PACKAGE  # 都没装：不改，启动时照常报错


def test_resolve_package_both_installed(monkeypatch):
    """两个版本都装了：用正在运行的那个，都在运行或都没运行时照配置。"""
    both = (INTL_PACKAGE, INTL_PLAY_PACKAGE)
    assert resolved(monkeypatch, INTL_PACKAGE, installed=both, running=(INTL_PLAY_PACKAGE,)) == INTL_PLAY_PACKAGE
    assert resolved(monkeypatch, INTL_PLAY_PACKAGE, installed=both, running=(INTL_PACKAGE,)) == INTL_PACKAGE
    assert resolved(monkeypatch, INTL_PACKAGE, installed=both, running=both) == INTL_PACKAGE
    assert resolved(monkeypatch, INTL_PACKAGE, installed=both) == INTL_PACKAGE


def test_resolve_package_other_servers(monkeypatch):
    """日服或自己填的其他包名：不查也不改。"""
    calls = fake_adb(monkeypatch, installed=(INTL_PACKAGE, INTL_PLAY_PACKAGE))
    for package in (JP_PACKAGE, "com.example.game"):
        cfg = DeviceConfig(package=package)
        adb.resolve_package(cfg)
        assert cfg.package == package
    assert calls == []


def test_resolve_package_adb_error(monkeypatch):
    """adb 出错时不改，留给之后启动 / 连接时报错。"""
    for error in (FileNotFoundError("adb.exe"), subprocess.TimeoutExpired("adb", 15)):
        assert resolved(monkeypatch, INTL_PACKAGE, error=error) == INTL_PACKAGE


def test_start_app_not_installed(monkeypatch):
    """游戏没装时说清楚，而不是抛 monkey 的退出码（#62）"""
    from ournotes_auto.context import SetupError

    fake_adb(monkeypatch, installed=(INTL_PLAY_PACKAGE,))
    assert adb.start_app(DeviceConfig(package=INTL_PLAY_PACKAGE))
    with pytest.raises(SetupError, match=f"没装 {INTL_PACKAGE}"):
        adb.start_app(DeviceConfig(package=INTL_PACKAGE))


def test_start_app_brings_background_game_to_front(monkeypatch):
    """游戏在运行但模拟器停在桌面：用 monkey 切回前台（不 force-stop）；已在前台或读不到焦点窗口时什么都不做。"""
    cfg = DeviceConfig(package=INTL_PACKAGE)
    calls = fake_adb(monkeypatch, installed=(INTL_PACKAGE,), running=(INTL_PACKAGE,), focus="app.lawnchair")
    assert adb.start_app(cfg)
    assert [c[0] for c in calls] == ["pidof", "dumpsys", "monkey"]
    for focus in (INTL_PACKAGE, None):
        calls = fake_adb(monkeypatch, installed=(INTL_PACKAGE,), running=(INTL_PACKAGE,), focus=focus)
        assert not adb.start_app(cfg)
        assert "monkey" not in [c[0] for c in calls]


DAEMON_FAILED = b"* daemon not running; starting now at tcp:5037\n* failed to start daemon\n"
NO_PORTS = (
    "--- adb starting (pid 68020) ---\n"
    "11-01 14:04:03.691 68020 27984 F adb     : fdevent_poll.cpp:64 failed to create fdevent interrupt socketpair: "
    "no buffer space\n"
)


def fake_connect(monkeypatch, tmp_path, fails, log="", hog=""):
    """假的 adb connect：前 ``fails`` 次失败（退出码 1，输出 adb 起不来）。adb 日志的内容是 ``log``，
    连接最多的进程是 ``hog``。返回执行过的命令。"""
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if len(calls) <= fails:
            raise subprocess.CalledProcessError(1, cmd, b"", DAEMON_FAILED)
        return subprocess.CompletedProcess(cmd, 0, b"connected to 127.0.0.1:16416\n", b"")

    (tmp_path / "adb.log").write_text(log, encoding="utf-8")
    monkeypatch.setattr(adb, "ADB_LOG", tmp_path / "adb.log")
    monkeypatch.setattr(adb.subprocess, "run", run)
    monkeypatch.setattr(adb.time, "sleep", lambda s: None)
    monkeypatch.setattr(adb, "_port_hog", lambda: hog)
    return calls


def test_connect_retries(monkeypatch, tmp_path):
    """adb 后台服务起不来时 connect 退出码为 1：隔一会儿重试。"""
    calls = fake_connect(monkeypatch, tmp_path, fails=adb.CONNECT_TRIES - 1)
    adb.connect("adb.exe", "127.0.0.1:16416", 15)
    assert calls == [["adb.exe", "connect", "127.0.0.1:16416"]] * adb.CONNECT_TRIES


def test_connect_error_shows_output(monkeypatch, tmp_path):
    """都失败时报错里有 adb 的输出（原来只有「returned non-zero exit status 1」）；仍是 SubprocessError，原来接住它的地方照旧。"""
    fake_connect(monkeypatch, tmp_path, fails=99, log="--- adb starting (pid 1) ---\n")
    with pytest.raises(adb.AdbError) as e:
        adb.connect("adb.exe", "127.0.0.1:16416", 15)
    assert str(e.value) == "adb connect 127.0.0.1:16416 失败：* daemon not running; starting now at tcp:5037 / * failed to start daemon"
    assert isinstance(e.value, subprocess.SubprocessError)


def test_connect_error_ports_exhausted(monkeypatch, tmp_path):
    """adb 日志里最近一次启动时报 no buffer space：说明是本机的网络端口用光了，带上连接最多的进程。"""
    fake_connect(monkeypatch, tmp_path, fails=99, log="old\n" + NO_PORTS, hog="Leaky.exe（PID 1234，14094 个连接）")
    with pytest.raises(adb.AdbError, match=r"网络端口用光了.*「no buffer space」.*连接最多的是 Leaky\.exe（PID 1234，14094 个连接）"):
        adb.connect("adb.exe", "127.0.0.1:16416", 15)
    fake_connect(monkeypatch, tmp_path, fails=99, log=NO_PORTS)  # 查不到是哪个进程
    with pytest.raises(adb.AdbError, match="网络端口用光了.*重启电脑后恢复"):
        adb.connect("adb.exe", "127.0.0.1:16416", 15)
    # 后台服务早就起来了，之后端口用光：建服务连接失败的那行不带原因
    log = "--- adb starting (pid 5) ---\ncannot create service socket pair\n"
    fake_connect(monkeypatch, tmp_path, fails=99, log=log, hog="Leaky.exe（PID 1234，14094 个连接）")
    with pytest.raises(adb.AdbError, match="网络端口用光了.*「cannot create service socket pair」"):
        adb.connect("adb.exe", "127.0.0.1:16416", 15)


@pytest.mark.parametrize(
    "log",
    [
        "",
        NO_PORTS + "--- adb starting (pid 2) ---\nok\n",
        # socketpair 失败但不是端口用光（安全软件拦了本机回环连接）
        "--- adb starting (pid 3) ---\nF adb     : failed to create fdevent interrupt socketpair: permission denied\n",
    ],
)
def test_ports_hint_only_latest_start(monkeypatch, tmp_path, log):
    """以前哪次启动时端口用光过、没有日志、或者是别的原因：不提端口。"""
    fake_connect(monkeypatch, tmp_path, fails=0, log=log, hog="Leaky.exe（PID 1234，3 个连接）")
    if not log:
        (tmp_path / "adb.log").unlink()
    assert adb.ports_hint() == ""


def test_port_hog(monkeypatch):
    """netstat 里 TCP 连接最多的 PID，再用 tasklist 查它的名字；TIME_WAIT 记在 PID 0 名下，不算。"""
    netstat = (
        "\r\nActive Connections\r\n\r\n  Proto  Local Address          Foreign Address        State           PID\r\n"
        "  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       99\r\n"
        + "  TCP    127.0.0.1:50001        127.0.0.1:443          CLOSE_WAIT      1234\r\n" * 3
        + "  TCP    127.0.0.1:50002        127.0.0.1:443          TIME_WAIT       0\r\n" * 5
        + "  TCP    0.0.0.0:445            0.0.0.0:0              LISTENING       4\r\n" * 4
    )
    tasklist = '"Leaky.exe","1234","Console","1","10,000 K"\r\n'

    def run(cmd, **kw):
        out = netstat if cmd[0] == "netstat" else tasklist if cmd[2] == "PID eq 1234" else "INFO: none\r\n"
        return subprocess.CompletedProcess(cmd, 0, out.encode("ascii"), b"")

    monkeypatch.setattr(adb.subprocess, "run", run)
    assert adb._port_hog() == "Leaky.exe（PID 1234，3 个连接）"
    tasklist = "INFO: No tasks are running which match the specified criteria.\r\n"  # 进程刚好退出了
    assert adb._port_hog() == "未知进程（PID 1234，3 个连接）"

    def broken(cmd, **kw):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(adb.subprocess, "run", broken)
    assert adb._port_hog() == ""
