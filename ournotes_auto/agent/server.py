"""Agent 服务：注册自定义动作 ``OurNotesRun``。只在 Agent 进程里导入（导入 maa.agent 会切换到 Agent 服务模式）。

Agent 只转发：把任务名、界面选项和控制器信息交给演奏进程（``python -m ournotes_auto agent-run``），参数转换和演奏
都在那边。MFAA 的 Agent 常驻、换文件后不会重新加载，所以这里只导入少数几个模块（release.SHELL），其他代码改了热更新就能生效。
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

from .. import __version__, release, sysmem
from ..release import EXIT_RERUN, EXIT_UPDATE
from .logs import set_ui_debug, setup_logging
from .update import UpdateBusyError, UpdateCheck
from .worker import WorkerResult, apply_proxy, run_worker

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
PARAM_NODE = "OurNotesParam"  # 界面选项都覆盖到这个节点的 attach 上（和 params.PARAM_NODE 一致）
UPDATE = UpdateCheck(ROOT)
SECRET_KEYS = ("notify_target",)  # 等于密码的选项（推送地址），日志里不记
PROXY_PASSWORD = re.compile(r"(?<=:)[^:@/]*(?=@)")  # 代理地址 user:pass@host:port 里的密码，日志里不记
IDLE_TASKS = ("idle", "arena_idle")  # 挂机（和 params.IDLE_TASKS 一致）：开着「每天重跑前面的任务」时日期变更后退出（EXIT_RERUN）
RERUN_KEY = "rerun_daily"  # 「每天重跑前面的任务」开关（attach 里的键）
NO_RERUN = frozenset((*IDLE_TASKS, "records", "notify_test"))  # 不重跑的任务：挂机本身、不打歌的（打开统计窗口会弹出窗口）
# MFAA 的 Agent 常驻：上一个任务结束这么久以后才开始的任务算新的一次运行（界面上又点了一次开始）；MXU 每次开始都启动新的 Agent
RUN_GAP_S = 60.0
LAUNCHED_ENV = "OURNOTES_LAUNCHED"  # 这次打开界面的时刻，演奏进程继承（和 charts/bdon.py 一致）


def _masked(attach: Mapping[str, Any]) -> dict[str, Any]:
    return {k: "***" if k in SECRET_KEYS and v else _hide_password(v) if k == "proxy" else v for k, v in attach.items()}


def _hide_password(proxy: Any) -> Any:
    return PROXY_PASSWORD.sub("***", proxy) if isinstance(proxy, str) else proxy


def _flag(attach: Mapping[str, Any], key: str, default: bool) -> bool:
    """Agent 自己用的开关（调试日志、检查更新）；认不出的值用默认值，参数校验在演奏进程里做。"""
    raw = attach.get(key, default)
    if isinstance(raw, bool):
        return raw
    low = str(raw).strip().lower()
    return True if low in ("true", "yes", "1") else False if low in ("false", "no", "0") else default


def _controller_info(context: Context) -> Any:
    try:
        return context.tasker.controller.info
    except Exception as e:  # noqa: BLE001 - 演奏进程会报「没有连接模拟器」
        logger.debug("读取控制器信息失败：%s", e)
        return {}


class RunHistory:
    """这次运行（界面上点一次开始）里已经运行过的任务和选项：挂机开着「每天重跑前面的任务」时，游戏日期变更后
    按顺序重跑它们再接着挂机。手动停止、这样的挂机结束（它总在最后）后清空。"""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.tasks: list[tuple[str, dict[str, Any]]] = []
        self.since = 0.0  # 这些任务（最近一轮）开始运行的时刻（Unix 秒），挂机按它算哪天该重跑
        self._last_end = float("-inf")  # 上一个任务结束的时刻

    def begin(self, task: str, attach: Mapping[str, Any]) -> None:
        """一个任务开始：离上一个任务结束太久就是新的一次运行，之前的不算。"""
        now = self.clock()
        if now - self._last_end > RUN_GAP_S:
            self.tasks.clear()
        if not self.tasks:
            self.since = now
        if task not in NO_RERUN:
            self.tasks.append((task, dict(attach)))

    def end(self, stopped: bool, last: bool = False) -> None:
        """一个任务结束。``last``：开着「每天重跑前面的任务」、不限局数的挂机，出错结束后也清空（MFAA 的 Agent 常驻，
        一分钟内再点开始时前面的任务不重复记）；限了局数的挂机后面还有任务，不清空。"""
        self._last_end = self.clock()
        if stopped or last:
            self.tasks.clear()


HISTORY = RunHistory()


def _reruns(task: str, attach: Mapping[str, Any]) -> bool:
    """开着「每天重跑前面的任务」、不限局数的挂机（演奏进程只在这时为重跑退出）。"""
    if task not in IDLE_TASKS or not _flag(attach, RERUN_KEY, False):
        return False
    try:
        return int(str(attach.get("max_plays", 0)).strip()) <= 0
    except ValueError:  # 演奏进程会报参数错误
        return False


class OurNotesRun(CustomAction):
    """``custom_action_param`` 为 ``{"task": "start" | "switch_account" | "repeat" | "clear_lb" | "idle" | "ap" | "challenge" | "arena" | "arena_idle" | "records" | "notify_test"}``，
    选项在 ``OurNotesParam`` 节点的 attach 里。"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        try:
            param = json.loads(argv.custom_action_param or "{}")
        except ValueError as e:
            logger.error("任务参数不是有效的 JSON：%s", e)
            return False
        task = param.get("task", "") if isinstance(param, dict) else ""
        attach = (context.get_node_data(PARAM_NODE) or {}).get("attach") or {}
        set_ui_debug(_flag(attach, "debug_log", False))
        logger.debug("任务 %s，参数 %s", task, _masked(attach))
        proxy = apply_proxy(attach.get("proxy"))
        if proxy:
            logger.debug("网络代理：%s", _hide_password(proxy))
        UPDATE.start(check=_flag(attach, "check_update", True), auto=_flag(attach, "auto_update", True))
        HISTORY.begin(task, attach)
        payload: dict[str, Any] = {"task": task, "attach": attach, "controller": _controller_info(context)}
        if task in IDLE_TASKS and HISTORY.tasks:
            payload["rerun_since"] = HISTORY.since  # 演奏进程看开关决定重不重跑
        elif task in IDLE_TASKS and _flag(attach, RERUN_KEY, False):
            logger.info("这次在挂机前面没有运行别的任务，日期变更后不用重跑")

        def stopping() -> bool:
            return context.tasker.stopping

        while True:
            result = _work(payload, stopping)
            if result is None:
                HISTORY.end(stopped=False, last=_reruns(task, attach))
                UPDATE.report()
                return False
            if task not in IDLE_TASKS or result.returncode != EXIT_RERUN or result.stopped or stopping():
                break
            # 挂机在游戏日期变更后退出了：按顺序重跑前面的任务，再用同样的参数接着挂机
            _rerun(context, stopping)
            if stopping():
                break
            payload["rerun_since"] = HISTORY.since
            payload["continues"] = True
        stopped = result.stopped or stopping()
        HISTORY.end(stopped, last=_reruns(task, attach))
        if stopped:
            logger.info("已停止")
        else:
            _report_exit(result)
        UPDATE.report()
        return result.returncode == 0


