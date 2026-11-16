"""谱面站客户端的下载、缓存和连不上时的处理（ournotes_auto/charts/bdon.py，#108）。"""

import dataclasses
import gzip
import hashlib
import json
import logging
import os
import time

import pytest
import requests

from ournotes_auto import __version__
from ournotes_auto.charts import bdon
from ournotes_auto.charts.bdon import BdonClient, ChartNotFound, HostDown, failure_kind, launched, refused, route
from ournotes_auto.config import ChartsConfig

RESET = requests.ConnectionError(
    "('Connection aborted.', ConnectionResetError(10054, '远程主机强迫关闭了一个现有的连接。', None, 10054, None))"
)
INDEX = json.dumps({"format": 2, "charts": [{"musicId": 1}]}).encode()


def response(status: int, body: bytes = b"") -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = body
    return r


class FakeHttp:
    """按顺序返回给定的结果（异常就抛出），记下请求的网址。"""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.urls = []
        self.timeouts = []
        self.headers = {}

    def get(self, url, timeout):
        self.urls.append(url)
        self.timeouts.append(timeout)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


@pytest.fixture
def sleeps(monkeypatch):
    out = []
    monkeypatch.setattr(bdon.time, "sleep", out.append)
    monkeypatch.setattr(bdon, "_explained", set())
    monkeypatch.setattr(bdon, "route", lambda url: "直连")
    monkeypatch.delenv(bdon.LAUNCHED_ENV, raising=False)  # 没有界面：这次打开程序从测试进程开始算
    return out


def client(tmp_path, *outcomes) -> BdonClient:
    return BdonClient(ChartsConfig(cache_dir=str(tmp_path)), session=FakeHttp(*outcomes))


def stale_index(tmp_path, hours: float = 13) -> None:
    path = tmp_path / "charts.json"
    path.write_bytes(INDEX)
    old = time.time() - hours * 3600
    os.utime(path, (old, old))


def down_file(tmp_path, host="assets.bdon.moe"):
    return tmp_path / bdon.DOWN_DIR / host


def test_user_agent_has_version():
    assert __version__ in bdon.USER_AGENT
    assert BdonClient(ChartsConfig()).http.headers["User-Agent"] == bdon.USER_AGENT


def test_get_no_sleep_after_last_try(tmp_path, sleeps):
    c = client(tmp_path, RESET, RESET, RESET)
    with pytest.raises(ConnectionError, match="无法下载"):
        c._get(c.base + "charts.json")
    assert len(c.http.urls) == 3
    assert sleeps == [1.5, 3.0]  # 第三次失败后不再白等
    assert c.http.timeouts == [(10, 30), (20, 30), (30, 30)]  # 连接超时逐次放宽，最后一次和原来一样
    assert down_file(tmp_path).exists()


def test_get_success_clears_down(tmp_path, sleeps):
    c = client(tmp_path, RESET, response(200, b"ok"))
    down_file(tmp_path).parent.mkdir(parents=True)
    down_file(tmp_path).touch()
    assert c._get(c.base + "charts.json") == b"ok"
    assert sleeps == [1.5]
    assert not down_file(tmp_path).exists()


def test_get_404_not_retried(tmp_path, sleeps):
    c = client(tmp_path, response(404))
    down_file(tmp_path).parent.mkdir(parents=True)
    down_file(tmp_path).touch()
    with pytest.raises(ChartNotFound):
        c._get(c.base + "charts/1_expert.json")
    assert len(c.http.urls) == 1 and sleeps == []
    assert not down_file(tmp_path).exists()  # 网站连得上，标记清掉


def test_stale_index_tries_once_then_waits(tmp_path, sleeps, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger=bdon.logger.name)
    stale_index(tmp_path)
    c = client(tmp_path, RESET)
    assert c.index() == [{"musicId": 1}]
    assert len(c.http.urls) == 1 and sleeps == []  # 有旧缓存时只试一次
    assert c.http.timeouts == [(10, 30)]
    assert "使用过期缓存" in caplog.text
    # 这次打开程序后面的任务（新的演奏进程，界面没关）直接用旧缓存，不再等
    monkeypatch.setenv(bdon.LAUNCHED_ENV, str(time.time() - 3600))
    again = client(tmp_path)
    assert again.index() == [{"musicId": 1}]
    assert again.http.urls == []
    assert "这次打开程序后下载失败过，先用过期缓存" in caplog.text
    # 下次打开程序再试，成功就更新缓存
    monkeypatch.setenv(bdon.LAUNCHED_ENV, str(time.time() + 1))
    later = client(tmp_path, response(200, json.dumps({"format": 2, "charts": []}).encode()))
    assert later.index() == []
    assert len(later.http.urls) == 1
    assert not down_file(tmp_path).exists()
    assert client(tmp_path).index() == []  # 新缓存在有效期内


