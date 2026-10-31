"""bdon.moe 谱面站点客户端（带本地缓存）。

- 索引：``charts.json``（format 2），每项含 musicId / difficulty / title / manifest / fullComboCount；
- 清单：``charts/{musicId}_{difficulty}.json``，``files["score/NNNN_0X.notes.json"]`` 指向谱面文件：
  小文件为 ``{"asset": "assets/<sha256>.json"}``，大文件按顶层键拆分为
  ``{"parts": [[key, asset, size], ...], "size": 总大小}``；
- 资源文件名即内容的 sha256，下载后校验。

国际服与日服谱面一致，国际服曲名（含简体中文）与封面缩略图从 haneoka.org 获取，仅用于识别曲目。

连不上时少等（#108）：某个主机下载失败后记在缓存目录里，到下次打开程序前过期的索引直接用旧缓存、缺的封面先不下载；
谱面照常重试。每个进程对每个主机说明一次连不上的原因（错误归类、走的代理还是直连、解析到的 IP）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit
from urllib.request import getproxies_environment

import requests

from .. import __version__
from ..config import ChartsConfig
from .model import Chart
from .parser import parse_live_score

logger = logging.getLogger(__name__)

USER_AGENT = f"ournotes-auto/{__version__} (+personal use; cached, serial requests)"
DIFFICULTIES = ("easy", "normal", "hard", "expert")
CONNECT_S = 10  # 连接（含加密握手）超时，第 n 次试等 n 倍：被拦时早点放弃，第三次和原来一样等 30 秒
READ_S = 30  # 读超时：两次收到数据之间最多等多久
DOWN_DIR = ".down"  # 缓存目录下记下载失败的主机：文件名是主机名，修改时间是失败的时刻
LAUNCHED_ENV = "OURNOTES_LAUNCHED"  # 这次打开界面的时刻（Agent 设，演奏进程继承；和 agent/server.py 一致）
_STARTED = time.time()  # 命令行运行时没有界面，「这次打开程序」从这个进程开始算
PROXY_PASSWORD = re.compile(r"(?<=:)[^:@/]*(?=@)")  # 同 agent/server.py：代理地址 user:pass@host:port 里的密码不记
_explained: set[str] = set()  # 这个进程里已经说明过连不上原因的主机


class ChartNotFound(LookupError):
    pass


class HostDown(ConnectionError):
    """主机这次打开程序后下载失败过，可有可无的文件（封面）先不下载。"""


class BdonClient:
    def __init__(self, cfg: ChartsConfig, session: requests.Session | None = None):
        self.cfg = cfg
        self.base = cfg.base_url.rstrip("/") + "/"
        self.cache = Path(cfg.cache_dir)
        self.http = session or requests.Session()
        self.http.headers["User-Agent"] = USER_AGENT  # Session 自带 python-requests 的，setdefault 换不掉

    # —— 底层 ——

    def _get(self, url: str, retries: int = 3) -> bytes:
        last: Exception | None = None
        for i in range(retries):
            if i:
                time.sleep(1.5 * i)
            try:
                r = self.http.get(url, timeout=(CONNECT_S * (i + 1), READ_S))
                if r.status_code != 404:
                    r.raise_for_status()
            except requests.RequestException as e:
                last = e
                logger.warning("下载失败（%d/%d）%s：%s", i + 1, retries, url, e)
                continue
            self._mark_down(url, False)
            if r.status_code == 404:
                raise ChartNotFound(url)
            return r.content
        self._mark_down(url, True)
        explain_failure(url, last)
        raise ConnectionError(f"无法下载 {url}：{last}")

    def _down_path(self, url: str) -> Path:
        return self.cache / DOWN_DIR / (urlsplit(url).hostname or "unknown")

    def _mark_down(self, url: str, down: bool) -> None:
        try:
            path = self._down_path(url)
            if down:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            else:
                path.unlink(missing_ok=True)
        except OSError:
            pass

    def _is_down(self, url: str) -> bool:
        """``url`` 的主机这次打开程序后下载失败过（之后又成功过的不算）。"""
        try:
            return self._down_path(url).stat().st_mtime >= launched()
        except OSError:
            return False

    @staticmethod
    def _verify(raw: bytes, asset: str) -> None:
        digest = asset.rsplit("/", 1)[-1].split(".")[0]
        if len(digest) == 64 and hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError(f"校验失败：{asset}")

    def _cached(
        self, rel: str, url: str, max_age_s: float | None = None, asset: str | None = None, optional: bool = False
    ) -> bytes:
        """``optional``：可有可无的文件，主机这次打开程序后下载失败过就先不下载（抛出 :class:`HostDown`）。"""
        retries = 3
        path = self.cache / rel
        if path.exists():
            if max_age_s is None or time.time() - path.stat().st_mtime < max_age_s:
                return path.read_bytes()
            if max_age_s:  # 过期了（不是强制刷新）：有旧的可用，只试一次；这次打开程序后主机失败过就先不下载
                if self._is_down(url):
                    logger.info("%s 这次打开程序后下载失败过，先用过期缓存 %s，下次打开程序再下载", urlsplit(url).hostname, path)
                    return path.read_bytes()
                retries = 1
        elif optional and self._is_down(url):
            raise HostDown(f"{urlsplit(url).hostname} 这次打开程序后下载失败过，下次打开程序再下载 {url}")
        try:
            raw = self._get(url, retries)
        except (ConnectionError, ChartNotFound):
            if path.exists():
                logger.warning("使用过期缓存 %s", path)
                return path.read_bytes()
            raise
        if asset:
            self._verify(raw, asset)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(raw)
        tmp.replace(path)
        return raw

    # —— 索引 / 清单 / 谱面 ——

    def index(self, refresh: bool = False) -> list[dict[str, Any]]:
        ttl = 0.0 if refresh else self.cfg.index_ttl_hours * 3600
        data = json.loads(self._cached("charts.json", self.base + "charts.json", max_age_s=ttl))
        if data.get("format") != 2:
            logger.warning("未知的索引格式 %r，尝试继续解析", data.get("format"))
        return data.get("charts", [])

    def manifest(self, music_id: int, difficulty: str) -> dict[str, Any]:
        key = f"{music_id}_{difficulty}"
        rel = f"manifests/{key}.json"
        return json.loads(self._cached(rel, self.base + f"charts/{key}.json"))

    def _asset(self, asset: str) -> bytes:
        return self._cached(asset, self.base + asset, asset=asset)

    def notes_raw(self, music_id: int, difficulty: str) -> dict[str, Any]:
        man = self.manifest(music_id, difficulty)
        files = man.get("files", {})
        keys = sorted(k for k in files if k.startswith("score/") and k.endswith(".notes.json"))
        if not keys:
            raise ChartNotFound(f"{music_id}_{difficulty} 的清单中没有谱面文件")
        entry = files[keys[0]]
        if "asset" in entry:
            return json.loads(self._asset(entry["asset"]))
        if "parts" in entry:
            chunks = []
            for part_key, asset, size in entry["parts"]:
                raw = self._asset(asset)
                if len(raw) != size:
                    raise ValueError(f"{asset} 大小 {len(raw)} 与清单 {size} 不符")
                chunks.append(json.dumps(part_key).encode() + b":" + raw)
            return json.loads(b"{" + b",".join(chunks) + b"}")
        raise ValueError(f"无法识别的清单条目：{entry}")

    def chart(self, music_id: int, difficulty: str, title: str = "") -> Chart:
        difficulty = difficulty.lower()
        if difficulty not in DIFFICULTIES:
            raise ValueError(f"未知难度 {difficulty}")
        return parse_live_score(self.notes_raw(music_id, difficulty), music_id, difficulty, title)

    # —— 国际服曲名 / 封面 ——

    def intl_songs(self, refresh: bool = False) -> dict[int, dict[str, Any]]:
        """haneoka 的国际服曲目表（musicId → 条目，含 musicTitle[日/英/繁/简/韩] 与 jacketThumbUrl）；
        获取失败时返回空表。"""
        url = self.cfg.titles_url
        if not url:
            return {}
        ttl = 0.0 if refresh else self.cfg.index_ttl_hours * 3600
        try:
            data = json.loads(self._cached("intl_songs.json", url, max_age_s=ttl))
        except (ConnectionError, ChartNotFound, ValueError) as e:
            logger.warning("获取国际服曲目表失败：%s", e)
            return {}
        return {int(mid): song for mid, song in data.items()}

    def jacket(self, music_id: int, url: str) -> bytes:
        """封面缩略图（PNG）；``url`` 为曲目表里的 jacketThumbUrl（相对 titles_url 所在站点）。

        封面只是帮着认歌（认不出还有曲名），主机这次打开程序后下载失败过就先不下载（抛出 :class:`HostDown`），
        免得缺的每张都等一遍。"""
        return self._cached(f"jackets/{music_id}.png", urljoin(self.cfg.titles_url, url), optional=True)

    def jacket_cached(self, music_id: int) -> bool:
        return (self.cache / f"jackets/{music_id}.png").exists()


# —— 连不上时的说明 ——


def launched() -> float:
    """这次打开程序的时刻：主机在这之后下载失败过，到下次打开程序前过期的索引先用旧缓存、缺的封面先不下载。"""
    try:
        return float(os.environ[LAUNCHED_ENV])
    except (KeyError, ValueError):
        return _STARTED


def explain_failure(url: str, err: Exception | None) -> None:
    """最终下载失败时说明原因，每个进程每个主机说一次（重试的那几行只有原始报错，看不出是代理还是网络的问题）。"""
    host = urlsplit(url).hostname or url
    if host in _explained:
        return
    _explained.add(host)
    logger.warning(
        "下载不了 %s（%s），%s。浏览器能打开这个网站时，多半是浏览器走了代理插件、自动代理脚本、HTTP/3 或安全 DNS，"
        "本程序用不了这些；开着代理软件的话，请在「网络代理」里填它的端口。下次打开程序前，过期的曲目列表先用旧缓存、"
        "缺的封面先不下载",
        host,
        failure_kind(err),
        route(url),
    )


def failure_kind(err: Exception | None) -> str:
    """下载失败的原因，归成用户看得懂的几类。"""
    text = str(err)
    if "Tunnel connection failed" in text:  # 代理回了 407/403/502 之类
        return "代理拒绝转发：代理连不上网站，或要账号密码"
    if isinstance(err, requests.exceptions.ProxyError):
        return "代理连不上：代理软件没开或端口不对"
    if isinstance(err, requests.exceptions.Timeout) or "timed out" in text:
        return "超时"
    if isinstance(err, requests.exceptions.SSLError):
        return "加密连接失败"
    if isinstance(err, requests.exceptions.HTTPError) and err.response is not None:
        return f"服务器返回 HTTP {err.response.status_code}"
    if "Failed to resolve" in text or "getaddrinfo" in text:
        return "域名解析失败"
    if "10054" in text or "Connection aborted" in text or "ConnectionReset" in text:
        return "连接被中途断开"
    return "连接失败"


def route(url: str) -> str:
    """请求走的路：代理（环境变量或系统代理，密码打码）或直连（附上本机解析出的 IP，看域名有没有被解析错）。"""
    try:
        proxies = requests.utils.get_environ_proxies(url)
    except Exception:  # noqa: BLE001 - 只是说明，读不到就当直连
        proxies = {}
    if proxy := proxies.get(urlsplit(url).scheme) or proxies.get("all"):
        if getproxies_environment():  # 环境变量里有代理设置时，requests 不读系统代理
            source = "环境变量里的代理（「网络代理」选项或系统环境变量 HTTPS_PROXY 等设的）"
        else:
            source = "系统代理"
        return f"走的是{source} {PROXY_PASSWORD.sub('***', proxy)}"
    host = urlsplit(url).hostname or ""
    try:
        ips = sorted({str(info[4][0]) for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
        text = f"直连，解析到 {', '.join(ips)}"
    except OSError as e:
        text = f"直连，域名解析失败（{e}）"
    if uses_pac():
        text += "；系统设了自动代理脚本（PAC），本程序读不到它"
    return text


def uses_pac() -> bool:
    """Windows 的代理设置里有没有「使用设置脚本」（PAC 地址可能带令牌，不记）。"""
    try:
        import winreg

        key = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return bool(winreg.QueryValueEx(k, "AutoConfigURL")[0])
    except (ImportError, OSError):
        return False
