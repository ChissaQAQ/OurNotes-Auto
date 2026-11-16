"""发布包的文件清单、分档规则和锁（ournotes_auto/release.py）。"""

import ast
import html
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from ournotes_auto import release

ROOT = Path(__file__).resolve().parents[1]


def _write(root: Path, rel: str, text: str = "x") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_version():
    assert release.parse_version("v0.1.0") == (0, 1, 0)
    assert release.parse_version(" 1.10.2 ") == (1, 10, 2)
    assert release.parse_version("v0.1.6.1") == (0, 1, 6, 1)
    for text in ("dev-1a2b3c4", "v1.2", "v1.2.3-beta", "v1.2.3.4.5", "v1.2.3.", "", None):
        assert release.parse_version(text) is None


@pytest.mark.parametrize(
    "latest, current, newer",
    [
        ("v0.2.0", "v0.1.0", True),
        ("v0.10.0", "v0.9.9", True),  # 按数字比，不按字符串
        ("v0.1.0", "v0.1.0", False),
        ("v0.1.0", "v0.2.0", False),
        ("v0.1.6.1", "v0.1.6", True),  # 小修补
        ("v0.1.6.10", "v0.1.6.9", True),
        ("v0.1.7", "v0.1.6.3", True),
        ("v0.1.6", "v0.1.6.1", False),
        ("v0.2.0", "dev-1a2b3c4", False),  # 开发版不提示
        (None, "v0.1.0", False),
    ],
)
def test_is_newer(latest, current, newer):
    assert release.is_newer(latest, current) is newer


def test_render_interface():
    data = json.loads((ROOT / "interface.json").read_text(encoding="utf-8"))
    mxu = release.render_interface(data, "mxu", "v9.9.9")
    mfaa = release.render_interface(data, "mfaa", "v9.9.9")
    assert [s["name"] for s in mxu["setting"]] == ["stats", "global", "humanize", "update"]
    # 全局选项分成「所有任务共用」「拟人化」「更新」三组，各自保持 global_option 里的顺序，合起来一个不少
    shared, humanize, update = (mxu["setting"][i]["option"] for i in (1, 2, 3))
    assert humanize == list(release.HUMANIZE_OPTIONS) and update == [release.UPDATE_OPTION, *release.UPDATE_OPTIONS]
    assert sorted(shared + humanize + update[1:], key=mxu["global_option"].index) == mxu["global_option"]
    assert shared == [o for o in mxu["global_option"] if o not in release.HUMANIZE_OPTIONS + release.UPDATE_OPTIONS]
    assert [s["name"] for s in release.render_interface({**data, "global_option": ["Touch"]}, "mxu")["setting"]] == ["stats", "global"]
    # 设置页的「统计与成就」卡片：没有可选项的多选框，界面只显示它的说明（data/stats_mxu.md）
    assert mxu["setting"][0]["option"] == [release.STATS_OPTION] and mxu["option"][release.STATS_OPTION]["cases"] == []
    assert release.STATS_OPTION not in mfaa.get("option", {}) and release.STATS_OPTION not in data.get("option", {})
    # 「版本状态」卡片定义在 tasks/common.json 里（两种界面的包一样），只有 MXU 的设置页引用：打包时不能再往
    # interface.json 的 option 里加东西，v0.2.0～v0.2.6 算摘要时只去掉 STATS_OPTION，整包更新核对会对不上
    assert set(mxu["option"]) == {release.STATS_OPTION} and release.UPDATE_OPTION not in data["global_option"]
    card = json.loads((ROOT / "tasks" / "common.json").read_text(encoding="utf-8"))["option"][release.UPDATE_OPTION]
    assert card == {"type": "checkbox", "label": "$opt.UpdateView", "description": "$opt.UpdateView.desc", "cases": []}
    for path in data["languages"].values():
        assert json.loads((ROOT / path).read_text(encoding="utf-8"))["opt.UpdateView.desc"] == release.UPDATE_CARD
    assert "setting" not in mfaa and "setting" not in data  # MFAA 2.16.2 读到 setting 会连接卡住
    assert release.render_interface(mxu, "mxu") == mxu  # 再生成一遍不变（Agent 补卡片时）
    assert (mfaa["version"], mfaa["agent"]["child_exec"], mfaa["agent"]["child_args"]) == ("v9.9.9", release.AGENT_EXEC, list(release.AGENT_ARGS))
    # 只差打包时写的部分，摘要一样；改了别的就不一样
    digest = release.interface_digest(data)
    assert release.interface_digest(mxu) == release.interface_digest(mfaa) == digest
    assert release.interface_digest({**data, "global_option": []}) != digest