def test_refresh_and_first_run_keep_retries(tmp_path, sleeps):
    down_file(tmp_path).parent.mkdir(parents=True)
    down_file(tmp_path).touch()
    # 第一次运行（没有缓存）：照常重试三次
    c = client(tmp_path, RESET, RESET, response(200, INDEX))
    assert c.index() == [{"musicId": 1}]
    assert len(c.http.urls) == 3
    # 强制刷新：主机刚失败过也照常重试
    down_file(tmp_path).touch()
    c = client(tmp_path, RESET, RESET, RESET)
    assert c.index(refresh=True) == [{"musicId": 1}]  # 下载不了还是用旧的
    assert len(c.http.urls) == 3


def test_chart_files_keep_retries_when_host_down(tmp_path, sleeps):
    down_file(tmp_path).parent.mkdir(parents=True)
    down_file(tmp_path).touch()
    c = client(tmp_path, RESET, RESET, response(200, b"{}"))
    assert c.manifest(1, "expert") == {}  # 谱面是打歌必需的，照常重试
    assert len(c.http.urls) == 3


SHA_A = "a" * 64
JACKET = b"\x89PNG fake jacket"


def jacket_manifest(asset=None, name="livescene/textures/jkt_001_100001-0785d933.png") -> bytes:
    """一份带封面的清单；``asset`` 没给时按 JACKET 的内容算（文件名就是内容的 sha256）。"""
    asset = asset or f"assets/{hashlib.sha256(JACKET).hexdigest()}.png"
    files = {
        "score/0001_03.notes.json": {"asset": f"assets/{SHA_A}.json", "size": 1},
        "livescene/textures/lane_base-d14cea1c.png": {"asset": f"assets/{'b' * 64}.png", "size": 1},
        "livescene/textures/not_jkt_x.png": {"asset": f"assets/{'c' * 64}.png", "size": 1},
    }
    if name:
        files[name] = {"asset": asset, "size": len(JACKET)}
    return json.dumps({"files": files}).encode()


def test_jacket_from_chart_manifest(tmp_path, sleeps):
    """封面从谱面站取（#142）：下载这首歌一张谱面的清单，里面的 jkt_*.png 就是封面；内容按文件名校验，存进缓存前
    先缩小；之后只读缓存。不请求别的网站。"""
    c = client(tmp_path, response(200, jacket_manifest()), response(200, JACKET))
    assert c.jacket(100001, ["expert", "hard"], shrink=lambda raw: b"small:" + raw[:4]) == b"small:\x89PNG"
    asset = hashlib.sha256(JACKET).hexdigest()
    assert c.http.urls == [
        "https://assets.bdon.moe/chart-site/charts/100001_expert.json",
        f"https://assets.bdon.moe/chart-site/assets/{asset}.png",
    ]
    assert (tmp_path / "jackets/100001.png").read_bytes() == b"small:\x89PNG" and c.jacket_cached(100001)
    assert (tmp_path / "manifests/100001_expert.json").exists()  # 清单留着，打这张谱面时不用再下载
    assert c.jacket(100001, ["expert"]) == b"small:\x89PNG" and len(c.http.urls) == 2


def test_jacket_uses_cached_manifest_and_old_cache(tmp_path, sleeps):
    """已经有清单的难度先用（不为了封面再下载一份）；以前从别处下载的缩略图在缓存里，照常用、不联网。"""
    (tmp_path / "manifests").mkdir()
    (tmp_path / "manifests/100001_easy.json").write_bytes(jacket_manifest())
    c = client(tmp_path, response(200, JACKET))
    assert c.jacket(100001, ["expert", "hard", "easy"]) == JACKET
    assert [u.rsplit("/", 2)[-2] for u in c.http.urls] == ["assets"]
    (tmp_path / "jackets/7.png").write_bytes(b"old thumb")
    offline = client(tmp_path)
    assert offline.jacket(7, ["expert"]) == b"old thumb" and offline.http.urls == []


