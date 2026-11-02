"""经 adb 启动 / 重启游戏。只连配置里的模拟器实例，不会碰 USB 真机。"""

from __future__ import annotations

import csv
import logging
import re
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path

from ..config import INTL_PACKAGES, INTL_PLAY_PACKAGE, DeviceConfig

logger = logging.getLogger(__name__)

# adb 的后台服务没在运行时 connect 会先启动它，启动失败时退出码为 1（比如别的程序的 adb 正好在重启它）：隔一会儿再试
CONNECT_TRIES = 3
CONNECT_RETRY_S = 2.0
# adb 后台服务的日志（Windows 上在 %TEMP%）：本机的网络端口用光时它建不了内部连接，日志里是这两种
# （安全软件拦了本机回环连接时 socketpair 也会失败，但报的是别的原因，不算）
ADB_LOG = Path(tempfile.gettempdir()) / "adb.log"
ADB_LOG_TAIL = 16384
PORTS_GONE = re.compile(r"no buffer space|cannot create service socket ?pair", re.I)
# netstat 里 TIME_WAIT 的连接记在 PID 0 名下，PID 4 是 System：都结束不了，不算
SYSTEM_PIDS = ("0", "4")


class AdbError(subprocess.SubprocessError):
    """adb 命令失败：消息里带上 adb 的输出和看得出来的原因。"""


def connect(adb: str, serial: str, timeout_s: float) -> None:
    """``adb connect``，失败时隔一会儿重试，都失败时抛 :class:`AdbError`。"""
    for attempt in range(1, CONNECT_TRIES + 1):
        try:
            subprocess.run([adb, "connect", serial], check=True, capture_output=True, timeout=timeout_s)
            return
        except subprocess.CalledProcessError as e:
            if attempt == CONNECT_TRIES:
                raise AdbError(f"adb connect {serial} 失败：{_output(e)}{ports_hint()}") from e
            logger.debug("adb connect %s 失败（%s），%.0fs 后重试", serial, _output(e), CONNECT_RETRY_S)
            time.sleep(CONNECT_RETRY_S)


def _output(e: subprocess.CalledProcessError) -> str:
    """adb 的输出连成一行；没有输出时为退出码。"""
    raw = b"".join(x for x in (e.stdout, e.stderr) if isinstance(x, bytes))
    lines = [s.strip() for s in raw.decode("utf-8", "replace").splitlines() if s.strip()]
    return " / ".join(lines) or f"退出码 {e.returncode}"


def ports_hint() -> str:
    """adb 后台服务最近一次启动时因为本机的网络端口用光而失败时的说明（带上连接最多的进程），否则为空串。
    端口用光时电脑上要联网的程序都会时不时失败，模拟器也可能连不上网，光重试没用（实测是别的程序的连接
    一直不关，占满了 Windows 的临时端口）。"""
    try:
        with ADB_LOG.open("rb") as f:
            f.seek(max(0, f.seek(0, 2) - ADB_LOG_TAIL))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    m = PORTS_GONE.search(tail.rsplit("--- adb starting", 1)[-1])
    if not m:
        return ""
    hog = _port_hog()
    who = f"连接最多的是 {hog}，结束它或重启电脑后恢复" if hog else "重启电脑后恢复"
    return f"。本机的网络端口用光了，adb 启动不了（{ADB_LOG} 里有「{m.group(0)}」）：{who}"


def _port_hog() -> str:
    """TCP 连接最多的进程「名字（PID，连接数）」（不算系统进程），查不到时为空串。"""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, timeout=15).stdout
        rows = (line.split() for line in out.decode("ascii", "replace").splitlines())
        counts = Counter(r[-1] for r in rows if len(r) >= 4 and r[0] == "TCP" and r[-1] not in SYSTEM_PIDS)
        if not counts:
            return ""
        pid, n = counts.most_common(1)[0]
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, timeout=15)
        line = out.stdout.decode("oem", "replace").strip()
    except (OSError, subprocess.SubprocessError, LookupError):
        return ""
    name = next(csv.reader([line]))[0] if line.startswith('"') else ""
    return f"{name or '未知进程'}（PID {pid}，{n} 个连接）"


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

    connect(adb, serial, timeout_s)
    return serial, run


MONKEY_NO_APP = 252  # monkey 找不到这个包名能启动的界面（没装）时退出码是 -4


def _launch(run, serial: str, package: str) -> None:
    try:
        run("-s", serial, "shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1")
    except subprocess.CalledProcessError as e:
        if e.returncode != MONKEY_NO_APP:
            raise
        from ..context import SetupError

        raise SetupError(f"模拟器（{serial}）里没装 {package}，启动不了：确认游戏装在这个模拟器里、选的服和装的一致") from e


def _running(run, serial: str, package: str) -> bool:
    return bool(run("-s", serial, "shell", "pidof", package, check=False).stdout.strip())


def _in_front(run, serial: str, package: str) -> bool:
    """游戏的界面在前台（获得焦点的窗口属于它）；读不到时当作在前台。"""
    out = run("-s", serial, "shell", "dumpsys", "window", check=False).stdout.decode("utf-8", "replace")
    m = re.search(r"mCurrentFocus=Window\{\S+ \S+ ([^\s/}]+)", out)
    return m is None or m.group(1) == package


def app_running(cfg: DeviceConfig, timeout_s: float = 15.0) -> bool:
    serial, run = _runner(cfg, timeout_s)
    return _running(run, serial, cfg.package)


def app_in_front(cfg: DeviceConfig, timeout_s: float = 15.0) -> bool:
    serial, run = _runner(cfg, timeout_s)
    return _in_front(run, serial, cfg.package)


def start_app(cfg: DeviceConfig, timeout_s: float = 30.0) -> bool:
    """游戏没在运行时启动它，在后台时切回前台（不重启），返回是否启动或切换了。"""
    serial, run = _runner(cfg, timeout_s)
    if _running(run, serial, cfg.package):
        if _in_front(run, serial, cfg.package):
            return False
        logger.info("游戏在后台，经 adb（%s）切回前台", serial)
    else:
        logger.info("经 adb（%s）启动 %s", serial, cfg.package)
    _launch(run, serial, cfg.package)
    return True


def resolve_package(cfg: DeviceConfig, timeout_s: float = 15.0) -> None:
    """国际服有两个包名（官网 / B 站版、Google Play 版），配置的是其中一个时按模拟器里装的改 ``cfg.package``：
    配置的没装、另一个装了，或者两个都装了、只有另一个在运行，就改用另一个。adb 出错时不改。"""
    if cfg.package not in INTL_PACKAGES:
        return
    other = next(p for p in INTL_PACKAGES if p != cfg.package)
    try:
        serial, run = _runner(cfg, timeout_s)
        # 两个包名都以 Google Play 版的开头
        out = run("-s", serial, "shell", "pm", "list", "packages", INTL_PLAY_PACKAGE, check=False).stdout
        installed = set(re.findall(r"^package:(\S+)", out.decode("utf-8", "replace"), re.M))
        if other not in installed:
            return
        if cfg.package in installed:
            if _running(run, serial, cfg.package) or not _running(run, serial, other):
                return  # 两个都装了：用在运行的那个，都没在运行时照配置
            logger.info("国际服两个版本都装了，正在运行的是 %s，改用它（配置的是 %s）", other, cfg.package)
        else:
            logger.info("模拟器里没装 %s，改用国际服的另一个版本 %s", cfg.package, other)
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("检查装了哪个版本的国际服失败：%s", e)
        return
    cfg.package = other


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