def test_app_files(tmp_path):
    for rel in (
        "interface.json",
        "README.md",
        "README.en.md",
        "LICENSE",
        "agent/main.py",
        "ournotes_auto/cli.py",
        "ournotes_auto/__pycache__/cli.cpython-314.pyc",
        "ournotes_auto/charts/titles.json",  # 随包附带的曲名表、封面在 app 层
        "ournotes_auto/charts/jackets/100001.png",
        "ournotes_auto/charts/bundle/charts.json",  # 随整包附带的谱面不在：自动更新不带、不动它
        "ournotes_auto/charts/bundle/100001_expert.json.gz",
        "tasks/common.json",
        "resource/pipeline/x.json",
        "resource/model/ocr/det.onnx",  # 模型算运行环境
        "docs/使用说明.md",
        "python/python.exe",
        "config/config.yaml",
        "mxu.exe",
        "LICENSE-MXU",
    ):
        _write(tmp_path, rel, "{}" if rel.endswith(".json") else rel)
    files = release.app_files(tmp_path)
    assert files == sorted(
        [
            "LICENSE",
            "README.en.md",
            "README.md",
            "agent/main.py",
            "docs/使用说明.md",
            "interface.json",
            "ournotes_auto/charts/jackets/100001.png",
            "ournotes_auto/charts/titles.json",
            "ournotes_auto/cli.py",
            "resource/pipeline/x.json",
            "tasks/common.json",
        ]
    )
    manifest = release.manifest_files(tmp_path)
    assert manifest["interface.json"] == release.interface_digest({})
    assert manifest["LICENSE"] == release.sha256_file(tmp_path / "LICENSE")


def test_tree_digest(tmp_path):
    empty = release.tree_digest(tmp_path, "resource/model")
    _write(tmp_path, "resource/model/ocr/det.onnx", "a")
    one = release.tree_digest(tmp_path, "resource/model")
    assert one != empty
    _write(tmp_path, "resource/model/ocr/det.onnx", "b")
    assert release.tree_digest(tmp_path, "resource/model") != one


def _local(files, runtime="r1"):
    return {"ui": "mfaa", "runtime": runtime, "files": files}


def _manifest(files, runtime="r1"):
    return {"runtime": {"mfaa": runtime, "mxu": "other"}, "files": files}


@pytest.mark.parametrize(
    "new, runtime, expected",
    [
        ({"ournotes_auto/runner.py": "2"}, "r1", "hot"),
        ({"ournotes_auto/runner.py": "1", "docs/new.md": "1"}, "r1", "hot"),
        ({"ournotes_auto/runner.py": "1", "tasks/common.json": "2"}, "r1", "restart"),
        ({"ournotes_auto/runner.py": "1", "interface.json": "2"}, "r1", "restart"),
        ({"ournotes_auto/runner.py": "1", "ournotes_auto/agent/server.py": "2"}, "r1", "restart"),
        ({}, "r1", "restart"),  # 删掉的也算改动
        ({"ournotes_auto/runner.py": "1", "tasks/common.json": "1", "interface.json": "1", "ournotes_auto/agent/server.py": "1"}, "r2", "full"),
    ],
)
def test_tier(new, runtime, expected):
    old = {"ournotes_auto/runner.py": "1", "tasks/common.json": "1", "interface.json": "1", "ournotes_auto/agent/server.py": "1"}
    assert release.tier(_local(old), _manifest({**old, **new} if new else {}, runtime)) == expected


