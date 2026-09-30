"""封面匹配：用乐队确认页左下角的封面识别曲目。

OCR 用的中文模型读不出假名曲名，同系列曲名（Symbol I～IV）也只差编号；封面则每首歌都不同。
做法：把截图里的封面与 haneoka 的缩略图都缩到 24x24，去均值后算相关系数。
实测（MuMu 1280x720）正确曲目约 0.99，第二名约 0.55；曲库内最相似的两张封面之间也只有 0.81。
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)

JACKET_ROI = (13, 598, 95, 95)  # 乐队确认页左下角封面（1280x720 下的 x, y, w, h）
DESIGN_W, DESIGN_H = 1280, 720


def decode(raw: bytes) -> np.ndarray:
    """PNG → BGR；半透明（圆角）部分按黑底合成。"""
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("无法解码封面图片")
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        return (img[:, :, :3] * alpha).astype(np.uint8)
    return img


def crop_jacket(frame: np.ndarray, roi=JACKET_ROI) -> np.ndarray:
    h, w = frame.shape[:2]
    sx, sy = w / DESIGN_W, h / DESIGN_H
    x, y, rw, rh = roi
    return frame[round(y * sy) : round((y + rh) * sy), round(x * sx) : round((x + rw) * sx)]


class JacketMatcher:
    def __init__(
        self,
        jackets: dict[int, np.ndarray],
        size: int = 24,
        min_score: float = 0.9,
        min_margin: float = 0.15,
        inset: float = 0.0,
        row_norm: bool = False,
    ):
        """``inset``：比较前四边各裁掉的比例（封面和截图都裁）；``row_norm``：逐行去均值、归一化
        （选曲列表最上、最下两行是渐隐的，整体变暗、变淡，逐行归一化后照样认得出）。"""
        self.size = size
        self.min_score = min_score
        self.min_margin = min_margin
        self.inset = inset
        self.row_norm = row_norm
        self.images = jackets
        self.ids = list(jackets)
        self._features = self.features(list(jackets.values()))

    @classmethod
    def load(cls, client, catalog) -> JacketMatcher:
        """下载（有缓存）目录中每首曲目的封面缩略图；个别下载失败只少认这几首。"""
        songs = [s for s in catalog.songs.values() if s.jacket_url]
        missing = sum(not client.jacket_cached(s.music_id) for s in songs)
        if missing:
            logger.info("下载 %d 张曲目封面（用来认歌，之后有缓存）", missing)
        images = {}
        for song in songs:
            try:
                images[song.music_id] = decode(client.jacket(song.music_id, song.jacket_url))
            except (ConnectionError, LookupError, ValueError) as e:
                logger.warning("封面 %d 获取失败：%s", song.music_id, e)
        logger.debug("封面：%d 张", len(images))
        return cls(images)

    def variant(self, **params) -> JacketMatcher:
        """用同一批封面、换一组参数再建一个（参数同构造函数，没给的沿用这个的）。"""
        kw = dict(
            size=self.size,
            min_score=self.min_score,
            min_margin=self.min_margin,
            inset=self.inset,
            row_norm=self.row_norm,
        )
        return JacketMatcher(self.images, **{**kw, **params})

    def features(self, imgs: list[np.ndarray]) -> np.ndarray:
        """每张图一行的特征矩阵。"""
        out = np.empty((len(imgs), self.size * self.size * 3), np.float32)
        for i, img in enumerate(imgs):
            if img.ndim == 2:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            if self.inset:
                h, w = img.shape[:2]
                ky, kx = round(h * self.inset), round(w * self.inset)
                img = img[ky : h - ky, kx : w - kx]
            out[i] = cv2.resize(img, (self.size, self.size), interpolation=cv2.INTER_AREA).ravel()
        if self.row_norm:
            rows = out.reshape(len(imgs), self.size, -1)
            rows -= rows.mean(axis=2, keepdims=True)
            rows /= np.linalg.norm(rows, axis=2, keepdims=True) + 1e-3
        else:
            out -= out.mean(axis=1, keepdims=True)
        return out / (np.linalg.norm(out, axis=1, keepdims=True) + 1e-6)

    def scores(self, crops: list[np.ndarray]) -> np.ndarray:
        """每张截图与每张封面的相关系数，形状 (截图数, 封面数)，列的顺序同 :attr:`ids`。"""
        return self.features(crops) @ self._features.T

    def rank(self, crop: np.ndarray) -> list[tuple[int, float]]:
        """按相关系数从高到低返回 (musicId, 相关系数)。"""
        if not self.ids:
            return []
        scores = self.scores([crop])[0]
        order = np.argsort(-scores)
        return [(self.ids[i], float(scores[i])) for i in order]

    def identify(
        self, crop: np.ndarray, min_score: float | None = None, min_margin: float | None = None
    ) -> tuple[int, float] | None:
        """足够像且明显比第二名像时返回 (musicId, 相关系数)，否则 None。阈值默认用构造时的。"""
        ranked = self.rank(crop)
        if not ranked:
            return None
        mid, score = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else -1.0
        min_score = self.min_score if min_score is None else min_score
        min_margin = self.min_margin if min_margin is None else min_margin
        if score < min_score or score - second < min_margin:
            logger.debug("封面不确定：最像 %d（%.2f），第二 %.2f", mid, score, second)
            return None
        return mid, score
