"""推送到手机：各推送方式的请求、出错处理，任务结束时推什么。"""

import io
import json
import logging
import threading
import urllib.error
import urllib.parse

import pytest

from ournotes_auto import notify
from ournotes_auto.config import NotifyConfig
from ournotes_auto.notify import NotifyError, build_request

SECRET = "secret-topic_42"


def cfg(channel="ntfy", target=SECRET, events="done,error,maintenance"):
    return NotifyConfig(channel=channel, target=target, events=events)


def test_wanted():
    """没设推送方式不推；events 不分大小写、可以有空格。"""
    assert notify.wanted(cfg(), "done")
    assert not notify.wanted(cfg(channel=" "), "error")
    assert notify.wanted(cfg(events=" Error , maintenance"), "error")
    assert not notify.wanted(cfg(events="error,maintenance"), "done")
    assert not notify.wanted(cfg(events=""), "done")


def test_ntfy():
    """ntfy：JSON 发到服务器根地址（中文标题不走请求头）；出错的提高优先级；自建服务填完整网址。"""
    url, body, ctype = build_request(cfg(target=f" {SECRET} "), "done", "连续演奏完成", "共演奏 3 局")
    assert (url, ctype) == ("https://ntfy.sh/", "application/json")
    assert json.loads(body) == {"topic": SECRET, "title": "OurNotes-Auto：连续演奏完成", "message": "共演奏 3 局"}
    assert json.loads(build_request(cfg(), "error", "t", "x")[1])["priority"] == 4
    url, body, _ = build_request(cfg(target="https://ntfy.example.com/sub/mine/"), "done", "t", "x")
    assert url == "https://ntfy.example.com/sub/" and json.loads(body)["topic"] == "mine"
    with pytest.raises(NotifyError, match="带上主题"):
        build_request(cfg(target="https://ntfy.example.com/"), "done", "t", "x")


def test_serverchan():
    """Server酱：Turbo 版的 SendKey 发到 sctapi，Server酱³ 的（sctp<数字>t…）发到自己的子域名；标题最长 32 个字。"""
    url, body, ctype = build_request(cfg("serverchan", "SCT123abc"), "done", "连续演奏完成", "共演奏 3 局")
    assert (url, ctype) == ("https://sctapi.ftqq.com/SCT123abc.send", "application/x-www-form-urlencoded")
    assert urllib.parse.parse_qs(body.decode()) == {"title": ["OurNotes-Auto：连续演奏完成"], "desp": ["共演奏 3 局"]}
    url, body, _ = build_request(cfg("ServerChan", "sctp1234tAbC"), "error", "很" * 40, "x")
    assert url == "https://1234.push.ft07.com/send/sctp1234tAbC.send"
    assert len(urllib.parse.parse_qs(body.decode())["title"][0]) == notify.SERVERCHAN_TITLE_MAX


def test_bark():
    """Bark：JSON 发到 /push，key 放在内容里；出错的发成时效性通知；App 里复制的网址（带不带 https://、后面带不带示例的
    推送内容）只取 key（发到路径里的会盖掉标题和内容）；自建服务器换成它的地址。"""
    key = "ynJ5Ft4atkMkWeo2PAvFhF"
    url, body, ctype = build_request(cfg("Bark", f" {key} "), "done", "连续演奏完成", "共演奏 3 局")
    assert (url, ctype) == ("https://api.day.app/push", "application/json")
    assert json.loads(body) == {"device_key": key, "title": "OurNotes-Auto：连续演奏完成", "body": "共演奏 3 局", "group": "OurNotes-Auto"}
    assert json.loads(build_request(cfg("bark", key), "error", "t", "x")[1])["level"] == "timeSensitive"
    for target in (f"https://api.day.app/{key}/", f"https://api.day.app/{key}/这里改成你自己的推送内容", f"api.day.app/{key}"):
        url, body, _ = build_request(cfg("bark", target), "done", "t", "x")
        assert url == "https://api.day.app/push" and json.loads(body)["device_key"] == key, target
    url, body, _ = build_request(cfg("bark", f"http://bark.example.com:8080/{key}?x=1"), "done", "t", "x")
    assert url == "http://bark.example.com:8080/push" and json.loads(body)["device_key"] == key
    for target in ("https://api.day.app/", "https://api.day.app"):
        with pytest.raises(NotifyError, match="带上 key"):
            build_request(cfg("bark", target), "done", "t", "x")


