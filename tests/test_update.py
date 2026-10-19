"""自动更新的 Agent 这边：查新版本、启动更新器、挂机热更新的时机、任务结束时的提示（ournotes_auto/agent/update.py）。"""

import json
import logging
import subprocess
import time

import pytest
import requests

from ournotes_auto import release
from ournotes_auto.agent import update
from ournotes_auto.agent.update import DOWNLOAD_URL, UpdateCheck, cached_manifest, current_version, fetch_latest, fetch_manifest

LOGGER = "ournotes_auto.agent.update"


def _head(monkeypatch, status, location=None):
    resp = requests.Response()
    resp.status_code = status
    if location:
        resp.headers["Location"] = location
    monkeypatch.setattr(requests, "head", lambda url, **kw: resp)


def _get(monkeypatch, status, body=None, urls=None):
    resp = requests.Response()
    resp.status_code = status
    resp._content = json.dumps(body).encode() if body is not None else b""

    def get(url, **kw):
        if urls is not None:
            urls.append(url)
        return resp

    monkeypatch.setattr(requests, "get", get)


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


def test_fetch_manifest(monkeypatch):
    urls = []
    _get(monkeypatch, 200, {"version": "v0.2.0", "files": {}}, urls)
    assert fetch_manifest() == {"version": "v0.2.0", "manifest": {"version": "v0.2.0", "files": {}}}
    assert urls == [update.MANIFEST_URL]
    monkeypatch.setenv(update.URL_ENV, "http://127.0.0.1:1/update.json")  # 测试时换地址
    fetch_manifest()
    assert urls[-1] == "http://127.0.0.1:1/update.json"
    monkeypatch.delenv(update.URL_ENV)
    # 最新的 Release 没有 update.json（旧版本发布的）：只知道版本号
    _get(monkeypatch, 404)
    _head(monkeypatch, 302, "https://github.com/ChissaQAQ/OurNotes-Auto/releases/tag/v0.1.4")
    assert fetch_manifest() == {"version": "v0.1.4", "manifest": None}
    _get(monkeypatch, 200, {"version": "dev"})
    with pytest.raises(ValueError, match="格式"):
        fetch_manifest()
    _get(monkeypatch, 500)
    with pytest.raises(requests.HTTPError):
        fetch_manifest()


def test_cached_manifest(tmp_path):
    cache = tmp_path / "cache" / "update" / "manifest.json"
    calls = []

    def fetch():
        calls.append(1)
        return {"version": f"v0.{len(calls)}.0", "manifest": None}

    assert cached_manifest(cache, fetch, now=lambda: 1000.0, ttl_s=60)["version"] == "v0.1.0"
    assert cached_manifest(cache, fetch, now=lambda: 1050.0, ttl_s=60)["version"] == "v0.1.0"  # 缓存有效
    assert cached_manifest(cache, fetch, now=lambda: 1100.0, ttl_s=60)["version"] == "v0.2.0"  # 过期重查
    assert json.loads(cache.read_text(encoding="utf-8")) == {"checked_at": 1100.0, "version": "v0.2.0", "manifest": None}
    cache.write_text("{", encoding="utf-8")  # 坏掉的缓存当没有
    assert cached_manifest(cache, fetch, now=lambda: 1100.0, ttl_s=60)["version"] == "v0.3.0"
    assert cached_manifest(cache, fetch, now=lambda: 900.0, ttl_s=60)["version"] == "v0.4.0"  # 时钟往回调了


# ---------------------------------------------------------------- UpdateCheck


