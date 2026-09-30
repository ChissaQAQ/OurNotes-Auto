"""封面匹配与曲目识别（封面优先、曲名兜底）。"""

import cv2
import numpy as np
import pytest

from ournotes_auto.charts.catalog import Catalog, Song
from ournotes_auto.nav.jacket import JACKET_ROI, JacketMatcher, crop_jacket, decode
from ournotes_auto.runner import NavigationError, SongLabel, identify_song


def jacket(seed: int, size: int = 128) -> np.ndarray:
    """色块拼成的假封面：不同 seed 互不相像。"""
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 256, (6, 6, 3), dtype=np.uint8)
    return cv2.resize(small, (size, size), interpolation=cv2.INTER_CUBIC)


def screen_with(img: np.ndarray, w=1280, h=720, seed=0) -> np.ndarray:
    """把封面按 JACKET_ROI 贴到截图上（截图分辨率可以不是 1280x720），加一点噪声。"""
    rng = np.random.default_rng(seed)
    frame = rng.integers(0, 60, (h, w, 3), dtype=np.uint8)
    x, y, rw, rh = JACKET_ROI
    sx, sy = w / 1280, h / 720
    x0, y0, x1, y1 = round(x * sx), round(y * sy), round((x + rw) * sx), round((y + rh) * sy)
    frame[y0:y1, x0:x1] = cv2.resize(img, (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA)
    noisy = frame.astype(np.int16) + rng.integers(-6, 7, frame.shape)
    return np.clip(noisy, 0, 255).astype(np.uint8)


@pytest.mark.parametrize("w,h", [(1280, 720), (1920, 1080)])
def test_identify_jacket(w, h):
    matcher = JacketMatcher({100000 + i: jacket(i) for i in range(30)})
    for i in (0, 7, 29):
        found = matcher.identify(crop_jacket(screen_with(jacket(i), w, h, seed=i), JACKET_ROI))
        assert found is not None and found[0] == 100000 + i and found[1] > 0.95


def test_unknown_or_ambiguous_jacket():
    base = jacket(1)
    twin = np.clip(base.astype(np.int16) + 8, 0, 255).astype(np.uint8)  # 几乎一样的两张封面
    matcher = JacketMatcher({1: base, 2: twin, 3: jacket(3)})
    assert matcher.identify(crop_jacket(screen_with(base))) is None  # 分不清就不认
    assert matcher.identify(crop_jacket(screen_with(jacket(99)))) is None  # 不在曲库里


def test_identify_threshold_override():
    matcher = JacketMatcher({1: jacket(1), 2: jacket(2)})
    crop = crop_jacket(screen_with(jacket(1)))
    score = matcher.rank(crop)[0][1]
    assert matcher.identify(crop, min_score=score + 0.01) is None
    assert matcher.identify(crop, min_score=score - 0.01)[0] == 1
    assert matcher.identify(crop, min_margin=2.0) is None


def test_decode_blends_alpha():
    rgba = np.zeros((8, 8, 4), np.uint8)
    rgba[..., :3] = 200
    rgba[..., 3] = 255
    rgba[0, 0, 3] = 0  # 透明圆角
    ok, buf = cv2.imencode(".png", rgba)
    img = decode(buf.tobytes())
    assert img.shape == (8, 8, 3) and img[0, 0].tolist() == [0, 0, 0] and img[4, 4].tolist() == [200, 200, 200]


def song(mid, title, expert):
    return Song(mid, [title], difficulties={"expert": {"level": expert}})


CATALOG = Catalog(
    {
        s.music_id: s
        for s in (
            song(100033, "Symbol I : △", 26),
            song(100034, "Symbol II : 🜁", 25),
            song(100050, "碧い瞳の中に", 21),
        )
    }
)


def ident(title, level, jacket_id=None):
    label = SongLabel(title, "expert", level, None if jacket_id is None else (jacket_id, 0.99))
    return identify_song(CATALOG, label, "expert")[0].music_id


def test_jacket_beats_unreadable_title():
    assert ident("", 21, 100050) == 100050  # 假名曲名读不出
    assert ident("碧瞳中E", None, 100050) == 100050


def test_title_fallback_without_jacket():
    assert ident("Symbol I : △", 26) == 100033
    with pytest.raises(NavigationError):
        ident("", 21)


def test_jacket_level_conflict():
    # 等级读错但曲名指向同一首：采用
    assert ident("Symbol II : 🜁", 26, 100034) == 100034
    # 等级与曲名都对不上封面：不打
    with pytest.raises(NavigationError):
        ident("", 26, 100034)


class JacketClient:
    def __init__(self, cached=()):
        self.cached = set(cached)
        self.fetched = []

    def jacket_cached(self, music_id):
        return music_id in self.cached

    def jacket(self, music_id, url):
        self.fetched.append(music_id)
        if music_id == 3:
            raise ConnectionError("timeout")
        ok, buf = cv2.imencode(".png", jacket(music_id))
        return buf.tobytes()


def catalog_with_jackets():
    songs = {mid: Song(mid, [str(mid)]) for mid in (1, 2, 3, 4)}
    for mid in (1, 2, 3):
        songs[mid].jacket_url = f"/j/{mid}.png"
    return Catalog(songs)


def test_load_skips_failed_jackets(caplog):
    client = JacketClient(cached=(1,))
    with caplog.at_level("INFO"):
        m = JacketMatcher.load(client, catalog_with_jackets())
    assert m.ids == [1, 2] and client.fetched == [1, 2, 3]
    assert "下载 2 张曲目封面" in caplog.text


def test_load_jackets_async(monkeypatch):
    from ournotes_auto import context
    from ournotes_auto.config import Config

    monkeypatch.setattr(JacketMatcher, "load", classmethod(lambda cls, client, catalog: ("matcher", catalog)))
    assert context.load_jackets_async(Config(), "catalog").result(timeout=5) == ("matcher", "catalog")

    def fail(cls, client, catalog):
        raise OSError("disk full")

    monkeypatch.setattr(JacketMatcher, "load", classmethod(fail))
    with pytest.raises(OSError):
        context.load_jackets_async(Config(), "catalog").result(timeout=5)
