"""Agent 的日志：界面上看得到的那部分按通用界面（环境变量 PI_CLIENT_NAME）的约定输出。

- MFAAvalonia：每行以 ``info:`` / ``warn:`` / ``err:`` 等开头的输出显示在界面日志里（按 Markdown 渲染），
  其余只进它自己的日志文件；
- MXU：标准输出里的 HTML ``<span>`` 行；
- 其他（命令行调试）：普通文本。
"""

from __future__ import annotations

import html
import logging
import os
import sys
from pathlib import Path

MFAA_PREFIX = {
    "DEBUG": "debug",
    "INFO": "info",
    "WARNING": "warn",
    "ERROR": "err",
    "CRITICAL": "critical",
}
# 普通信息用界面默认颜色，和 MXU 自己的日志一致
HTML_COLOR = {
    "DEBUG": "deepskyblue",
    "WARNING": "darkorange",
    "ERROR": "crimson",
    "CRITICAL": "firebrick",
}


def client_name() -> str:
    return os.environ.get("PI_CLIENT_NAME", "").strip().upper()


class UiFormatter(logging.Formatter):
    def __init__(self, client: str):
        super().__init__("%(asctime)s %(levelname).1s %(message)s", "%H:%M:%S")
        self.client = client

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)
        lines = msg.splitlines() or [""]
        if self.client in ("MFAAVALONIA", "MFAA"):
            prefix = MFAA_PREFIX.get(record.levelname, "info")
            return "\n".join(f"{prefix}:{line}" for line in lines)
        if self.client == "MXU":
            # 整行包在 span 里：MXU 会把像路径、网址、全大写单词的纯文本行当成文件或网址去加载
            color = HTML_COLOR.get(record.levelname)
            tag = f'<span style="color:{color};">' if color else "<span>"
            return "\n".join(f"{tag}{html.escape(line)}</span>" for line in lines)
        return super().format(record)


def setup_logging(log_file: str | None = "data/agent.log", client: str | None = None) -> None:
    client = client_name() if client is None else client
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    ui = logging.StreamHandler(sys.stdout if client == "MXU" else sys.stderr)
    ui.setLevel(logging.INFO)
    ui.setFormatter(UiFormatter(client))
    root.addHandler(ui)
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname).1s %(name)s: %(message)s"))
        root.addHandler(fh)
