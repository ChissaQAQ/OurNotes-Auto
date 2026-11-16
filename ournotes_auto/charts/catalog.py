"""曲目目录：把选曲界面 OCR 到的曲名匹配到 musicId。

谱面站的索引里每首歌只有一个曲名（繁体中文区的写法）。各语言客户端显示的曲名在随包附带的 ``titles.json`` 里
（musicId → [日, 英, 繁中, 简中, 韩]，2026-11-10 的曲目）；表里还没有的新歌只有谱面站的曲名，靠封面认。
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz, process

from ..nav.lang import localize
from .bdon import BdonClient

logger = logging.getLogger(__name__)

TITLES = Path(__file__).with_name("titles.json")


def load_titles(path: Path = TITLES) -> dict[int, list[str]]:
    """随包附带的曲名表（musicId → 各语言的曲名，空的不要）。读不了（文件丢了、坏了）时是空表：只用谱面站的曲名。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {int(mid): [t for t in titles if isinstance(t, str) and t] for mid, titles in data.items()}
    except (OSError, ValueError, TypeError, AttributeError) as e:
        logger.warning("读不了曲名表 %s（%s），只按谱面站的曲名认歌（译名不一样的歌靠封面）", path, e)
        return {}


def normalize(text: str) -> str:
    """全半角统一、和 OCR 结果一样换成简中说法（繁体转简体等）、去空白与常见标点、转小写，减少 OCR 误差的影响。"""
    text = localize(unicodedata.normalize("NFKC", text)).lower().replace("|", "l")  # OCR 常把 l/I 读成 |
    return re.sub(r"[\s　·・,，.。!！?？'\"“”‘’()（）\[\]【】「」『』~〜\-－_:：/]", "", text)


@dataclass
class Song:
    music_id: int
    titles: list[str]
    band: str = ""
    difficulties: dict[str, dict] = field(default_factory=dict)  # difficulty → 索引条目

    @property
    def title(self) -> str:
        return self.titles[0] if self.titles else str(self.music_id)

    def display_title(self, lang_index: int = 3) -> str:
        """lang_index：0 日文、1 英文、2 繁中、3 简中、4 韩文（titles.json 里的顺序）。"""
        return self.titles[lang_index] if len(self.titles) > lang_index else self.title

    def level(self, difficulty: str) -> int | None:
        entry = self.difficulties.get(difficulty)
        return entry.get("level") if entry else None


class Catalog:
    def __init__(self, songs: dict[int, Song]):
        self.songs = songs
        self._choices: dict[str, int] = {}
        for song in songs.values():
            for t in song.titles:
                key = normalize(t)
                if key:
                    self._choices.setdefault(key, song.music_id)

    @classmethod
    def load(cls, client: BdonClient, refresh: bool = False) -> "Catalog":
        songs: dict[int, Song] = {}
        for entry in client.index(refresh=refresh):
            mid = int(entry["musicId"])
            song = songs.get(mid)
            if song is None:
                bands = entry.get("bands") or []
                song = songs[mid] = Song(mid, [entry.get("title", "")], band=bands[0] if bands else "")
            song.difficulties[entry["difficulty"]] = entry
        known = load_titles()
        for mid, titles in known.items():
            song = songs.get(mid)
            if song is None:
                continue  # 谱面站没有的曲目：无法演奏
            # 保持表里的语言顺序（日/英/繁/简/韩）；谱面站的曲名接在后面兜底
            song.titles = titles + [t for t in song.titles if t not in titles]
        logger.debug(
            "曲目目录：%d 首，%d 个曲名，曲名表里没有的 %d 首", len(songs), sum(len(s.titles) for s in songs.values()), len(songs.keys() - known.keys())
        )
        return cls(songs)

    def match(
        self,
        text: str,
        min_score: float = 70.0,
        difficulty: str | None = None,
        level: int | None = None,
        min_margin: float = 8.0,
    ) -> tuple[Song, float] | None:
        """模糊匹配曲名，返回 (曲目, 相似度 0~100)；识别不出或有歧义时返回 None。

        非完全匹配时，第二像的另一首歌与最像的相差不到 ``min_margin`` 视为歧义（宁可不打也不要打错谱面）。
        给出 ``difficulty`` 与 ``level`` 时用等级排除同系列的其他曲目；但等级只用来消歧：
        最像的曲目等级不符（多半是等级读错）时不改选一首没那么像的，而是只按曲名判断。
        """
        key = normalize(text)
        if not key:
            return None
        best: dict[int, float] = {}  # musicId → 各语言曲名中的最高相似度
        for choice, score, _ in process.extract(key, self._choices.keys(), scorer=fuzz.ratio, limit=None):
            mid = self._choices[choice]
            best[mid] = max(best.get(mid, 0.0), score)
        ranked = sorted(best.items(), key=lambda kv: -kv[1])
        if not ranked:
            return None
        if difficulty and level is not None:
            same = [(mid, s) for mid, s in ranked if self.songs[mid].level(difficulty) == level]
            if same and same[0][1] >= ranked[0][1]:
                ranked = same
            else:
                mid, score = ranked[0]
                logger.debug(
                    "曲名 %r 最接近 %s（%.0f），但它的 %s 等级为 %s，画面上为 %d：只按曲名判断",
                    text,
                    self.songs[mid].title,
                    score,
                    difficulty,
                    self.songs[mid].level(difficulty),
                    level,
                )
        mid, score = ranked[0]
        if score < min_score:
            logger.debug("曲名 %r 最接近 %s（%.0f），低于阈值", text, self.songs[mid].title, score)
            return None
        if score < 100 and len(ranked) > 1 and score - ranked[1][1] < min_margin:
            other, s2 = ranked[1]
            logger.debug(
                "曲名 %r 有歧义：%s（%.0f）与 %s（%.0f）",
                text,
                self.songs[mid].title,
                score,
                self.songs[other].title,
                s2,
            )
            return None
        return self.songs[mid], float(score)

    def get(self, music_id: int) -> Song:
        return self.songs[music_id]

    def lookup(self, text: str) -> tuple[Song, float] | None:
        """人输入的曲目 ID 或曲名（任意语言，模糊匹配，取最像的）→ (曲目, 相似度 0~100)。"""
        text = text.strip()
        if text.isdigit() and int(text) in self.songs:
            return self.songs[int(text)], 100.0
        return self.match(text, min_margin=0)
