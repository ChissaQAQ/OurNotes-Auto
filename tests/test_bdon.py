"""谱面站客户端的下载、缓存和连不上时的处理（ournotes_auto/charts/bdon.py，#108）。"""

import json
import logging
import os
import time

import pytest
import requests

from ournotes_auto import __version__
from ournotes_auto.charts import bdon
from ournotes_auto.charts.bdon import BdonClient, ChartNotFound, HostDown, failure_kind, launched, route
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


def test_jacket_skipped_when_host_down(tmp_path, sleeps):
    c = client(tmp_path, RESET, RESET, RESET)
    with pytest.raises(ConnectionError) as e:
        c.jacket(1, "/assets/intl/jkt_1.png")
    assert not isinstance(e.value, HostDown) and len(c.http.urls) == 3
    assert down_file(tmp_path, "haneoka.org").exists()
    with pytest.raises(HostDown):
        c.jacket(2, "/assets/intl/jkt_2.png")
    assert len(c.http.urls) == 3  # 这次打开程序后主机失败过：后面缺的封面先不下载


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
