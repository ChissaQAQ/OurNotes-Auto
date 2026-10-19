"""自动更新（Agent 这边）：任务开始时到 GitHub 查最新 Release 的 update.json，有新版本就启动后台的更新器
（ournotes_auto/updater.py）下载、在合适的时机换上；任务结束时在界面日志里报告进度。

更新器用复制到 cache/update/runner/ 的嵌入式 Python 运行，界面、Agent 退出或整包替换都不影响它。Agent 和更新器
用锁配合（ournotes_auto/release.py）：演奏进程运行期间锁住 busy/<pid>.lock，更新器只在没有演奏进程时替换文件；
启动演奏进程前先等更新器放开 apply.lock，不会用到换了一半的文件。

开发环境（没有 release.json）、关掉「自动更新」、自动更新失败过的版本，只提示有新版本。
"""

from __future__ import annotations

import contextlib
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import requests

from .. import release
from ..release import is_newer, parse_version

logger = logging.getLogger(__name__)

REPO = release.REPO
DOWNLOAD_URL = f"https://github.com/{REPO}/releases/latest"  # 跳转到最新正式发布的，不含草稿和预发布
MANIFEST_URL = f"{DOWNLOAD_URL}/download/{release.UPDATE_ASSET}"
URL_ENV = "OURNOTES_UPDATE_URL"  # 测试用：换成别的 update.json 地址
CACHE_TTL_S = 6 * 3600  # MXU 每次运行任务都重启 Agent，查过的结果缓存一阵，免得每次都访问 GitHub
RETRY_S = 3600  # 更新器出错（网络等）后隔多久再试
STATUS_POLL_S = 30.0  # 任务运行中多久看一次更新器的状态
APPLY_WAIT_S = 60.0  # 启动演奏进程前最多等更新器换文件多久
HOT_WAIT_S = 15 * 60  # 挂机为热更新退出后最多等多久（多开时要等其他实例也到安全点退出）
RUNNER_DIR = f"{release.UPDATE_DIR}/runner"
AUTO_OFF_FILE = f"{release.UPDATE_DIR}/auto_off"  # 关掉「自动更新」时建立，正在等待的更新器看到后退出
ANNOUNCED_FILE = f"{release.UPDATE_DIR}/announced.json"  # 已经提示过的更新结果，每个版本只提示一次
_TAG_URL = re.compile(r"/releases/tag/([^/?#]+)$")


class UpdateBusyError(RuntimeError):
    """更新锁不可用，不能启动会读取项目文件的演奏进程。"""


def current_version(root: Path) -> str | None:
    """当前版本：发布包的 release.json（热更新后会变），开发环境用 ``interface.json``。"""
    info = release.read_release(root)
    if info is not None:
        return str(info.get("version"))
    data = release.read_json(root / "interface.json")
    return str(data["version"]) if isinstance(data, dict) and "version" in data else None


def fetch_latest(timeout_s: float = 5.0) -> str:
    """GitHub 上最新正式发布的 tag：``/releases/latest`` 会跳转到 ``/releases/tag/<tag>``。

    不用 REST API：不带认证时每个 IP 每小时只有 60 次，公司、学校等共用出口的网络很容易用完。
    """
    resp = requests.head(DOWNLOAD_URL, allow_redirects=False, timeout=timeout_s)
    location = resp.headers.get("Location", "")
    m = _TAG_URL.search(location)
    if not resp.is_redirect or m is None:
        raise ValueError(f"没有找到发布的版本（HTTP {resp.status_code}，跳转到 {location or '无'}）")
    return unquote(m.group(1))


def fetch_manifest(timeout_s: float = 10.0) -> dict[str, Any]:
    """``{"version": 最新版本, "manifest": update.json}``；最新的 Release 没有 update.json（旧版本）时 manifest 为 None。"""
    resp = requests.get(os.environ.get(URL_ENV) or MANIFEST_URL, timeout=timeout_s)
    if resp.status_code == 404:
        return {"version": fetch_latest(), "manifest": None}
    resp.raise_for_status()
    manifest = resp.json()
    if not isinstance(manifest, dict) or parse_version(manifest.get("version")) is None:
        raise ValueError("update.json 格式不对")
    return {"version": manifest["version"], "manifest": manifest}


