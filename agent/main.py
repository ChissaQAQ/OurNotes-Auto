"""MaaFramework Agent 入口，由通用界面（MFAAvalonia / MXU）按 interface.json 启动，最后一个参数是连接标识。"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)  # 配置、缓存、日志都相对项目根目录；界面启动时的工作目录不一定是这里
sys.path.insert(0, str(ROOT))
for stream in (sys.stdout, sys.stderr):
    if stream is not None:
        stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from ournotes_auto.agent.server import main  # noqa: E402

sys.exit(main())
