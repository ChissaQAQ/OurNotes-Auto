"""Agent 服务：注册自定义动作 ``OurNotesRun``。只在 Agent 进程里导入（导入 maa.agent 会切换到 Agent 服务模式）。

Agent 只转发：把任务名、界面选项和控制器信息交给演奏进程（``python -m ournotes_auto agent-run``），参数转换和演奏
都在那边。MFAA 的 Agent 常驻、换文件后不会重新加载，所以这里只导入少数几个模块（release.SHELL），其他代码改了热更新就能生效。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

from .. import __version__
from ..release import EXIT_UPDATE
from .logs import set_ui_debug, setup_logging
from .update import UpdateBusyError, UpdateCheck
from .worker import apply_proxy, run_worker

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
PARAM_NODE = "OurNotesParam"  # 界面选项都覆盖到这个节点的 attach 上（和 params.PARAM_NODE 一致）
UPDATE = UpdateCheck(ROOT)


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
    """``custom_action_param`` 为 ``{"task": "start" | "switch_account" | "repeat" | "clear_lb" | "idle" | "ap" | "records"}``，
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
        logger.debug("任务 %s，参数 %s", task, attach)
        if apply_proxy(attach.get("proxy")):
            logger.debug("网络代理：%s", attach.get("proxy"))
        UPDATE.start(check=_flag(attach, "check_update", True), auto=_flag(attach, "auto_update", True))
        payload = {"task": task, "attach": attach, "controller": _controller_info(context)}
        cmd = [sys.executable, "-m", "ournotes_auto", "agent-run", json.dumps(payload, ensure_ascii=False, default=str)]
        logger.debug("启动演奏进程：%s", cmd)

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
        UPDATE.report()
        return result.returncode == 0


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
    if not AgentServer.start_up(argv[-1]):
        logger.error("Agent 服务启动失败")
        return 1
    AgentServer.join()
    AgentServer.shut_down()
    return 0
