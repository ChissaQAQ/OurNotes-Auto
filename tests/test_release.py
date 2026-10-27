"""发布包的文件清单、分档规则和锁（ournotes_auto/release.py）。"""

import ast
import json
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
    assert [s["name"] for s in mxu["setting"]] == ["stats", "global"]
    assert mxu["setting"][1]["option"] == mxu["global_option"]
    # 设置页的「统计与成就」卡片：没有可选项的多选框，界面只显示它的说明（data/stats_mxu.md）
    assert mxu["setting"][0]["option"] == [release.STATS_OPTION] and mxu["option"][release.STATS_OPTION]["cases"] == []
    assert release.STATS_OPTION not in mfaa.get("option", {}) and release.STATS_OPTION not in data.get("option", {})
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
