import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("install", ROOT / "tools" / "install.py")
install = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(install)


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