def _work(payload: Mapping[str, Any], stopping: Callable[[], bool]) -> WorkerResult | None:
    """运行一个演奏进程；挂机为热更新退出时等换上新代码，用同样的参数重新启动。正在更新、启动不了时返回 None。"""
    cmd = [sys.executable, "-m", "ournotes_auto", "agent-run", json.dumps(payload, ensure_ascii=False, default=str)]
    shown = json.dumps({**payload, "attach": _masked(payload["attach"])}, ensure_ascii=False, default=str)
    logger.debug("启动演奏进程：%s", [*cmd[:-1], shown])
    while True:
        try:
            with UPDATE.busy():
                result = run_worker(cmd, cwd=ROOT, should_stop=stopping, should_update=UPDATE.hot_ready)
        except UpdateBusyError as e:
            logger.error("%s", e)
            return None
        if result.returncode != EXIT_UPDATE or result.stopped or stopping():
            return result
        # 挂机为热更新在安全的时机退出了：等换上新代码，用同样的参数重新启动
        UPDATE.wait_applied(stopping)
        if stopping():
            return result


def _rerun(context: Context, stopping: Callable[[], bool]) -> None:
    """按顺序重跑这次运行里挂机前面的任务（选项同当时，出错了接着跑下一个），手动停止时不再跑。"""
    tasks = list(HISTORY.tasks)
    HISTORY.since = HISTORY.clock()
    for i, (task, attach) in enumerate(tasks, 1):
        if stopping():
            return
        logger.info("重跑挂机前面的任务（%d/%d）", i, len(tasks))
        result = _work({"task": task, "attach": attach, "controller": _controller_info(context), "continues": True}, stopping)
        if result is not None and not result.stopped and not stopping():
            _report_exit(result)


def _report_exit(result: WorkerResult) -> None:
    """演奏进程出错退出时提示看详细日志（停止的不算）。"""
    if result.returncode:
        logger.error("任务结束，退出码 %d（详细日志见 data/ournotes.log）", result.returncode)
        _report_memory(result.out_of_memory)