def _dev(tmp_path, version):
    """开发环境：只有 interface.json。"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "interface.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    return tmp_path


FILES = {"ournotes_auto/runner.py": "1", "tasks/common.json": "1"}


def _package(tmp_path, version="v0.1.0", ui="mxu", runtime="r1"):
    """发布包：有 release.json。"""
    _dev(tmp_path, version)
    info = {"format": 1, "version": version, "ui": ui, "ui_exe": release.UI_EXE[ui], "runtime": runtime, "files": FILES, "top": []}
    release.write_json(tmp_path / release.RELEASE_FILE, info)
    return tmp_path


def _manifest(version="v0.2.0", files=None, runtime="r1", **extra):
    return {
        "format": 1,
        "version": version,
        "url": f"https://example.com/{version}",
        "runtime": {"mfaa": runtime, "mxu": runtime},
        "full": {"mfaa": {}, "mxu": {}},
        "files": files or {**FILES, "ournotes_auto/runner.py": "2"},
        **extra,
    }


def _check(root, manifest, spawned=None, version=None):
    info = {"version": version or (manifest or {}).get("version"), "manifest": manifest}
    spawned = spawned if spawned is not None else []
    return UpdateCheck(root, lookup=lambda: info, spawn=lambda: spawned.append(1)), spawned


def _infos(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]


def test_current_version(tmp_path):
    assert current_version(tmp_path) is None
    assert current_version(_dev(tmp_path, "dev-1")) == "dev-1"
    _package(tmp_path, "v0.3.0")
    (tmp_path / "interface.json").write_text(json.dumps({"version": "v0.1.0"}), encoding="utf-8")
    assert current_version(tmp_path) == "v0.3.0"  # 热更新后以 release.json 为准


def test_spawns_updater(tmp_path, caplog):
    check, spawned = _check(_package(tmp_path), _manifest())
    check.start()
    with caplog.at_level(logging.DEBUG, LOGGER):
        check.report()
    assert spawned == [1]
    assert not _infos(caplog)  # 更新器还没写状态，不提示


@pytest.mark.parametrize(
    "setup, kwargs, reason",
    [
        (lambda root: _dev(root, "v0.1.0"), {}, "开发环境"),
        (_package, {"auto": False}, "没有开自动更新"),
        (_package, {"manifest": None}, "没有自动更新文件"),
        (_package, {"manifest": _manifest(format=2)}, "新格式"),
        (_package, {"failed": True}, "失败过"),
        (_package, {"manifest": {**_manifest(runtime="r2"), "full": {"mfaa": {}}}}, "没有 mxu 的发布包"),
    ],
)
def test_notice_only(tmp_path, caplog, setup, kwargs, reason):
    """不能自动更新时只提示新版本和下载页，每个 Agent 进程只提示一次。"""
    root = setup(tmp_path)
    manifest = kwargs.get("manifest", _manifest())
    if kwargs.get("failed"):
        release.write_json(root / release.FAILED_FILE, {"v0.2.0": {"error": "x"}})
    check, spawned = _check(root, manifest, version="v0.2.0")
    check.start(auto=kwargs.get("auto", True))
    with caplog.at_level(logging.DEBUG, LOGGER):
        check.report()
        check.report()
    assert not spawned
    assert any(reason in r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG)
    hints = _infos(caplog)
    assert len(hints) == 1 and "v0.2.0" in hints[0]
    assert ((manifest or {}).get("url") or DOWNLOAD_URL) in hints[0]


def test_quiet(tmp_path, caplog):
    def fail():
        raise ConnectionError("offline")

    with caplog.at_level(logging.DEBUG, LOGGER):
        for lookup in (lambda: {"version": "v0.1.0", "manifest": None}, fail):  # 已是最新；查不到
            check = UpdateCheck(_package(tmp_path), lookup=lookup, spawn=lambda: pytest.fail("不该启动"))
            check.start()
            check.report()
        calls = []
        check = UpdateCheck(_package(tmp_path), lookup=lambda: calls.append(1), spawn=lambda: None)
        check.start(check=False)  # 关掉「检查更新」不查
        check.report()
    assert not _infos(caplog) and not calls
    assert any("offline" in r.getMessage() for r in caplog.records)


def test_auto_off_flag(tmp_path):
    root = _package(tmp_path)
    flag = root / update.AUTO_OFF_FILE
    check, _ = _check(root, _manifest())
    check.start(auto=False)
    assert flag.exists()  # 正在等待的更新器看到后退出
    check.start(check=False, auto=True)  # 自动更新要先开检查更新
    assert flag.exists()
    check.start()
    assert not flag.exists()


def test_no_spawn_while_running_or_after_error(tmp_path):
    root = _package(tmp_path)
    lock = release.FileLock(root / release.UPDATER_LOCK)
    assert lock.acquire()
    try:
        check, spawned = _check(root, _manifest())
        check.start()
        check.report()
        assert not spawned  # 更新器已经在运行
    finally:
        lock.release()
    release.write_json(root / release.STATUS_FILE, {"version": "v0.2.0", "state": "error", "time": time.time()})
    check, spawned = _check(root, _manifest())
    check.start()
    check.report()
    assert not spawned  # 刚出过错，过一阵再试
    release.write_json(root / release.STATUS_FILE, {"version": "v0.2.0", "state": "error", "time": time.time() - update.RETRY_S - 1})
    check, spawned = _check(root, _manifest())
    check.start()
    check.report()
    assert spawned == [1]


def test_spawn_failure_falls_back_to_notice(tmp_path, caplog):
    def spawn():
        raise OSError("no python")

    check = UpdateCheck(_package(tmp_path), lookup=lambda: {"version": "v0.2.0", "manifest": _manifest()}, spawn=spawn)
    check.start()
    with caplog.at_level(logging.DEBUG, LOGGER):
        check.report()
    assert any("发现新版本 v0.2.0" in m for m in _infos(caplog))


def _status(root, state, version="v0.2.0", tier="hot", **extra):
    release.write_json(root / release.STATUS_FILE, {"version": version, "tier": tier, "state": state, "url": "https://example.com/v", "time": time.time(), **extra})


def test_report_progress(tmp_path, caplog):
    root = _package(tmp_path)
    lookup = {"version": "v0.1.0", "manifest": None}
    with caplog.at_level(logging.INFO, LOGGER):
        for state, tier, extra in (
            ("downloading", "hot", {"progress": 0.5}),
            ("waiting", "hot", {}),
            ("waiting", "restart", {}),
            ("waiting_close", "restart", {}),
            ("error", "full", {"error": "断网"}),
        ):
            _status(root, state, tier=tier, **extra)
            check = UpdateCheck(root, lookup=lambda: lookup, spawn=lambda: None)
            check.start()
            check.report()
    assert _infos(caplog) == [
        "正在后台下载新版本 v0.2.0（50%）",
        "新版本 v0.2.0 已下载，没有任务运行时自动换上",
        "新版本 v0.2.0 已下载，任务都结束后会自动重启界面完成更新",
        "新版本 v0.2.0 已下载；开着「启动时自动执行」时不会自动重启界面，关闭界面后自动完成更新",
        "自动更新到 v0.2.0 暂时没成功（断网），稍后会重试；也可以到 https://example.com/v 手动下载",
    ]


def test_report_result_once(tmp_path, caplog):
    """更新结果每个版本只提示一次：MXU 每次任务都重启 Agent，记在文件里。"""
    root = _package(tmp_path, "v0.2.0")
    _status(root, "done")
    with caplog.at_level(logging.INFO, LOGGER):
        for _ in range(2):
            check = UpdateCheck(root, lookup=lambda: {"version": "v0.2.0", "manifest": None}, spawn=lambda: None)
            check.start()
            check.report()
        _status(root, "failed", version="v0.3.0", error="x")
        for _ in range(2):
            check = UpdateCheck(root, lookup=lambda: {"version": "v0.2.0", "manifest": None}, spawn=lambda: None)
            check.start()
            check.report()
    msgs = _infos(caplog)
    assert len(msgs) == 2
    assert msgs[0].startswith("已自动更新到 v0.2.0") and msgs[1].startswith("自动更新到 v0.3.0 失败")


@pytest.mark.parametrize("tier", ["hot", "ui"])
def test_report_done_title_note(tmp_path, caplog, tier):
    """热更新不重启界面：提示窗口标题里的版本号要下次打开界面才更新（重启了界面的不用提示）。"""
    root = _package(tmp_path, "v0.2.0")
    _status(root, "done", tier=tier)
    with caplog.at_level(logging.INFO, LOGGER):
        check = UpdateCheck(root, lookup=lambda: {"version": "v0.2.0", "manifest": None}, spawn=lambda: None)
        check.start()
        check.report()
    assert ("界面标题" in _infos(caplog)[0]) == (tier == "hot")


def test_hot_ready(tmp_path, monkeypatch):
    root = _package(tmp_path)
    check = UpdateCheck(root, lookup=lambda: {"version": "v0.1.0", "manifest": None}, spawn=lambda: None)
    check.start()
    assert not check.hot_ready()
    _status(root, "waiting", tier="hot")
    assert not check.hot_ready()  # STATUS_POLL_S 内用上次的结果
    monkeypatch.setattr(update, "STATUS_POLL_S", 0.0)
    lock = release.FileLock(root / release.UPDATER_LOCK)
    assert lock.acquire()
    try:
        assert check.hot_ready()
    finally:
        lock.release()
    assert not check.hot_ready()  # 更新器退出了，遗留的 waiting 状态不能触发重启
    _status(root, "waiting", tier="restart")
    assert not check.hot_ready()  # 要重启界面的不用重启演奏进程
    _status(root, "waiting", tier="hot", version="v0.1.0")
    assert not check.hot_ready()  # 已经换上了
    _status(root, "waiting", tier="hot")
    check.start(auto=False)
    assert not check.hot_ready()


def test_busy_lock(tmp_path):
    root = _package(tmp_path)
    check = UpdateCheck(root, lookup=lambda: {}, spawn=lambda: None)
    with check.busy():
        assert release.busy(root)
    assert not release.busy(root) and not list((root / release.BUSY_DIR).glob("*.lock"))
    with UpdateCheck(_dev(tmp_path / "dev", "v0.1.0"), lookup=lambda: {}, spawn=lambda: None).busy():
        assert not (tmp_path / "dev" / release.BUSY_DIR).exists()  # 开发环境不用锁


def test_busy_lock_failure_does_not_start_task(tmp_path, monkeypatch):
    check = UpdateCheck(_package(tmp_path), lookup=lambda: {}, spawn=lambda: None)
    monkeypatch.setattr(release.FileLock, "acquire", lambda self: False)
    with pytest.raises(update.UpdateBusyError, match="无法锁定"):
        with check.busy():
            pytest.fail("没有保护锁就不能启动任务")


def test_failed_rollback_blocks_new_updates_and_tasks(tmp_path, caplog):
    root = _package(tmp_path)
    _status(root, "rollback_failed", error="文件被占用")
    check, spawned = _check(root, _manifest(version="v0.3.0"))
    with caplog.at_level(logging.INFO, LOGGER):
        check.start()
        check.report()
    assert not spawned and "还原失败" in caplog.text
    with pytest.raises(update.UpdateBusyError, match="还原失败"):
        with check.busy():
            pytest.fail("不能运行未恢复的代码")


def test_busy_waits_while_release_is_being_replaced(tmp_path, monkeypatch):
    root = _package(tmp_path)
    info = release.read_release(root)
    (root / release.RELEASE_FILE).unlink()
    apply = release.FileLock(root / release.APPLY_LOCK)
    assert apply.acquire()
    check = UpdateCheck(root, lookup=lambda: {}, spawn=lambda: None)

    def finish():
        assert release.busy(root)  # 不能把暂时缺失的 release.json 当开发环境
        release.write_json(root / release.RELEASE_FILE, info)
        apply.release()

    monkeypatch.setattr(check, "_wait_apply", finish)
    try:
        with check.busy():
            assert release.busy(root)
    finally:
        apply.release()
    assert not release.busy(root)


def test_busy_waits_for_apply(tmp_path):
    root = _package(tmp_path)
    check = UpdateCheck(root, lookup=lambda: {}, spawn=lambda: None)
    apply = release.FileLock(root / release.APPLY_LOCK)
    assert apply.acquire()
    t0 = time.monotonic()
    try:
        with pytest.raises(update.UpdateBusyError, match="仍在替换文件"):
            check._wait_apply(timeout_s=0.6)  # 更新器在换文件：超时后不能读半更新的代码
    finally:
        apply.release()
    assert time.monotonic() - t0 >= 0.5
    t0 = time.monotonic()
    check._wait_apply(timeout_s=5)
    assert time.monotonic() - t0 < 0.5


def test_wait_applied(tmp_path, caplog):
    root = _package(tmp_path)
    check = UpdateCheck(root, lookup=lambda: {}, spawn=lambda: None)
    _status(root, "waiting")
    t0 = time.monotonic()
    with caplog.at_level(logging.INFO, LOGGER):
        check.wait_applied(timeout_s=5)  # 更新器没在运行：不等
    assert time.monotonic() - t0 < 1
    assert "正在换上新版本 v0.2.0" in _infos(caplog)[0]
    lock = release.FileLock(root / release.UPDATER_LOCK)
    assert lock.acquire()
    try:
        t0 = time.monotonic()
        check.wait_applied(timeout_s=0.6)
        assert time.monotonic() - t0 >= 0.5
        stops = iter([False, True])
        check.wait_applied(lambda: next(stops), timeout_s=30)  # 用户停止任务
    finally:
        lock.release()


def test_spawn_updater(tmp_path, monkeypatch):
    root = _package(tmp_path)
    py = root / "python"
    py.mkdir()
    for name in ("python.exe", "pythonw.exe", "python314.dll", "python314.zip", "python314._pth"):
        (py / name).write_text(name, encoding="utf-8")
    (py / "Lib").mkdir()
    pkg = root / "ournotes_auto"
    pkg.mkdir()
    for name in ("updater.py", "release.py"):
        (pkg / name).write_text(name, encoding="utf-8")
    calls = []

    def popen(cmd, **kw):
        calls.append((cmd, kw))
        if kw["creationflags"] & subprocess.CREATE_BREAKAWAY_FROM_JOB:
            raise PermissionError("不允许脱离作业对象")

    monkeypatch.setattr(subprocess, "Popen", popen)
    update.spawn_updater(root)
    runner = root / update.RUNNER_DIR
    assert sorted(p.name for p in runner.iterdir()) == sorted(["python.exe", "pythonw.exe", "python314.dll", "python314.zip", "python314._pth", "updater.py", "release.py"])
    assert (runner / "python314._pth").read_text(encoding="utf-8").splitlines() == ["python314.zip", "."]
    assert len(calls) == 2  # 脱离不了作业对象时不带这个标志再试
    cmd, kw = calls[-1]
    assert cmd == [str(runner / "pythonw.exe"), str(runner / "updater.py"), str(root)]
    assert kw["creationflags"] & subprocess.DETACHED_PROCESS and kw["cwd"] == root
