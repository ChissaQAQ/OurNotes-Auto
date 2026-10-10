import subprocess

from ournotes_auto.config import INTL_PACKAGE, INTL_PLAY_PACKAGE, JP_PACKAGE, DeviceConfig
from ournotes_auto.device import adb


def fake_adb(monkeypatch, installed=(), running=(), error=None):
    """假的 adb：``pm list packages <过滤>`` 列出 ``installed`` 里含过滤串的，``pidof`` 只对 ``running`` 里的有输出；
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