def _report_memory(out_of_memory: bool) -> None:
    """演奏进程出错退出时，若是分配不到内存（或电脑的内存快用完了）就说清楚（#94）。"""
    status = sysmem.memory_status()
    if not out_of_memory and not sysmem.low(status):
        return
    mem = sysmem.describe(status)
    logger.error(
        "%s%s：请关掉占内存多的程序或重启电脑后再试；任务管理器「详细信息」按「提交大小」排序能看到是谁占的",
        "电脑内存不足，演奏进程分配不到内存退出了" if out_of_memory else "电脑内存快用完了，出错可能与此有关",
        f"（{mem}）" if mem else "",
    )


PLACEHOLDERS = {
    # 任务「打开统计窗口」的说明
    "stats.md": "统计页还没有生成：运行一次这个任务或打完一局后，这里会显示累计运行时长、演奏数据和成就"
    "（MFAA 点一下别的任务再点回来、MXU 打开设置再回来可以刷新）。\n",
    # MXU 设置页「统计与成就」卡片
    "stats_mxu.md": "还没有统计：打完一局或运行一次任务「打开统计窗口」后，这里会显示累计运行时长、演奏数据和成就"
    "（重新打开设置可以刷新）。\n",
}


def _ensure_stats_page(data: Path = Path("data")) -> None:
    """界面上显示的统计页是 ``data`` 里的几个文件（打歌、运行任务「打开统计窗口」时由 ``stats`` 生成）；
    还没有时先放一句提示，不然界面上显示的是文件路径。"""
    for name, text in PLACEHOLDERS.items():
        path = data / name
        if path.exists():
            continue
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError as e:
            logger.debug("写入统计页提示失败：%s", e)


def _ensure_stats_card(root: Path = ROOT) -> None:
    """MXU 发布包的 interface.json 里没有设置页的「统计与成就」卡片时补上（下次打开界面显示）。

    更新器用旧版本的 release.py 生成 interface.json，从没有这张卡片的版本更新过来时要在这里补。"""
    info = release.read_release(root)
    if not info or info.get("ui") != "mxu":
        return
    path = root / "interface.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if release.STATS_OPTION in (data.get("option") or {}):
            return
        agent = data.get("agent") or {}
        fixed = release.render_interface(data, "mxu", agent_exec=agent.get("child_exec", release.AGENT_EXEC), agent_args=agent.get("child_args", release.AGENT_ARGS))
        release.write_json(path, fixed)
    except (OSError, ValueError, TypeError, KeyError) as e:
        logger.debug("补设置页的统计卡片失败：%s", e)
        return
    logger.info("设置页加上了「统计与成就」卡片，下次打开界面时显示")


def _started(pid: int) -> float | None:
    """进程启动的时刻（Unix 时间）；读不到（不是 Windows、进程已退出等）返回 None。"""
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except (ImportError, AttributeError, OSError):
        return None
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.GetProcessTimes.argtypes = [wintypes.HANDLE, *[ctypes.POINTER(wintypes.FILETIME)] * 4]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    times = [wintypes.FILETIME() for _ in range(4)]
    try:
        if not k32.GetProcessTimes(h, *map(ctypes.byref, times)):
            return None
    finally:
        k32.CloseHandle(h)
    ticks = times[0].dwHighDateTime << 32 | times[0].dwLowDateTime
    return ticks / 1e7 - 11644473600  # FILETIME 是 1601 年起的 100ns 数


def _launched() -> float:
    """这次打开界面的时刻（谱面站连不上时，到下次打开程序再下载曲目列表、封面，#108）。MXU 每次开始任务都启动新的
    Agent，所以看界面（父进程）；读不到、或父进程已退出 PID 被后来的进程占了时，用 Agent 自己启动的时刻。"""
    me = _started(os.getpid()) or time.time()
    ui = _started(os.getppid())
    return ui if ui is not None and ui <= me else me


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    setup_logging()
    if not argv:
        logger.error("缺少 Agent 连接标识（应由界面启动）")
        return 2
    # 导入检查不应创建原生 Agent 服务；仅在真正启动时注册动作。
    if not AgentServer.register_custom_action("OurNotesRun", OurNotesRun()):
        logger.error("注册 Agent 动作失败")
        return 1
    logger.debug("Agent %s 启动（%s），连接标识 %s", __version__, os.environ.get("PI_CLIENT_NAME", "未知界面"), argv[-1])
    os.environ[LAUNCHED_ENV] = str(_launched())
    _ensure_stats_page()
    _ensure_stats_card()
    if not AgentServer.start_up(argv[-1]):
        logger.error("Agent 服务启动失败")
        return 1
    try:
        UPDATE.start_on_launch()  # MFAA 打开界面时也查一次更新
    except Exception as e:  # noqa: BLE001 - 查不了等任务开始再查，不影响 Agent
        logger.debug("检查更新失败：%s", e)
    AgentServer.join()
    AgentServer.shut_down()
    return 0