def test_webhook():
    url, body, _ = build_request(cfg("webhook", "https://example.com/hook?k=1"), "maintenance", "服务器维护中", "x")
    data = json.loads(body)
    assert url == "https://example.com/hook?k=1"
    assert (data["event"], data["title"], data["text"]) == ("maintenance", "OurNotes-Auto：服务器维护中", "x")
    assert isinstance(data["time"], int)
    with pytest.raises(NotifyError, match="http"):
        build_request(cfg("webhook", "example.com/hook"), "done", "t", "x")


def test_build_request_errors():
    with pytest.raises(NotifyError, match="推送方式应为 ntfy/serverchan/bark/webhook"):
        build_request(cfg("telegram"), "done", "t", "x")
    with pytest.raises(NotifyError, match="没有填写Server酱的 SendKey"):
        build_request(cfg("serverchan", " "), "done", "t", "x")
    with pytest.raises(NotifyError, match="没有填写Bark 的 key"):
        build_request(cfg("bark", ""), "done", "t", "x")


def test_post_errors(monkeypatch):
    """服务器回错误、连不上、网址格式不对：都换成 NotifyError，消息里不带推送地址。"""
    url = f"https://ntfy.sh/{SECRET}"

    def http_error(req, timeout):
        assert timeout == notify.TIMEOUT_S and req.get_header("User-agent").startswith("OurNotes-Auto/")
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, io.BytesIO(b"daily quota reached"))

    monkeypatch.setattr(notify.urllib.request, "urlopen", http_error)
    with pytest.raises(NotifyError, match="推送服务器返回 429：daily quota reached") as e:
        notify._post(url, b"{}", "application/json")
    assert SECRET not in str(e.value)

    def unreachable(req, timeout):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(notify.urllib.request, "urlopen", unreachable)
    with pytest.raises(NotifyError, match="连不上推送服务器：timed out") as e:
        notify._post(url, b"{}", "application/json")
    assert SECRET not in str(e.value)
    with pytest.raises(NotifyError, match="格式不对"):
        notify._post(f"ntfy.sh/{SECRET}", b"{}", "application/json")


def test_bad_url_hides_secret(monkeypatch):
    """地址中间有空格或控制字符：http.client 抛 InvalidURL，消息里带着路径（可能有 SendKey），换成「格式不对」。"""
    real = notify.urllib.request.urlopen
    monkeypatch.setattr(notify.urllib.request, "urlopen", real)
    for target in (f"https://sctapi.ftqq.com/{SECRET} x.send", f"https://sctapi.ftqq.com/{SECRET}\x01x.send"):
        with pytest.raises(NotifyError, match=r"^推送地址的格式不对$") as e:
            notify.send(cfg("serverchan", target), "test", "t", "x")
        assert SECRET not in str(e.value)


def test_send_checks_serverchan_reply(monkeypatch):
    """Server酱出错时也回 200：看回复里的 code。别的推送方式不看回复。"""
    reply = {}
    monkeypatch.setattr(notify, "_post", lambda url, body, ctype: reply["body"])
    reply["body"] = json.dumps({"code": 40001, "message": "bad pushkey"}).encode()
    with pytest.raises(NotifyError, match="Server酱：bad pushkey"):
        notify.send(cfg("serverchan", "SCT1"), "test", "t", "x")
    notify.send(cfg("ntfy"), "test", "t", "x")
    for ok in (b'{"code": 0}', b"<html>", b'{"data": 1}'):
        reply["body"] = ok
        notify.send(cfg("serverchan", "SCT1"), "test", "t", "x")


