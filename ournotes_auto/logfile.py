"""日志文件（data/ournotes.log、data/agent.log）：一直追加，打开时太大就换掉。"""

from __future__ import annotations

import logging
from pathlib import Path

LOG_MAX_BYTES = 5 * 1024 * 1024


def file_handler(path: str | Path) -> logging.FileHandler:
    """完整（DEBUG 级别）、带日期的文件日志，反馈问题时附上它。

    文件超过 :data:`LOG_MAX_BYTES` 时先改名为 ``*.old.log``，只留一份旧的；别的进程正开着它时改不了名，就接着追加。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        if p.stat().st_size > LOG_MAX_BYTES:
            p.replace(p.with_suffix(".old" + p.suffix))
    except OSError:
        pass
    fh = logging.FileHandler(p, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname).1s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
    return fh
