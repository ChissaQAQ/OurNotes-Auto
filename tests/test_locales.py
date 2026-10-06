"""界面翻译：interface.json / tasks 里的 ``$键`` 都有翻译，各语言的键一致。"""

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_FIELDS = ("label", "description", "welcome", "pattern_msg")
CJK = re.compile(r"[぀-ヿ一-鿿가-힣]")
# 给各语言用户看的语言名，不翻译
LITERAL = {"简体中文 / 繁體中文 / English", "한국어"}


def _load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _texts(node, out: list[str]) -> list[str]:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in TEXT_FIELDS and isinstance(v, str):
                out.append(v)
            else:
                _texts(v, out)
    elif isinstance(node, list):
        for v in node:
            _texts(v, out)
    return out


def _interface_texts() -> list[str]:
    iface = _load("interface.json")
    texts = _texts(iface, [])
    for path in iface["import"]:
        _texts(_load(path), texts)
    return texts


def test_every_language_has_same_keys():
    langs = _load("interface.json")["languages"]
    base = _load(langs["zh_cn"])
    for code, path in langs.items():
        data = _load(path)
        assert list(data) == list(base), code
        assert all(isinstance(v, str) and v.strip() for v in data.values()), code
        assert (ROOT / data["app.welcome"]).is_file(), code


def test_interface_keys_translated():
    base = _load(_load("interface.json")["languages"]["zh_cn"])
    texts = _interface_texts()
    used = {t[1:] for t in texts if t.startswith("$")}
    # 组装 MXU 时加的 setting 分组
    used |= {"setting.global", "setting.global.desc"}
    assert used - set(base) == set()
    assert set(base) - used == set()
    # 新加的界面文字要走翻译文件
    assert [t for t in texts if not t.startswith("$") and CJK.search(t) and t not in LITERAL] == []


def test_mxu_setting_uses_keys(tmp_path):
    spec = importlib.util.spec_from_file_location("install", ROOT / "tools" / "install.py")
    install = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(install)
    install.write_interface(tmp_path, "mxu", None, "python.exe", [])
    setting = json.loads((tmp_path / "interface.json").read_text(encoding="utf-8"))["setting"][0]
    assert (setting["label"], setting["description"]) == ("$setting.global", "$setting.global.desc")


def test_readme_language_links():
    for readme in ROOT.glob("README*.md"):
        text = readme.read_text(encoding="utf-8")
        for target in re.findall(r"\]\((README[^)#]*\.md)\)", text):
            assert (ROOT / target).is_file(), (readme.name, target)
