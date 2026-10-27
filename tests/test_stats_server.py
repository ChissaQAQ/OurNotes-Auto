"""「统计与成就」窗口背后的服务：要密钥、Host 只认 127.0.0.1:<端口>、只给统计页那几个文件；导出、预览（什么都不写）、
导入（导入后重新统计）；有打歌任务在运行时不导入；密钥不记进日志；窗口关掉后退出。"""

import http.client
import io
import json
import logging
import socket
import threading
from pathlib import Path
from urllib.parse import quote

import pytest

from ournotes_auto import backup, release, stats, stats_server, stats_window
from ournotes_auto.runner import RunStats
from ournotes_auto.stats import Tracker
from test_backup import OFFSETS, STATE, T0, H, cli_main, files, make, rec, ses  # noqa: F401 - cli_main 是 fixture

PAGE = "<!doctype html><title>统计页</title>".encode()


@pytest.fixture(autouse=True)
def same_pc(monkeypatch):
    monkeypatch.setattr(backup, "machine_id", lambda: "pc-1")


@pytest.fixture
def serve():
    """``serve(data)`` 在后台起一个服务，测试完关掉。"""
    started = []

    def start(data, refresh=None):
        srv = stats_server.Server(data, refresh)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        started.append(srv)
        return srv

    yield start
    for srv in started:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def old(tmp_path):
    """旧的包：两局演奏、一次运行、一个成就、学到的 offset；有统计页。"""
    data = make(tmp_path / "old" / "data", [rec(1, T0), rec(2, T0 + 60)], [ses(T0 - H)], {"fc_1": T0}, STATE)
    (data / stats.PAGE_HTML).write_bytes(PAGE)
    (data / stats.PAGE_VER).write_text("statsVer(1)", encoding="utf-8")
    return data


@pytest.fixture
def new(tmp_path):
    """新的包：已经有旧的包里的一局。"""
    return make(tmp_path / "new" / "data", [rec(1, T0)])


def call(srv, method, path, body=None, headers=None, key=True):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=10)
    try:
        conn.request(method, path, body=body, headers={**({stats_server.KEY_HEADER: srv.key} if key else {}), **(headers or {})})
        r = conn.getresponse()
        return r.status, dict(r.getheaders()), r.read()
    finally:
        conn.close()


def post(srv, path, zip_bytes=None, name="", path_json=None):
    if path_json is not None:
        body, headers = json.dumps(path_json, ensure_ascii=False).encode(), {"Content-Type": "application/json"}
    else:
        body, headers = zip_bytes, {"Content-Type": "application/zip", stats_server.NAME_HEADER: quote(name)}
    status, _, raw = call(srv, "POST", path, body, headers)
    return status, json.loads(raw)


def test_needs_key_and_host(old, serve):
    """没带密钥、密钥不对、Host 不是 127.0.0.1:<端口>（DNS 重绑定）都不给；密钥放在请求头或地址里都行。"""
    srv = serve(old)
    assert call(srv, "GET", "/stats.html", key=False)[0] == 403
    assert call(srv, "GET", "/stats.html", key=False, headers={stats_server.KEY_HEADER: srv.key + "x"})[0] == 403
    assert call(srv, "GET", "/stats.html?key=nope", key=False)[0] == 403
    for host in (f"evil.example:{srv.server_port}", f"localhost:{srv.server_port}", "127.0.0.1"):
        assert call(srv, "GET", "/stats.html", headers={"Host": host})[0] == 403
    status, headers, body = call(srv, "GET", f"/stats.html?key={srv.key}", key=False)
    assert (status, body, headers["Cache-Control"]) == (200, PAGE, "no-store")
    assert headers["Content-Type"].startswith("text/html")
    assert call(srv, "GET", "/")[2] == PAGE

    before = files(old)
    for path in ("/api/export", "/api/preview", "/api/import"):
        method = "GET" if path == "/api/export" else "POST"
        status, _, body = call(srv, method, path, body=b"{}", key=False, headers={"Content-Type": "application/json"})
        assert (status, body) == (403, b"forbidden")
    assert files(old) == before


def test_only_page_files(old, serve):
    """只给统计页那几个文件（没生成的 404），data 里别的文件、往上走的路径都不给。"""
    srv = serve(old)
    status, headers, body = call(srv, "GET", f"/{stats.PAGE_VER}")
    assert (status, body) == (200, b"statsVer(1)") and headers["Content-Type"].startswith("text/javascript")
    assert call(srv, "GET", f"/{stats.PAGE_LIVE}")[0] == 404
    for path in ("/records.jsonl", "/achievements.json", "/../data/records.jsonl", "/%2e%2e/records.jsonl", "/api/nope", "/stats.html/"):
        assert call(srv, "GET", path)[0] == 404, path
    assert call(srv, "POST", "/stats.html", b"")[0] == 404


