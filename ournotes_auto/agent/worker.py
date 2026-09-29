"""启动演奏子进程（``python -m ournotes_auto --json-log --stdin-stop ...``），转发日志并传递停止请求。"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

logger = logging.getLogger(__name__)

WORKER_LOGGER = "ournotes_auto.worker"  # 子进程里不是 JSON 的输出（未捕获的异常等）记在这个名字下
_LEVELS = logging.getLevelNamesMapping()


@dataclass
class WorkerResult:
    returncode: int
    stopped: bool  # 是否发出过停止请求
    killed: bool  # 停止请求后没有按时退出、被强制结束


def parse_line(raw: bytes) -> tuple[int, str, str] | None:
    """子进程的一行输出 → (级别, 日志名, 消息)；空行返回 None。"""
    text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
    if not text.strip():
        return None
    try:
        obj = json.loads(text)
    except ValueError:
        obj = None
    if isinstance(obj, dict) and isinstance(obj.get("msg"), str):
        level = _LEVELS.get(str(obj.get("level", "")), logging.INFO)
        return level, str(obj.get("name") or WORKER_LOGGER), obj["msg"]
    return logging.WARNING, WORKER_LOGGER, text


def relay(raw: bytes) -> None:
    """按子进程里的日志名与级别重新记一遍，由本进程的处理器输出到界面和文件。"""
    parsed = parse_line(raw)
    if parsed is not None:
        level, name, msg = parsed
        logging.getLogger(name).log(level, "%s", msg)


def _pump(stream: IO[bytes], q: queue.Queue) -> None:
    try:
        for raw in stream:
            q.put(raw)
    except (OSError, ValueError):
        pass
    finally:
        q.put(None)


def _send_stop(proc: subprocess.Popen) -> None:
    try:
        proc.stdin.write(b"stop\n")
        proc.stdin.flush()
    except (OSError, ValueError):
        pass  # 子进程已经退出


def run_worker(
    cmd: Sequence[str],
    *,
    cwd: str | Path,
    should_stop: Callable[[], bool],
    on_line: Callable[[bytes], None] = relay,
    poll_s: float = 0.2,
    grace_s: float = 20.0,
    exit_drain_s: float = 1.0,
) -> WorkerResult:
    """运行子进程直到结束。在调用线程里轮询 ``should_stop``（Agent 的 context 只能在回调线程里用）；
    返回真时经标准输入发送 stop，``grace_s`` 秒内没退出就强制结束。"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(
        list(cmd),
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    q: queue.Queue[bytes | None] = queue.Queue()
    threading.Thread(target=_pump, args=(proc.stdout, q), name="worker-output", daemon=True).start()
    stop_at: float | None = None
    exited_at: float | None = None
    killed = False
    try:
        while True:
            try:
                raw = q.get(timeout=poll_s)
            except queue.Empty:
                raw = b""
            if raw is None:
                break
            if raw:
                on_line(raw)
            now = time.monotonic()
            if proc.poll() is not None:
                # 子进程启动的程序（如 adb 服务）可能继承了输出管道，退出后不一定读得到 EOF
                exited_at = exited_at or now
                if q.empty() and now - exited_at > exit_drain_s:
                    break
            elif stop_at is None:
                if should_stop():
                    logger.info("正在停止…")
                    stop_at = now
                    _send_stop(proc)
            elif not killed and now - stop_at > grace_s:
                logger.warning("演奏进程 %.0f 秒内没有退出，强制结束", grace_s)
                proc.kill()
                killed = True
        return WorkerResult(proc.wait(), stop_at is not None, killed)
    finally:
        if proc.poll() is None:  # on_line / should_stop 出错
            proc.kill()
            proc.wait()
        try:
            proc.stdin.close()
        except OSError:
            pass
        # 输出管道留给读取线程：它可能还阻塞在 read 上，这时 close 会等它的锁