def test_tier_missing_runtime_for_ui():
    assert release.tier(_local({}), {"runtime": {"mxu": "r1"}, "files": {}}) == "full"


# ---------------------------------------------------------------- MXU 设置页的「版本状态」卡片


def _mxu(root: Path, version: str = "v0.1.0", ui: str = "mxu") -> Path:
    info = {"format": 1, "version": version, "ui": ui, "ui_exe": release.UI_EXE[ui], "runtime": "r", "files": {}, "top": []}
    release.write_json(root / release.RELEASE_FILE, info)
    return root


def _rows(root: Path) -> list[str]:
    """卡片中间的几行（第一行是版本，最后一行是说明）的文字。"""
    lines = release.update_card(root).splitlines()
    assert lines[0].startswith("<div ") and lines[-1] == "</div>" and all(lines)  # 一个 HTML 块，中间没有空行
    return [html.unescape(re.sub(r"<[^>]+>", "", line)) for line in lines[2:-2]]


def test_update_card(tmp_path):
    t = 1_800_000_000.0
    when, never = release._when(t), "还没有检查过更新，开始任意任务时会检查。"
    assert re.fullmatch(r"\d\d-\d\d \d\d:\d\d", when) and release._when(None) == release._when("x") == release._when(float("inf")) == ""
    assert release.update_card(tmp_path) is None and release.update_card(_mxu(tmp_path / "mfaa", ui="mfaa")) is None
    root = _mxu(tmp_path / "mxu")
    text = release.update_card(root)
    assert ">v0.1.0</b>" in text and html.escape(release.BADGE_URL) in text and text.count(f'href="{release.DOWNLOAD_URL}"') == 2
    assert "label=%E6%9C%80%E6%96%B0%E7%89%88%E6%9C%AC" in release.BADGE_URL and f"{release.CHECK_TTL_S // 60} 分钟内查过的不重复查" in text
    assert _rows(root) == [never]

    def check(version, manifest=None, at=t):
        release.write_json(root / release.MANIFEST_FILE, {"checked_at": at, "version": version, "manifest": manifest})

    def status(state, version="v0.2.0", **extra):
        release.write_json(root / release.STATUS_FILE, {"state": state, "version": version, "time": t + 60, **extra})

    check("v0.1.0")
    assert _rows(root) == [f"{when} 检查：已是最新版本。"]
    status("done", version="v0.1.0")  # 换上新版本、界面重开后
    assert _rows(root) == [f"{when} 检查：已是最新版本。", f"{release._when(t + 60)} 已自动更新到 v0.1.0。"]
    check("v0.2.0", {"format": 1})
    found, soon = f"{when} 检查：发现新版本 v0.2.0。", "v0.2.0 会在开始任务后自动下载，下载好了自动换上。"
    assert _rows(root) == [found, soon]  # 更新器还没开始
    for state, extra, step in (
        ("downloading", {"progress": 0.5}, "正在后台下载 v0.2.0。"),
        ("waiting", {"tier": "hot"}, "v0.2.0 已下载，没有任务运行时自动换上。"),
        ("waiting", {"tier": "restart"}, "v0.2.0 已下载，任务都结束、界面空闲 2 分钟后自动重启界面完成更新。"),
        ("waiting_close", {"tier": "full"}, "v0.2.0 已下载；开着「启动时自动执行」时不会自动重启界面，关闭界面后自动完成更新。"),
        ("applying", {}, "正在换上 v0.2.0。"),
        ("error", {"error": "断网 <1>"}, "自动更新到 v0.2.0 暂时没成功（断网 <1>），稍后会重试；也可以到发布页手动下载。"),
        ("failed", {"error": "x"}, "自动更新到 v0.2.0 失败过，已恢复原版本（详见 data/update.log），请到发布页手动下载。"),
        ("idle", {}, soon),
    ):
        status(state, **extra)
        assert _rows(root) == [found, step], state
    status("error", error="断网 <1>")
    assert "断网 &lt;1&gt;" in release.update_card(root)  # 原因里的字符不会被当成 HTML
    status("waiting", version="v0.1.9", tier="hot")  # 别的版本留下的状态不算
    assert _rows(root) == [found, soon]
    status("rollback_failed", error="x")
    assert _rows(root)[1].startswith("自动更新到 v0.2.0 后还原失败，任务暂时开始不了")
    status("idle")
    release.write_json(root / release.FAILED_FILE, {"v0.2.0": {"error": "x"}})
    assert _rows(root)[1].startswith("自动更新到 v0.2.0 失败过")
    (root / release.FAILED_FILE).unlink()
    for manifest in (None, {"format": release.FORMAT + 1}):  # 这个版本没有自动更新文件、update.json 是新格式
        check("v0.2.0", manifest)
        assert _rows(root) == [found, "v0.2.0 不能自动更新，请到发布页手动下载。"]
    check("v0.2.0", {"format": 1})
    manual = "没有开「自动更新」，v0.2.0 请到发布页手动下载。"
    (root / release.AUTO_OFF_FILE).write_text("", encoding="utf-8")
    assert _rows(root) == [found, manual]
    (root / release.CHECK_OFF_FILE).write_text("", encoding="utf-8")
    assert _rows(root) == ["「检查更新」关着：开始任务时不会检查，这里也不联网取最新版本。", manual]
    off = release.update_card(root)  # 关掉「检查更新」后卡片不联网：没有最新版本的图片，只留一个手动下载的链接
    assert "<img" not in off and "shields.io" not in off and off.count(f'href="{release.DOWNLOAD_URL}"') == 1
    (root / release.AUTO_OFF_FILE).unlink()
    (root / release.CHECK_OFF_FILE).unlink()
    # 没查到：比上次查到的新才算；之前查到的新版本照常更新
    release.write_json(root / release.CHECK_ERROR_FILE, {"time": t - 1, "error": "连不上 GitHub"})
    assert _rows(root) == [found, soon]
    release.write_json(root / release.CHECK_ERROR_FILE, {"time": t + 1, "error": "连不上 GitHub"})
    rows = _rows(root)
    assert rows[0].startswith(f"{release._when(t + 1)} 检查更新没成功（连不上 GitHub）。") and "网络代理" in rows[0] and rows[1:] == [soon]
    (root / release.MANIFEST_FILE).unlink()  # 从来没查到过
    assert len(_rows(root)) == 1 and "检查更新没成功" in _rows(root)[0]
    # 读不了、格式不对的文件当没有
    for content in ("[1", '{"version": 3, "checked_at": "x", "time": "x", "state": []}', '"x"'):
        for rel in (release.MANIFEST_FILE, release.CHECK_ERROR_FILE, release.STATUS_FILE):
            (root / rel).write_text(content, encoding="utf-8")
        assert _rows(root) == [never], content