def test_export(old, tmp_path, serve):
    """导出：zip 能读回来，X-Backup-Info 里是文件名和导出了多少（只有 ASCII）；没有记录时说清楚。"""
    srv = serve(old)
    status, headers, body = call(srv, "GET", "/api/export")
    assert status == 200 and headers["Content-Type"] == "application/zip"
    info = json.loads(headers[stats_server.INFO_HEADER])
    assert info["name"].startswith("OurNotes-Auto-backup-") and info["name"].endswith(".zip")
    assert (info["records"], info["sessions"], info["achievements"]) == (2, 1, 1)
    assert headers[stats_server.INFO_HEADER].isascii() and "filename*=UTF-8''OurNotes-Auto-backup-" in headers["Content-Disposition"]
    b = backup.read_zip(io.BytesIO(body), info["name"])
    assert (len(b.records), len(b.sessions), b.unlocked, b.offsets) == (2, 1, {"fc_1": T0}, OFFSETS)

    empty = serve(make(tmp_path / "empty"))
    status, headers, body = call(empty, "GET", "/api/export")
    assert status == 400 and json.loads(body) == {"ok": False, "error": "还没有演奏记录，不用备份"}


def test_preview_then_import(old, new, serve):
    """选的备份文件：预览什么都不写、不重新统计；导入合并进来、重新统计；再导入一次没有改动。"""
    refreshed = []
    srv = serve(new, refresh=lambda: refreshed.append(1))
    _, blob, _ = backup.export_bytes(old)
    before = files(new)
    status, j = post(srv, "/api/preview", blob, "旧的 备份.zip")
    assert status == 200 and j["ok"] and j["name"] == "旧的 备份.zip"
    assert j["total"] == {"records": 2, "sessions": 1, "achievements": 1}
    assert (j["records"], j["known"], j["bad"], j["sessions"], j["achievements"], j["offsets"], j["changed"], j["backup"]) == (
        1, 1, 0, 1, 1, True, True, "",
    )
    assert files(new) == before and not (new / backup.SAFETY_DIR).exists() and refreshed == []

    status, j = post(srv, "/api/import", blob, "旧的 备份.zip")
    assert status == 200 and (j["records"], j["changed"]) == (1, True) and refreshed == [1]
    assert j["backup"].startswith(str(new / backup.SAFETY_DIR))
    assert len((new / "records.jsonl").read_text(encoding="utf-8").splitlines()) == 2

    status, j = post(srv, "/api/import", blob, "旧的 备份.zip")
    assert status == 200 and (j["records"], j["known"], j["changed"], j["backup"]) == (0, 2, False, "") and refreshed == [1]


def test_import_refresh_failure(old, new, serve, caplog):
    """导入后重新统计出错：已经导入了，照样回成功（统计页下次刷新时会更新）。"""
    srv = serve(new, refresh=lambda: 1 / 0)
    _, blob, _ = backup.export_bytes(old)
    with caplog.at_level(logging.WARNING):
        status, j = post(srv, "/api/import", blob, "b.zip")
    assert status == 200 and j["records"] == 1 and "导入后更新统计页失败" in caplog.text


def test_import_from_dir(old, new, tmp_path, serve):
    """没导出过：填旧的包目录（资源管理器「复制文件地址」带的引号去掉）；没填、不是完整路径、格式不对都说清楚。"""
    srv = serve(new)
    status, j = post(srv, "/api/preview", path_json={"path": f' "{old.parent}" '})
    assert status == 200 and (j["records"], j["known"], j["sessions"]) == (1, 1, 1) and j["name"] == str(old)
    cases = [
        ({"path": "  "}, "先填旧的包目录"),
        ({"path": "OurNotes-Auto"}, "要填完整的路径"),
        ({"path": str(tmp_path / "nope")}, "找不到"),
        ({"path": str(new.parent)}, "这个包自己的记录"),
        ({"dir": str(old)}, "请求的格式不对"),
        (["x"], "请求的格式不对"),
    ]
    for body, error in cases:
        status, j = post(srv, "/api/import", path_json=body)
        assert status == 400 and j["ok"] is False and error in j["error"], body
    status, j = post(srv, "/api/preview", b"PK not a zip", "坏的.zip")
    assert status == 400 and j["error"].startswith("坏的.zip 不是 OurNotes-Auto 的备份")
    status, j = post(srv, "/api/preview", b"", "")
    assert status == 400 and "选的文件" in j["error"]
    assert not (new / backup.SAFETY_DIR).exists()


