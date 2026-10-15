import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from ournotes_auto import release

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("install", ROOT / "tools" / "install.py")
install = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(install)

OCR_FILES = ("det.onnx", "rec.onnx", "keys.txt", "LICENSE-MaaCommonAssets.txt", "LICENSE-PaddleOCR.txt")
PYPROJECT = '[project]\nname = "x"\ndependencies = [{deps}]\n'
CONSTRAINTS = "# 注释\nMaaFw==5.14.0\n\nPyYAML==6.0.3\n"
HEX64 = re.compile(r"[0-9a-f]{64}")


def _write(root: Path, rel: str, text: str = "x") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path, monkeypatch):
    """install 指向假的项目目录（发布模式读的文件都有，代码和模型是假的）；不装 maafw 和嵌入式 Python。"""
    root = tmp_path / "project"
    _write(root, "interface.json", (ROOT / "interface.json").read_text(encoding="utf-8"))
    _write(root, "pyproject.toml", PYPROJECT.format(deps='"PyYAML>=6.0", "MaaFw>=5.14"'))
    _write(root, "tools/constraints.txt", CONSTRAINTS)
    for rel in (
        "agent/main.py",
        "ournotes_auto/cli.py",
        "ournotes_auto/__pycache__/cli.cpython-314.pyc",
        "tasks/common.json",
        "locales/zh_cn.json",
        "docs/使用说明.md",
        "resource/pipeline/x.json",
        "resource/model/ocr/ko_kr/rec.onnx",  # 其他语言的模型不打包
        "README.md",
        "README.en.md",
        "LICENSE",
        "TERMS_OF_SERVICE.md",
        *(f"resource/model/ocr/{f}" for f in OCR_FILES),
    ):
        _write(root, rel, rel)
    monkeypatch.setattr(install, "ROOT", root)
    monkeypatch.setattr(install, "install_maafw", lambda out, kind: "test")
    monkeypatch.setattr(install, "install_python", lambda embed, out: _write(out, f"{install.PY_DIR}/python.exe"))
    return root


def _assemble(tmp_path: Path, monkeypatch, kind: str, *args: str) -> Path:
    """用解压后的假界面目录跑 main，返回输出目录。"""
    ui = tmp_path / f"ui-{kind}"
    for rel in (install.UIS[kind][0], "lib/a.dll"):
        _write(ui, rel, f"{kind} {rel}")
    out = tmp_path / "dist" / kind
    monkeypatch.setattr(sys, "argv", ["install.py", "--ui", str(ui), "--out", str(out), *args])
    assert install.main() == 0
    return out


def _release(tmp_path: Path, monkeypatch, kind: str, version: str = "v1.2.3") -> Path:
    return _assemble(tmp_path, monkeypatch, kind, "--python", "python-embed.zip", "--version", version)


def _written(tmp_path: Path, kind: str) -> dict:
    install.write_interface(tmp_path, kind, "v9.9.9", "./python/python.exe", ["./agent/main.py"])
    return json.loads((tmp_path / "interface.json").read_text(encoding="utf-8"))


def test_setting_only_for_mxu(tmp_path):
    mxu = _written(tmp_path, "mxu")
    assert mxu["setting"][0]["option"] == mxu["global_option"]
    assert (mxu["version"], mxu["agent"]["child_exec"], mxu["agent"]["child_args"]) == (
        "v9.9.9",
        "./python/python.exe",
        ["./agent/main.py"],
    )
    # MFAA 2.16.2 读到 setting 会连接卡住
    assert "setting" not in _written(tmp_path, "mfaa")
    assert "setting" not in json.loads((ROOT / "interface.json").read_text(encoding="utf-8"))


def test_icon(tmp_path):
    # MXU 读 interface.json 的 icon（相对项目根目录，组装后 docs 在同一位置）；MFAA 读 Assets/logo.ico
    iface = json.loads((ROOT / "interface.json").read_text(encoding="utf-8"))
    assert (ROOT / iface["icon"]).is_file()
    install.install_icon(tmp_path / "mfaa", "mfaa")
    assert (tmp_path / "mfaa" / "Assets" / "logo.ico").read_bytes() == install.ICON.read_bytes()
    install.install_icon(tmp_path / "mxu", "mxu")
    assert not (tmp_path / "mxu").exists()