def test_write_update_card(tmp_path, monkeypatch):
    root = _mxu(tmp_path)
    path = root / release.UPDATE_CARD
    release.write_update_card(root)
    assert path.read_text(encoding="utf-8") == release.update_card(root)
    with monkeypatch.context() as m:
        m.setattr(release, "write_text", lambda *a, **k: pytest.fail("内容没变不重写"))
        release.write_update_card(root)
    with monkeypatch.context() as m:  # 卡片只是给人看的：算不出来、写不了都不报错
        m.setattr(release, "update_card", lambda root: 1 / 0)
        release.write_update_card(root)
        m.setattr(release, "update_card", lambda root: "new")
        m.setattr(release, "write_text", lambda *a, **k: (_ for _ in ()).throw(PermissionError("locked")))
        release.write_update_card(root)
    (tmp_path / "dev").mkdir()
    release.write_update_card(tmp_path / "dev")  # 不是 MXU 的发布包不写
    assert not (tmp_path / "dev" / release.UPDATE_CARD).exists()


def test_zip_name_and_safe_rel(tmp_path):
    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w") as zf:
        info = zipfile.ZipInfo("docs/使用说明.md")
        zf.writestr(info, "x")
    with zipfile.ZipFile(path) as zf:
        assert [release.zip_name(i) for i in zf.infolist()] == ["docs/使用说明.md"]
    assert release.safe_rel("./a\\b/c.txt") == "a/b/c.txt"
    for bad in ("../x", "a/../../x", "C:/x", "", "/"):
        assert release.safe_rel(bad) is None
    assert release.safe_rel("/abs/x") == "abs/x"  # 开头的 / 去掉，仍在解压目录里