def test_upload_too_big(new, serve, monkeypatch):
    """太大、Content-Length 不对：不读就拒绝。"""
    monkeypatch.setattr(backup, "MAX_UPLOAD", 10)
    srv = serve(new)
    for length in ("11", "abc", "-1"):
        conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=10)
        conn.putrequest("POST", "/api/import")
        conn.putheader(stats_server.KEY_HEADER, srv.key)
        conn.putheader("Content-Length", length)
        conn.endheaders()  # 不发内容：服务不读就回了
        r = conn.getresponse()
        assert r.status == 400 and "文件太大" in json.loads(r.read())["error"], length
        conn.close()


def test_no_import_while_task_running(old, new, serve):
    """有打歌任务在运行时预览、导入都不行，什么都不改。"""
    srv = serve(new)
    _, blob, _ = backup.export_bytes(old)
    tracker = Tracker(new, "挂机", clock=lambda: T0)
    tracker.start()
    try:
        before = files(new)
        for path in ("/api/preview", "/api/import"):
            status, j = post(srv, path, blob, "b.zip")
            assert status == 400 and "有打歌任务正在运行" in j["error"]
        assert files(new) == before and not (new / backup.SAFETY_DIR).exists()
    finally:
        tracker.finish(RunStats())


def test_key_not_logged(old, serve, caplog):
    """请求记日志时只记路径；请求行有问题时（这时还没有 path）照样回 400，记下的整行里的密钥换成 ***。"""
    srv = serve(old)
    with caplog.at_level(logging.DEBUG, logger="ournotes_auto.stats_server"):
        assert call(srv, "GET", f"/stats.html?key={srv.key}", key=False)[0] == 200
        with socket.create_connection(("127.0.0.1", srv.server_port), timeout=10) as s:
            s.sendall(f"GET /stats.html?key={srv.key} x HTTP/1.1\r\n\r\n".encode())
            reply = b""
            while chunk := s.recv(4096):
                reply += chunk
    assert reply.startswith(b"HTTP/1.0 400")
    assert "GET /stats.html → 200" in caplog.text and "Bad request syntax" in caplog.text and "***" in caplog.text
    assert srv.key not in caplog.text


def test_serve_until_window_closed(old, tmp_path, monkeypatch):
    """起来后把端口和密钥写进 ready 文件；窗口关掉（wait_window_closed 返回）后不再听。"""
    ready = tmp_path / "cache" / "ready.json"
    seen = []

    def wait():
        info = release.read_json(ready)
        conn = http.client.HTTPConnection("127.0.0.1", info["port"], timeout=10)
        conn.request("GET", f"/stats.html?key={info['key']}")
        seen.append((info["port"], conn.getresponse().status))
        conn.close()

    monkeypatch.setattr(stats_server, "wait_window_closed", wait)
    stats_server.serve(old, ready)
    assert seen and seen[0][1] == 200
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", seen[0][0]), timeout=2).close()


def test_wait_window_closed(monkeypatch):
    """等窗口出来再等它关掉；一直没出来时等够时间就退出。"""
    windows, alive, sleeps = [0, 0, 9], [True, True, False], []
    monkeypatch.setattr(stats_window, "find_window", lambda: windows.pop(0))
    monkeypatch.setattr(stats_server, "_alive", lambda hwnd: hwnd == 9 and alive.pop(0))
    monkeypatch.setattr(stats_server.time, "sleep", sleeps.append)
    stats_server.wait_window_closed(appear_s=60, check_s=5)
    assert windows == [] and alive == [] and sleeps == [0.5, 0.5, 0.5, 5, 5]

    monkeypatch.setattr(stats_window, "find_window", lambda: pytest.fail("不该再找窗口"))
    stats_server.wait_window_closed(appear_s=0)


def test_cli(tmp_path, monkeypatch, cli_main):  # noqa: F811 - cli_main 是从 test_backup 导入的 fixture
    """stats-serve：在包目录里起服务，导入后重新统计（更新 data 里的统计页）。"""
    calls = []
    monkeypatch.setattr(stats_server, "serve", lambda data, ready, refresh: calls.append((data, ready, refresh)))
    make(tmp_path / "data", [rec(1, T0)])
    monkeypatch.chdir(tmp_path)
    assert cli_main(["stats-serve", "--ready", "cache/r.json"]) == 0
    [(data, ready, refresh)] = calls
    assert (data, ready) == (Path("data"), Path("cache", "r.json")) and not (tmp_path / "data" / stats.PAGE_HTML).exists()
    refresh()
    assert "1 局演奏" in (tmp_path / "data" / stats.PAGE_HTML).read_text(encoding="utf-8")
