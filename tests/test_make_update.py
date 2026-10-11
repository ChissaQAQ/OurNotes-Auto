"""发布包生成更新清单，再由更新器读取、应用的离线流程。"""

import sys
import zipfile

import pytest

from ournotes_auto import release
from ournotes_auto.updater import FilesApply, extract, verify_stage
from tools import make_update


def package(tmp_path, ui, version="v1.2.3", text="new"):
    root = tmp_path / ui
    root.mkdir()
    (root / "docs").mkdir()
    (root / "docs/使用说明.md").write_text(text, encoding="utf-8")
    interface = release.render_interface({"global_option": [], "agent": {}}, ui, version)
    release.write_json(root / "interface.json", interface)
    (root / release.UI_EXE[ui]).write_text(ui, encoding="utf-8")
    runtime = {"ui": ui, "python": "3.14"}
    release.write_json(root / release.RELEASE_FILE, release.release_info(root, ui, version, runtime))
    archive = tmp_path / f"{ui}.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in root.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(root).as_posix())
    return archive, root


@pytest.mark.parametrize("ui", ["mfaa", "mxu"])
def test_generate_and_apply_update(tmp_path, monkeypatch, ui):
    archives = [package(tmp_path, kind)[0] for kind in release.UI_EXE]
    out = tmp_path / "dist"
    monkeypatch.setattr(sys, "argv", ["make_update.py", *map(str, archives), "--out", str(out)])
    assert make_update.main() == 0
    manifest = release.read_json(out / release.UPDATE_ASSET)
    assert manifest["version"] == "v1.2.3" and set(manifest["full"]) == set(release.UI_EXE)
    app = out / manifest["app"]["name"]
    assert release.sha256_file(app) == manifest["app"]["sha256"]
    for kind, archive in zip(release.UI_EXE, archives):
        assert manifest["full"][kind]["sha256"] == release.sha256_file(archive)
    stage = tmp_path / "stage"
    extract(app, stage)
    verify_stage(stage, manifest, full=False, ui=ui)
    old = tmp_path / "old"
    old.mkdir()
    _, root = package(old, ui, version="v1.2.2", text="old")
    local = release.read_release(root)
    assert release.tier(local, manifest) == "hot"
    (root / "config").mkdir()
    (root / "config/user.json").write_text("user", encoding="utf-8")
    job = FilesApply(root, stage, local, manifest, root / "cache/backup")
    job.run()
    assert (root / "docs/使用说明.md").read_text(encoding="utf-8") == "new"
    assert (root / "config/user.json").read_text(encoding="utf-8") == "user"
    assert release.read_release(root)["files"] == manifest["files"] == release.manifest_files(root)
    interface = release.read_json(root / "interface.json")
    assert interface["version"] == "v1.2.3" and ("setting" in interface) == (ui == "mxu")
    job.rollback()
    assert release.read_release(root)["version"] == "v1.2.2"


@pytest.mark.parametrize("version,text", [("v1.2.4", "new"), ("v1.2.3", "different")])
def test_reject_inconsistent_packages(tmp_path, monkeypatch, version, text):
    mfaa, _ = package(tmp_path, "mfaa")
    mxu, _ = package(tmp_path, "mxu", version, text)
    monkeypatch.setattr(sys, "argv", ["make_update.py", str(mfaa), str(mxu), "--out", str(tmp_path / "dist")])
    with pytest.raises(SystemExit, match="不一致"):
        make_update.main()


def test_reject_package_with_modified_files(tmp_path):
    archive, _ = package(tmp_path, "mfaa")
    with zipfile.ZipFile(archive, "a") as zf:
        zf.writestr("docs/extra.md", "not in manifest")
    with pytest.raises(SystemExit, match="对不上"):
        make_update.read_package(archive)
