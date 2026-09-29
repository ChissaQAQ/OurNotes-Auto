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