def test_release_copy_skips_extra_ocr_models(tmp_path):
    src = tmp_path / "resource"
    for f in ("model/ocr/det.onnx", "model/ocr/ko_kr/rec.onnx", "pipeline/x.json"):
        (src / f).parent.mkdir(parents=True, exist_ok=True)
        (src / f).write_text("x")
    install.place(src, tmp_path / "out", link=False)
    assert (tmp_path / "out/model/ocr/det.onnx").is_file()
    assert (tmp_path / "out/pipeline/x.json").is_file()
    assert not (tmp_path / "out/model/ocr/ko_kr").exists()


# ---------------------------------------------------------------- 运行环境指纹与 release.json


@pytest.mark.parametrize("kind", ["mxu", "mfaa"])
def test_runtime_info(tmp_path, project, kind):
    out = tmp_path / "out"
    _write(out, install.UIS[kind][0], "exe")
    _write(out, "Assets/logo.ico", "ico")
    _write(out, "resource/model/ocr/det.onnx", "det")
    info = install.runtime_info(out, kind)
    keys = {"ui", "python", "deps", "model", "layout", "files"}
    assert set(info) == (keys | {"icon"} if kind == "mfaa" else keys)  # MXU 的图标在 interface.json 里，不算
    assert info["ui"] == release.sha256_file(out / install.UIS[kind][0])
    assert info["python"] == f"{sys.version_info.major}.{sys.version_info.minor}"
    assert info["model"] == release.tree_digest(out, release.MODEL_DIR) != release.tree_digest(tmp_path / "nope", release.MODEL_DIR)
    assert info["layout"] == release.LAYOUT and HEX64.fullmatch(info["deps"])
    if kind == "mfaa":
        assert info["icon"] == release.sha256_file(out / "Assets" / "logo.ico")
    # 分档时直接比较 release.json 和 update.json 里的指纹：写进去再读出来要一样，重算也一样
    assert json.loads(json.dumps(info)) == info == install.runtime_info(out, kind)


def test_runtime_info_deps(tmp_path, project):
    """依赖指纹只看固定的版本和 pyproject 的依赖，注释、空行和顺序不算。"""
    out = tmp_path / "out"
    _write(out, "mxu.exe")

    def deps() -> str:
        return install.runtime_info(out, "mxu")["deps"]

    base = deps()
    constraints = project / "tools" / "constraints.txt"
    constraints.write_text("PyYAML==6.0.3\n  # 另一条注释\nMaaFw==5.14.0\n", encoding="utf-8")
    assert deps() == base
    constraints.write_text(CONSTRAINTS.replace("5.14.0", "5.15.0"), encoding="utf-8")
    assert deps() != base
    constraints.write_text(CONSTRAINTS, encoding="utf-8")
    _write(project, "pyproject.toml", PYPROJECT.format(deps='"MaaFw>=5.14", "PyYAML>=6.0"'))
    assert deps() == base
    _write(project, "pyproject.toml", PYPROJECT.format(deps='"MaaFw>=5.14", "PyYAML>=6.0", "requests>=2.31"'))
    assert deps() != base


def test_runtime_info_reads_project_files(tmp_path):
    # 项目自己的 constraints.txt 和 pyproject.toml 能解析
    _write(tmp_path, "mxu.exe")
    assert HEX64.fullmatch(install.runtime_info(tmp_path, "mxu")["deps"])


def test_runtime_info_detects_dll_and_python_changes(tmp_path, project):
    _write(tmp_path, "mxu.exe")
    _write(tmp_path, "lib/ui.dll", "old")
    _write(tmp_path, "python/python314.dll", "old")
    base = install.runtime_info(tmp_path, "mxu")["files"]
    _write(tmp_path, "lib/ui.dll", "new")
    changed = install.runtime_info(tmp_path, "mxu")["files"]
    assert changed != base
    _write(tmp_path, "python/python314.dll", "new")
    python_changed = install.runtime_info(tmp_path, "mxu")["files"]
    assert python_changed != changed
    # app、用户数据、自动更新元数据、运行时生成的字节码不改变运行环境。
    for rel in ("ournotes_auto/cli.py", "config/user.json", release.RELEASE_FILE, "python/Lib/__pycache__/x.pyc"):
        _write(tmp_path, rel)
    assert install.runtime_info(tmp_path, "mxu")["files"] == python_changed
    # pip 生成的启动器带打包时间、RECORD 记着它们的哈希，每次构建都不一样，也不算
    for rel in ("python/Lib/site-packages/bin/f2py.exe", "python/Lib/site-packages/numpy-2.5.3.dist-info/RECORD"):
        _write(tmp_path, rel, "build 1")
        assert install.runtime_info(tmp_path, "mxu")["files"] == python_changed
        _write(tmp_path, rel, "build 2")
        assert install.runtime_info(tmp_path, "mxu")["files"] == python_changed
    _write(tmp_path, "python/Lib/site-packages/numpy/core.pyd", "new")
    assert install.runtime_info(tmp_path, "mxu")["files"] != python_changed  # 依赖本身的文件照算