def cached_manifest(
    cache: Path,
    fetch: Callable[[], dict[str, Any]] = fetch_manifest,
    now: Callable[[], float] = time.time,
    ttl_s: float = CACHE_TTL_S,
) -> dict[str, Any]:
    """:func:`fetch_manifest` 的结果；``ttl_s`` 内查过就用缓存（更新器也读这个文件）。"""
    data = release.read_json(cache)
    try:
        if 0 <= now() - float(data["checked_at"]) < ttl_s and "version" in data:
            return data
    except (KeyError, TypeError, ValueError):
        pass
    data = {"checked_at": now(), **fetch()}
    try:
        release.write_json(cache, data)
    except OSError:
        pass
    return data


def _copy_if_changed(src: Path, dst: Path) -> None:
    try:
        a, b = src.stat(), dst.stat()
        if (a.st_size, a.st_mtime_ns) == (b.st_size, b.st_mtime_ns):
            return
    except OSError:
        pass
    shutil.copy2(src, dst)


def spawn_updater(root: Path) -> None:
    """把嵌入式 Python 的程序文件（不含 Lib，更新器只用标准库）和更新器复制到 runner 目录，从那里启动更新器。

    更新器要比界面和 Agent 活得久：脱离控制台和进程组，也尽量脱离界面的作业对象（脱离不了的由更新器自己处理），
    界面关掉不会连带结束它。
    """
    runner = root / RUNNER_DIR
    runner.mkdir(parents=True, exist_ok=True)
    py = root / Path(release.AGENT_EXEC).parent
    pth = None
    for src in py.iterdir():
        if src.name.endswith("._pth"):
            pth = src.name
        elif src.is_file():
            _copy_if_changed(src, runner / src.name)
    if pth is None:
        raise FileNotFoundError(f"{py} 里没有 ._pth")
    # 只用自带的标准库和 runner 目录（更新器、release.py），不加载 site-packages 和项目目录
    zips = sorted(p.name for p in py.glob("python*.zip"))
    (runner / pth).write_text("\n".join([*zips, ".", ""]), encoding="utf-8")
    for name in ("updater.py", "release.py"):
        shutil.copy2(root / "ournotes_auto" / name, runner / name)
    exe = runner / "pythonw.exe" if (runner / "pythonw.exe").is_file() else runner / "python.exe"
    cmd = [str(exe), str(runner / "updater.py"), str(root)]
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    io = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    try:
        subprocess.Popen(cmd, cwd=root, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, close_fds=True, **io)
    except OSError:  # 作业对象不允许脱离
        subprocess.Popen(cmd, cwd=root, creationflags=flags, close_fds=True, **io)
    logger.debug("已启动更新器：%s", cmd)


