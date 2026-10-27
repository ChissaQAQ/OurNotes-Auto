"""推送到手机：打歌、领取日常完成，任务出错结束，挂机遇到服务器维护时发一条消息（设置见 ``config.NotifyConfig``）。

在演奏进程里发、不在 Agent 里：这里的代码改了热更新就生效，命令行运行时也一样推送。
发送失败只记警告，不影响任务；每次最多等 TIMEOUT_S 秒。
"""

from __future__ import annotations

import http.client
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import __version__
from .config import NotifyConfig

logger = logging.getLogger(__name__)

CHANNELS = ("ntfy", "serverchan", "bark", "webhook")
EVENTS = ("done", "error", "maintenance")
TIMEOUT_S = 5.0
APP = "OurNotes-Auto"
NTFY_SERVER = "https://ntfy.sh"
BARK_SERVER = "https://api.day.app"
TARGET_NAMES = {"ntfy": "ntfy 的主题", "serverchan": "Server酱的 SendKey", "bark": "Bark 的 key", "webhook": "webhook 的网址"}
SERVERCHAN_TITLE_MAX = 32  # Server酱的标题最长 32 个字
# 会推送的命令 → 命令行运行时消息里的任务名（从界面运行时用界面上的任务名）
COMMANDS = {"run": "连续演奏", "daily": "领取日常", "start": "启动游戏", "switch-account": "切换账号"}
DONE_COMMANDS = ("run", "daily")  # 完成时也推送（启动游戏、切换账号只是准备步骤，出错时才推送）
LOG_HINT = "详细日志见 data/ournotes.log"


class NotifyError(Exception):
    """推送失败，消息直接显示给用户（不含推送地址：它等于密码）。"""


def wanted(cfg: NotifyConfig, event: str) -> bool:
    """设置了推送方式，并且要推送这类消息。"""
    events = {e.strip().lower() for e in cfg.events.split(",")}
    return bool(cfg.channel.strip()) and event in events


def _json(body: dict) -> bytes:
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def _ntfy_topic(target: str) -> tuple[str, str]:
    """ntfy 的主题，或自建服务的完整网址 → (服务器, 主题)。"""
    if "://" not in target:
        return NTFY_SERVER, target
    parts = urllib.parse.urlsplit(target)
    path, _, topic = parts.path.rstrip("/").rpartition("/")
    if not topic:
        raise NotifyError("ntfy 的网址要带上主题，如 https://ntfy.sh/主题名")
    return f"{parts.scheme}://{parts.netloc}{path}", topic


def _bark_key(target: str) -> tuple[str, str]:
    """Bark 的 key，或 App 里复制的网址（https://api.day.app/key/…，自建服务器是它的地址）→ (服务器, key)。
    key 后面的路径（App 里示例网址的推送内容）不要：发到网址路径里的会盖掉标题和内容。"""
    if "://" not in target and "/" not in target.strip("/"):
        return BARK_SERVER, target.strip("/")
    parts = urllib.parse.urlsplit(target if "://" in target else f"https://{target}")
    key = parts.path.strip("/").split("/")[0]
    if not key:
        raise NotifyError("Bark 的网址要带上 key，如 https://api.day.app/你的key")
    return f"{parts.scheme}://{parts.netloc}", key


def _secrets(channel: str, target: str) -> list[str]:
    """推送地址里等于密码的部分，长的在前（服务器回的出错消息里可能带着它，如 Bark 回的 key）。"""
    parts = {target}
    try:
        if channel == "ntfy":
            parts.add(_ntfy_topic(target)[1])
        elif channel == "bark":
            parts.add(_bark_key(target)[1])
    except NotifyError:
        pass
    return sorted(filter(None, parts), key=len, reverse=True)


def _serverchan_url(key: str) -> str:
    """Server酱³ 的 SendKey（sctp<数字>t…）发到自己的子域名，Turbo 版（SCT…）发到 sctapi（同官方 SDK）。"""
    m = re.match(r"sctp(\d+)t", key)
    return f"https://{m[1]}.push.ft07.com/send/{key}.send" if m else f"https://sctapi.ftqq.com/{key}.send"


