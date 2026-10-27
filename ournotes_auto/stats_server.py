"""「统计与成就」窗口背后的小服务：只在本机（127.0.0.1）上听，给页面上的「备份与恢复」导出、导入。

窗口直接打开 data/stats.html（file://）时页面读写不了文件，所以 :func:`.stats_window.open_window` 先起这个服务
（``stats-serve``，没有控制台），窗口打开 ``http://127.0.0.1:<端口>/stats.html?key=<密钥>``；服务起不来时还是
打开文件，只是用不了导出、导入。窗口关掉后服务自己退出。

每个请求都要带密钥（页面从地址里拿到，放在请求头 X-Stats-Key 或地址的 key 里），Host 必须是 127.0.0.1:<端口>：
别的网页猜不到密钥，用 DNS 重绑定换了域名也进不来。只给统计页那几个文件，不给别的。密钥不记进日志。
"""

from __future__ import annotations

import hmac
import io
import json
import logging
import secrets
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import backup, release, stats, stats_window

logger = logging.getLogger(__name__)

KEY_HEADER = "X-Stats-Key"
NAME_HEADER = "X-File-Name"  # 上传的备份的文件名（URL 编码）
INFO_HEADER = "X-Backup-Info"  # 导出时：文件名和导出了多少（JSON，只有 ASCII）
JS = "text/javascript; charset=utf-8"
FILES = {  # 地址 → data 里的文件、类型
    "/": (stats.PAGE_HTML, "text/html; charset=utf-8"),
    f"/{stats.PAGE_HTML}": (stats.PAGE_HTML, "text/html; charset=utf-8"),
    f"/{stats.PAGE_VER}": (stats.PAGE_VER, JS),
    f"/{stats.PAGE_LIVE}": (stats.PAGE_LIVE, JS),
}
APPEAR_S = 120.0  # 等窗口出来最多多久（浏览器没打开窗口时不一直留着）
CHECK_S = 5.0  # 每隔多久看一次窗口关了没有


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, data: Path, refresh: Callable[[], object] | None = None, port: int = 0):
        super().__init__(("127.0.0.1", port), Handler)
        self.data = data
        self.refresh = refresh  # 导入后重新统计（页面重新载入就看到导入的记录）
        self.key = secrets.token_urlsafe(24)
        self.host = f"127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    server: Server

    def log_request(self, code="-", size="-") -> None:  # 只记路径：地址里有密钥。请求行有问题时还没有 path
        logger.debug("统计窗口服务：%s %s → %s", self.command, urlsplit(getattr(self, "path", "") or "").path, code)

    def log_message(self, format, *args) -> None:  # noqa: A002 - 同父类
        logger.debug("统计窗口服务：%s", (format % args).replace(self.server.key, "***"))

    def do_GET(self) -> None:  # noqa: N802 - http.server 的命名
        url = urlsplit(self.path)
        if not self._allowed(parse_qs(url.query).get("key", [""])[0]):
            return
        if url.path == "/api/export":
            self._export()
        elif url.path in FILES:
            name, kind = FILES[url.path]
            try:
                body = (self.server.data / name).read_bytes()
            except OSError:
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            self._send(200, body, kind)
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        url = urlsplit(self.path)
        if not self._allowed():
            return
        if url.path not in ("/api/preview", "/api/import"):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        data = self.server.data
        try:
            b = self._source()
            result = backup.preview(data, b) if url.path == "/api/preview" else backup.import_bundle(data, b)
        except (backup.BackupError, OSError) as e:
            self._json(400, {"ok": False, "error": str(e)})
            return
        out = {
            "ok": True,
            "name": b.name,
            "total": {"records": len(b.records), "sessions": len(b.sessions), "achievements": len(b.unlocked)},
            "records": result.records,
            "known": result.known,
            "bad": result.bad,
            "sessions": result.sessions,
            "achievements": result.achievements,
            "offsets": result.offsets,
            "changed": result.changed,
            "backup": str(result.backup or ""),
        }
        if result.backup is not None and self.server.refresh is not None:
            try:
                self.server.refresh()
            except Exception as e:  # noqa: BLE001 - 已经导入了，统计页下次刷新时会更新
                logger.warning("导入后更新统计页失败：%s", e)
        self._json(200, out)

    def _allowed(self, key: str = "") -> bool:
        key = self.headers.get(KEY_HEADER) or key
        if self.headers.get("Host") == self.server.host and hmac.compare_digest(key.encode(), self.server.key.encode()):
            return True
        self._send(403, b"forbidden", "text/plain; charset=utf-8")
        return False

    def _source(self) -> backup.Bundle:
        """请求里的备份：上传的备份文件，或者 JSON ``{"path": 旧的包目录}``。"""
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            size = -1
        if not 0 <= size <= backup.MAX_UPLOAD:
            raise backup.BackupError(f"文件太大（超过 {backup.MAX_UPLOAD // 1024 // 1024} MB），不是导出的备份")
        body = self.rfile.read(size)
        if (self.headers.get("Content-Type") or "").startswith("application/json"):
            try:
                path = str(json.loads(body)["path"]).strip().strip('"').strip()
            except (ValueError, KeyError, TypeError) as e:
                raise backup.BackupError("请求的格式不对") from e
            if not path:
                raise backup.BackupError("先填旧的包目录")
            if not Path(path).is_absolute():
                raise backup.BackupError("要填完整的路径，如 D:\\OurNotes-Auto-v0.1.5-win-x64-MXU")
            return backup.read_source(Path(path), self.server.data)
        name = Path(unquote(self.headers.get(NAME_HEADER) or "")).name or "选的文件"
        return backup.read_zip(io.BytesIO(body), name)

    def _export(self) -> None:
        try:
            name, blob, b = backup.export_bytes(self.server.data)
        except (backup.BackupError, OSError) as e:
            self._json(400, {"ok": False, "error": str(e)})
            return
        info = json.dumps({"name": name, "records": len(b.records), "sessions": len(b.sessions), "achievements": len(b.unlocked)})
        self._send(200, blob, "application/zip", {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}", INFO_HEADER: info})

    def _json(self, code: int, data: dict) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _send(self, code: int, body: bytes, kind: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)


def _alive(hwnd: int) -> bool:
    import ctypes

    return bool(ctypes.windll.user32.IsWindow(hwnd))


def wait_window_closed(appear_s: float = APPEAR_S, check_s: float = CHECK_S) -> None:
    """等统计窗口出来、再等它关掉；一直没出来时等 ``appear_s`` 秒。"""
    deadline = time.monotonic() + appear_s
    hwnd = 0
    while not hwnd and time.monotonic() < deadline:
        time.sleep(0.5)
        hwnd = stats_window.find_window()
    while hwnd and _alive(hwnd):
        time.sleep(check_s)


def serve(data: Path, ready: Path, refresh: Callable[[], object] | None = None) -> None:
    """起服务，把端口和密钥写进 ``ready``（打开窗口的进程读到就删），窗口关掉后返回。"""
    server = Server(data, refresh)
    threading.Thread(target=server.serve_forever, name="stats-serve", daemon=True).start()
    try:
        release.write_json(ready, {"port": server.server_port, "key": server.key})
        logger.debug("统计窗口服务在端口 %d 上", server.server_port)
        wait_window_closed()
    finally:
        server.shutdown()
        server.server_close()
