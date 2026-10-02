"""更新提示：版本比较、缓存、任务结束时的提示。"""

import json
import logging

import pytest
import requests

from ournotes_auto.agent.update import DOWNLOAD_URL, UpdateCheck, fetch_latest, is_newer, latest_tag, parse_version


def test_parse_version():
    assert parse_version("v0.1.0") == (0, 1, 0)
    assert parse_version(" 1.10.2 ") == (1, 10, 2)
    for text in ("dev-1a2b3c4", "v1.2", "v1.2.3-beta", "", None):
        assert parse_version(text) is None


@pytest.mark.parametrize(
    "latest, current, newer",
    [
        ("v0.2.0", "v0.1.0", True),
        ("v0.10.0", "v0.9.9", True),  # 按数字比，不按字符串
        ("v0.1.0", "v0.1.0", False),
        ("v0.1.0", "v0.2.0", False),
        ("v0.2.0", "dev-1a2b3c4", False),  # 开发版不提示
        (None, "v0.1.0", False),
    ],
)
def test_is_newer(latest, current, newer):
    assert is_newer(latest, current) is newer


def _head(monkeypatch, status, location=None):
    resp = requests.Response()
    resp.status_code = status
    if location:
        resp.headers["Location"] = location
    monkeypatch.setattr(requests, "head", lambda url, **kw: resp)


def test_fetch_latest(monkeypatch):
    _head(monkeypatch, 302, "https://github.com/ChissaQAQ/OurNotes-Auto/releases/tag/v0.2.0")
    assert fetch_latest() == "v0.2.0"
    # 还没有正式发布时跳转到发布列表
    _head(monkeypatch, 302, "https://github.com/ChissaQAQ/OurNotes-Auto/releases")
    with pytest.raises(ValueError, match="没有找到"):
        fetch_latest()
    _head(monkeypatch, 404)
    with pytest.raises(ValueError, match="404"):
        fetch_latest()


def test_latest_tag_cache(tmp_path):
    cache = tmp_path / "cache" / "update.json"
    calls = []

    def fetch():
        calls.append(1)
        return f"v0.{len(calls)}.0"

    assert latest_tag(cache, fetch, now=lambda: 1000.0, ttl_s=60) == "v0.1.0"
    assert latest_tag(cache, fetch, now=lambda: 1050.0, ttl_s=60) == "v0.1.0"  # 缓存有效
    assert latest_tag(cache, fetch, now=lambda: 1100.0, ttl_s=60) == "v0.2.0"  # 过期重查
    assert json.loads(cache.read_text(encoding="utf-8")) == {"checked_at": 1100.0, "latest": "v0.2.0"}
    cache.write_text("{", encoding="utf-8")  # 坏掉的缓存当没有
    assert latest_tag(cache, fetch, now=lambda: 1100.0, ttl_s=60) == "v0.3.0"


def _root(tmp_path, version):
    (tmp_path / "interface.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    return tmp_path


def test_report_newer(tmp_path, caplog):
    check = UpdateCheck(_root(tmp_path, "v0.1.0"), lookup=lambda: "v0.2.0")
    check.start()
    with caplog.at_level(logging.DEBUG, "ournotes_auto.agent.update"):
        check.report()
        check.report()  # 只提示一次
    hints = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(hints) == 1
    assert "v0.2.0" in hints[0].getMessage() and DOWNLOAD_URL in hints[0].getMessage()


def test_report_quiet(tmp_path, caplog):
    def fail():
        raise ConnectionError("offline")

    dev_calls = []
    cases = [
        ("v0.2.0", lambda: "v0.2.0"),  # 已是最新
        ("v0.2.0", fail),  # 查不到只记调试日志
        ("dev-1a2b3c4", lambda: dev_calls.append(1) or "v9.9.9"),  # 开发版不查
    ]
    with caplog.at_level(logging.DEBUG, "ournotes_auto.agent.update"):
        for version, lookup in cases:
            check = UpdateCheck(_root(tmp_path, version), lookup=lookup)
            check.start()
            check.report()
    assert all(r.levelno == logging.DEBUG for r in caplog.records)
    assert any("offline" in r.getMessage() for r in caplog.records)
    assert not dev_calls
