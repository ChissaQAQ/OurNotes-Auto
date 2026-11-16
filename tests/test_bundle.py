"""随包附带的数据：发版前更新它的工具（tools/fetch_bundle.py），和包里现在那份是不是齐全、读得出。"""

import gzip
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
import requests

from ournotes_auto import release
from ournotes_auto.charts import bdon
from ournotes_auto.charts.bdon import BdonClient, ChartNotFound
from ournotes_auto.config import ChartsConfig
from ournotes_auto.nav.jacket import THUMB, decode

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("fetch_bundle", ROOT / "tools" / "fetch_bundle.py")
tool = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = tool  # 工具里有 dataclass：它要在 sys.modules 里找自己所在的模块
_spec.loader.exec_module(tool)
BASE = "https://assets.bdon.moe/chart-site/"
SCORE_FILE = "score/0001_03.notes.json"


def score(taps: int) -> bytes:
    """有 ``taps`` 个点按音符的谱面文件。"""
    notes = [
        {"id": i + 1, "opName": "Normal", "tick": 480 * (i + 1), "timeMs": None, "laneStartFloat": 6, "laneEndFloat": 11, "direction": "Normal",
         "lineIds": [], "lineEase": "Linear", "lineEaseR": "Linear", "judgement": True, "judgementType": "Normal", "flick": False}
        for i in range(taps)
    ]
    bpm = {"bpm": [{"startTick": 0, "startTimeMs": 0, "bpm": 120}]}
    return json.dumps({"format": "nnnotes.live-score/1", "judgementNoteCount": taps, "notes": notes, "lines": [], "tickSegments": bpm}).encode()


def png(seed: int, side: int = 256) -> bytes:
    """一张假封面；``side`` 不一样，文件大小就不一样。"""
    return cv2.imencode(".png", np.random.default_rng(seed).integers(0, 256, (side, side, 3), dtype=np.uint8))[1].tobytes()


class Site:
    """假的谱面站：相对地址 → 内容，没有的回 404；记下请求过的地址。"""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.charts: dict[str, dict] = {}
        self.urls: list[str] = []
        self.headers: dict = {}

    def get(self, url, timeout=None):
        rel = url.removeprefix(BASE)
        self.urls.append(rel)
        r = requests.Response()
        r.status_code = 200 if rel in self.files else 404
        r._content = self.files.get(rel, b"")
        return r

    def put(self, raw: bytes, ext: str) -> str:
        name = f"assets/{hashlib.sha256(raw).hexdigest()}.{ext}"
        self.files[name] = raw
        return name

    def list(self) -> None:
        self.files["charts.json"] = json.dumps({"format": 2, "charts": list(self.charts.values())}).encode()

    def chart(self, mid: int, diff: str, taps: int, jacket: bytes, split: bool = False) -> None:
        """放上（或换掉）一张谱面：谱面文件（``split``：按顶层键拆开）、封面、清单，曲目列表里的条目。"""
        raw = score(taps)
        files = {"livescene/textures/jkt_001-0a.png": {"asset": self.put(jacket, "png"), "size": len(jacket)}}
        if split:
            parts = [(k, json.dumps(v).encode()) for k, v in json.loads(raw).items()]
            files[SCORE_FILE] = {"parts": [[k, self.put(part, "json"), len(part)] for k, part in parts], "size": sum(len(part) for _, part in parts)}
        else:
            files[SCORE_FILE] = {"asset": self.put(raw, "json"), "size": len(raw)}
        key = f"{mid}_{diff}"
        self.files[f"charts/{key}.json"] = json.dumps({"files": files}).encode()
        total = sum(f["size"] for f in files.values())
        self.charts[key] = {"musicId": mid, "difficulty": diff, "title": f"曲 {mid}", "bytes": total, "notes": taps, "fullComboCount": taps, "durationMs": 1000}
        self.list()


class Offline:
    """不许联网的连接。"""

    headers: dict = {}

    def get(self, url, timeout=None):
        raise AssertionError(f"不该联网：{url}")


@pytest.fixture
def work(tmp_path, monkeypatch):
    """假的谱面站、空的包（封面和谱面目录）、空的本机缓存；返回 (谱面站, 运行一次工具)。"""
    monkeypatch.setattr(bdon, "JACKETS", tmp_path / "jackets")
    monkeypatch.setattr(bdon, "BUNDLE", tmp_path / "bundle")
    site = Site()

    def run(check_all: bool = False):
        site.urls.clear()
        return tool.update(tool.Fresh(ChartsConfig(cache_dir=str(tmp_path / "cache")), session=site), check_all)

    return site, run


def packed(tmp_path) -> dict[str, bytes]:
    return {f"{d}/{p.name}": p.read_bytes() for d in ("bundle", "jackets") for p in sorted((tmp_path / d).iterdir())}


