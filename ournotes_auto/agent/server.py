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
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

from .. import __version__, release, sysmem
from ..release import EXIT_UPDATE
from .logs import set_ui_debug, setup_logging
from .update import UpdateBusyError, UpdateCheck
from .worker import apply_proxy, run_worker

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
PARAM_NODE = "OurNotesParam"  # 界面选项都覆盖到这个节点的 attach 上（和 params.PARAM_NODE 一致）
UPDATE = UpdateCheck(ROOT)
SECRET_KEYS = ("notify_target",)  # 等于密码的选项（推送地址），日志里不记
PROXY_PASSWORD = re.compile(r"(?<=:)[^:@/]*(?=@)")  # 代理地址 user:pass@host:port 里的密码，日志里不记


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
        payload = {"task": task, "attach": attach, "controller": _controller_info(context)}
        cmd = [sys.executable, "-m", "ournotes_auto", "agent-run", json.dumps(payload, ensure_ascii=False, default=str)]
        shown = json.dumps({**payload, "attach": _masked(attach)}, ensure_ascii=False, default=str)
        logger.debug("启动演奏进程：%s", [*cmd[:-1], shown])

        def stopping() -> bool:
            return context.tasker.stopping

        while True:
            try:
                with UPDATE.busy():
                    result = run_worker(cmd, cwd=ROOT, should_stop=stopping, should_update=UPDATE.hot_ready)
            except UpdateBusyError as e:
                logger.error("%s", e)
                UPDATE.report()
                return False
            if result.returncode != EXIT_UPDATE or result.stopped or stopping():
                break
            # 挂机为热更新在安全的时机退出了：等换上新代码，用同样的参数重新启动
            UPDATE.wait_applied(stopping)
            if stopping():
                break
        if result.stopped or stopping():
            logger.info("已停止")
        elif result.returncode:
            logger.error("任务结束，退出码 %d（详细日志见 data/ournotes.log）", result.returncode)
            _report_memory(result.out_of_memory)
        UPDATE.report()
        return result.returncode == 0


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
