"""曲目目录：把选曲界面 OCR 到的曲名匹配到 musicId。"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from .bdon import BdonClient

logger = logging.getLogger(__name__)


def normalize(text: str) -> str:
    """全半角统一、去空白与常见标点、转小写，减少 OCR 误差的影响。"""
    text = unicodedata.normalize("NFKC", text).lower().replace("|", "l")  # OCR 常把 l/I 读成 |
    return re.sub(r"[\s　·・,，.。!！?？'\"“”‘’()（）\[\]【】「」『』~〜\-－_:：/]", "", text)


@dataclass
class Song:
    music_id: int
    titles: list[str]
    band: str = ""
    difficulties: dict[str, dict] = field(default_factory=dict)  # difficulty → 索引条目
    jacket_url: str = ""  # 国际服封面缩略图（haneoka 的相对路径）

    @property
    def title(self) -> str:
        return self.titles[0] if self.titles else str(self.music_id)

    def display_title(self, lang_index: int = 3) -> str:
        """lang_index：0 日文、1 英文、2 繁中、3 简中、4 韩文（按 haneoka 的顺序）。"""
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
        for mid, entry in client.intl_songs(refresh=refresh).items():
            song = songs.get(mid)
            if song is None:
                continue  # 国际服有但谱面站还没有的曲目：无法演奏
            titles = [t for t in entry.get("musicTitle") or [] if t]
            # 保持 haneoka 的语言顺序（日/英/繁/简/韩）；bdon 的日文曲名作为首项兜底
            song.titles = titles + [t for t in song.titles if t not in titles]
            song.jacket_url = entry.get("jacketThumbUrl") or ""
        logger.debug("曲目目录：%d 首，%d 个曲名", len(songs), sum(len(s.titles) for s in songs.values()))
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