def test_fetch_bundle(work, tmp_path):
    """第一次：曲目列表里的谱面、每首歌的封面都放进包里（谱面是谱面文件的原样内容，拆开的拼好；封面缩小）。"""
    site, run = work
    cover = png(1)
    site.chart(100001, "expert", 3, cover)
    site.chart(100001, "easy", 2, cover, split=True)
    site.chart(999999, "hard", 4, png(2))
    report = run()
    assert (report.total, report.charts, report.jackets) == (3, ["100001_easy", "100001_expert", "999999_hard"], [100001, 999999])
    assert (report.failed, report.removed) == ([], [])
    assert report.untitled == [(999999, "曲 999999")]  # 100001 在曲名表里
    bundle, jackets = tmp_path / "bundle", tmp_path / "jackets"
    assert sorted(p.name for p in bundle.iterdir()) == ["100001_easy.json.gz", "100001_expert.json.gz", "999999_hard.json.gz", "charts.json"]
    assert gzip.decompress((bundle / "100001_expert.json.gz").read_bytes()) == score(3)
    assert json.loads(gzip.decompress((bundle / "100001_easy.json.gz").read_bytes())) == json.loads(score(2))
    assert bdon.chart_entries(bdon.bundled_index()) == site.charts
    assert (bundle / "charts.json").read_text(encoding="utf-8").count("\n") == 3 + 5  # 一张谱面一行
    assert sorted(p.name for p in jackets.iterdir()) == ["100001.png", "999999.png"]
    assert decode((jackets / "100001.png").read_bytes()).shape[:2] == (THUMB, THUMB)
    assert site.urls.count(f"assets/{hashlib.sha256(cover).hexdigest()}.png") == 1  # 同一首歌各难度是同一张，只下载一次

    # 程序拿它就能用：没有缓存、连不上谱面站，曲目列表和谱面都是包里的
    offline = BdonClient(ChartsConfig(cache_dir=str(tmp_path / "empty")), session=Site())
    assert sorted(f"{e['musicId']}_{e['difficulty']}" for e in offline.index()) == sorted(site.charts)
    assert offline.chart(100001, "easy").parsed_judged == 2 and offline.chart(999999, "hard").parsed_judged == 4
    assert offline.http.urls == ["charts.json"]

    # 再跑一遍：什么都没变，只下载曲目列表，文件一个字节都不动
    before = packed(tmp_path)
    report = run()
    assert (report.charts, report.jackets, report.failed, report.removed) == ([], [], [], []) and site.urls == ["charts.json"]
    assert packed(tmp_path) == before


def test_fetch_bundle_follows_site(work, tmp_path):
    """谱面站之后改了谱面、换了封面、出了新歌、撤了一张：只取变了的，包里跟着变。"""
    site, run = work
    cover, other = png(1), png(2)
    site.chart(100001, "expert", 3, cover)
    site.chart(100001, "easy", 2, cover)
    site.chart(999999, "hard", 4, other)
    run()
    before = packed(tmp_path)
    site.chart(100001, "expert", 5, cover)  # 改了谱面
    site.chart(999999, "hard", 4, png(3, 300))  # 换了封面
    site.chart(100002, "normal", 1, png(4))  # 新歌
    del site.charts["100001_easy"]  # 撤了
    site.list()
    report = run()
    assert (report.charts, report.jackets, report.removed, report.failed) == (["100001_expert", "100002_normal"], [100002, 999999], ["谱面 100001_easy"], [])
    after = packed(tmp_path)
    assert sorted(after) == ["bundle/100001_expert.json.gz", "bundle/100002_normal.json.gz", "bundle/999999_hard.json.gz", "bundle/charts.json",
                             "jackets/100001.png", "jackets/100002.png", "jackets/999999.png"]
    assert gzip.decompress(after["bundle/100001_expert.json.gz"]) == score(5)
    assert after["bundle/999999_hard.json.gz"] == before["bundle/999999_hard.json.gz"]  # 谱面没变：文件不动
    assert after["jackets/100001.png"] == before["jackets/100001.png"] and after["jackets/999999.png"] != before["jackets/999999.png"]
    assert bdon.chart_entries(bdon.bundled_index()) == site.charts
    # 没变的资源文件在本机缓存里，不重复下载；清单是重新下载的
    unchanged = {f"assets/{hashlib.sha256(raw).hexdigest()}.{ext}" for raw, ext in ((score(4), "json"), (cover, "png"))}
    assert not unchanged & set(site.urls) and {"charts/100001_expert.json", "charts/999999_hard.json", "charts/100002_normal.json"} <= set(site.urls)

    # --all：每张谱面的清单都重新下载核对，没变的文件照样不动
    report = run(check_all=True)
    assert (report.charts, report.jackets, report.failed) == ([], [], []) and packed(tmp_path) == after
    assert [u for u in site.urls if u.startswith("charts/")] == ["charts/100001_expert.json", "charts/100002_normal.json", "charts/999999_hard.json"]
    assert not [u for u in site.urls if u.startswith("assets/")]


