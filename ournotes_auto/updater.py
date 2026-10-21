"""后台更新器：下载新版本，等合适的时机换上，需要时重启界面。由 Agent 启动（ournotes_auto/agent/update.py）::

    cache/update/runner/pythonw.exe cache/update/runner/updater.py <包根目录>

用复制到 runner 目录的嵌入式 Python 运行、只用标准库，界面和 Agent 退出、整包替换 python 目录都不影响它；
启动后发现自己在界面的作业对象里（MFAA）就通过 WMI 重新启动一个脱离出去（:func:`escape_job`）。
按 Agent 缓存的 update.json（cache/update/manifest.json）和本地的 release.json 分档（release.tier）：

- 热更新：没有演奏进程时（busy 目录里的锁都没人锁）替换文件，下个任务直接用新代码；挂机由 Agent 在安全的时机重启演奏进程。
- 重启更新、整包更新：界面没有任务、闲置 2 分钟后关掉界面，替换后重新打开（不带自动执行）；开着「启动时自动执行」时
  不重启界面，等用户关掉界面再换；界面没开时直接换。

替换前把旧文件移到 cache/update/backup/<旧版本>，换完用新代码试导入一次，失败就还原并记进 failed.json（不再自动重试）。
日志写 data/update.log，进度写 cache/update/status.json（Agent 读了在界面日志里提示）。最多等 24 小时，之后由 Agent 再启动。
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

try:
    from . import release
except ImportError:  # 从 runner 目录直接运行
    import release  # type: ignore[no-redef]

logger = logging.getLogger("ournotes_auto.updater")

LOG_FILE = "data/update.log"
LOG_MAX_BYTES = 1 << 20
AUTO_OFF_FILE = f"{release.UPDATE_DIR}/auto_off"  # 和 agent/update.py 一致
BACKUP_DIR = f"{release.UPDATE_DIR}/backup"
MAX_WAIT_S = 24 * 3600
IDLE_S = 120.0  # 界面闲置（没有任务、用户没在操作）多久后重启它
POLL_S = 2.0
EXIT_WAIT_S = 15.0  # 关掉界面后等包里的进程退出多久，之后强制结束
RENAME_TRIES = 20
SELFTEST = "import ournotes_auto.agent.server, ournotes_auto.cli, ournotes_auto.commands, ournotes_auto.runner"
SELFTEST_TIMEOUT_S = 180.0
PYCACHE_DIRS = ("agent", "ournotes_auto")
USER_AGENT = "OurNotes-Auto-updater"
ESCAPED = "--escaped"  # 已经脱离界面的作业对象（escape_job 启动的）


class UpdateError(Exception):
    """暂时更新不了（网络、磁盘空间等），过一阵 Agent 会重新启动更新器再试。"""


class RollbackError(UpdateError):
    """自动还原失败，保留备份并停止自动重试。"""


# ---------------------------------------------------------------- 下载、解压


def download(
    url: str,
    dest: Path,
    size: int,
    sha256: str,
    progress: Callable[[float], None] = lambda f: None,
    tries: int = 6,
    timeout_s: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """下载到 ``dest``，断了从 ``.part`` 续传；大小和 sha256 对不上时删掉重下。"""
    if dest.is_file() and dest.stat().st_size == size and release.sha256_file(dest) == sha256:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    last_error: Exception | None = None
    for attempt in range(tries):
        if attempt:
            sleep(min(60.0, 5.0 * 2 ** (attempt - 1)))
        have = part.stat().st_size if part.is_file() else 0
        if have > size:
            part.unlink()
            have = 0
        if have < size:
            headers = {"User-Agent": USER_AGENT, **({"Range": f"bytes={have}-"} if have else {})}
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout_s) as resp:
                    if have and resp.status != 206:  # 服务器不支持续传，从头下
                        have = 0
                    with open(part, "ab" if have else "wb") as f:
                        while chunk := resp.read(1 << 16):
                            f.write(chunk)
                            have += len(chunk)
                            progress(min(have / size, 1.0) if size else 1.0)
            except urllib.error.HTTPError as e:
                last_error = e
                if e.code == 416:  # 续传的位置不对
                    part.unlink(missing_ok=True)
                logger.warning("下载 %s 出错：%s", url, e)
                continue
            except (OSError, ValueError, http.client.HTTPException) as e:  # 网络断开、超时等，下次续传
                last_error = e
                logger.warning("下载 %s 出错：%s", url, e)
                continue
        got = part.stat().st_size if part.is_file() else 0
        if got < size:
            last_error = UpdateError(f"只下载了 {got}/{size} 字节")
            logger.warning("%s 没下载完（%d/%d），继续下载", url, got, size)
            continue
        if got == size and release.sha256_file(part) == sha256:
            os.replace(part, dest)
            return
        last_error = UpdateError("下载的文件校验不对")
        logger.warning("%s 的大小或 sha256 不对，重新下载", url)
        part.unlink(missing_ok=True)
    raise UpdateError(f"下载失败：{last_error}")


def extract(archive: Path, stage: Path) -> None:
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            rel = release.safe_rel(release.zip_name(info))
            if rel is None or info.is_dir():
                continue
            dst = stage / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(dst, "wb") as out:
                shutil.copyfileobj(src, out)


def verify_stage(stage: Path, manifest: dict, full: bool, ui: str) -> None:
    """解压出来的文件和 update.json 的清单逐个核对；整包另外核对 release.json。"""
    for rel, digest in manifest["files"].items():
        path = stage / rel
        if not path.is_file() or release.file_digest(path, rel) != digest:
            raise UpdateError(f"新版本的文件 {rel} 和清单对不上")
    if full:
        info = release.read_release(stage)
        if info is None or info.get("version") != manifest["version"] or info.get("ui") != ui:
            raise UpdateError("整包里的 release.json 不对")


# ---------------------------------------------------------------- 替换


def _rename(src: Path, dst: Path, sleep: Callable[[float], None] = time.sleep) -> None:
    """改名；文件刚被关掉的进程占着时重试一会儿。"""
    for attempt in range(RENAME_TRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == RENAME_TRIES - 1:
                raise
            sleep(0.5)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".new")
    shutil.copy2(src, tmp)
    _rename(tmp, dst)


class FilesApply:
    """热更新、重启更新：只换 app 层改动的文件，旧文件备份到 ``backup``，失败时 :meth:`rollback`。"""

    def __init__(self, root: Path, stage: Path, local: dict, manifest: dict, backup: Path):
        self.root, self.stage, self.local, self.manifest, self.backup = root, stage, local, manifest, backup
        self.saved: list[str] = []  # 备份了的（原来就有的）
        self.added: list[str] = []  # 原来没有的

    def _save(self, rel: str) -> None:
        path = self.root / rel
        if path.is_file():
            _copy(path, self.backup / rel)
            self.saved.append(rel)
        else:
            self.added.append(rel)

    def run(self) -> None:
        root, new = self.root, self.manifest["files"]
        # 和磁盘上的实际内容比（上次换到一半中断、用户改过的也会换回来）
        changed = [rel for rel, digest in new.items() if _disk_digest(root, rel) != digest]
        removed = [rel for rel in self.local["files"] if rel not in new and (root / rel).is_file()]
        logger.info("替换 %d 个文件，删除 %d 个", len(changed), len(removed))
        if self.backup.exists():
            shutil.rmtree(self.backup)
        for rel in sorted({*changed, *removed, "interface.json", release.RELEASE_FILE}):
            self._save(rel)
        for rel in changed:
            if rel != "interface.json":
                _copy(self.stage / rel, root / rel)
        for rel in removed:
            (root / rel).unlink()
        # interface.json 每次都按本地的界面重新生成（版本号、Agent 启动命令、MXU 的设置分组）
        data = json.loads((self.stage / "interface.json").read_text(encoding="utf-8"))
        data = release.render_interface(keep_user_fields(data, root / "interface.json"), self.local["ui"], self.manifest["version"])
        (root / "interface.json").write_text(release.dump_json(data), encoding="utf-8")
        top = set(self.local.get("top") or ()) | {rel.split("/")[0] for rel in new}
        info = {**self.local, "version": self.manifest["version"], "files": new, "top": sorted(top)}
        release.write_json(root / release.RELEASE_FILE, info)
        clear_pycache(root)

    def rollback(self) -> None:
        for rel in self.added:
            (self.root / rel).unlink(missing_ok=True)
        for rel in self.saved:
            _copy(self.backup / rel, self.root / rel)
        clear_pycache(self.root)


class FullApply:
    """整包更新：旧包的条目（release.json 的 ``top``）整个移到 ``backup``，新包的条目移进来。

    用户数据（config、data、cache、debug）和界面运行时自己建的（MFAA 的 appsettings.json、logs 等，不在旧包里）不动。
    """

    def __init__(self, root: Path, stage: Path, local: dict, backup: Path):
        self.root, self.stage, self.local, self.backup = root, stage, local, backup
        self.moved_out: list[str] = []
        self.moved_in: list[str] = []

    def run(self) -> None:
        root = self.root
        old = set(self.local.get("top") or ()) - release.USER_DIRS
        new = [p.name for p in sorted(self.stage.iterdir()) if p.name not in release.USER_DIRS]
        preserved = {name for name in new if name not in old and (root / name).exists()}
        new = [name for name in new if name in old or not (root / name).exists()]
        info = release.read_release(self.stage)
        if info is not None and preserved:
            # 保留的用户文件不能记成新包拥有的条目，否则下次整包更新会把它们移走。
            info["top"] = sorted(set(info.get("top") or ()) - preserved)
            release.write_json(self.stage / release.RELEASE_FILE, info)
        if self.backup.exists():
            shutil.rmtree(self.backup)
        self.backup.mkdir(parents=True)
        for name in sorted(old):
            if (root / name).exists() or (root / name).is_symlink():
                _rename(root / name, self.backup / name)
                self.moved_out.append(name)
        for name in new:
            _rename(self.stage / name, root / name)
            self.moved_in.append(name)
        # 运行时按需下载的其他语言 OCR 模型（新包里没有）留着
        old_ocr, new_ocr = self.backup / "resource/model/ocr", root / "resource/model/ocr"
        if old_ocr.is_dir() and new_ocr.is_dir():
            for d in old_ocr.iterdir():
                if d.is_dir() and not (new_ocr / d.name).exists():
                    shutil.copytree(d, new_ocr / d.name)
        iface = root / "interface.json"
        if "interface.json" in self.moved_in and iface.is_file():
            try:
                data = json.loads(iface.read_text(encoding="utf-8"))
                kept = keep_user_fields(data, self.backup / "interface.json")
                if kept != data:
                    release.write_json(iface, kept)
            except (OSError, ValueError) as e:  # 只是没保留住用户的字段，不影响更新
                logger.warning("interface.json 没能保留用户加的字段：%s", e)

    def rollback(self) -> None:
        for name in self.moved_in:
            _remove(self.root / name)
        for name in self.moved_out:
            _rename(self.backup / name, self.root / name)


def _disk_digest(root: Path, rel: str) -> str | None:
    """磁盘上 ``rel`` 的摘要；没有或读不了（如 interface.json 被改坏）时为 None，当作要换。"""
    try:
        return release.file_digest(root / rel, rel) if (root / rel).is_file() else None
    except (OSError, ValueError, AttributeError):
        return None


def keep_user_fields(data: dict, old: Path) -> dict:
    """新的 interface.json ``data`` 补上旧文件 ``old`` 里用户自己加的顶层字段（新版本里没有的，如 ``github``）。

    新版本里有的字段和打包时生成的 MXU 设置分组用新的；旧文件读不了时原样返回 ``data``。
    """
    try:
        local = json.loads(old.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return data
    if not isinstance(local, dict):
        return data
    extra = {k: v for k, v in local.items() if k not in data and k != "setting"}
    if extra:
        logger.info("interface.json 保留用户加的字段：%s", "、".join(extra))
    return {**data, **extra}


def clear_pycache(root: Path) -> None:
    for name in PYCACHE_DIRS:
        for d in (root / name).rglob("__pycache__"):
            shutil.rmtree(d, ignore_errors=True)


def self_test(root: Path) -> str | None:
    """用新代码试导入一次；失败时返回错误信息。"""
    try:
        r = subprocess.run(
            [str(root / release.AGENT_EXEC), "-c", SELFTEST],
            cwd=root,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=SELFTEST_TIMEOUT_S,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return str(e)
    if r.returncode:
        return r.stderr.decode("utf-8", "replace").strip()[-2000:] or f"退出码 {r.returncode}"
    return None


# ---------------------------------------------------------------- 界面


def autorun_on(root: Path, ui: str) -> bool:
    """重新打开界面会自动开始任务（「启动时自动执行」等），这时不替用户重启界面。"""
    if ui == "mxu":
        for path in (root / "config").glob("mxu-*.json"):
            data = release.read_json(path)
            if isinstance(data, dict) and (data.get("settings") or {}).get("autoRunOnLaunch"):
                return True
        return False
    # MFAA：根目录的 appsettings.json（值都是字符串）；打开时进入上次的实例，「启动前」含脚本（StartupScriptOnly 等）就自动执行
    settings = release.read_json(root / "appsettings.json")
    settings = settings if isinstance(settings, dict) else {}
    if str(settings.get("NoAutoStart", "")).lower() == "true":
        return False
    if str(settings.get("GlobalStartEnabled", "")).lower() == "true":
        return True
    instance_id = settings.get("Instances.LastActive") or "default"
    instance = release.read_json(root / "config" / "instances" / f"{instance_id}.json")
    shared = release.read_json(root / "config" / "config.json")
    # 实例文件里没有时，MFAA 依次回退到 config.json 的 Instance.<id>.BeforeTask、Instance.default.BeforeTask、BeforeTask
    for data, key in (
        (instance, "BeforeTask"),
        (shared, f"Instance.{instance_id}.BeforeTask"),
        (shared, "Instance.default.BeforeTask"),
        (shared, "BeforeTask"),
    ):
        if isinstance(data, dict) and key in data:
            # 和 MFAA 自己进入自动分支的条件一致（RootView.axaml.cs）：Startup 开头的都会启动模拟器或脚本
            return "Startup" in str(data[key])
    return False


class System:
    """进程、前台窗口、键鼠闲置时间等 Windows 接口（测试里换成假的）。"""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes, self.wintypes = ctypes, wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.K32EnumProcesses.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        u32.GetForegroundWindow.restype = wintypes.HWND
        u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.k32, self.u32 = k32, u32

    def processes(self) -> list[tuple[int, str]]:
        """（pid, 程序路径）；读不到路径的（系统进程等）跳过。"""
        ctypes, wintypes = self.ctypes, self.wintypes
        n = 4096
        while True:
            pids = (wintypes.DWORD * n)()
            got = wintypes.DWORD()
            if not self.k32.K32EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(got)):
                return []
            if got.value < ctypes.sizeof(pids):
                break
            n *= 2
        out = []
        buf = ctypes.create_unicode_buffer(32768)
        for pid in pids[: got.value // ctypes.sizeof(wintypes.DWORD)]:
            h = self.k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                continue
            try:
                size = wintypes.DWORD(len(buf))
                if self.k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    out.append((int(pid), buf.value))
            finally:
                self.k32.CloseHandle(h)
        return out

    def kill(self, pid: int) -> None:
        h = self.k32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
        if h:
            try:
                self.k32.TerminateProcess(h, 1)
            finally:
                self.k32.CloseHandle(h)

    def foreground_pid(self) -> int | None:
        hwnd = self.u32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = self.wintypes.DWORD()
        self.u32.GetWindowThreadProcessId(hwnd, self.ctypes.byref(pid))
        return int(pid.value) or None

    def idle_seconds(self) -> float:
        """距上次键盘、鼠标操作多少秒。"""

        class LastInput(self.ctypes.Structure):
            _fields_ = [("cbSize", self.wintypes.UINT), ("dwTime", self.wintypes.DWORD)]

        info = LastInput()
        info.cbSize = self.ctypes.sizeof(info)
        if not self.u32.GetLastInputInfo(self.ctypes.byref(info)):
            return 0.0
        return ((self.k32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000

    def launch(self, exe: Path) -> None:
        os.startfile(str(exe), cwd=str(exe.parent))  # MXU 要管理员权限，和双击一样走 UAC


def _norm(path: str | Path) -> str:
    path = os.path.abspath(str(path))
    if "~" in path:  # 8.3 短名（C:\Users\BLUEBE~1\...）展开成长名，否则同一个目录对不上
        path = _long_path(path)
    return os.path.normcase(path)


def _long_path(path: str) -> str:
    """展开 8.3 短名；不存在的部分（GetLongPathNameW 只认存在的路径）原样接在后面。"""
    if os.name != "nt":
        return path
    import ctypes

    buf = ctypes.create_unicode_buffer(32768)
    head, tail = path, ""
    while True:
        n = ctypes.windll.kernel32.GetLongPathNameW(head, buf, len(buf))
        if 0 < n < len(buf):
            return os.path.join(buf.value, tail) if tail else buf.value
        parent, name = os.path.split(head)
        if not name or parent == head:
            return path
        head, tail = parent, os.path.join(name, tail) if tail else name


# ---------------------------------------------------------------- 主流程


class Updater:
    def __init__(
        self,
        root: Path,
        system: Any = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        jailed: bool = False,
    ):
        self.root = root
        self.system = system if system is not None else System()
        self.clock, self.sleep = clock, sleep
        # 还在界面的作业对象里（脱离失败）：界面一关就会被一起结束，不能去关界面
        self.jailed = jailed
        self.local: dict[str, Any] = {}
        self.status: dict[str, Any] = {}
        self._status_at = 0.0

    def set_status(self, state: str, **extra: Any) -> None:
        self.status = {**{k: self.status.get(k) for k in ("version", "tier", "url")}, "state": state, "time": time.time(), **extra}
        release.write_json(self.root / release.STATUS_FILE, self.status)

    def _progress(self, fraction: float) -> None:
        now = self.clock()
        if fraction >= 1.0 or now - self._status_at >= 1.0:
            self._status_at = now
            self.set_status("downloading", progress=round(fraction, 3))

    # ------------------------------------------------------------ 进程

    def _under_root(self) -> list[tuple[int, str]]:
        """包内程序的进程（更新器自己除外）。

        只算 release.json 里记的包内条目：用户把包解压到放着别的程序的目录时（zip 没有顶层文件夹），
        同一个目录下的其他程序不能被当成包里的进程结束。
        """
        names = set(self.local.get("top") or ()) | release.USER_DIRS
        prefixes = [_norm(self.root / name) for name in names]
        runner = _norm(self.root / release.UPDATE_DIR / "runner") + os.sep
        me = os.getpid()
        out = []
        for pid, path in self.system.processes():
            if pid == me:
                continue
            p = _norm(path)
            if p.startswith(runner) or not any(p == x or p.startswith(x + os.sep) for x in prefixes):
                continue
            out.append((pid, path))
        return out

    def ui_pids(self, exe: Path) -> set[int]:
        exe_n = _norm(exe)
        return {pid for pid, path in self.system.processes() if _norm(path) == exe_n}

    def stop_ui(self, pids: set[int]) -> None:
        """关掉界面（Agent、演奏进程随之退出），等包里的进程都退出；超时的强制结束。"""
        logger.info("关闭界面（进程 %s）", sorted(pids))
        for pid in pids:
            self.system.kill(pid)
        deadline = self.clock() + EXIT_WAIT_S
        while (left := self._under_root()) and self.clock() < deadline:
            self.sleep(0.5)
        for pid, path in left:
            logger.info("强制结束 %s（%d）", path, pid)
            self.system.kill(pid)
        if left:
            self.sleep(1.0)

    def user_active(self, pids: set[int]) -> bool:
        """用户正在操作界面（界面在前台、最近 IDLE_S 秒内有键鼠操作）。"""
        return self.system.foreground_pid() in pids and self.system.idle_seconds() < IDLE_S

    # ------------------------------------------------------------ 流程

    def run(self) -> int:
        root = self.root
        previous = release.read_json(root / release.STATUS_FILE)
        if isinstance(previous, dict) and previous.get("state") == "rollback_failed":
            logger.error("上次更新还原失败，保留备份等待手动恢复，不继续自动更新")
            return 1
        local = release.read_release(root)
        if local is None:
            logger.info("没有 release.json（开发环境），不自动更新")
            return 0
        self.local = local
        cached = release.read_json(root / release.MANIFEST_FILE)
        manifest = cached.get("manifest") if isinstance(cached, dict) else None
        if not isinstance(manifest, dict) or not release.is_newer(manifest.get("version"), local.get("version")):
            logger.info("没有要更新的版本")
            return 0
        version = manifest["version"]
        failed = release.read_json(root / release.FAILED_FILE)
        if int(manifest.get("format", 0)) > release.FORMAT or (isinstance(failed, dict) and version in failed):
            logger.info("不自动更新到 %s（格式更新或之前失败过）", version)
            return 0
        tier = release.tier(local, manifest)
        self.status = {"version": version, "tier": tier, "url": manifest.get("url")}
        if self.jailed and tier != "hot":
            # 没能脱离界面的作业对象：关掉界面时更新器会被一起结束，文件换到一半、界面也不会重开
            self.set_status("error", error="没能脱离界面的作业对象，请手动下载新版本")
            logger.warning("没能脱离界面的作业对象，%s 需要关闭界面才能换文件，改为提示手动下载", version)
            return 1
        logger.info("更新 %s → %s（%s）", local.get("version"), version, {"hot": "热更新", "restart": "重启更新", "full": "整包更新"}[tier])
        try:
            stage = self.prepare(manifest, local, tier)
            self.wait_and_apply(stage, manifest, local, tier)
        except RollbackError as e:
            logger.error("%s", e)
            self.set_status("rollback_failed", error=str(e))
            return 1
        except UpdateError as e:
            logger.warning("%s", e)
            self.set_status("error", error=str(e))
            return 1
        return 0

    def prepare(self, manifest: dict, local: dict, tier: str) -> Path:
        """下载、解压、核对，返回解压目录。"""
        version = manifest["version"]
        base = self.root / release.UPDATE_DIR
        for d in base.iterdir():  # 之前版本没用完的下载
            if d.is_dir() and d.name != version and release.parse_version(d.name) is not None:
                shutil.rmtree(d, ignore_errors=True)
        asset = manifest["full"][local["ui"]] if tier == "full" else manifest["app"]
        work = base / version
        archive = work / asset["name"]
        need = int(asset["size"]) * 3 + (200 << 20)
        if shutil.disk_usage(self.root).free < need and not archive.is_file():
            raise UpdateError(f"磁盘空间不够（需要约 {need >> 20} MB）")
        self.set_status("downloading", progress=0.0)
        download(manifest["base_url"] + asset["name"], archive, int(asset["size"]), asset["sha256"], self._progress, sleep=self.sleep)
        stage = work / "stage"
        logger.info("解压 %s", archive.name)
        try:
            extract(archive, stage)
            verify_stage(stage, manifest, tier == "full", local["ui"])
        except (zipfile.BadZipFile, OSError) as e:
            raise UpdateError(f"解压失败：{e}") from None
        return stage

    def wait_and_apply(self, stage: Path, manifest: dict, local: dict, tier: str) -> None:
        exe = self.root / local["ui_exe"]
        deadline = self.clock() + MAX_WAIT_S
        idle_since: float | None = None
        self.set_status("waiting")
        while self.clock() < deadline:
            if (self.root / AUTO_OFF_FILE).exists():
                logger.info("已关闭自动更新，不再等待")
                self.set_status("idle")
                return
            busy = release.busy(self.root)
            pids = self.ui_pids(exe)
            if tier == "hot" or not pids:
                # 界面没开时，包里还有别的进程（用户手动运行的命令行等）占着文件，等它们退出
                if not busy and (tier == "hot" or not self._under_root()) and self.try_apply(stage, manifest, local, tier, set()):
                    return
            elif autorun_on(self.root, local["ui"]):
                idle_since = None
                if self.status.get("state") != "waiting_close":
                    logger.info("界面开着「启动时自动执行」，等界面关闭后再更新")
                    self.set_status("waiting_close")
            elif busy or self.user_active(pids):
                idle_since = None
                if self.status.get("state") != "waiting":
                    self.set_status("waiting")
            else:
                now = self.clock()
                idle_since = now if idle_since is None else idle_since
                if now - idle_since >= IDLE_S and self.try_apply(stage, manifest, local, tier, pids):
                    return
            self.sleep(POLL_S)
        raise UpdateError("24 小时内没有合适的时机更新")

    def try_apply(self, stage: Path, manifest: dict, local: dict, tier: str, ui_pids: set[int]) -> bool:
        """拿到 apply.lock、确认没有演奏进程后替换；``ui_pids`` 非空时先关掉界面、换完重新打开。"""
        lock = release.FileLock(self.root / release.APPLY_LOCK)
        if not lock.acquire():
            return False
        stopped = False
        restored = True
        try:
            if release.busy(self.root):  # 拿锁之前 Agent 刚启动了演奏进程
                return False
            self.set_status("applying")
            if ui_pids:
                stopped = True
                self.stop_ui(ui_pids)
            self.apply(stage, manifest, local, tier)
        except RollbackError:
            restored = False
            raise
        finally:
            lock.release()
            if stopped and restored:  # 更新失败、成功还原了也重新打开
                try:
                    logger.info("重新打开界面")
                    self.system.launch(self.root / local["ui_exe"])
                except OSError as e:
                    logger.warning("重新打开界面失败：%s", e)
        return True

    def apply(self, stage: Path, manifest: dict, local: dict, tier: str) -> None:
        version = manifest["version"]
        backup = self.root / BACKUP_DIR / str(local.get("version"))
        if backup.parent.is_dir():  # 上次没删干净的备份
            for d in backup.parent.iterdir():
                if d != backup:
                    _remove(d)
        job: FilesApply | FullApply = FullApply(self.root, stage, local, backup) if tier == "full" else FilesApply(self.root, stage, local, manifest, backup)
        try:
            job.run()
        except OSError as e:  # 文件被占用等，还原后过一阵再试
            logger.error("替换文件失败：%s，还原到 %s", e, local.get("version"))
            self._rollback(job, backup, version)
            raise UpdateError(f"替换文件失败：{e}") from None
        error = self_test(self.root)
        if error is None:
            logger.info("已更新到 %s", version)
            self.set_status("done")
            shutil.rmtree(stage.parent, ignore_errors=True)
            shutil.rmtree(backup, ignore_errors=True)  # 整包更新的备份有几百 MB
            return
        error = f"新版本试运行失败：{error}"
        logger.error("%s，还原到 %s", error, local.get("version"))
        self._rollback(job, backup, version)
        self._mark_failed(version, error)
        self.set_status("failed", error=error)

    def _mark_failed(self, version: str, error: str) -> None:
        failed = release.read_json(self.root / release.FAILED_FILE)
        failed = failed if isinstance(failed, dict) else {}
        failed[version] = {"time": time.time(), "error": error}
        release.write_json(self.root / release.FAILED_FILE, failed)

    def _rollback(self, job: FilesApply | FullApply, backup: Path, version: str) -> None:
        try:
            job.rollback()
        except OSError as e:
            error = f"还原失败：{e}，备份保留在 {backup}，请手动恢复或重新下载"
            self._mark_failed(version, error)
            raise RollbackError(error) from e


def setup_logging(root: Path) -> None:
    path = root / LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.stat().st_size > LOG_MAX_BYTES:
            os.replace(path, path.with_name("update.old.log"))
    except OSError:
        pass
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname).1s %(message)s", "%Y-%m-%d %H:%M:%S"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)


def in_job() -> bool:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    result = wintypes.BOOL()
    return bool(k32.IsProcessInJob(k32.GetCurrentProcess(), None, ctypes.byref(result))) and bool(result.value)


def escape_job(root: Path) -> bool:
    """通过 WMI 另外启动一个更新器（父进程是 WMI，不在任何作业对象里），成功返回 True。

    MFAA 把 Agent 放进「关闭时结束全部进程」、不允许脱离的作业对象，结束 Agent 时还会连同子进程一起结束；
    更新器留在那里的话，界面一关就被结束了。
    """
    exe = Path(sys.executable)
    exe = exe.with_name("pythonw.exe") if exe.with_name("pythonw.exe").is_file() else exe
    cmd = subprocess.list2cmdline([str(exe), str(Path(__file__).resolve()), str(root), ESCAPED])
    script = (
        "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create"
        " -Arguments @{CommandLine=$env:OURNOTES_CMD; CurrentDirectory=$env:OURNOTES_CWD}; exit [int]$r.ReturnValue"
    )
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            env={**os.environ, "OURNOTES_CMD": cmd, "OURNOTES_CWD": str(exe.parent)},
            capture_output=True,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("脱离界面的作业对象失败：%s", e)
        return False
    if r.returncode:
        logger.warning("脱离界面的作业对象失败（%d）：%s", r.returncode, r.stderr.decode("mbcs", "replace").strip()[-500:])
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    escaped = ESCAPED in argv
    argv = [a for a in argv if a != ESCAPED]
    if len(argv) != 1:
        print("用法：updater.py <包根目录>", file=sys.stderr)
        return 2
    root = Path(argv[0]).resolve()
    os.chdir(root / release.UPDATE_DIR)  # 不占着包根目录下要移走的目录
    setup_logging(root)
    jailed = not escaped and in_job()
    if jailed and escape_job(root):
        return 0
    lock = release.FileLock(root / release.UPDATER_LOCK)
    if not lock.acquire():
        return 0  # 已经有一个在运行
    updater = None
    try:
        updater = Updater(root, jailed=jailed)
        return updater.run()
    except Exception as e:  # noqa: BLE001
        logger.exception("更新器出错")
        try:
            if updater is not None:
                updater.set_status("error", error=str(e))
        except OSError:
            pass
        return 1
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
