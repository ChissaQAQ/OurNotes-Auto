"""生成 ournotes_auto/nav/t2s.py：繁体字 → 简体字的单字对照表（只留 OCR 模型认得的字）。

数据来自 OpenCC 的 TSCharacters.txt（Apache-2.0，https://github.com/BYVoid/OpenCC），一字多义时取第一个。
用法：python tools/gen_t2s.py TSCharacters.txt
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEYS = ROOT / "resource" / "model" / "ocr" / "keys.txt"
OUT = ROOT / "ournotes_auto" / "nav" / "t2s.py"


def main(src: str) -> None:
    keys = set(KEYS.read_text(encoding="utf-8").split("\n"))
    trad, simp = [], []
    for line in Path(src).read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        k, v = line.split("\t")
        v = v.split()[0]
        if len(k) == 1 and len(v) == 1 and k != v and k in keys:
            trad.append(k)
            simp.append(v)
    rows = [f'    "{"".join(trad[i : i + 50])}"' for i in range(0, len(trad), 50)]
    rows2 = [f'    "{"".join(simp[i : i + 50])}"' for i in range(0, len(simp), 50)]
    OUT.write_text(
        '"""繁体字 → 简体字（tools/gen_t2s.py 由 OpenCC 的 TSCharacters.txt 生成，Apache-2.0）。"""\n\n'
        "TRAD = (\n" + "\n".join(rows) + "\n)\n"
        "SIMP = (\n" + "\n".join(rows2) + "\n)\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"{len(trad)} 字 → {OUT}")


if __name__ == "__main__":
    main(sys.argv[1])