def test_fetch_bundle_failures(work, tmp_path, caplog):
    """没取到、核对不过的谱面不留旧的（程序靠包里的曲目列表判断那张是不是现在的那一版）；旧封面留着。
    曲目列表都取不到：报错，包里的数据不动。"""
    site, run = work
    site.chart(100001, "expert", 3, png(1))
    site.chart(100001, "easy", 2, png(1))
    site.chart(100002, "hard", 4, png(2))
    site.chart(100003, "hard", 4, png(3))
    run()
    before = packed(tmp_path)
    # 谱面站重新生成了三张：一张的清单下载不了，一张清单和曲目列表对不上（正在更新），一张音符数和列表里写的不一样
    site.chart(100001, "expert", 5, png(1))
    del site.files["charts/100001_expert.json"]
    site.chart(100002, "hard", 6, png(2))
    site.charts["100002_hard"]["bytes"] += 1
    site.chart(100003, "hard", 7, png(3))
    site.charts["100003_hard"]["fullComboCount"] = 8
    site.list()
    report = run()
    assert report.failed == ["谱面 100001_expert", "谱面 100002_hard", "谱面 100003_hard"] and report.charts == []
    assert "正在更新" in caplog.text and "音符数对不上" in caplog.text
    after = packed(tmp_path)
    assert sorted(set(before) - set(after)) == ["bundle/100001_expert.json.gz", "bundle/100002_hard.json.gz", "bundle/100003_hard.json.gz"]
    assert after["bundle/100001_easy.json.gz"] == before["bundle/100001_easy.json.gz"] and after["jackets/100001.png"] == before["jackets/100001.png"]
    assert bdon.chart_entries(bdon.bundled_index()) == site.charts
    # 程序那边：包里没有的照常从谱面站取
    client = BdonClient(ChartsConfig(cache_dir=str(tmp_path / "user")), session=Offline())
    assert client.chart(100001, "easy").parsed_judged == 2
    with pytest.raises(AssertionError, match="charts/100003_hard.json"):
        client.chart(100003, "hard")

    del site.files["charts.json"]
    with pytest.raises(ChartNotFound):
        run()
    assert packed(tmp_path) == after


def test_same_image_and_dump_index():
    a = png(1)
    again = cv2.imencode(".bmp", cv2.imdecode(np.frombuffer(a, np.uint8), cv2.IMREAD_UNCHANGED))[1].tobytes()
    assert again != a and tool.same_image(a, again)  # 编码不一样、像素一样：不算变了
    assert not tool.same_image(a, png(2)) and not tool.same_image(a, png(1, 64))
    assert not tool.same_image(a, b"not a png") and not tool.same_image(a, b"")
    index = {"format": 2, "charts": [{"musicId": 1, "title": "迷星叫"}, {"musicId": 2}]}
    text = tool.dump_index(index)
    assert json.loads(text) == index and text.splitlines()[3] == '{"musicId": 1, "title": "迷星叫"},'


# ---------------------------------------------------------------- 包里现在的那份


def test_bundle_layers(real_bundle, real_jackets):
    """谱面只在整包里：不算 app 层（自动更新不带、不动它），程序读的就是那个目录；封面、曲名表在 app 层。"""
    assert real_bundle == ROOT / release.BUNDLE_DIR
    for name in (bdon.BUNDLE_INDEX, "100001_expert.json.gz"):
        assert not release.is_app_file(f"{release.BUNDLE_DIR}/{name}")
    assert release.is_app_file(f"{real_jackets.relative_to(ROOT).as_posix()}/100001.png") and release.is_app_file("ournotes_auto/charts/titles.json")


def test_bundled_charts(real_bundle, tmp_path, monkeypatch):
    """包里现在的谱面（发版前用 tools/fetch_bundle.py 更新）：曲目列表里的每张都有、没有多余的；不联网每张都读得出、
    解析得了，音符数和曲目列表里写的一样。"""
    entries = bdon.chart_entries(json.loads((real_bundle / bdon.BUNDLE_INDEX).read_text(encoding="utf-8")))
    assert len(entries) >= 384
    assert sorted(p.name for p in real_bundle.iterdir()) == sorted([bdon.BUNDLE_INDEX, *(f"{key}.json.gz" for key in entries)])
    monkeypatch.setattr(bdon, "BUNDLE", real_bundle)
    client = BdonClient(ChartsConfig(cache_dir=str(tmp_path / "cache")), session=Offline())
    for key, entry in entries.items():
        chart = client.chart(entry["musicId"], entry["difficulty"])
        assert chart.parsed_judged == chart.judged_count == entry["fullComboCount"], key
