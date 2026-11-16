"""曲名匹配：OCR 误差、同系列曲名的歧义与等级消歧；随包附带的曲名表。"""

import json
import logging

import pytest

from ournotes_auto.charts import catalog as catalog_mod
from ournotes_auto.charts.catalog import TITLES, Catalog, Song, load_titles, normalize


def song(mid, title, easy, expert):
    return Song(mid, [title], difficulties={"easy": {"level": easy}, "expert": {"level": expert}})


CATALOG = Catalog(
    {
        s.music_id: s
        for s in (
            song(100001, "迷星叫", 7, 23),
            song(100026, "Ave Mujica", 8, 27),
            song(100033, "Symbol I : △", 8, 26),
            song(100034, "Symbol II : 🜁", 8, 25),
            song(100035, "Symbol III : ▽", 5, 21),
            song(100036, "Symbol IV : 🜃", 8, 24),
        )
    }
)


def mid(text, **kw):
    found = CATALOG.match(text, **kw)
    return found and found[0].music_id


def test_normalize_ocr_confusions():
    assert normalize("Symbo| Ⅳ：寻") == normalize("symbol iv 寻")


@pytest.mark.parametrize(
    "text, kw, expected",
    [
        ("迷星叫", {}, 100001),
        ("AveMuica", {}, 100026),
        ("编", {}, None),  # 转场动画中只读到一个字
        # 只差编号与符号：不看等级就有歧义
        ("Symbo|：仑", {}, None),
        ("Symbo|：仑", {"difficulty": "expert", "level": 26}, 100033),
        ("Symbo|Iv：寻", {"difficulty": "expert", "level": 24}, 100036),
        # 等级读错：最像的 Symbol IV 等级不符，不能改选 Symbol I；只看曲名又有歧义
        ("Symbo|Iv：寻", {"difficulty": "expert", "level": 26}, None),
        # 等级读错（21 读成 2）时只看曲名：罗马数字对相似度影响太小，仍有歧义
        ("SymbolⅢ:v", {"difficulty": "expert", "level": 2}, None),
        ("SymbolⅢ:v", {"difficulty": "expert", "level": 21}, 100035),
        # 曲名本身足够明确时不受读错的等级影响
        ("迷星叫", {"difficulty": "expert", "level": 2}, 100001),
        # EASY 等级都是 8，消不了歧义
        ("Symbo|：仑", {"difficulty": "easy", "level": 8}, None),
        # 等级与完全匹配一致
        ("迷星叫", {"difficulty": "expert", "level": 23}, 100001),
    ],
)
def test_match(text, kw, expected):
    assert mid(text, **kw) == expected


def test_match_without_margin_for_human_input():
    assert mid("Symbol", min_margin=0) in (100033, 100034, 100035, 100036)


class IndexClient:
    """只有谱面站索引的假客户端。"""

    def __init__(self, *entries):
        self.entries = [{"musicId": mid, "difficulty": diff, "title": title, "bands": [band], "level": 20} for mid, diff, title, band in entries]

    def index(self, refresh=False):
        return self.entries


def test_bundled_titles():
    """随包附带的曲名表（#142：不再从别的网站取）：每首歌五种语言的曲名（日、英、繁中、简中、韩），都不空。"""
    raw = json.loads(TITLES.read_text(encoding="utf-8"))
    assert len(raw) >= 96 and all(k.isdigit() and len(v) == 5 and all(isinstance(t, str) and t for t in v) for k, v in raw.items())
    titles = load_titles()
    assert titles[100001] == ["迷星叫", "Mayoiuta", "迷星叫", "迷星叫", "헤매는 노래"]
    assert titles[100008][3] == "诗超绊" and titles[100008][2] == "詩超絆"


def test_load_uses_bundled_titles():
    """曲目目录：谱面站的索引 + 曲名表。各语言的曲名都认得出，显示用简中的；表里没有的新歌只有谱面站的曲名；
    表里有、谱面站没有的不进目录（打不了）。"""
    client = IndexClient(
        (100001, "expert", "迷星叫", "MyGO!!!!!"), (100001, "easy", "迷星叫", "MyGO!!!!!"),
        (100008, "expert", "詩超絆", "MyGO!!!!!"), (999999, "expert", "新しい歌", "MyGO!!!!!"),
    )
    cat = Catalog.load(client)
    assert sorted(cat.songs) == [100001, 100008, 999999]
    assert cat.get(100001).titles == ["迷星叫", "Mayoiuta", "迷星叫", "迷星叫", "헤매는 노래"]
    assert sorted(cat.get(100001).difficulties) == ["easy", "expert"] and cat.get(100001).band == "MyGO!!!!!"
    assert cat.get(100008).display_title() == "诗超绊" and cat.get(999999).display_title() == "新しい歌"
    for text, want in (("Mayoiuta", 100001), ("헤매는 노래", 100001), ("Utakotoba", 100008), ("诗超绊", 100008), ("新しい歌", 999999)):
        assert cat.match(text)[0].music_id == want, text


def test_titles_file_missing_or_broken(tmp_path, monkeypatch, caplog):
    """曲名表丢了、坏了：记一行警告，只用谱面站的曲名，照常能用（封面还认得出歌）。"""
    bad = tmp_path / "titles.json"
    with caplog.at_level(logging.WARNING, logger=catalog_mod.logger.name):
        assert load_titles(bad) == {}
        bad.write_text("{坏的", encoding="utf-8")
        assert load_titles(bad) == {}
        bad.write_text('{"1": "不是列表", "x": []}', encoding="utf-8")
        assert load_titles(bad) == {}
    assert len(caplog.records) == 3 and all("读不了曲名表" in r.getMessage() for r in caplog.records)
    bad.write_text('{"100001": ["迷星叫", "", null, "迷星叫"]}', encoding="utf-8")
    assert load_titles(bad) == {100001: ["迷星叫", "迷星叫"]}  # 空的不要
    monkeypatch.setattr(catalog_mod, "TITLES", tmp_path / "nope.json")
    monkeypatch.setattr(catalog_mod.load_titles, "__defaults__", (tmp_path / "nope.json",))
    cat = Catalog.load(IndexClient((100008, "expert", "詩超絆", "MyGO!!!!!")))
    assert cat.get(100008).titles == ["詩超絆"] and cat.match("诗超绊")[0].music_id == 100008  # 繁简换成一样的说法再比