def test_send_hides_secret(monkeypatch):
    """服务器回的出错消息里带着推送地址等于密码的部分（Bark 找不到 key 时回的消息里有 key）：换成 ***。"""
    key = "ynJ5Ft4atkMkWeo2PAvFhF"
    sent = []

    def bad_key(req, timeout):
        sent.append((req.full_url, json.loads(req.data)["device_key"]))
        detail = f'{{"code":400,"message":"failed to get device token: failed to get [{key}] device token from database"}}'
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(detail.encode()))

    monkeypatch.setattr(notify.urllib.request, "urlopen", bad_key)
    for target in (key, f"https://api.day.app/{key}/这里改成你自己的推送内容"):
        with pytest.raises(NotifyError, match=r"推送服务器返回 400：.*failed to get \[\*\*\*\] device token") as e:
            notify.send(cfg("bark", target), "test", "t", "x")
        assert key not in str(e.value)
    assert sent == [("https://api.day.app/push", key)] * 2

    def echo(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(f"no topic {SECRET}".encode()))

    monkeypatch.setattr(notify.urllib.request, "urlopen", echo)
    with pytest.raises(NotifyError, match=r"no topic \*\*\*$") as e:
        notify.send(cfg("ntfy", f"https://ntfy.example.com/{SECRET}"), "test", "t", "x")
    assert SECRET not in str(e.value)


def test_push_failure_only_warns(monkeypatch, caplog):
    """推送失败（包括意外的错误）只记警告；不要的消息不发；wait=False 时在后台线程发。"""
    sent = []

    def send(c, event, title, text):
        sent.append((event, title, threading.current_thread().name))
        raise RuntimeError("boom")

    monkeypatch.setattr(notify, "send", send)
    with caplog.at_level(logging.WARNING, logger="ournotes_auto.notify"):
        notify.push(cfg(events="error"), "done", "完成", "x")
        assert sent == []
        notify.push(cfg(), "error", "出错", "x")
    assert sent == [("error", "出错", "MainThread")]
    assert caplog.messages == ["推送「出错」失败：boom"]

    done = threading.Event()
    monkeypatch.setattr(notify, "send", lambda *a: sent.append(threading.current_thread().name) or done.set())
    notify.push(cfg(), "maintenance", "服务器维护中", "x", wait=False)
    assert done.wait(5) and sent[-1] == "notify"


def test_last_error():
    """记下最后一条错误日志的第一行；意外错误附上异常。"""
    log = logging.getLogger("test_notify.last")
    handler = notify.LastError()
    log.addHandler(handler)
    try:
        log.warning("不算")
        assert handler.last == ""
        log.error("画面 60s 没有变化\n第二行")
        assert handler.last == "画面 60s 没有变化"
        try:
            raise KeyError("x")
        except KeyError:
            log.exception("意外错误")
        assert handler.last == "意外错误：KeyError('x')"
    finally:
        log.removeHandler(handler)


@pytest.fixture
def pushed(monkeypatch):
    out = []
    monkeypatch.setattr(notify, "push", lambda c, event, title, text, wait=True: out.append((event, title, text)))
    return out


def test_task_end(pushed):
    """连续演奏、领取日常完成时推送；启动游戏、切换账号只在出错时推送；别的命令不推送。"""
    c = cfg()
    notify.task_end(c, "run", "挂机", 0, "共演奏 3 局：FC 2，AP 1，失败 0", "", 125)
    notify.task_end(c, "daily", None, 0, "", "", 30)
    notify.task_end(c, "start", "启动游戏", 0, "", "", 30)
    notify.task_end(c, "records", "打开统计窗口", 1, "", "x", 1)
    assert pushed == [
        ("done", "挂机完成", "共演奏 3 局：FC 2，AP 1，失败 0\n用时 2 分钟"),
        ("done", "领取日常完成", "用时 30 秒"),
    ]
    pushed.clear()
    notify.task_end(c, "switch-account", None, 1, "", "找不到账号 user_12", 90)
    notify.task_end(c, "run", "清体力", 2, "", "", 7200)
    assert pushed == [
        ("error", "切换账号出错", f"找不到账号 user_12\n用时 2 分钟\n{notify.LOG_HINT}"),
        ("error", "清体力出错", f"退出码 2\n用时 2.0 小时\n{notify.LOG_HINT}"),
    ]