def test_jacket_bundled(tmp_path, sleeps, monkeypatch):
    """随包附带的封面：不用下载、不往缓存里再放一份；缓存里也有时用包里的（封面换了图，更新一下就跟着换）；
    包里没有的（发版之后才出的新歌）用缓存里的，缓存里也没有才从谱面站取。"""
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    (bundled / "5.png").write_bytes(b"bundled 5")
    (bundled / "6.png").write_bytes(b"bundled 6")
    monkeypatch.setattr(bdon, "JACKETS", bundled)
    cache = tmp_path / "cache"
    (cache / "jackets").mkdir(parents=True)
    (cache / "jackets/6.png").write_bytes(b"cached 6")
    c = client(cache, response(200, jacket_manifest()), response(200, JACKET))
    (cache / "jackets/8.png").write_bytes(b"cached 8")
    assert c.jacket(5, ["expert"]) == b"bundled 5" and c.jacket(6, ["expert"]) == b"bundled 6" and c.jacket(8, ["expert"]) == b"cached 8"
    assert c.jacket_cached(5) and c.jacket_cached(6) and not c.jacket_cached(7)
    assert c.http.urls == [] and not (cache / "jackets/5.png").exists()
    assert c.jacket(7, ["expert"]) == JACKET and len(c.http.urls) == 2 and c.jacket_cached(7)


def test_jacket_missing_or_corrupt(tmp_path, sleeps):
    """清单里没有封面、这首歌没有谱面：ChartNotFound；内容和文件名对不上：ValueError，不存进缓存。"""
    c = client(tmp_path, response(200, jacket_manifest(name=None)))
    with pytest.raises(ChartNotFound, match="清单里没有封面"):
        c.jacket(1, ["expert"])
    with pytest.raises(ChartNotFound, match="没有谱面"):
        c.jacket(2, [])
    c = client(tmp_path, response(200, jacket_manifest(asset=f"assets/{SHA_A}.png")), response(200, JACKET))
    with pytest.raises(ValueError, match="校验失败"):
        c.jacket(3, ["expert"])
    assert not c.jacket_cached(3) and not c.jacket_cached(1)

    def broken(raw):
        raise ValueError("无法解码封面图片")

    c = client(tmp_path, response(200, jacket_manifest()), response(200, JACKET))
    with pytest.raises(ValueError, match="无法解码"):
        c.jacket(4, ["expert"], shrink=broken)
    assert not c.jacket_cached(4)


def test_jacket_skipped_when_host_down(tmp_path, sleeps):
    """谱面站这次打开程序后下载失败过：缺的封面（连同只为它下载的清单）先不下载，免得每张都等一遍；打歌要的清单照常重试。"""
    c = client(tmp_path, RESET, RESET, RESET)
    with pytest.raises(ConnectionError) as e:
        c.jacket(1, ["expert"])
    assert not isinstance(e.value, HostDown) and len(c.http.urls) == 3
    assert down_file(tmp_path).exists()
    with pytest.raises(HostDown):
        c.jacket(2, ["expert"])
    assert len(c.http.urls) == 3
    (tmp_path / "manifests").mkdir()
    (tmp_path / "manifests/3_expert.json").write_bytes(jacket_manifest())
    with pytest.raises(HostDown):  # 清单有了，封面那张图也先不下载
        c.jacket(3, ["expert"])
    assert len(c.http.urls) == 3


def test_no_requests_to_other_sites(tmp_path, sleeps):
    """认歌要的东西只从谱面站取：曲目列表、清单、封面的网址都在 base_url 下面；配置里没有别的网站的地址。"""
    index = json.dumps({"format": 2, "charts": [{"musicId": 100001, "difficulty": "expert", "title": "迷星叫", "bands": ["MyGO!!!!!"]}]}).encode()
    c = client(tmp_path, response(200, index), response(200, jacket_manifest()), response(200, JACKET))
    from ournotes_auto.charts.catalog import Catalog
    from ournotes_auto.nav.jacket import JacketMatcher

    catalog = Catalog.load(c)
    assert JacketMatcher.load(c, catalog).ids == []  # 假封面解不了，少认这一首：这里只看请求了哪里
    assert len(c.http.urls) == 3 and all(u.startswith("https://assets.bdon.moe/chart-site/") for u in c.http.urls)
    assert [f.name for f in dataclasses.fields(ChartsConfig)] == ["base_url", "cache_dir", "index_ttl_hours"]


