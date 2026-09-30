"""bdon.moe 谱面站点客户端（带本地缓存）。

- 索引：``charts.json``（format 2），每项含 musicId / difficulty / title / manifest / fullComboCount；
- 清单：``charts/{musicId}_{difficulty}.json``，``files["score/NNNN_0X.notes.json"]`` 指向谱面文件：
  小文件为 ``{"asset": "assets/<sha256>.json"}``，大文件按顶层键拆分为
  ``{"parts": [[key, asset, size], ...], "size": 总大小}``；
- 资源文件名即内容的 sha256，下载后校验。

国际服与日服谱面一致，国际服曲名（含简体中文）与封面缩略图从 haneoka.org 获取，仅用于识别曲目。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

from ..config import ChartsConfig
from .model import Chart
from .parser import parse_live_score

logger = logging.getLogger(__name__)

USER_AGENT = "ournotes-auto/0.1 (+personal use; cached, serial requests)"
DIFFICULTIES = ("easy", "normal", "hard", "expert")


class ChartNotFound(LookupError):
    pass


class BdonClient:
    def __init__(self, cfg: ChartsConfig, session: requests.Session | None = None):
        self.cfg = cfg
        self.base = cfg.base_url.rstrip("/") + "/"
        self.cache = Path(cfg.cache_dir)
        self.http = session or requests.Session()
        self.http.headers.setdefault("User-Agent", USER_AGENT)

    # —— 底层 ——

    def _get(self, url: str, retries: int = 3) -> bytes:
        last: Exception | None = None
        for i in range(retries):
            try:
                r = self.http.get(url, timeout=30)
                if r.status_code == 404:
                    raise ChartNotFound(url)
                r.raise_for_status()
                return r.content
            except ChartNotFound:
                raise
            except requests.RequestException as e:
                last = e
                logger.warning("下载失败（%d/%d）%s：%s", i + 1, retries, url, e)
                time.sleep(1.5 * (i + 1))
        raise ConnectionError(f"无法下载 {url}：{last}")

    @staticmethod
    def _verify(raw: bytes, asset: str) -> None:
        digest = asset.rsplit("/", 1)[-1].split(".")[0]
        if len(digest) == 64 and hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError(f"校验失败：{asset}")

    def _cached(self, rel: str, url: str, max_age_s: float | None = None, asset: str | None = None) -> bytes:
        path = self.cache / rel
        if path.exists() and (max_age_s is None or time.time() - path.stat().st_mtime < max_age_s):
            return path.read_bytes()
        try:
            raw = self._get(url)
        except (ConnectionError, ChartNotFound):
            if path.exists():
                logger.warning("使用过期缓存 %s", path)
                return path.read_bytes()
            raise
        if asset:
            self._verify(raw, asset)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(raw)
        tmp.replace(path)
        return raw

    # —— 索引 / 清单 / 谱面 ——

    def index(self, refresh: bool = False) -> list[dict[str, Any]]:
        ttl = 0.0 if refresh else self.cfg.index_ttl_hours * 3600
        data = json.loads(self._cached("charts.json", self.base + "charts.json", max_age_s=ttl))
        if data.get("format") != 2:
            logger.warning("未知的索引格式 %r，尝试继续解析", data.get("format"))
        return data.get("charts", [])

    def manifest(self, music_id: int, difficulty: str) -> dict[str, Any]:
        key = f"{music_id}_{difficulty}"
        rel = f"manifests/{key}.json"
        return json.loads(self._cached(rel, self.base + f"charts/{key}.json"))

    def _asset(self, asset: str) -> bytes:
        return self._cached(asset, self.base + asset, asset=asset)

    def notes_raw(self, music_id: int, difficulty: str) -> dict[str, Any]:
        man = self.manifest(music_id, difficulty)
        files = man.get("files", {})
        keys = sorted(k for k in files if k.startswith("score/") and k.endswith(".notes.json"))
        if not keys:
            raise ChartNotFound(f"{music_id}_{difficulty} 的清单中没有谱面文件")
        entry = files[keys[0]]
        if "asset" in entry:
            return json.loads(self._asset(entry["asset"]))
        if "parts" in entry:
            chunks = []
            for part_key, asset, size in entry["parts"]:
                raw = self._asset(asset)
                if len(raw) != size:
                    raise ValueError(f"{asset} 大小 {len(raw)} 与清单 {size} 不符")
                chunks.append(json.dumps(part_key).encode() + b":" + raw)
            return json.loads(b"{" + b",".join(chunks) + b"}")
        raise ValueError(f"无法识别的清单条目：{entry}")

    def chart(self, music_id: int, difficulty: str, title: str = "") -> Chart:
        difficulty = difficulty.lower()
        if difficulty not in DIFFICULTIES:
            raise ValueError(f"未知难度 {difficulty}")
        return parse_live_score(self.notes_raw(music_id, difficulty), music_id, difficulty, title)

    # —— 国际服曲名 / 封面 ——

    def intl_songs(self, refresh: bool = False) -> dict[int, dict[str, Any]]:
        """haneoka 的国际服曲目表（musicId → 条目，含 musicTitle[日/英/繁/简/韩] 与 jacketThumbUrl）；
        获取失败时返回空表。"""
        url = self.cfg.titles_url
        if not url:
            return {}
        ttl = 0.0 if refresh else self.cfg.index_ttl_hours * 3600
        try:
            data = json.loads(self._cached("intl_songs.json", url, max_age_s=ttl))
        except (ConnectionError, ChartNotFound, ValueError) as e:
            logger.warning("获取国际服曲目表失败：%s", e)
            return {}
        return {int(mid): song for mid, song in data.items()}

    def jacket(self, music_id: int, url: str) -> bytes:
        """封面缩略图（PNG）；``url`` 为曲目表里的 jacketThumbUrl（相对 titles_url 所在站点）。"""
        return self._cached(f"jackets/{music_id}.png", urljoin(self.cfg.titles_url, url))

    def jacket_cached(self, music_id: int) -> bool:
        return (self.cache / f"jackets/{music_id}.png").exists()
