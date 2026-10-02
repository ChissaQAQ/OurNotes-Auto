"""经 adb 启动 / 重启游戏。只连配置里的模拟器实例，不会碰 USB 真机。"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from ..config import DeviceConfig

logger = logging.getLogger(__name__)


def adb_path(cfg: DeviceConfig) -> str:
    if cfg.adb_path:
        return cfg.adb_path
    for sub in ("nx_main", "shell"):  # MuMu 12 新版 / 旧版
        p = Path(cfg.mumu_path) / sub / "adb.exe"
        if p.is_file():
            return str(p)
    raise FileNotFoundError(f"在 {cfg.mumu_path} 下找不到 adb.exe，请配置 device.adb_path")


def adb_serial(cfg: DeviceConfig) -> str:
    return cfg.adb_serial or f"127.0.0.1:{16384 + 32 * cfg.instance}"


def _runner(cfg: DeviceConfig, timeout_s: float):
    adb, serial = adb_path(cfg), adb_serial(cfg)

    def run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run([adb, *args], check=check, capture_output=True, timeout=timeout_s)

    run("connect", serial)
    return serial, run


def _launch(run, serial: str, package: str) -> None:
    run("-s", serial, "shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1")


def _running(run, serial: str, package: str) -> bool:
    return bool(run("-s", serial, "shell", "pidof", package, check=False).stdout.strip())


def app_running(cfg: DeviceConfig, timeout_s: float = 15.0) -> bool:
    serial, run = _runner(cfg, timeout_s)
    return _running(run, serial, cfg.package)


def start_app(cfg: DeviceConfig, timeout_s: float = 30.0) -> bool:
    """游戏没在运行时启动它，返回是否启动了。"""
    serial, run = _runner(cfg, timeout_s)
    if _running(run, serial, cfg.package):
        return False
    logger.info("经 adb（%s）启动 %s", serial, cfg.package)
    _launch(run, serial, cfg.package)
    return True


def versions(cfg: DeviceConfig, timeout_s: float = 10.0) -> tuple[str, str]:
    """(Android 版本, 游戏版本)，读不到的为空串。"""
    serial, run = _runner(cfg, timeout_s)
    cmd = f"getprop ro.build.version.release; dumpsys package {cfg.package} 2>/dev/null | grep -m1 versionName"
    out = run("-s", serial, "shell", cmd, check=False).stdout.decode("utf-8", "replace")
    lines = out.split()
    m = re.search(r"versionName=(\S+)", out)
    return (lines[0] if lines and "=" not in lines[0] else ""), (m.group(1) if m else "")


def restart_app(cfg: DeviceConfig, timeout_s: float = 30.0) -> None:
    serial, run = _runner(cfg, timeout_s)
    logger.warning("经 adb（%s）重启 %s", serial, cfg.package)
    run("-s", serial, "shell", "am", "force-stop", cfg.package)
    _launch(run, serial, cfg.package)
