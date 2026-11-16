"""bdon.moe 谱面站点客户端（带本地缓存）。

- 索引：``charts.json``（format 2），每项含 musicId / difficulty / title / manifest / fullComboCount；
- 清单：``charts/{musicId}_{difficulty}.json``，``files["score/NNNN_0X.notes.json"]`` 指向谱面文件：
  小文件为 ``{"asset": "assets/<sha256>.json"}``，大文件按顶层键拆分为
  ``{"parts": [[key, asset, size], ...], "size": 总大小}``；
- 资源文件名即内容的 sha256，下载后校验；
- 索引条目的 ``bytes`` 是清单里各文件大小的和：谱面站重新生成过一张谱面（改了谱面、换了封面……），它就变了。
  缓存里的清单、随包附带的谱面是不是旧的都靠它看（:meth:`BdonClient._manifest_outdated`、:data:`SAME_CHART`）；
- 封面：清单里的 ``livescene/textures/jkt_*.png``（同一首歌各难度是同一张），只用来认歌，见 :meth:`BdonClient.jacket`。

随包附带的数据（发版前用 tools/fetch_bundle.py 更新）：封面 :data:`JACKETS` 在 app 层，每次更新都带；谱面和打包时的
曲目列表 :data:`BUNDLE` 只在整包里。包里有的不用下载，之后才出的新歌、谱面站重新生成过的谱面照常从谱面站取。

国际服与日服谱面一致。各语言的曲名随包附带（``titles.json``，见 catalog.py）。以前曲名和封面从 haneoka.org 取，
2026-11 起它拒绝本程序的请求（#142），不再访问。

连不上时少等（#108）：某个主机下载失败后记在缓存目录里，到下次打开程序前过期的索引直接用旧缓存、缺的封面先不下载；
谱面照常重试。每个进程对每个主机说明一次连不上的原因（错误归类、走的代理还是直连、解析到的 IP）。
"""

from __future__ import annotations

import functools
import gzip
import hashlib
import json
import logging
import os
import re
import socket
import time
import zlib
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
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
REFUSED = (401, 403, 451)  # 网站自己回的拒绝（如 Cloudflare 按 IP、按程序标识拦下来回 403）：连得上，不是代理端口的问题
JACKET_FILE = re.compile(r"(?:^|/)jkt_[^/]*\.png$")  # 清单里的封面，如 livescene/textures/jkt_001_100001-0785d933.png
JACKETS = Path(__file__).with_name("jackets")  # 随包附带的封面（{musicId}.png，已缩小），在 app 层，每次更新都带
# 随整包附带的谱面（{musicId}_{difficulty}.json.gz，谱面文件的原样内容）和打包时的曲目列表（charts.json）。只在整包里，
# 自动更新不带、不动它（release.BUNDLE_DIR）：安装目录里可能没有（从旧版本自动更新上来的），也可能比谱面站旧
BUNDLE = Path(__file__).with_name("bundle")
BUNDLE_INDEX = "charts.json"
# 曲目列表的条目里这几项和打包时一样，包里的谱面就还是谱面站现在的那一版（bytes 见模块说明）
SAME_CHART = ("bytes", "notes", "fullComboCount", "durationMs")
_explained: set[str] = set()  # 这个进程里已经说明过连不上原因的主机


class ChartNotFound(LookupError):
    pass


class HostDown(ConnectionError):
    """主机这次打开程序后下载失败过，可有可无的文件（封面）先不下载。"""


def bundled_index() -> dict[str, Any] | None:
    """随整包附带的曲目列表（打包时的）；包里没带、读不了时为 None。"""
    try:
        data = json.loads((BUNDLE / BUNDLE_INDEX).read_bytes())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("charts"), list) else None


def chart_entries(index: Any) -> dict[str, dict[str, Any]]:
    """曲目列表 → 各张谱面的条目（``"{musicId}_{difficulty}"`` → 条目）。"""
    charts = index.get("charts") if isinstance(index, Mapping) else None
    return {f"{e.get('musicId')}_{e.get('difficulty')}": e for e in charts or [] if isinstance(e, dict)}