@pytest.mark.parametrize("kind", ["mxu", "mfaa"])
def test_release_writes_release_json(tmp_path, monkeypatch, project, kind):
    out = tmp_path / "dist" / kind
    _write(out, "config/config.yaml")  # 在已有的目录上重新组装：用户数据
    _write(out, f"{release.UPDATE_DIR}/status.json", "{}")
    _write(out, f"LICENSE-{kind.upper()}")  # CI 事先放好的界面许可证
    assert _release(tmp_path, monkeypatch, kind) == out
    info = release.read_release(out)
    assert info is not None
    assert (info["format"], info["version"], info["ui"], info["ui_exe"]) == (release.FORMAT, "v1.2.3", kind, release.UI_EXE[kind])
    assert info["runtime"] == install.runtime_info(out, kind)
    assert info["runtime"]["ui"] == release.sha256_file(out / install.UIS[kind][0])
    # files 是 app 层文件：界面程序、Python、OCR 模型、用户数据和 __pycache__ 都不在
    assert info["files"] == release.manifest_files(out)
    assert sorted(info["files"]) == [
        "LICENSE",
        "README.en.md",
        "README.md",
        "TERMS_OF_SERVICE.md",
        "agent/main.py",
        "docs/使用说明.md",
        "interface.json",
        "locales/zh_cn.json",
        "ournotes_auto/cli.py",
        "resource/pipeline/x.json",
        "tasks/common.json",
    ]
    assert info["files"]["interface.json"] == release.interface_digest(json.loads((project / "interface.json").read_text(encoding="utf-8")))
    # top 是包根目录下除用户数据外的全部条目，整包更新时只换这些
    assert info["top"] == sorted({p.name for p in out.iterdir()} - release.USER_DIRS)
    assert {release.RELEASE_FILE, install.PY_DIR, install.UIS[kind][0], f"LICENSE-{kind.upper()}", "lib", "agent", "resource"} <= set(info["top"])
    assert {"config", "cache"}.isdisjoint(info["top"])


def test_release_files_same_for_both_uis(tmp_path, monkeypatch, project):
    """两种界面的 app 层清单一样（tools/make_update.py 要求），interface.json 的版本号、Agent 命令、setting 不算。"""
    mxu_out = _release(tmp_path, monkeypatch, "mxu", "v1.2.3")
    mfaa_out = _release(tmp_path, monkeypatch, "mfaa", "v2.0.0")
    mxu, mfaa = release.read_release(mxu_out), release.read_release(mfaa_out)
    assert mxu["files"] == mfaa["files"] and mxu["runtime"] != mfaa["runtime"]
    mxu_iface = json.loads((mxu_out / "interface.json").read_text(encoding="utf-8"))
    mfaa_iface = json.loads((mfaa_out / "interface.json").read_text(encoding="utf-8"))
    assert (mxu_iface["version"], mfaa_iface["version"]) == ("v1.2.3", "v2.0.0")
    assert "setting" in mxu_iface and "setting" not in mfaa_iface
    assert mxu_iface["agent"]["child_exec"] == mfaa_iface["agent"]["child_exec"] == release.AGENT_EXEC


def test_dev_mode_no_release_json(tmp_path, monkeypatch, project):
    # 开发模式不写 release.json：Agent 读不到就不自动更新
    out = _assemble(tmp_path, monkeypatch, "mxu", "--dev")
    assert not (out / release.RELEASE_FILE).exists() and release.read_release(out) is None
    iface = json.loads((out / "interface.json").read_text(encoding="utf-8"))
    assert iface["agent"]["child_exec"] == str(project / ".venv" / "Scripts" / "python.exe")
