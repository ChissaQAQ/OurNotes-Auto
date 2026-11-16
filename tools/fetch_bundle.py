"""发版前更新随包附带的数据::

    python tools/fetch_bundle.py          # 只取新出的歌、谱面站重新生成过的谱面
    python tools/fetch_bundle.py --all    # 每张谱面都重新下载清单核对一遍（约 9 MB）

- 封面 ``ournotes_auto/charts/jackets/{musicId}.png``：缩小过，在 app 层，每次更新都带；
- 谱面 ``ournotes_auto/charts/bundle/{musicId}_{difficulty}.json.gz`` 和这时的曲目列表 ``charts.json``：只在整包里
  （release.BUNDLE_DIR）。新安装不联网就能打包里的歌；之后谱面站重新生成过的谱面，程序照常从谱面站取。

清单每次都重新下载（本机缓存里的可能是旧的）；谱面文件、封面原图按 sha256 放在本机缓存（cache/charts）里，没变的不重复
下载。每张谱面放进包里之前先解析一遍、核对音符数；没取到的不留旧的。最后列出曲名表（ournotes_auto/charts/titles.json）
里还没有的曲目：各语言客户端显示的曲名要手动补，没补的只按谱面站的曲名（繁体中文区的写法）和封面认。
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ournotes_auto.charts import bdon  # noqa: E402
from ournotes_auto.charts.bdon import DIFFICULTIES, BdonClient, ChartNotFound  # noqa: E402
from ournotes_auto.charts.catalog import load_titles  # noqa: E402
from ournotes_auto.charts.parser import parse_live_score  # noqa: E402
from ournotes_auto.config import ChartsConfig  # noqa: E402
from ournotes_auto.nav.jacket import thumbnail  # noqa: E402

logger = logging.getLogger("fetch_bundle")
FAILED = (ConnectionError, LookupError, ValueError)  # 下载不了、谱面站上没有、内容不对


class Fresh(BdonClient):
    """曲目列表和清单都重新下载（下载不了就报错，不拿旧的顶），这次运行里取过的清单留着；资源文件照常按 sha256 用缓存。"""

    def __init__(self, cfg: ChartsConfig, session=None):
        super().__init__(cfg, session)
        self.manifests: dict[str, dict[str, Any]] = {}

    def live_index(self) -> dict[str, Any]:
        return json.loads(self._get(self.base + "charts.json"))

    def manifest(self, music_id: int, difficulty: str, optional: bool = False) -> dict[str, Any]:
        key = f"{music_id}_{difficulty}"
        if key not in self.manifests:
            self.manifests[key] = json.loads(self._get(self.base + f"charts/{key}.json"))
        return self.manifests[key]


@dataclass
class Report:
    total: int = 0  # 曲目列表里的谱面数
    charts: list[str] = field(default_factory=list)  # 新放进包里、换了内容的谱面
    jackets: list[int] = field(default_factory=list)  # 新放进包里、换了图的封面
    removed: list[str] = field(default_factory=list)  # 谱面站上没有了，从包里拿掉的
    failed: list[str] = field(default_factory=list)  # 没取到的
    untitled: list[tuple[int, str]] = field(default_factory=list)  # 曲名表里没有的（曲目，谱面站的曲名）


def chart_bytes(client: Fresh, entry: dict[str, Any]) -> bytes:
    """谱面站现在的这张谱面（谱面文件的原样内容）。核对过：清单和曲目列表是同一版，解析得了，音符数对得上。"""
    mid, diff = int(entry["musicId"]), str(entry["difficulty"])
    files = client.manifest(mid, diff).get("files", {})
    total = sum(f.get("size", 0) for f in files.values())
    if total != entry.get("bytes"):
        raise ValueError(f"清单里的文件共 {total} 字节，曲目列表里写的是 {entry.get('bytes')}（谱面站正在更新？过一会儿再试）")
    raw = client.notes_bytes(mid, diff)
    chart = parse_live_score(json.loads(raw), mid, diff)
    want = entry.get("fullComboCount", chart.parsed_judged)
    if not chart.parsed_judged == chart.judged_count == want:
        raise ValueError(f"音符数对不上：解析出 {chart.parsed_judged} 个，谱面里写的 {chart.judged_count}，曲目列表里 {want}")
    return raw


def gunzip(path: Path) -> bytes | None:
    try:
        return gzip.decompress(path.read_bytes())
    except (OSError, EOFError, zlib.error):
        return None


def same_image(a: bytes, b: bytes) -> bool:
    """两张图的像素一样（编码出来的字节不一样不算变了）。"""
    if not a or not b:
        return False
    x, y = (cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED) for raw in (a, b))
    return x is not None and y is not None and x.shape == y.shape and bool(np.array_equal(x, y))


def dump_index(index: dict[str, Any]) -> str:
    """曲目列表写成一张谱面一行（版本库里看得清改了哪几张）。"""
    head = [f"{json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in sorted(index.items()) if k != "charts"]
    charts = ",\n".join(json.dumps(e, ensure_ascii=False, sort_keys=True) for e in index.get("charts", []))
    return "{\n" + ",\n".join([*head, '"charts": [\n' + charts + "\n]"]) + "\n}\n"


def update(client: Fresh, check_all: bool = False) -> Report:
    """把包里的谱面、封面更新到谱面站现在的样子。取不到曲目列表时抛出异常，包里的数据不动。"""
    live = client.live_index()
    entries = bdon.chart_entries(live)
    before = bdon.chart_entries(bdon.bundled_index())
    report = Report(total=len(entries))
    bdon.BUNDLE.mkdir(parents=True, exist_ok=True)
    bdon.JACKETS.mkdir(parents=True, exist_ok=True)

    # 谱面：包里没有的、曲目列表里的条目变了的（谱面站重新生成过）
    for key, entry in sorted(entries.items()):
        path = bdon.BUNDLE / f"{key}.json.gz"
        if path.is_file() and before.get(key) == entry and not check_all:
            continue
        try:
            raw = chart_bytes(client, entry)
        except FAILED as e:
            logger.error("谱面 %s 没取到：%s", key, e)
            path.unlink(missing_ok=True)  # 不留旧的：程序靠 charts.json 里的条目判断包里的是不是现在的那一版
            report.failed.append(f"谱面 {key}")
            continue
        if gunzip(path) != raw:
            path.write_bytes(gzip.compress(raw, 9, mtime=0))  # mtime=0：内容一样，文件就一样
            report.charts.append(key)
            logger.info("谱面 %s", key)
    for path in sorted(bdon.BUNDLE.glob("*.json.gz")):
        key = path.name.removesuffix(".json.gz")
        if key not in entries:
            path.unlink()
            report.removed.append(f"谱面 {key}")

    # 封面：包里没有的，和这次重新取过清单的歌（谱面站重新生成过，封面可能换了图）
    songs: dict[int, list[str]] = {}
    for entry in entries.values():
        songs.setdefault(int(entry["musicId"]), []).append(str(entry["difficulty"]))
    for mid, diffs in sorted(songs.items()):
        path = bdon.JACKETS / f"{mid}.png"
        fetched = [d for d in reversed(DIFFICULTIES) if f"{mid}_{d}" in client.manifests]
        if path.is_file() and not fetched:
            continue
        try:
            diff = (fetched or [d for d in reversed(DIFFICULTIES) if d in diffs] or diffs)[0]
            asset = bdon.jacket_asset(client.manifest(mid, diff))
            if not asset:
                raise ChartNotFound(f"{mid}_{diff} 的清单里没有封面")
            small = thumbnail(client.asset(asset))
        except FAILED as e:  # 包里原有的那张留着：多半没变，变了也只是这首歌退回按曲名认
            logger.error("封面 %d 没取到：%s", mid, e)
            report.failed.append(f"封面 {mid}")
            continue
        if not path.is_file() or not same_image(path.read_bytes(), small):
            path.write_bytes(small)
            report.jackets.append(mid)
            logger.info("封面 %d", mid)
    for path in sorted(bdon.JACKETS.glob("*.png")):
        if path.stem.isdigit() and int(path.stem) not in songs:
            path.unlink()
            report.removed.append(f"封面 {path.stem}")

    (bdon.BUNDLE / bdon.BUNDLE_INDEX).write_text(dump_index(live), encoding="utf-8", newline="\n")
    known = load_titles()
    titles = {int(e["musicId"]): str(e.get("title", "")) for e in entries.values()}
    report.untitled = sorted((mid, title) for mid, title in titles.items() if mid not in known)
    return report


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--all", action="store_true", help="每张谱面都重新下载清单核对一遍，不只看曲目列表里变了的")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        report = update(Fresh(ChartsConfig(cache_dir=str(ROOT / "cache" / "charts"))), args.all)
    except FAILED as e:
        print(f"没取到曲目列表，包里的数据没动：{e}")
        return 1
    bundled = len(list(bdon.BUNDLE.glob("*.json.gz")))
    size = sum(p.stat().st_size for p in bdon.BUNDLE.iterdir()) / 2**20
    print(f"谱面：曲目列表里 {report.total} 张，包里 {bundled} 张（{size:.1f} MB），这次新放进去、换了内容的 {len(report.charts)} 张")
    print(f"封面：包里 {len(list(bdon.JACKETS.glob('*.png')))} 张，这次新放进去、换了图的 {len(report.jackets)} 张" + (f"（{', '.join(map(str, report.jackets[:20]))}）" if report.jackets else ""))
    if report.removed:
        print(f"谱面站上没有了、从包里拿掉的：{', '.join(report.removed)}")
    if report.failed:
        print(f"没取到的 {len(report.failed)} 项（原因见上面）：{', '.join(report.failed)}")
    if report.untitled:
        print(f"曲名表里还没有的 {len(report.untitled)} 首，各语言的曲名（日、英、繁中、简中、韩）要手动补进 ournotes_auto/charts/titles.json：")
        for mid, title in report.untitled:
            print(f"  {mid} {title}")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