def test_refused_no_proxy_port_hint(tmp_path, sleeps, caplog):
    """网站自己回 403（按 IP 或程序标识拦的，#142）：连得上，说明里不叫人去填代理端口；别的照旧（重试、记下主机）。"""
    caplog.set_level(logging.WARNING, logger=bdon.logger.name)
    c = client(tmp_path, response(403), response(403), response(403))
    with pytest.raises(ConnectionError, match="无法下载"):
        c.index()
    assert len(c.http.urls) == 3 and down_file(tmp_path).exists()
    explained = [r.getMessage() for r in caplog.records if r.getMessage().startswith("下载不了")]
    assert explained == [
        "下载不了 assets.bdon.moe（服务器返回 HTTP 403），直连。网站连得上，是它拒绝了这次请求（按 IP 或程序标识拦的），"
        "不是代理端口没填对。换一条线路（开或关代理）再试一次，还是这样就是网站不让本程序下载。"
        "下次打开程序前，过期的曲目列表先用旧缓存、缺的封面先不下载"
    ]
    assert "「网络代理」" not in caplog.text


def test_refused():
    assert [refused(_http_error(s)) for s in (401, 403, 451)] == [401, 403, 451]
    for err in (_http_error(503), _http_error(429), RESET, requests.exceptions.ProxyError("Tunnel connection failed: 403 Forbidden"), None):
        assert refused(err) is None, err
    assert refused(requests.HTTPError("403")) is None  # 没带响应的不算


def test_down_before_launch_ignored(tmp_path, sleeps, monkeypatch):
    """上次打开程序时记下的失败不算：这次打开后照常下载。"""
    stale_index(tmp_path)
    down_file(tmp_path).parent.mkdir(parents=True)
    down_file(tmp_path).touch()
    monkeypatch.setenv(bdon.LAUNCHED_ENV, str(time.time() + 1))
    c = client(tmp_path, response(200, INDEX))
    assert c.index() == [{"musicId": 1}] and len(c.http.urls) == 1
    assert not down_file(tmp_path).exists()


def test_launched(monkeypatch):
    monkeypatch.setenv(bdon.LAUNCHED_ENV, "1700000000.5")
    assert launched() == 1700000000.5
    monkeypatch.setenv(bdon.LAUNCHED_ENV, "x")
    assert launched() == bdon._STARTED  # 读不懂就从这个进程开始算
    monkeypatch.delenv(bdon.LAUNCHED_ENV)
    assert launched() == bdon._STARTED <= time.time()


def test_explain_once_per_host(tmp_path, sleeps, caplog):
    caplog.set_level(logging.WARNING, logger=bdon.logger.name)
    c = client(tmp_path, RESET, RESET)
    for name in ("a", "b"):
        with pytest.raises(ConnectionError):
            c._get(f"https://haneoka.org/{name}.png", retries=1)
    explained = [r.getMessage() for r in caplog.records if r.getMessage().startswith("下载不了")]
    want = (
        "下载不了 haneoka.org（连接被中途断开），直连。浏览器能打开这个网站时，多半是浏览器走了代理插件、自动代理脚本、"
        "HTTP/3 或安全 DNS，本程序用不了这些；开着代理软件的话，请在「网络代理」里填它的端口。"
        "下次打开程序前，过期的曲目列表先用旧缓存、缺的封面先不下载"
    )
    assert explained == [want]


def test_cache_dir_unwritable_still_raises(tmp_path, sleeps):
    blocker = tmp_path / "file"
    blocker.write_text("")
    c = BdonClient(ChartsConfig(cache_dir=str(blocker)), session=FakeHttp(RESET))
    with pytest.raises(ConnectionError, match="无法下载"):  # 记不下失败的主机也照常报错
        c._get("https://haneoka.org/x", retries=1)


def _http_error(status):
    return requests.HTTPError(f"{status} Server Error", response=response(status))


