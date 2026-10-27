"""「统计与成就」窗口：用 Edge（没有时用 Chrome）的应用模式打开统计页，像一个单独的程序窗口；
都没有时用默认浏览器打开 ``data/stats.html``。已经开着时切到前面，不再开一个。

窗口打开的是背后的本机服务给的页面（见 stats_server.py），页面上才能导出、导入备份；服务起不来时直接打开
``data/stats.html``，别的都一样。

发布包根目录下放一个「统计与成就」快捷方式（:func:`ensure_shortcut`），双击就能打开，不用先打开界面。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

from . import release

logger = logging.getLogger(__name__)

TITLE = "统计与成就 · Our Notes Auto"  # 页面标题，按它找已经开着的窗口（页面不能改标题）
PROFILE = Path("cache", "stats_window")  # 窗口专用的浏览器配置目录（记住窗口大小位置，不碰用户平时的浏览器）
WINDOW_SIZE = "1280,880"  # 第一次打开时的窗口大小，之后浏览器自己记住
SIZED = Path("cache", "stats_window_sized.txt")  # 给过窗口大小了（浏览器按地址记窗口位置，换成服务的地址后要再给一次）
SERVE_WAIT_S = 15.0  # 等窗口背后的服务起来最多多久
SHORTCUT = "统计与成就.lnk"
SHORTCUT_MARK = Path("cache", "stats_shortcut.txt")  # 建快捷方式时包在哪里（包挪了位置要重建）
BROWSERS = (
    ("msedge.exe", Path("Microsoft", "Edge", "Application", "msedge.exe")),
    ("chrome.exe", Path("Google", "Chrome", "Application", "chrome.exe")),
)
_DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
_BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB


def find_browser() -> Path | None:
    """Edge 或 Chrome 的程序路径：先查注册表 App Paths，再查常见的安装位置。"""
    if sys.platform != "win32":
        return None
    import winreg

    for exe, rel in BROWSERS:
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            with suppress(OSError):
                with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                    path = Path(str(winreg.QueryValueEx(k, "")[0]).strip('"'))
                if path.is_file():
                    return path
        for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base and (Path(base) / rel).is_file():
                return Path(base) / rel
    return None


def is_stats_window(cls: str, title: str) -> bool:
    """是不是统计窗口：只认应用模式的窗口（标题就是页面标题）。浏览器普通窗口的标题后面还有浏览器名，
    那是某个浏览器碰巧在看统计页，不算——否则切过去（还常常切不到前台）就不开窗口了。"""
    return cls == "Chrome_WidgetWin_1" and title == TITLE


def find_window() -> int:
    """已经开着的统计窗口的句柄，没有时为 0。"""
    if sys.platform != "win32":
        return 0
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    found = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def check(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        text = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, text, 256)
        if is_stats_window(cls.value, text.value):
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(proc(check), 0)
    return found[0] if found else 0


def focus(hwnd: int) -> None:
    """切到前面（最小化了先还原）。后台进程不一定能抢到前台，抢不到时任务栏按钮会闪。"""
    import ctypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    if not user32.SetForegroundWindow(hwnd):
        user32.SwitchToThisWindow(hwnd, True)


def _wmi_start(cmd: str, cwd: Path) -> bool:
    """通过 WMI 启动（父进程是 WMI，不在界面的作业对象里）：MFAA 把 Agent 放进不允许脱离的作业对象，
    关界面时连同子进程一起结束。做法同 :func:`.updater.escape_job`。"""
    script = (
        "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create"
        " -Arguments @{CommandLine=$env:OURNOTES_CMD; CurrentDirectory=$env:OURNOTES_CWD}; exit [int]$r.ReturnValue"
    )
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            env={**os.environ, "OURNOTES_CMD": cmd, "OURNOTES_CWD": str(cwd)},
            capture_output=True,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("通过 WMI 打开统计窗口失败：%s", e)
        return False
    if r.returncode:
        logger.debug("通过 WMI 打开统计窗口失败（%d）：%s", r.returncode, r.stderr.decode("mbcs", "replace").strip()[-500:])
    return r.returncode == 0


def _spawn(args: list[str], cwd: Path) -> subprocess.Popen | bool:
    """启动一个不随界面一起结束的进程，返回进程（通过 WMI 启动时为 True）；失败为 False。"""
    from .updater import in_job

    jailed = in_job()
    try:
        return subprocess.Popen(
            args,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=_DETACHED | (_BREAKAWAY if jailed else 0),
        )
    except OSError as e:
        if not jailed:
            logger.debug("启动 %s 失败：%s", Path(args[0]).name, e)
            return False
    return _wmi_start(subprocess.list2cmdline(args), cwd)  # 作业对象不允许脱离


def launch(browser: Path, url: str, root: Path) -> bool:
    """用应用模式打开 ``url``，不随界面一起结束。成功返回 True。"""
    sized = root / SIZED
    args = [str(browser), f"--app={url}", f"--user-data-dir={root / PROFILE}", "--no-first-run", "--no-default-browser-check"]
    if not sized.exists():
        args.append(f"--window-size={WINDOW_SIZE}")
    if not _spawn(args, browser.parent):
        return False
    with suppress(OSError):
        sized.parent.mkdir(parents=True, exist_ok=True)
        sized.write_text(url.partition("?")[0], encoding="utf-8")
    return True


def start_server(page: Path, root: Path) -> str | None:
    """起窗口背后的服务（见 stats_server.py），返回窗口要打开的地址；起不来时为 None（直接打开页面文件，
    只是用不了导出、导入）。"""
    ready = root / "cache" / f"stats_serve_{os.getpid()}_{time.monotonic_ns()}.json"
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")  # 通过 WMI 启动 python.exe 会弹出控制台窗口
    proc = _spawn([str(pythonw if pythonw.is_file() else exe), "-m", "ournotes_auto", "stats-serve", "--ready", str(ready)], root)
    deadline = time.monotonic() + SERVE_WAIT_S
    while proc and time.monotonic() < deadline:
        time.sleep(0.1)
        info = release.read_json(ready)
        if isinstance(info, dict) and isinstance(info.get("port"), int) and isinstance(info.get("key"), str):
            with suppress(OSError):
                ready.unlink()
            return f"http://127.0.0.1:{info['port']}/{page.name}?key={info['key']}"
        if isinstance(proc, subprocess.Popen) and proc.poll() is not None:
            logger.debug("统计窗口服务没起来（退出码 %s）", proc.returncode)
            break
    else:
        if proc:
            logger.debug("等了 %.0f 秒统计窗口服务还没起来", SERVE_WAIT_S)
    return None


def open_window(page: Path, root: Path, wait_s: float = 10.0) -> str:
    """打开统计窗口，返回怎么打开的："focused"（已经开着，切到前面）、"window"（新开了窗口）、
    "browser"（没有 Edge / Chrome，用默认浏览器打开）。"""
    page = page.resolve()
    if sys.platform != "win32":
        import webbrowser

        webbrowser.open(page.as_uri())
        return "browser"
    hwnd = find_window()
    if hwnd:
        focus(hwnd)
        return "focused"
    browser = find_browser()
    root = root.resolve()
    if browser is None or not launch(browser, start_server(page, root) or page.as_uri(), root):
        os.startfile(str(page))  # noqa: S606 - 本地生成的页面
        return "browser"
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:  # 等窗口出来再切到前面
        time.sleep(0.2)
        hwnd = find_window()
        if hwnd:
            focus(hwnd)
            break
    return "window"


def ensure_shortcut(root: Path) -> bool:
    """发布包根目录下没有「统计与成就」快捷方式（或包挪了位置）时建一个，建了返回 True。"""
    if sys.platform != "win32" or release.read_release(root) is None:
        return False
    root = root.resolve()
    lnk, mark = root / SHORTCUT, root / SHORTCUT_MARK
    with suppress(OSError):
        if lnk.exists() and mark.read_text(encoding="utf-8") == str(root):
            return False
    pythonw = root / "python" / "pythonw.exe"
    if not pythonw.is_file():
        return False
    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:OURNOTES_LNK);"
        "$s.TargetPath=$env:OURNOTES_EXE;$s.Arguments='-m ournotes_auto stats-window';"
        "$s.WorkingDirectory=$env:OURNOTES_ROOT;$s.IconLocation=$env:OURNOTES_ICON;"
        "$s.Description=$env:OURNOTES_DESC;$s.Save()"
    )
    env = {
        **os.environ,
        "OURNOTES_LNK": str(lnk),
        "OURNOTES_EXE": str(pythonw),
        "OURNOTES_ROOT": str(root),
        "OURNOTES_ICON": f"{root / 'docs' / 'ui' / 'logo.ico'},0",
        "OURNOTES_DESC": "Our Notes Auto 统计与成就",
    }
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env=env,
        capture_output=True,
        timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if r.returncode or not lnk.exists():
        raise OSError(f"建快捷方式失败（{r.returncode}）：{r.stderr.decode('mbcs', 'replace').strip()[-300:]}")
    mark.parent.mkdir(parents=True, exist_ok=True)
    mark.write_text(str(root), encoding="utf-8")
    return True