def jacket_asset(manifest: Mapping[str, Any]) -> str | None:
    """清单里封面的地址（``assets/<sha256>.png``），没有封面时为 None。"""
    files = manifest.get("files", {})
    return next((files[name].get("asset") for name in sorted(files) if JACKET_FILE.search(name)), None)


class BdonClient:
    def __init__(self, cfg: ChartsConfig, session: requests.Session | None = None):
        self.cfg = cfg
        self.base = cfg.base_url.rstrip("/") + "/"
        self.cache = Path(cfg.cache_dir)
        self.http = session or requests.Session()
        self.http.headers["User-Agent"] = USER_AGENT  # Session 自带 python-requests 的，setdefault 换不掉
        self._noted_bundled_index = False  # 「先用随包附带的曲目列表」说过了

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
        raise ConnectionError(f"无法下载 {url}：{last}") from last

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
        self,
        rel: str,
        url: str,
        max_age_s: float | None = None,
        asset: str | None = None,
        optional: bool = False,
        transform: Callable[[bytes], bytes] | None = None,
        outdated: bool = False,
    ) -> bytes:
        """``optional``：可有可无的文件（封面），主机这次打开程序后下载失败过就先不下载（抛出 :class:`HostDown`）。
        ``transform``：下载、校验之后，存进缓存之前对内容做的处理（把封面缩小）。
        ``outdated``：缓存里的那份已知是旧的，和过期了一样办——只试一次，下载不了先用旧的。"""
        retries = 3
        path = self.cache / rel
        if path.exists():
            if not outdated and (max_age_s is None or time.time() - path.stat().st_mtime < max_age_s):
                return path.read_bytes()
            if outdated or max_age_s:  # 过期了（不是强制刷新）：有旧的可用，只试一次；这次打开程序后主机失败过就先不下载
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
        if transform is not None:
            raw = transform(raw)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(raw)
        tmp.replace(path)
        return raw

    # —— 索引 / 清单 / 谱面 ——

    def index(self, refresh: bool = False) -> list[dict[str, Any]]:
        """曲目列表。下载不了、缓存里也没有时用随整包附带的那份（打包时的）：包里带的歌照常能打。"""
        ttl = 0.0 if refresh else self.cfg.index_ttl_hours * 3600
        try:
            # 有随包的那份可退：主机这次打开程序后下载失败过就不再等一遍
            raw = self._cached("charts.json", self.base + "charts.json", max_age_s=ttl, optional=(BUNDLE / BUNDLE_INDEX).is_file())
            data = json.loads(raw)
        except (ConnectionError, ChartNotFound):
            data = bundled_index()
            if data is None:
                raise
            if not self._noted_bundled_index:
                self._noted_bundled_index = True
                logger.warning("下载不了曲目列表，先用随包附带的那份（打包时的）：包里带的歌照常能打，之后新出的歌要等连得上谱面站")
        if data.get("format") != 2:
            logger.warning("未知的索引格式 %r，尝试继续解析", data.get("format"))
        return data.get("charts", [])

    def _known(self) -> tuple[dict[str, dict[str, Any]], float | None]:
        """手头的曲目列表里各张谱面的条目（不为这个联网）和这份列表下载的时刻：缓存里的那份；缓存里没有时是随整包
        附带的那份（时刻为 None）。"""
        path = self.cache / "charts.json"
        try:
            when: float | None = path.stat().st_mtime
            data = json.loads(path.read_bytes())
        except (OSError, ValueError):
            when, data = None, bundled_index()
        return chart_entries(data), when

    def manifest(self, music_id: int, difficulty: str, optional: bool = False) -> dict[str, Any]:
        """``optional``：只是为了取封面，见 :meth:`_cached`。缓存里的清单旧了（:meth:`_manifest_outdated`）就重新下载，
        下载不了先用旧的。"""
        key = f"{music_id}_{difficulty}"
        rel = self._manifest_rel(music_id, difficulty)
        return json.loads(self._cached(rel, self.base + f"charts/{key}.json", optional=optional, outdated=self._manifest_outdated(key, rel)))

    @staticmethod
    def _manifest_rel(music_id: int, difficulty: str) -> str:
        return f"manifests/{music_id}_{difficulty}.json"

    def _manifest_outdated(self, key: str, rel: str) -> bool:
        """缓存里的清单是不是旧的：比曲目列表下载得早，各文件大小的和又对不上列表里的 ``bytes``，就是谱面站在那之后
        重新生成过这张谱面。以前清单下载一次就一直用，谱面站改过的谱面打的还是旧的（实测 360 张里 2 张，
        其中一张音符时间差到 80 ms）。

        重新下载的清单比列表新，不会再判成旧的：``bytes`` 的算法哪天变了，也只是每次更新列表后各多下载一次清单。"""
        path = self.cache / rel
        try:
            cached_at = path.stat().st_mtime
        except OSError:
            return False  # 没有缓存：照常下载
        entries, listed_at = self._known()
        want = (entries.get(key) or {}).get("bytes")
        if listed_at is None or cached_at >= listed_at or not isinstance(want, int):
            return False
        try:
            files = json.loads(path.read_bytes()).get("files", {})
            outdated = sum(f.get("size", 0) for f in files.values()) != want
        except (OSError, ValueError, AttributeError, TypeError):
            outdated = True  # 读不了的清单：重新下载
        if outdated:
            logger.info("谱面站重新生成过 %s，重新下载它的清单", key)
        return outdated

    def asset(self, name: str) -> bytes:
        """按内容命名的资源文件（``assets/<sha256>.<扩展名>``），下载后校验，有缓存。"""
        return self._cached(name, self.base + name, asset=name)

    def notes_bytes(self, music_id: int, difficulty: str) -> bytes:
        """谱面站上这张谱面的谱面文件（JSON 原样内容，有缓存）；拆成几部分的按清单拼起来。"""
        man = self.manifest(music_id, difficulty)
        files = man.get("files", {})
        keys = sorted(k for k in files if k.startswith("score/") and k.endswith(".notes.json"))
        if not keys:
            raise ChartNotFound(f"{music_id}_{difficulty} 的清单中没有谱面文件")
        entry = files[keys[0]]
        if "asset" in entry:
            return self.asset(entry["asset"])
        if "parts" in entry:
            chunks = []
            for part_key, asset, size in entry["parts"]:
                raw = self.asset(asset)
                if len(raw) != size:
                    raise ValueError(f"{asset} 大小 {len(raw)} 与清单 {size} 不符")
                chunks.append(json.dumps(part_key).encode() + b":" + raw)
            return b"{" + b",".join(chunks) + b"}"
        raise ValueError(f"无法识别的清单条目：{entry}")

    @functools.cached_property
    def _bundled(self) -> dict[str, dict[str, Any]]:
        """随整包附带的谱面在打包时的曲目列表里的条目；包里没带时是空的。"""
        return chart_entries(bundled_index())

    def _bundled_notes(self, music_id: int, difficulty: str) -> dict[str, Any] | None:
        """随整包附带的这张谱面。包里没带、读不了，或者它已经不是谱面站现在的那一版（手头的曲目列表里这张的条目和
        打包时的对不上，见 :data:`SAME_CHART`）时为 None：照常从谱面站取，不比没带更糟。"""
        key = f"{music_id}_{difficulty}"
        path = BUNDLE / f"{key}.json.gz"
        if not path.is_file():
            return None
        then, now = self._bundled.get(key), self._known()[0].get(key)
        if then is None or now is None or any(then.get(f) != now.get(f) for f in SAME_CHART):
            logger.debug("随包附带的谱面 %s 不是谱面站现在的那一版，从谱面站取", key)
            return None
        try:
            data = json.loads(gzip.decompress(path.read_bytes()))
        except (OSError, EOFError, ValueError, zlib.error) as e:
            logger.warning("随包附带的谱面 %s 读不了（%s），从谱面站取", path, e)
            return None
        return data if isinstance(data, dict) else None

    def notes_raw(self, music_id: int, difficulty: str) -> dict[str, Any]:
        """谱面数据：随整包附带的那份还是谱面站现在的那一版就用它（不联网），否则从谱面站取（有缓存）。"""
        data = self._bundled_notes(music_id, difficulty)
        return data if data is not None else json.loads(self.notes_bytes(music_id, difficulty))

    def chart(self, music_id: int, difficulty: str, title: str = "") -> Chart:
        difficulty = difficulty.lower()
        if difficulty not in DIFFICULTIES:
            raise ValueError(f"未知难度 {difficulty}")
        return parse_live_score(self.notes_raw(music_id, difficulty), music_id, difficulty, title)

    # —— 封面 ——

    def jacket(self, music_id: int, difficulties: Iterable[str], shrink: Callable[[bytes], bytes] | None = None) -> bytes:
        """封面（PNG）。随包附带的（:data:`JACKETS`）直接用——封面换了图，更新一下就跟着换；包里没有的（发版之后才出的
        新歌）用缓存里的，缓存里也没有才下载。

        谱面站每张谱面的清单里带着封面（:data:`JACKET_FILE`，同一首歌各难度是同一张）：从 ``difficulties``
        （这首歌有的难度）里已经有清单的那个取，都没有就下载排在最前面那个的清单。``shrink``：存进缓存前把图缩小
        （原图 512x512、几百 KB，认歌用不着这么大）。

        封面只是帮着认歌（认不出还有曲名），主机这次打开程序后下载失败过就先不下载（抛出 :class:`HostDown`），
        免得缺的每张都等一遍。清单里没有封面时抛出 :class:`ChartNotFound`。"""
        rel = f"jackets/{music_id}.png"
        for path in (JACKETS / f"{music_id}.png", self.cache / rel):
            if path.exists():
                return path.read_bytes()
        # sorted 是稳定的：都没有清单时就是给的第一个
        diffs = sorted(difficulties, key=lambda d: not (self.cache / self._manifest_rel(music_id, d)).exists())
        if not diffs:
            raise ChartNotFound(f"{music_id} 没有谱面，取不到封面")
        asset = jacket_asset(self.manifest(music_id, diffs[0], optional=True))
        if not asset:
            raise ChartNotFound(f"{music_id}_{diffs[0]} 的清单里没有封面")
        return self._cached(rel, self.base + asset, asset=asset, optional=True, transform=shrink)

    def jacket_cached(self, music_id: int) -> bool:
        """这首歌的封面不用下载：随包附带，或者缓存里有。"""
        return (JACKETS / f"{music_id}.png").exists() or (self.cache / f"jackets/{music_id}.png").exists()


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
    if refused(err):  # 连得上：不叫人去填代理端口（#142：照着试了一圈，都还是 403）
        why = "网站连得上，是它拒绝了这次请求（按 IP 或程序标识拦的），不是代理端口没填对。换一条线路（开或关代理）再试一次，还是这样就是网站不让本程序下载"
    else:
        why = (
            "浏览器能打开这个网站时，多半是浏览器走了代理插件、自动代理脚本、HTTP/3 或安全 DNS，"
            "本程序用不了这些；开着代理软件的话，请在「网络代理」里填它的端口"
        )
    logger.warning(
        "下载不了 %s（%s），%s。%s。下次打开程序前，过期的曲目列表先用旧缓存、缺的封面先不下载",
        host,
        failure_kind(err),
        route(url),
        why,
    )


def refused(err: BaseException | None) -> int | None:
    """网站自己回了拒绝（:data:`REFUSED`）时的状态码，别的失败为 None。走代理时代理回的拒绝是另一种错
    （``Tunnel connection failed``，见 :func:`failure_kind`），不算。"""
    if isinstance(err, requests.exceptions.HTTPError) and err.response is not None and err.response.status_code in REFUSED:
        return err.response.status_code
    return None


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