@pytest.mark.parametrize(
    "err, kind",
    [
        (requests.exceptions.ProxyError("Unable to connect to proxy"), "代理连不上：代理软件没开或端口不对"),
        (
            requests.exceptions.ProxyError("OSError('Tunnel connection failed: 502 Bad Gateway')"),
            "代理拒绝转发：代理连不上网站，或要账号密码",
        ),
        (requests.exceptions.ConnectTimeout("Connection to haneoka.org timed out. (connect timeout=10)"), "超时"),
        (requests.exceptions.ReadTimeout("Read timed out. (read timeout=60)"), "超时"),
        (requests.ConnectionError("HTTPSConnectionPool(host='assets.bdon.moe', port=443): Read timed out."), "超时"),
        (requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED"), "加密连接失败"),
        (_http_error(503), "服务器返回 HTTP 503"),
        (RESET, "连接被中途断开"),
        (requests.ConnectionError("Failed to resolve 'haneoka.org' ([Errno 11001] getaddrinfo failed)"), "域名解析失败"),
        (requests.ConnectionError("[WinError 10061] 由于目标计算机积极拒绝，无法连接。"), "连接失败"),
        (None, "连接失败"),
    ],
)
def test_failure_kind(err, kind):
    assert failure_kind(err) == kind


def test_route_proxy_hides_password(monkeypatch):
    for key in ("http", "https", "all", "no"):
        monkeypatch.delenv(f"{key}_proxy", raising=False)
        monkeypatch.delenv(f"{key.upper()}_PROXY", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://user:secret@127.0.0.1:7890")
    text = route("https://haneoka.org/api")
    assert text.startswith("走的是环境变量里的代理")
    assert "http://user:***@127.0.0.1:7890" in text and "secret" not in text


def test_route_system_proxy(monkeypatch):
    monkeypatch.setattr(requests.utils, "get_environ_proxies", lambda url: {"https": "http://127.0.0.1:7890"})
    monkeypatch.setattr(bdon, "getproxies_environment", dict)  # 环境变量里没有代理设置，才是读的系统代理
    assert route("https://haneoka.org/api") == "走的是系统代理 http://127.0.0.1:7890"


def test_route_direct(monkeypatch):
    monkeypatch.setattr(requests.utils, "get_environ_proxies", lambda url: {})
    infos = [(2, 1, 6, "", ("104.16.4.14", 443)), (2, 1, 6, "", ("104.16.4.14", 443))]
    infos.append((23, 1, 6, "", ("2606::1", 443, 0, 0)))
    monkeypatch.setattr(bdon.socket, "getaddrinfo", lambda host, port, type: infos)
    monkeypatch.setattr(bdon, "uses_pac", lambda: False)
    assert route("https://haneoka.org/api") == "直连，解析到 104.16.4.14, 2606::1"
    monkeypatch.setattr(bdon, "uses_pac", lambda: True)
    assert route("https://haneoka.org/api").endswith("；系统设了自动代理脚本（PAC），本程序读不到它")

    def fail(host, port, type):
        raise OSError("[Errno 11001] getaddrinfo failed")

    monkeypatch.setattr(bdon.socket, "getaddrinfo", fail)
    assert route("https://haneoka.org/api").startswith("直连，域名解析失败（[Errno 11001] getaddrinfo failed）")


# ---------------------------------------------------------------- 随整包附带的谱面、旧了的清单

ENTRY = {"musicId": 7, "difficulty": "expert", "bytes": 1000, "notes": 3, "fullComboCount": 3, "durationMs": 90000, "title": "x"}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def chart_manifest(notes: bytes, other: int = 0) -> bytes:
    """只有谱面文件和另一个文件（``other`` 字节）的清单。"""
    files = {"score/0001_03.notes.json": {"asset": f"assets/{sha(notes)}.json", "size": len(notes)}, "audio/x.m4a": {"asset": "assets/x.m4a", "size": other}}
    return json.dumps({"files": files}).encode()


def put_bundle(tmp_path, monkeypatch, entries, notes):
    """包里带的：打包时的曲目列表 + 谱面（``"{musicId}_{difficulty}"`` → 谱面文件的内容）。"""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / bdon.BUNDLE_INDEX).write_text(json.dumps({"format": 2, "charts": entries}), encoding="utf-8")
    for key, raw in notes.items():
        (bundle / f"{key}.json.gz").write_bytes(gzip.compress(raw))
    monkeypatch.setattr(bdon, "BUNDLE", bundle)
    return bundle


def put_index(cache, entries, age_s: float = 0.0) -> None:
    """缓存里的曲目列表，``age_s`` 秒前下载的。"""
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / "charts.json"
    path.write_bytes(json.dumps({"format": 2, "charts": entries}).encode())
    when = time.time() - age_s
    os.utime(path, (when, when))


def test_index_falls_back_to_bundled(tmp_path, sleeps, monkeypatch, caplog):
    """下载不了曲目列表、缓存里也没有：用随整包附带的那份（说一次），包里的歌照常能打；主机这次打开程序后失败过
    就不再等一遍。谱面站上没有这个文件了也一样。有缓存时照旧用缓存（过期的也是）。"""
    caplog.set_level(logging.WARNING, logger=bdon.logger.name)
    put_bundle(tmp_path, monkeypatch, [ENTRY], {})
    cache = tmp_path / "cache"
    c = client(cache, RESET, RESET, RESET)
    assert c.index() == [ENTRY] and len(c.http.urls) == 3
    assert c.index() == [ENTRY] and len(c.http.urls) == 3
    said = [r.getMessage() for r in caplog.records if "随包附带" in r.getMessage()]
    assert said == ["下载不了曲目列表，先用随包附带的那份（打包时的）：包里带的歌照常能打，之后新出的歌要等连得上谱面站"]
    again = client(cache)  # 另一个进程，还是这次打开程序
    assert again.index() == [ENTRY] and again.http.urls == []
    assert client(tmp_path / "gone", response(404)).index() == [ENTRY]
    (tmp_path / "old").mkdir()
    stale_index(tmp_path / "old")
    assert client(tmp_path / "old", RESET).index() == [{"musicId": 1}]
    # 包里那份坏了：和没带一样，照旧报错
    (bdon.BUNDLE / bdon.BUNDLE_INDEX).write_text("{坏的", encoding="utf-8")
    with pytest.raises(ConnectionError, match="无法下载"):
        client(tmp_path / "none", RESET, RESET, RESET).index()


def test_bundled_chart(tmp_path, sleeps, monkeypatch, caplog):
    """随整包附带的谱面：手头的曲目列表里这张和打包时一样就用它，不联网；谱面站重新生成过（SAME_CHART 里有一项变了）、
    包里那份读不了、打包时的曲目列表里没有它，都照常从谱面站取。"""
    notes, newer = b'{"v": 1}', b'{"v": 2}'
    bundle = put_bundle(tmp_path, monkeypatch, [ENTRY], {"7_expert": notes})
    offline = client(tmp_path / "a")  # 缓存里没有曲目列表：手头的就是包里那份
    assert offline.notes_raw(7, "expert") == {"v": 1} and offline.http.urls == []
    put_index(tmp_path / "b", [{**ENTRY, "title": "别的项变了不算", "level": 30}])
    c = client(tmp_path / "b")
    assert c.notes_raw(7, "expert") == {"v": 1} and c.http.urls == []

    for i, name in enumerate(bdon.SAME_CHART):
        put_index(tmp_path / f"c{i}", [{**ENTRY, name: ENTRY[name] + 1}])
        c = client(tmp_path / f"c{i}", response(200, chart_manifest(newer)), response(200, newer))
        assert c.notes_raw(7, "expert") == {"v": 2}, name
        assert [u.rsplit("/", 2)[-2] for u in c.http.urls] == ["charts", "assets"]
    put_index(tmp_path / "d", [{**ENTRY, "difficulty": "hard"}])  # 现在的曲目列表里没有这张了
    with pytest.raises(ChartNotFound):
        client(tmp_path / "d", response(404)).notes_raw(7, "expert")

    caplog.set_level(logging.WARNING, logger=bdon.logger.name)
    for i, broken in enumerate((b"not gzip", gzip.compress(notes)[:-6], gzip.compress(b"[1]"), gzip.compress(b"{"))):
        (bundle / "7_expert.json.gz").write_bytes(broken)
        c = client(tmp_path / f"e{i}", response(200, chart_manifest(newer)), response(200, newer))
        assert c.notes_raw(7, "expert") == {"v": 2}, broken
    assert caplog.text.count("读不了") == 3  # 不是字典的那份没什么可说的
    # 打包时的曲目列表里没有这张（包里的文件和列表对不上号）：不用
    (bundle / "7_expert.json.gz").write_bytes(gzip.compress(notes))
    (bundle / bdon.BUNDLE_INDEX).write_text(json.dumps({"format": 2, "charts": []}), encoding="utf-8")
    put_index(tmp_path / "f", [ENTRY])
    c = client(tmp_path / "f", response(200, chart_manifest(newer)), response(200, newer))
    assert c.notes_raw(7, "expert") == {"v": 2}


def test_outdated_manifest(tmp_path, sleeps, caplog):
    """缓存里的清单比曲目列表下载得早、各文件大小的和又对不上列表里的 bytes：谱面站重新生成过这张谱面，重新下载清单
    （以前下载一次就一直用，谱面站改过的谱面打的还是旧的）。对得上的、比列表新的、列表里没写 bytes 的不动；
    下载不了先用旧的。"""
    old, new = b'{"v": 1}', b'{"v": 22}'
    old_total, new_total = len(old) + 10, len(new) + 10

    def cache_with(name, listed, manifest_age_s=3600.0):
        """缓存：旧谱面的清单和谱面文件，曲目列表里这张的 bytes 是 ``listed``（None：没写）。"""
        cache = tmp_path / name
        (cache / "manifests").mkdir(parents=True)
        (cache / "assets").mkdir()
        path = cache / "manifests/7_expert.json"
        path.write_bytes(chart_manifest(old, other=10))
        when = time.time() - manifest_age_s
        os.utime(path, (when, when))
        (cache / f"assets/{sha(old)}.json").write_bytes(old)
        put_index(cache, [{k: v for k, v in {**ENTRY, "bytes": listed}.items() if v is not None}])
        return cache

    caplog.set_level(logging.INFO, logger=bdon.logger.name)
    cache = cache_with("a", new_total)
    c = client(cache, response(200, chart_manifest(new, other=10)), response(200, new))
    assert c.notes_raw(7, "expert") == {"v": 22}
    assert [u.rsplit("/", 2)[-2] for u in c.http.urls] == ["charts", "assets"]
    assert "谱面站重新生成过 7_expert，重新下载它的清单" in caplog.text
    assert client(cache).notes_raw(7, "expert") == {"v": 22}  # 清单换成新的了：不再下载
    # 重新下载的还是对不上（bytes 的算法变了之类）：清单比列表新，不会回回都下载
    cache = cache_with("b", 999)
    c = client(cache, response(200, chart_manifest(old, other=10)))
    assert c.notes_raw(7, "expert") == {"v": 1} and len(c.http.urls) == 1
    assert client(cache).notes_raw(7, "expert") == {"v": 1}
    # 不动的
    assert client(cache_with("c", old_total)).notes_raw(7, "expert") == {"v": 1}
    assert client(cache_with("d", new_total, manifest_age_s=-5)).notes_raw(7, "expert") == {"v": 1}
    assert client(cache_with("e", None)).notes_raw(7, "expert") == {"v": 1}
    # 下载不了：先用旧的，只试一次；这次打开程序后失败过就不试了
    cache = cache_with("f", new_total)
    c = client(cache, RESET)
    assert c.notes_raw(7, "expert") == {"v": 1} and len(c.http.urls) == 1
    assert client(cache).notes_raw(7, "expert") == {"v": 1}
    # 读不了的清单也重新下载
    cache = cache_with("g", old_total)
    (cache / "manifests/7_expert.json").write_bytes(b"{")
    os.utime(cache / "manifests/7_expert.json", (time.time() - 3600,) * 2)
    assert client(cache, response(200, chart_manifest(old, other=10))).notes_raw(7, "expert") == {"v": 1}
    # 取封面时用到的清单也一样
    cache = cache_with("h", new_total)
    c = client(cache, response(200, jacket_manifest()), response(200, JACKET))
    assert c.jacket(7, ["expert"]) == JACKET and [u.rsplit("/", 2)[-2] for u in c.http.urls] == ["charts", "assets"]


def test_notes_split_into_parts(tmp_path, sleeps):
    """大的谱面文件按顶层键拆开放：按清单拼回去；哪一部分的大小和清单对不上就报错。"""
    parts = [("format", b'"f"'), ("notes", b"[1, 2]")]
    entry = {"parts": [[k, f"assets/{sha(raw)}.json", len(raw)] for k, raw in parts], "size": sum(len(raw) for _, raw in parts)}
    manifest = json.dumps({"files": {"score/0001_03.notes.json": entry}}).encode()
    c = client(tmp_path / "a", response(200, manifest), *(response(200, raw) for _, raw in parts))
    assert c.notes_bytes(7, "expert") == b'{"format":"f","notes":[1, 2]}' and c.notes_raw(7, "expert") == {"format": "f", "notes": [1, 2]}
    entry["parts"][1][2] += 1
    c = client(tmp_path / "b", response(200, json.dumps({"files": {"score/0001_03.notes.json": entry}}).encode()), *(response(200, raw) for _, raw in parts))
    with pytest.raises(ValueError, match="与清单 7 不符"):
        c.notes_raw(7, "expert")
