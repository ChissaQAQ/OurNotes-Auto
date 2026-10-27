"""后台更新器（ournotes_auto/updater.py）：下载续传、替换和还原、界面自动执行的识别、等待时机和重启界面。"""

import hashlib
import http.server
import json
import logging
import os
import threading
import zipfile
from pathlib import Path

import pytest

from ournotes_auto import release, updater
from ournotes_auto.updater import FilesApply, FullApply, UpdateError, Updater, autorun_on, download, extract, verify_stage

ROOT = Path(__file__).resolve().parents[1]
IFACE = {"name": "OurNotes", "version": "v0.0.0", "global_option": ["CheckUpdate"], "agent": {"child_exec": "python"}}


def _write(root: Path, rel: str, data: str | bytes = "x") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")
    return path


def _tree(root: Path, files: dict) -> Path:
    for rel, data in files.items():
        _write(root, rel, data)
    return root


def _snapshot(root: Path) -> dict[str, bytes]:
    """包里的文件（cache、__pycache__ 除外）。"""
    out = {}
    for p in root.rglob("*"):
        parts = p.relative_to(root).parts
        if p.is_file() and parts[0] != "cache" and "__pycache__" not in parts:
            out["/".join(parts)] = p.read_bytes()
    return out


# ---------------------------------------------------------------- 下载


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        srv = self.server
        rng = self.headers.get("Range")
        srv.log.append((self.path, rng))
        if srv.statuses:
            self.send_response(srv.statuses.pop(0))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = srv.files.get(self.path)
        if data is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start = int(rng[len("bytes=") : -1]) if rng and srv.ranges else 0
        body = data[start:]
        self.send_response(206 if start else 200)
        self.send_header("Content-Length", str(len(body)))
        if start:
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        self.end_headers()
        if srv.cuts:  # 发一半就断开
            srv.cuts -= 1
            self.wfile.write(body[: len(body) // 2])
            self.close_connection = True
            return
        self.wfile.write(body)


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "*")  # 不走系统代理
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.files, srv.log, srv.statuses, srv.cuts, srv.ranges = {}, [], [], 0, True
    srv.url = f"http://127.0.0.1:{srv.server_address[1]}"
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


DATA = os.urandom(300_000)
SHA = hashlib.sha256(DATA).hexdigest()


def _download(server, dest, data=DATA, sha=SHA, **kw):
    sleeps, progress = [], []
    download(f"{server.url}/a.zip", dest, len(data), sha, progress.append, sleep=sleeps.append, **kw)
    return sleeps, progress


def test_download(server, tmp_path):
    server.files["/a.zip"] = DATA
    dest = tmp_path / "x" / "a.zip"
    sleeps, progress = _download(server, dest)
    assert dest.read_bytes() == DATA and not dest.with_name("a.zip.part").exists()
    assert progress[-1] == 1.0 and not sleeps
    _download(server, dest)  # 已经下载好了不再下载
    assert len(server.log) == 1


@pytest.mark.parametrize("ranges", [True, False])
def test_download_resume(server, tmp_path, ranges):
    """断了从 .part 续传；服务器不支持续传时从头下。"""
    server.files["/a.zip"], server.cuts, server.ranges = DATA, 1, ranges
    dest = tmp_path / "a.zip"
    sleeps, _ = _download(server, dest)
    assert dest.read_bytes() == DATA
    assert [r for _, r in server.log] == [None, f"bytes={len(DATA) // 2}-"]
    assert sleeps == [5.0]


def test_download_bad_sha(server, tmp_path):
    server.files["/a.zip"] = DATA
    dest = tmp_path / "a.zip"
    with pytest.raises(UpdateError, match="校验"):
        _download(server, dest, sha="0" * 64, tries=2)
    assert not dest.exists() and not dest.with_name("a.zip.part").exists()
    assert len(server.log) == 2  # 校验不对删掉重下


def test_download_416_restarts(server, tmp_path):
    server.files["/a.zip"], server.statuses = DATA, [416]
    dest = tmp_path / "a.zip"
    _write(tmp_path, "a.zip.part", b"junk")
    _download(server, dest)
    assert dest.read_bytes() == DATA
    assert [r for _, r in server.log] == ["bytes=4-", None]


def test_download_gives_up(server, tmp_path):
    with pytest.raises(UpdateError, match="404"):
        _download(server, tmp_path / "a.zip", tries=3)
    assert len(server.log) == 3


# ---------------------------------------------------------------- 解压、核对


def _zip(path: Path, files: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, data in files.items():
            zf.writestr(rel, data)
    return path


def test_extract_and_verify(tmp_path):
    archive = _zip(tmp_path / "a.zip", {"docs/说明.md": "x", "../evil.txt": "x", "interface.json": json.dumps(IFACE)})
    stage = tmp_path / "stage"
    _write(stage, "old.txt")  # 之前没用完的解压目录
    extract(archive, stage)
    assert sorted(p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file()) == ["docs/说明.md", "interface.json"]
    assert not (tmp_path / "evil.txt").exists()
    manifest = {"version": "v0.2.0", "files": release.manifest_files(stage)}
    verify_stage(stage, manifest, full=False, ui="mxu")
    with pytest.raises(UpdateError, match="对不上"):
        verify_stage(stage, {**manifest, "files": {**manifest["files"], "docs/说明.md": "0"}}, full=False, ui="mxu")
    with pytest.raises(UpdateError, match="release.json"):
        verify_stage(stage, manifest, full=True, ui="mxu")
    release.write_json(stage / release.RELEASE_FILE, {"version": "v0.2.0", "ui": "mxu", "files": {}})
    verify_stage(stage, manifest, full=True, ui="mxu")
    with pytest.raises(UpdateError):
        verify_stage(stage, manifest, full=True, ui="mfaa")


# ---------------------------------------------------------------- 替换、还原

OLD = {
    "interface.json": json.dumps(release.render_interface(IFACE, "mxu", "v0.1.0")),
    "agent/main.py": "main",
    "ournotes_auto/runner.py": "old",
    "ournotes_auto/gone.py": "gone",
    "ournotes_auto/__pycache__/runner.cpython-314.pyc": "pyc",
    "tasks/common.json": "{}",
    "mxu.exe": "ui",
    "python/python.exe": "py",
    "config/config.yaml": "user",
}
NEW = {
    "interface.json": json.dumps({**IFACE, "global_option": ["CheckUpdate", "AutoUpdate"]}),
    "agent/main.py": "main",
    "ournotes_auto/runner.py": "new",
    "ournotes_auto/added.py": "added",
    "tasks/common.json": "{}",
}
HOT = {**NEW, "interface.json": json.dumps(IFACE)}  # 只改了演奏进程的代码
RESTART = {**HOT, "tasks/common.json": '{"x": 1}'}


def _package(root: Path, files=OLD, version="v0.1.0", ui="mxu", runtime=None) -> dict:
    _tree(root, files)
    info = release.release_info(root, ui, version, runtime or {"python": "3.14"})
    release.write_json(root / release.RELEASE_FILE, info)
    return info


def test_files_apply_and_rollback(tmp_path):
    root, stage = tmp_path / "pkg", _tree(tmp_path / "stage", NEW)
    local = _package(root)
    before = _snapshot(root)
    manifest = {"version": "v0.2.0", "files": release.manifest_files(stage)}
    job = FilesApply(root, stage, local, manifest, root / "cache/update/backup/v0.1.0")
    job.run()
    after = _snapshot(root)
    assert after["ournotes_auto/runner.py"] == b"new" and after["ournotes_auto/added.py"] == b"added"
    assert "ournotes_auto/gone.py" not in after and "ournotes_auto/__pycache__/runner.cpython-314.pyc" not in after
    assert after["config/config.yaml"] == b"user" and after["mxu.exe"] == b"ui"
    iface = json.loads(after["interface.json"])
    assert iface["version"] == "v0.2.0" and iface["setting"][-1]["option"] == ["CheckUpdate", "AutoUpdate"]
    assert iface["agent"]["child_exec"] == release.AGENT_EXEC
    info = release.read_release(root)
    assert info["version"] == "v0.2.0" and info["files"] == manifest["files"] == release.manifest_files(root)
    assert info["runtime"] == local["runtime"] and info["top"] == local["top"]
    job.rollback()
    assert _snapshot(root) == before


def test_files_apply_repairs_disk(tmp_path):
    """和磁盘上的实际内容比：上次换到一半、用户改过的文件也换回来。"""
    root, stage = tmp_path / "pkg", _tree(tmp_path / "stage", NEW)
    local = _package(root)
    _write(root, "agent/main.py", "edited")
    FilesApply(root, stage, local, {"version": "v0.2.0", "files": release.manifest_files(stage)}, tmp_path / "backup").run()
    assert (root / "agent/main.py").read_text(encoding="utf-8") == "main"
    assert (tmp_path / "backup/agent/main.py").read_text(encoding="utf-8") == "edited"


def test_full_apply_and_rollback(tmp_path):
    root = tmp_path / "pkg"
    local = _package(root, {**OLD, "resource/model/ocr/det.onnx": "det", "resource/pipeline/a.json": "{}"})
    # 运行时按需下载的模型、界面自己建的文件（不在旧包的 top 里）
    _tree(root, {"resource/model/ocr/ko_kr/rec.onnx": "ko", "appsettings.json": "ui-settings", "debug/x.png": "d"})
    stage = _tree(
        tmp_path / "stage",
        {**NEW, "mxu.exe": "ui2", "python/python.exe": "py2", "resource/model/ocr/det.onnx": "det2", "appsettings.json": "new", "config/config.yaml": "pkg"},
    )
    release.write_json(stage / release.RELEASE_FILE, {"version": "v0.2.0"})
    before = _snapshot(root)
    job = FullApply(root, stage, local, root / "cache/update/backup/v0.1.0")
    job.run()
    after = _snapshot(root)
    assert after["mxu.exe"] == b"ui2" and after["python/python.exe"] == b"py2" and after["resource/model/ocr/det.onnx"] == b"det2"
    assert "ournotes_auto/gone.py" not in after and "resource/pipeline/a.json" not in after
    assert after["resource/model/ocr/ko_kr/rec.onnx"] == b"ko"
    assert after["appsettings.json"] == b"ui-settings" and after["config/config.yaml"] == b"user" and after["debug/x.png"] == b"d"
    assert json.loads(after["release.json"]) == {"version": "v0.2.0"}
    job.rollback()
    assert _snapshot(root) == before


def test_full_apply_preserves_user_settings_across_updates(tmp_path):
    root = tmp_path / "pkg"
    local = _package(root)
    _write(root, "appsettings.json", "user")
    for version in ("v0.2.0", "v0.3.0"):
        stage = tmp_path / version
        _package(stage, {**NEW, "mxu.exe": "ui", "appsettings.json": "default"}, version=version)
        job = FullApply(root, stage, local, tmp_path / f"backup-{version}")
        job.run()
        local = release.read_release(root)
        assert "appsettings.json" not in local["top"]
        assert (root / "appsettings.json").read_text(encoding="utf-8") == "user"


def _user_iface(**extra):
    return json.dumps({**release.render_interface(IFACE, "mxu", "v0.1.0"), **extra})


@pytest.mark.parametrize("full", [False, True])
def test_update_keeps_user_interface_fields(tmp_path, full):
    """用户在 interface.json 里加的顶层字段（如 MFAA 的更新源 github，#91）更新后还在；
    新版本里有的字段、Agent 启动命令和 MXU 的设置分组照常用新的。"""
    root = tmp_path / "pkg"
    local = _package(root, {**OLD, "interface.json": _user_iface(github="https://example.com/x", name="改过", setting=[])})
    if full:
        iface = json.dumps(release.render_interface({**IFACE, "global_option": ["CheckUpdate", "AutoUpdate"]}, "mxu", "v0.2.0"))
        _package(stage := tmp_path / "stage", {**NEW, "interface.json": iface, "mxu.exe": "ui2"}, version="v0.2.0")
        job = FullApply(root, stage, local, tmp_path / "backup")
    else:
        stage = _tree(tmp_path / "stage", NEW)
        job = FilesApply(root, stage, local, {"version": "v0.2.0", "files": release.manifest_files(stage)}, tmp_path / "backup")
    job.run()
    after = json.loads((root / "interface.json").read_text(encoding="utf-8"))
    assert after["github"] == "https://example.com/x"
    assert after["name"] == "OurNotes" and after["version"] == "v0.2.0"
    assert [s["name"] for s in after["setting"]] == ["stats", "global"] and after["setting"][1]["option"] == ["CheckUpdate", "AutoUpdate"]
    assert after["option"] == {release.STATS_OPTION: release.render_interface(IFACE, "mxu")["option"][release.STATS_OPTION]}
    assert after["agent"]["child_exec"] == release.AGENT_EXEC
    job.rollback()
    assert json.loads((root / "interface.json").read_text(encoding="utf-8"))["name"] == "改过"


@pytest.mark.parametrize("old", ["{", "[]", None])
def test_update_ignores_unreadable_old_interface(tmp_path, old):
    """保底：旧的 interface.json 坏了、不是对象或者没有时，照常用新的。"""
    root, stage = tmp_path / "pkg", _tree(tmp_path / "stage", NEW)
    local = _package(root)
    if old is None:
        (root / "interface.json").unlink()
    else:
        _write(root, "interface.json", old)
    FilesApply(root, stage, local, {"version": "v0.2.0", "files": release.manifest_files(stage)}, tmp_path / "backup").run()
    after = json.loads((root / "interface.json").read_text(encoding="utf-8"))
    assert after == release.render_interface(json.loads(NEW["interface.json"]), "mxu", "v0.2.0")


def test_keep_user_fields_skips_generated(tmp_path, caplog):
    """打包时生成的部分（版本号、MXU 的设置页和统计卡片）不算用户加的；项目的 interface.json 顶层没有 option。"""
    old = _write(tmp_path, "interface.json", _user_iface(github="g"))
    new = {k: v for k, v in IFACE.items() if k != "version"}
    with caplog.at_level(logging.INFO, logger="ournotes_auto.updater"):
        assert updater.keep_user_fields(new, old) == {**new, "github": "g"}
    assert caplog.messages == ["interface.json 保留用户加的字段：github"]
    _write(tmp_path, "interface.json", '{"option": 1}')
    assert updater.keep_user_fields(new, old) == new


# ---------------------------------------------------------------- 试运行


def test_self_test():
    assert updater.self_test(ROOT.parent / "nope") is not None  # 没有 python


def test_self_test_runs_new_code(tmp_path, monkeypatch):
    import sys

    monkeypatch.setattr(release, "AGENT_EXEC", sys.executable)  # 绝对路径：root / 它 还是它
    assert updater.self_test(ROOT) is None
    _write(tmp_path, "ournotes_auto/__init__.py", "raise ImportError('坏了')")
    assert "坏了" in updater.self_test(tmp_path)


# ---------------------------------------------------------------- 界面自动执行


def test_autorun_mxu(tmp_path):
    assert not autorun_on(tmp_path, "mxu")
    release.write_json(tmp_path / "config/mxu-OurNotes.json", {"settings": {"autoRunOnLaunch": False}})
    assert not autorun_on(tmp_path, "mxu")
    release.write_json(tmp_path / "config/mxu-OurNotes.json", {"settings": {"autoRunOnLaunch": True}})
    assert autorun_on(tmp_path, "mxu")


def test_autorun_mfaa(tmp_path):
    """「启动前」含 Startup 时 MFAA 会自己启动模拟器或脚本，重开界面会把它再拉起来，所以不能自动重开。"""
    assert not autorun_on(tmp_path, "mfaa")
    settings = {"Instances.LastActive": "auto", "NoAutoStart": "False", "GlobalStartEnabled": "False"}
    release.write_json(tmp_path / "appsettings.json", settings)
    instance = tmp_path / "config/instances/auto.json"
    for before_task, expected in (("StartupScriptOnly", True), ("StartupSoftwareAndScript", True), ("StartupSoftware", True), ("None", False)):
        release.write_json(instance, {"BeforeTask": before_task})
        assert autorun_on(tmp_path, "mfaa") is expected, before_task
    release.write_json(instance, {"BeforeTask": "StartupScriptOnly"})
    release.write_json(tmp_path / "appsettings.json", {**settings, "NoAutoStart": "True"})  # 命令行 -noautostart 之类
    assert not autorun_on(tmp_path, "mfaa")
    release.write_json(tmp_path / "appsettings.json", {**settings, "GlobalStartEnabled": "True"})
    release.write_json(instance, {"BeforeTask": "None"})
    assert autorun_on(tmp_path, "mfaa")


@pytest.mark.parametrize(
    "shared, expected",
    [
        ({"Instance.auto.BeforeTask": "StartupScriptOnly", "BeforeTask": "None"}, True),
        ({"Instance.default.BeforeTask": "StartupScriptOnly"}, True),
        ({"BeforeTask": "StartupSoftwareAndScript"}, True),
        ({"Instance.auto.BeforeTask": "None", "BeforeTask": "StartupScriptOnly"}, False),
        ({}, False),
    ],
)
def test_autorun_mfaa_fallback(tmp_path, shared, expected):
    """实例文件里没有「启动前」时 MFAA 回退到 config.json。"""
    release.write_json(tmp_path / "appsettings.json", {"Instances.LastActive": "auto"})
    release.write_json(tmp_path / "config/instances/auto.json", {"InstanceName": "x"})
    release.write_json(tmp_path / "config/config.json", shared)
    assert autorun_on(tmp_path, "mfaa") is expected


# ---------------------------------------------------------------- 主流程


class FakeSystem:
    def __init__(self, procs=()):
        self.procs = list(procs)
        self.killed, self.launched = [], []
        self.foreground, self.idle = None, 1e9

    def processes(self):
        return list(self.procs)

    def kill(self, pid):
        self.killed.append(pid)
        self.procs = [p for p in self.procs if p[0] != pid]

    def foreground_pid(self):
        return self.foreground

    def idle_seconds(self):
        return self.idle

    def launch(self, exe):
        self.launched.append(Path(exe))


class Clock:
    """假时钟：``sleep`` 只往前拨，每拨一次调用 ``hooks``（模拟这段时间里发生的事）。"""

    def __init__(self):
        self.now = 0.0
        self.hooks = []

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.now += s
        for hook in list(self.hooks):
            hook(self.now)


@pytest.fixture
def setup(tmp_path, server, monkeypatch):
    """旧版本 v0.1.0 的 MXU 包、服务器上 v0.2.0 的 app 层压缩包、Agent 缓存的 update.json。"""
    monkeypatch.setattr(updater, "self_test", lambda root: None)
    root = tmp_path / "pkg"

    def make(new=HOT, ui="mxu", runtime=None):
        local = _package(root, ui=ui, files={**OLD, ("mxu.exe" if ui == "mxu" else "MFAAvalonia.exe"): "ui"})
        app = _zip(tmp_path / "srv" / "app.zip", new)
        stage = _tree(tmp_path / "srv" / "files", new)
        server.files["/app.zip"] = app.read_bytes()
        manifest = {
            "format": 1,
            "version": "v0.2.0",
            "url": "https://example.com/v0.2.0",
            "base_url": server.url + "/",
            "app": {"name": "app.zip", "size": app.stat().st_size, "sha256": release.sha256_file(app)},
            "full": {},
            "runtime": {ui: runtime or local["runtime"]},
            "files": release.manifest_files(stage),
        }
        release.write_json(root / release.MANIFEST_FILE, {"checked_at": 0, "version": "v0.2.0", "manifest": manifest})
        return root, local, manifest

    return make


def _updater(root, procs=()):
    clock = Clock()
    system = FakeSystem(procs)
    return Updater(root, system, clock=clock, sleep=clock.sleep), system, clock


def _status(root):
    return release.read_json(root / release.STATUS_FILE)


def test_run_hot(setup):
    root, _, _ = setup()
    up, system, _ = _updater(root, [(100, str(root / "mxu.exe"))])
    assert up.run() == 0
    assert (root / "ournotes_auto/runner.py").read_text(encoding="utf-8") == "new"
    assert release.read_release(root)["version"] == "v0.2.0"
    assert _status(root)["state"] == "done" and _status(root)["tier"] == "hot"
    assert not system.killed and not system.launched  # 热更新不动界面
    assert not (root / updater.BACKUP_DIR / "v0.1.0").exists()  # 成功了备份也删掉
    assert not (root / release.UPDATE_DIR / "v0.2.0").exists()  # 下载的用完删掉


def test_run_hot_waits_for_worker(setup):
    root, _, _ = setup()
    up, _, clock = _updater(root)
    busy = release.FileLock(root / release.BUSY_DIR / "1.lock")
    assert busy.acquire()
    clock.hooks.append(lambda now: now >= 10 and busy.release())  # 演奏进程 10 秒后结束
    assert up.run() == 0
    assert clock.now >= 10 and release.read_release(root)["version"] == "v0.2.0"


def test_run_restart_closes_idle_ui(setup):
    root, _, _ = setup(RESTART)
    ui, agent = (100, str(root / "mxu.exe")), (101, str(root / "python/python.exe"))
    up, system, clock = _updater(root, [ui, agent])
    system.foreground, system.idle = 100, 5.0  # 用户正在操作界面
    clock.hooks.append(lambda now: now >= 60 and setattr(system, "idle", 500.0))
    assert up.run() == 0
    assert system.killed == [100, 101]  # Agent 没跟着退出的强制结束
    assert system.launched == [root / "mxu.exe"]
    assert clock.now >= 60 + updater.IDLE_S
    assert release.read_release(root)["version"] == "v0.2.0" and _status(root)["tier"] == "restart"


def test_run_restart_waits_for_close_with_autorun(setup):
    root, _, _ = setup(RESTART)
    release.write_json(root / "config/mxu-OurNotes.json", {"settings": {"autoRunOnLaunch": True}})
    up, system, clock = _updater(root, [(100, str(root / "mxu.exe"))])
    states = []

    def user_closes(now):
        states.append(_status(root)["state"])
        if now >= 1000:
            system.procs = []

    clock.hooks.append(user_closes)
    assert up.run() == 0
    assert "waiting_close" in states and not system.killed and not system.launched
    assert release.read_release(root)["version"] == "v0.2.0"


def test_run_restart_waits_for_other_processes(setup):
    """界面没开、但包里还有别的进程（命令行等）时等它们退出。"""
    root, _, _ = setup(RESTART)
    up, system, clock = _updater(root, [(200, str(root / "python/python.exe"))])
    clock.hooks.append(lambda now: now >= 30 and setattr(system, "procs", []))
    assert up.run() == 0
    assert clock.now >= 30 and not system.killed and not system.launched


def test_run_failed_self_test_rolls_back(setup, monkeypatch):
    root, _, _ = setup(RESTART)
    before = _snapshot(root)
    monkeypatch.setattr(updater, "self_test", lambda root: "ImportError: 坏了")
    up, system, _ = _updater(root, [(100, str(root / "mxu.exe"))])
    assert up.run() == 0
    assert _snapshot(root) == before
    assert _status(root)["state"] == "failed" and "坏了" in _status(root)["error"]
    assert "v0.2.0" in release.read_json(root / release.FAILED_FILE)
    assert system.launched == [root / "mxu.exe"]  # 还原后也重新打开界面
    up, _, _ = _updater(root)
    assert up.run() == 0 and _status(root)["state"] == "failed"  # 不再自动重试


def test_run_replace_error_rolls_back(setup, monkeypatch):
    root, _, _ = setup()
    before = _snapshot(root)
    real, calls = updater._copy, []

    def copy(src, dst):
        calls.append(dst)
        if len(calls) == 6:  # 备份完、换了几个文件后出错
            raise PermissionError("被占用")
        real(src, dst)

    monkeypatch.setattr(updater, "_copy", copy)
    up, _, _ = _updater(root)
    assert up.run() == 1
    assert _snapshot(root) == before
    assert _status(root)["state"] == "error" and "被占用" in _status(root)["error"]
    assert not (root / release.FAILED_FILE).exists()  # 暂时的问题，之后再试


def test_failed_rollback_preserves_backup_and_does_not_relaunch(setup, monkeypatch):
    root, _, _ = setup(RESTART)
    monkeypatch.setattr(updater, "self_test", lambda root: "坏了")

    def fail_rollback(self):
        raise PermissionError("旧文件被占用")

    monkeypatch.setattr(FilesApply, "rollback", fail_rollback)
    up, system, _ = _updater(root, [(100, str(root / "mxu.exe"))])
    assert up.run() == 1
    assert _status(root)["state"] == "rollback_failed"
    assert "备份保留" in _status(root)["error"] and not system.launched
    assert (root / updater.BACKUP_DIR / "v0.1.0" / release.RELEASE_FILE).is_file()
    assert "v0.2.0" in release.read_json(root / release.FAILED_FILE)
    assert up.run() == 1  # 未手动恢复前不能继续更新或清理旧备份
    assert (root / updater.BACKUP_DIR / "v0.1.0" / release.RELEASE_FILE).is_file()


def test_run_full_download(setup, server, tmp_path):
    """运行环境变了：下载整包。"""
    root, local, manifest = setup(runtime={"python": "3.15"})
    pkg = _zip(tmp_path / "srv" / "full.zip", {**HOT, "mxu.exe": "ui2", "python/python.exe": "py2", "release.json": json.dumps({**local, "version": "v0.2.0", "ui": "mxu"})})
    server.files["/full.zip"] = pkg.read_bytes()
    manifest["full"] = {"mxu": {"name": "full.zip", "size": pkg.stat().st_size, "sha256": release.sha256_file(pkg)}}
    release.write_json(root / release.MANIFEST_FILE, {"checked_at": 0, "version": "v0.2.0", "manifest": manifest})
    up, _, _ = _updater(root)
    assert up.run() == 0
    assert _status(root)["tier"] == "full" and (root / "mxu.exe").read_text(encoding="utf-8") == "ui2"
    assert (root / "config/config.yaml").read_text(encoding="utf-8") == "user"
    assert [p for p, _ in server.log] == ["/full.zip"]


def test_run_skips(setup, tmp_path):
    assert Updater(tmp_path, FakeSystem()).run() == 0  # 开发环境
    root, _, manifest = setup()
    release.write_json(root / release.MANIFEST_FILE, {"manifest": {**manifest, "version": "v0.1.0"}})
    assert Updater(root, FakeSystem()).run() == 0 and not (root / release.STATUS_FILE).exists()
    release.write_json(root / release.MANIFEST_FILE, {"manifest": {**manifest, "format": 2}})
    assert Updater(root, FakeSystem()).run() == 0 and not (root / release.STATUS_FILE).exists()


def test_run_auto_off_and_timeout(setup, monkeypatch):
    root, _, _ = setup(RESTART)
    ui = (100, str(root / "mxu.exe"))
    up, system, clock = _updater(root, [ui])
    system.foreground, system.idle = 100, 0.0
    clock.hooks.append(lambda now: now >= 20 and _write(root, updater.AUTO_OFF_FILE, ""))
    assert up.run() == 0 and _status(root)["state"] == "idle"
    (root / updater.AUTO_OFF_FILE).unlink()
    monkeypatch.setattr(updater, "MAX_WAIT_S", 100.0)
    up, system, clock = _updater(root, [ui])
    system.foreground, system.idle = 100, 0.0
    assert up.run() == 1
    assert _status(root)["state"] == "error" and release.read_release(root)["version"] == "v0.1.0"


def test_run_download_error(setup, server):
    root, _, _ = setup()
    server.files.clear()
    up, _, _ = _updater(root)
    assert up.run() == 1
    st = _status(root)
    assert st["state"] == "error" and st["version"] == "v0.2.0" and "404" in st["error"]


# ---------------------------------------------------------------- 入口


def test_main(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # main 会切换工作目录
    monkeypatch.setattr(updater, "setup_logging", lambda root: None)
    monkeypatch.setattr(updater, "in_job", lambda: False)
    (tmp_path / release.UPDATE_DIR).mkdir(parents=True)
    assert updater.main([]) == 2
    assert updater.main([str(tmp_path)]) == 0  # 没有 release.json
    lock = release.FileLock(tmp_path / release.UPDATER_LOCK)
    assert lock.acquire()
    try:
        monkeypatch.setattr(updater, "Updater", lambda root: pytest.fail("已经有一个在运行"))
        assert updater.main([str(tmp_path)]) == 0
    finally:
        lock.release()


def test_main_escapes_job(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(updater, "setup_logging", lambda root: None)
    monkeypatch.setattr(updater, "in_job", lambda: True)
    escaped, ran = [], []
    monkeypatch.setattr(updater, "escape_job", lambda root: escaped.append(root) or True)
    monkeypatch.setattr(updater, "Updater", lambda root, **kw: type("U", (), {"run": lambda self: ran.append(root) or 0})())
    (tmp_path / release.UPDATE_DIR).mkdir(parents=True)
    assert updater.main([str(tmp_path)]) == 0
    assert escaped == [tmp_path] and not ran  # 交给脱离出去的那个
    assert updater.main([str(tmp_path), updater.ESCAPED]) == 0
    assert escaped == [tmp_path] and ran == [tmp_path]


def test_setup_logging_rotates(tmp_path):
    log = _write(tmp_path, updater.LOG_FILE, "x" * (updater.LOG_MAX_BYTES + 1))
    before = list(logging.getLogger().handlers)
    updater.setup_logging(tmp_path)
    try:
        assert log.with_name("update.old.log").stat().st_size > updater.LOG_MAX_BYTES
        assert not log.exists() or log.stat().st_size == 0
    finally:
        for h in logging.getLogger().handlers:
            if h not in before:
                logging.getLogger().removeHandler(h)
                h.close()


def test_in_job():
    assert isinstance(updater.in_job(), bool)


@pytest.mark.skipif(os.name != "nt", reason="8.3 短名只有 Windows 有")
def test_norm_expands_short_names(tmp_path):
    import ctypes

    long_dir = tmp_path / "long directory name"
    long_dir.mkdir()
    buf = ctypes.create_unicode_buffer(32768)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(long_dir), buf, len(buf))
    if not n or "~" not in buf.value:
        pytest.skip("这个盘没开 8.3 短名")
    assert updater._norm(Path(buf.value) / "mxu.exe") == updater._norm(long_dir / "mxu.exe")