def build_request(cfg: NotifyConfig, event: str, title: str, text: str) -> tuple[str, bytes, str]:
    """→ (网址, 内容, Content-Type)。标题前加上 APP。"""
    channel, target = cfg.channel.strip().lower(), cfg.target.strip()
    if channel not in CHANNELS:
        raise NotifyError(f"推送方式应为 {'/'.join(CHANNELS)}：{cfg.channel}")
    if not target:
        raise NotifyError(f"没有填写{TARGET_NAMES[channel]}")
    title = f"{APP}：{title}"
    if channel == "ntfy":
        # 发 JSON 而不是用 Title 头：头只能是 latin-1，中文标题会乱码
        server, topic = _ntfy_topic(target)
        body = {"topic": topic, "title": title, "message": text, **({"priority": 4} if event == "error" else {})}
        return f"{server}/", _json(body), "application/json"
    if channel == "serverchan":
        form = {"title": title[:SERVERCHAN_TITLE_MAX], "desp": text}
        return _serverchan_url(target), urllib.parse.urlencode(form).encode(), "application/x-www-form-urlencoded"
    if channel == "bark":
        # 出错的发成时效性通知：开着专注模式也会提醒
        server, key = _bark_key(target)
        level = {"level": "timeSensitive"} if event == "error" else {}
        body = {"device_key": key, "title": title, "body": text, "group": APP, **level}
        return f"{server}/push", _json(body), "application/json"
    if not target.lower().startswith(("http://", "https://")):
        raise NotifyError("webhook 的网址要以 http:// 或 https:// 开头")
    return target, _json({"event": event, "title": title, "text": text, "time": int(time.time())}), "application/json"


def _post(url: str, body: bytes, content_type: str) -> bytes:
    headers = {"Content-Type": content_type, "User-Agent": f"{APP}/{__version__}"}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers), timeout=TIMEOUT_S) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read(300).decode("utf-8", "replace").strip()
        raise NotifyError(f"推送服务器返回 {e.code}{f'：{detail}' if detail else ''}") from None
    except OSError as e:  # 连不上、超时（URLError 也是 OSError）
        raise NotifyError(f"连不上推送服务器：{getattr(e, 'reason', None) or e}") from None
    except ValueError:  # 网址格式不对
        raise NotifyError("推送地址的格式不对") from None
    except http.client.HTTPException:  # 地址中间有空格等（消息里带着路径，可能含密码，不要原样带出去）
        raise NotifyError("推送地址的格式不对") from None


def _check_serverchan(reply: bytes) -> None:
    """Server酱出错时也可能回 200，看回复里的 code。"""
    try:
        data = json.loads(reply)
    except ValueError:
        return
    if isinstance(data, dict) and data.get("code") not in (0, None):
        raise NotifyError(f"Server酱：{data.get('message') or data.get('code')}")


def send(cfg: NotifyConfig, event: str, title: str, text: str) -> None:
    """发一条推送（不看 ``events``），失败抛 :class:`NotifyError`（消息里推送地址等于密码的部分换成 ***）。"""
    channel = cfg.channel.strip().lower()
    try:
        reply = _post(*build_request(cfg, event, title, text))
        if channel == "serverchan":
            _check_serverchan(reply)
    except NotifyError as e:
        msg = str(e)
        for secret in _secrets(channel, cfg.target.strip()):
            msg = msg.replace(secret, "***")
        raise NotifyError(msg) from None


def push(cfg: NotifyConfig, event: str, title: str, text: str, wait: bool = True) -> None:
    """要推送这类消息时发出去，失败只记警告。``wait`` 为 False 时在后台线程发，不耽误打歌。"""
    if not wanted(cfg, event):
        return

    def run() -> None:
        try:
            send(cfg, event, title, text)
            logger.debug("已推送：%s", title)
        except Exception as e:  # noqa: BLE001 - 推送只是通知，出什么问题都不影响任务
            logger.warning("推送「%s」失败：%s", title, e)

    if wait:
        run()
    else:
        threading.Thread(target=run, name="notify", daemon=True).start()


class LastError(logging.Handler):
    """记下最后一条错误日志的第一行，任务出错结束时推送它。"""

    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.last = ""

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage().strip()
        if record.exc_info and record.exc_info[1] is not None:  # 意外错误：附上是什么错
            msg += f"：{record.exc_info[1]!r}"
        self.last = msg.splitlines()[0] if msg else ""


def task_end(cfg: NotifyConfig, command: str, label: str | None, code: int, summary: str, error: str, elapsed_s: float) -> None:
    """任务结束时推送（同步发：进程马上就退出了，后台线程会被打断）。手动停止、为更新退出的任务不要调用。"""
    if command not in COMMANDS:
        return
    from .stats import duration

    name = label or COMMANDS[command]
    took = f"{elapsed_s:.0f} 秒" if elapsed_s < 59.5 else duration(elapsed_s)
    lines = [*([summary] if summary else []), f"用时 {took}"]
    if code == 0:
        if command in DONE_COMMANDS:
            push(cfg, "done", f"{name}完成", "\n".join(lines))
        return
    push(cfg, "error", f"{name}出错", "\n".join([error or f"退出码 {code}", *lines, LOG_HINT]))