class UpdateCheck:
    """任务开始时 ``start`` 在后台查新版本、需要时启动更新器；任务运行中 ``hot_ready`` 告诉挂机要不要为热更新重启；
    任务结束时 ``report`` 在界面日志里提示。"""

    def __init__(
        self,
        root: Path,
        lookup: Callable[[], dict[str, Any]] | None = None,
        spawn: Callable[[], None] | None = None,
    ):
        self.root = root
        self._lookup = lookup or (lambda: cached_manifest(root / release.MANIFEST_FILE))
        self._spawn = spawn or (lambda: spawn_updater(root))
        self._thread: threading.Thread | None = None
        self._checked_at = -math.inf
        self._auto = True
        self._notice: tuple[str, str] | None = None  # 只提示的新版本：(版本, 下载页)
        self._said: set[tuple] = set()  # 这个 Agent 进程里已经提示过的
        self._polled_at = -math.inf
        self._hot = False
        self._hot_after = -math.inf  # 热更新等到这个时间之前不重启演奏进程

    # ------------------------------------------------------------ 检查

    def start(self, check: bool = True, auto: bool = True) -> None:
        """任务开始时调用：``check`` 为「检查更新」，``auto`` 为「自动更新」（要先开检查更新）。"""
        self._auto = check and auto
        self._set_auto_flag(self._auto)
        self._polled_at = -math.inf  # 上一个任务缓存的状态不作数，第一次 hot_ready 重新读
        self._hot = False
        if check:
            self._maybe_check()

    def _maybe_check(self) -> None:
        """后台查一次；Agent 常驻（MFAA）时隔一阵再查。"""
        if self._thread is not None and self._thread.is_alive():
            return
        if time.monotonic() - self._checked_at < CACHE_TTL_S:
            return
        self._checked_at = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="update-check", daemon=True)
        self._thread.start()

    def _set_auto_flag(self, auto: bool) -> None:
        if release.read_release(self.root) is None:
            return
        path = self.root / AUTO_OFF_FILE
        try:
            if auto:
                path.unlink(missing_ok=True)
            elif not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
        except OSError:
            pass

    def _run(self) -> None:
        try:
            info = self._lookup()
        except Exception as e:  # noqa: BLE001 - 查不到就算了，不影响任务
            logger.debug("检查更新失败：%s", e)
            return
        latest, manifest = info.get("version"), info.get("manifest")
        current = current_version(self.root)
        if not is_newer(latest, current):
            logger.debug("已是最新版本（%s，GitHub 上最新 %s）", current, latest)
            return
        reason = self._manual_reason(manifest)
        if reason:
            logger.debug("不自动更新到 %s：%s", latest, reason)
            self._notice = (latest, (manifest or {}).get("url") or DOWNLOAD_URL)
            return
        if release.is_locked(self.root / release.UPDATER_LOCK):
            return  # 更新器已经在运行
        status = self.status() or {}
        if status.get("state") == "error" and status.get("version") == latest and 0 <= time.time() - float(status.get("time", 0)) < RETRY_S:
            return
        try:
            self._spawn()
        except Exception as e:  # noqa: BLE001
            logger.debug("启动更新器失败：%s", e)
            self._notice = (latest, manifest.get("url") or DOWNLOAD_URL)

    def _manual_reason(self, manifest: dict | None) -> str | None:
        """不能自动更新、只提示的原因。"""
        local = release.read_release(self.root)
        if local is None:
            return "开发环境"
        if not self._auto:
            return "没有开自动更新"
        if (self.status() or {}).get("state") == "rollback_failed":
            return "上次更新还原失败，请先手动恢复"
        if manifest is None:
            return "这个版本没有自动更新文件"
        if int(manifest.get("format", 0)) > release.FORMAT:
            return "update.json 是新格式"
        failed = release.read_json(self.root / release.FAILED_FILE) or {}
        if isinstance(failed, dict) and manifest.get("version") in failed:
            return "自动更新到这个版本失败过"
        if release.tier(local, manifest) == "full" and local["ui"] not in (manifest.get("full") or {}):
            return f"没有 {local['ui']} 的发布包"
        return None

    # ------------------------------------------------------------ 状态

    def status(self) -> dict | None:
        data = release.read_json(self.root / release.STATUS_FILE)
        return data if isinstance(data, dict) else None

    def hot_ready(self) -> bool:
        """新版本已下载、只需热更新：挂机到安全的时机退出，换上新代码后重新启动。任务运行中反复调用，要便宜。"""
        if time.monotonic() - self._polled_at < STATUS_POLL_S:
            return self._hot
        self._polled_at = time.monotonic()
        if self._auto:
            self._maybe_check()
        st = self.status() or {}
        self._hot = (
            self._auto
            and st.get("state") == "waiting"
            and st.get("tier") == "hot"
            # 更新器还活着（被强杀会留下 waiting/hot 的状态，光看状态会一直重启演奏进程）
            and release.is_locked(self.root / release.UPDATER_LOCK)
            and time.monotonic() >= self._hot_after
            and is_newer(st.get("version"), current_version(self.root))
        )
        return self._hot

    @contextlib.contextmanager
    def busy(self) -> Iterator[None]:
        """演奏进程运行期间锁住 busy/<pid>.lock，更新器就不会替换文件；锁上之后再等正在进行的替换完成。"""
        if (self.status() or {}).get("state") == "rollback_failed":
            raise UpdateBusyError("上次更新还原失败，请先手动恢复备份或将发布包解压到新目录")
        if release.read_release(self.root) is None and not release.is_locked(self.root / release.APPLY_LOCK):
            yield
            return
        lock = release.FileLock(self.root / release.BUSY_DIR / f"{os.getpid()}.lock")
        if not lock.acquire():
            raise UpdateBusyError("无法锁定任务运行状态，暂不启动任务，请检查更新缓存目录的权限")
        try:
            self._wait_apply()
            if (self.status() or {}).get("state") == "rollback_failed":
                raise UpdateBusyError("更新还原失败，请先手动恢复备份或将发布包解压到新目录")
            yield
        finally:
            lock.release()
            with contextlib.suppress(OSError):
                lock.path.unlink()

    def _wait_apply(self, timeout_s: float = APPLY_WAIT_S) -> None:
        apply = release.FileLock(self.root / release.APPLY_LOCK)
        deadline = time.monotonic() + timeout_s
        while not apply.acquire():
            if time.monotonic() >= deadline:
                raise UpdateBusyError("更新器仍在替换文件，暂不启动任务，请稍后重试")
            time.sleep(0.5)
        apply.release()

    def wait_applied(self, should_stop: Callable[[], bool] = lambda: False, timeout_s: float = HOT_WAIT_S) -> None:
        """挂机为热更新退出后：等更新器换完文件（或放弃）再重新启动。"""
        st = self.status() or {}
        logger.info("正在换上新版本 %s，稍后继续", st.get("version"))
        deadline = time.monotonic() + timeout_s
        stopped = False
        while time.monotonic() < deadline:
            if should_stop():
                stopped = True
                break
            st = self.status() or {}
            if st.get("state") not in ("waiting", "applying") or not release.is_locked(self.root / release.UPDATER_LOCK):
                break
            time.sleep(0.5)
        self._polled_at = -math.inf
        self._hot = False
        # 另一个实例还在跑任务时更新器换不了，等满时间就让它先接着挂机，过一阵再试
        st = self.status() or {}
        if not stopped and st.get("state") == "waiting" and is_newer(st.get("version"), current_version(self.root)):
            self._hot_after = time.monotonic() + timeout_s
        self.report(wait_s=0)

    # ------------------------------------------------------------ 提示

    def _announce_once(self, kind: str, version: str) -> bool:
        """更新结果每个版本只提示一次（MXU 每次任务都重启 Agent，所以记在文件里）。"""
        path = self.root / ANNOUNCED_FILE
        data = release.read_json(path)
        data = data if isinstance(data, dict) else {}
        if data.get(kind) == version:
            return False
        data[kind] = version
        with contextlib.suppress(OSError):
            release.write_json(path, data)
        return True

    def _say_once(self, *key) -> bool:
        if key in self._said:
            return False
        self._said.add(key)
        return True

    def report(self, wait_s: float = 3.0) -> None:
        if self._thread is not None:
            self._thread.join(wait_s)
        current = current_version(self.root)
        st = self.status() or {}
        version, state, url = st.get("version"), st.get("state"), st.get("url") or DOWNLOAD_URL
        if state == "done" and version == current and self._announce_once("done", version):
            logger.info("已自动更新到 %s，更新内容见 %s", version, url)
        elif state == "failed" and self._announce_once("failed", version):
            logger.warning("自动更新到 %s 失败，已恢复原版本（详见 data/update.log），可到 %s 手动下载", version, url)
        elif state == "rollback_failed" and self._announce_once("rollback_failed", version):
            logger.error("自动更新到 %s 后还原失败（%s），请手动恢复备份或到 %s 重新下载", version, st.get("error", ""), url)
        elif self._auto and is_newer(version, current):
            hot = st.get("tier") == "hot"
            if state == "downloading":
                logger.info("正在后台下载新版本 %s（%d%%）", version, int(st.get("progress", 0) * 100))
            elif state == "waiting" and self._say_once(state, version):
                if hot:
                    logger.info("新版本 %s 已下载，没有任务运行时自动换上", version)
                else:
                    logger.info("新版本 %s 已下载，任务都结束后会自动重启界面完成更新", version)
            elif state == "waiting_close" and self._say_once(state, version):
                logger.info("新版本 %s 已下载；开着「启动时自动执行」时不会自动重启界面，关闭界面后自动完成更新", version)
            elif state == "error" and self._say_once(state, version):
                logger.info("自动更新到 %s 暂时没成功（%s），稍后会重试；也可以到 %s 手动下载", version, st.get("error", ""), url)
        if self._notice and is_newer(self._notice[0], current) and self._say_once("notice", self._notice[0]):
            logger.info("发现新版本 %s（当前 %s），可到 %s 下载", self._notice[0], current, self._notice[1])
