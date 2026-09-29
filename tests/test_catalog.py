"""曲名匹配：OCR 误差、同系列曲名的歧义与等级消歧。"""

import pytest

from ournotes_auto.charts.catalog import Catalog, Song, normalize


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
