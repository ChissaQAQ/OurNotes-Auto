"""更新提示：到 GitHub 查最新发布的版本，比当前的新就在界面日志里提一句（不会自动下载）。"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

REPO = "ChissaQAQ/ournotes-auto"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"  # 只返回正式发布的，不含草稿和预发布
DOWNLOAD_URL = f"https://github.com/{REPO}/releases/latest"
CACHE_TTL_S = 6 * 3600  # MXU 每次运行任务都重启 Agent，查过的结果缓存一阵，免得每次都访问 GitHub
_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


def parse_version(text: str | None) -> tuple[int, ...] | None:
    """``v1.2.3`` / ``1.2.3`` → (1, 2, 3)；开发版等其他格式返回 None。"""
    m = _VERSION.fullmatch(str(text or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def is_newer(latest: str | None, current: str | None) -> bool:
    new, cur = parse_version(latest), parse_version(current)
    return new is not None and cur is not None and new > cur


def current_version(root: Path) -> str | None:
    """``interface.json`` 里的版本号（发布包里是打包时的 tag）。"""
    try:
        return str(json.loads((root / "interface.json").read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def fetch_latest(timeout_s: float = 5.0) -> str:
    """GitHub 上最新正式发布的 tag。"""
    resp = requests.get(LATEST_API, headers={"Accept": "application/vnd.github+json"}, timeout=timeout_s)
    resp.raise_for_status()
    return str(resp.json()["tag_name"])


def latest_tag(
    cache: Path,
    fetch: Callable[[], str] = fetch_latest,
    now: Callable[[], float] = time.time,
    ttl_s: float = CACHE_TTL_S,
) -> str:
    """最新发布的 tag；``ttl_s`` 内查过就用缓存。"""
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        if 0 <= now() - float(data["checked_at"]) < ttl_s:
            return str(data["latest"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    tag = fetch()
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"checked_at": now(), "latest": tag}), encoding="utf-8")
    except OSError:
        pass
    return tag


class UpdateCheck:
    """任务开始时 ``start`` 在后台查，任务结束时 ``report`` 提示；每个 Agent 进程只提示一次。"""

    def __init__(self, root: Path, lookup: Callable[[], str] | None = None):
        self.root = root
        self._lookup = lookup or (lambda: latest_tag(root / "cache" / "update.json"))
        self._current: str | None = None
        self._latest: str | None = None
        self._thread: threading.Thread | None = None
        self._reported = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._current = current_version(self.root)
        if parse_version(self._current) is None:
            return  # 开发版不查
        self._thread = threading.Thread(target=self._run, name="update-check", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self._latest = self._lookup()
        except Exception as e:  # noqa: BLE001 - 查不到就算了，不影响任务
            logger.debug("检查更新失败：%s", e)

    def report(self, wait_s: float = 3.0) -> None:
        if self._thread is None or self._reported:
            return
        self._thread.join(wait_s)
        if self._thread.is_alive():
            return  # 网络慢，下个任务结束时再看
        self._reported = True
        if is_newer(self._latest, self._current):
            logger.info("发现新版本 %s（当前 %s），可到 %s 下载", self._latest, self._current, DOWNLOAD_URL)
        elif self._latest is not None:
            logger.debug("已是最新版本（%s，GitHub 上最新 %s）", self._current, self._latest)