def test_release_info_roundtrip(tmp_path):
    _write(tmp_path, "interface.json", "{}")
    _write(tmp_path, "ournotes_auto/cli.py")
    _write(tmp_path, "config/config.yaml")
    _write(tmp_path, "mxu.exe")
    info = release.release_info(tmp_path, "mxu", "v1.0.0", {"python": "3.14"})
    assert info["top"] == ["interface.json", "mxu.exe", "ournotes_auto", "release.json"]
    assert info["ui_exe"] == "mxu.exe" and info["files"] == release.manifest_files(tmp_path)
    release.write_json(tmp_path / release.RELEASE_FILE, info)
    assert release.read_release(tmp_path) == info
    (tmp_path / release.RELEASE_FILE).write_text(json.dumps({**info, "ui": "x"}), encoding="utf-8")
    assert release.read_release(tmp_path) is None
    (tmp_path / release.RELEASE_FILE).write_text("{", encoding="utf-8")
    assert release.read_release(tmp_path) is None
    assert release.read_release(tmp_path / "nope") is None


LOCK_CHILD = r"""
import sys, time
sys.path.insert(0, {root!r})
from pathlib import Path
from ournotes_auto.release import FileLock
lock = FileLock(Path(sys.argv[1]))
assert lock.acquire()
print("locked", flush=True)
sys.stdin.readline()
"""


def _hold(path: Path) -> subprocess.Popen:
    """另一个进程锁住 ``path``，直到给它的标准输入写一行。"""
    proc = subprocess.Popen(
        [sys.executable, "-c", LOCK_CHILD.format(root=str(ROOT)), str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout.readline().strip() == "locked"
    return proc


def test_file_lock(tmp_path):
    path = tmp_path / "x" / "a.lock"
    lock = release.FileLock(path)
    assert lock.acquire() and lock.held and lock.acquire()
    assert not release.FileLock(path).acquire()  # 同一进程另开一次也锁不上
    lock.release()
    assert not lock.held and not release.is_locked(path)
    proc = _hold(path)
    try:
        assert release.is_locked(path)
    finally:
        proc.communicate("\n", timeout=10)
    assert not release.is_locked(path)  # 进程退出后系统自动解锁
    assert not release.is_locked(tmp_path / "nope.lock")


def test_busy(tmp_path):
    base = tmp_path / release.BUSY_DIR
    assert not release.busy(tmp_path)
    _write(tmp_path, f"{release.BUSY_DIR}/1.lock", "")  # Agent 异常退出留下的
    assert not release.busy(tmp_path) and not (base / "1.lock").exists()
    proc = _hold(base / "2.lock")
    try:
        assert release.busy(tmp_path) and (base / "2.lock").exists()
    finally:
        proc.communicate("\n", timeout=10)
    assert not release.busy(tmp_path)


# ---------------------------------------------------------------- 约定的一致性


def test_agent_imports_only_shell():
    """Agent 进程实际导入的本项目模块都在 release.SHELL 里：其他模块改了热更新就能生效。"""
    code = "import sys; import ournotes_auto.agent.server; print('\\n'.join(sorted(m.__file__ for n, m in sys.modules.items() if n.startswith('ournotes_auto'))))"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", timeout=30, check=True).stdout
    loaded = {Path(p).resolve().relative_to(ROOT).as_posix() for p in out.split()}
    assert loaded - {"ournotes_auto/__init__.py"} <= release.SHELL
    assert "agent/main.py" in release.SHELL and all((ROOT / rel).is_file() for rel in release.SHELL)


def test_package_init_has_only_version():
    """ournotes_auto/__init__.py 不在 SHELL 里：只能有文档字符串和版本号，Agent 用旧的不影响运行。"""
    tree = ast.parse((ROOT / "ournotes_auto" / "__init__.py").read_text(encoding="utf-8"))
    body = tree.body
    assert isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
    assert len(body) == 2 and isinstance(body[1], ast.Assign) and body[1].targets[0].id == "__version__"


def test_versions_match():
    from ournotes_auto import __version__

    interface = json.loads((ROOT / "interface.json").read_text(encoding="utf-8"))
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert interface["version"].lstrip("v") == __version__ == project["version"]


def test_param_node_matches():
    from ournotes_auto.agent import params

    # 主测试进程不能导入 Agent：MaaFramework 切到服务模式后，后面的真实 OCR 测试就无法创建 Resource。
    tree = ast.parse((ROOT / "ournotes_auto/agent/server.py").read_text(encoding="utf-8"))
    value = next(node.value for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PARAM_NODE" for t in node.targets))
    assert ast.literal_eval(value) == params.PARAM_NODE


def test_updater_uses_only_stdlib():
    """更新器在 runner 目录里用嵌入式 Python 运行，只有标准库和 release.py。"""
    for name in ("updater.py", "release.py"):
        tree = ast.parse((ROOT / "ournotes_auto" / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""] if node.level == 0 else []
                assert node.level == 0 or node.names[0].name == "release", (name, ast.dump(node))
            else:
                continue
            for mod in mods:
                top = mod.split(".")[0]
                assert top == "release" or top in sys.stdlib_module_names, (name, mod)


def test_update_options():
    """「自动更新」紧跟在「检查更新」后面，两个开关的默认值和 pipeline 一致。"""
    interface = json.loads((ROOT / "interface.json").read_text(encoding="utf-8"))
    names = interface["global_option"]
    assert names[names.index("CheckUpdate") + 1] == "AutoUpdate"
    options = json.loads((ROOT / "tasks" / "common.json").read_text(encoding="utf-8"))["option"]
    pipeline = json.loads((ROOT / "resource" / "pipeline" / "ournotes.json").read_text(encoding="utf-8"))
    attach = pipeline["OurNotesParam"]["attach"]
    for name, key in (("CheckUpdate", "check_update"), ("AutoUpdate", "auto_update")):
        option = options[name]
        default = next(c for c in option["cases"] if c["name"] == option["default_case"])
        assert default["pipeline_override"]["OurNotesParam"]["attach"] == {key: True} and attach[key] is True


def test_write_retries_when_locked(tmp_path, monkeypatch):
    """文件被别的程序（浏览器、杀毒软件）读着、替换不了时等一下再试；一直不行就报错，不留临时文件。"""
    real, calls = release.os.replace, []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) < 3:
            raise PermissionError("locked")
        real(src, dst)

    def locked(src, dst):
        raise PermissionError("locked")

    monkeypatch.setattr(release.time, "sleep", lambda s: None)
    monkeypatch.setattr(release.os, "replace", flaky)
    release.write_text(tmp_path / "a.txt", "x")
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "x" and len(calls) == 3

    monkeypatch.setattr(release.os, "replace", locked)
    with pytest.raises(PermissionError):
        release.write_text(tmp_path / "b.txt", "y", tries=3)
    assert not (tmp_path / "b.txt").exists() and not list(tmp_path.glob("*.tmp"))
