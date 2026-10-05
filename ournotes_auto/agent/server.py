"""Agent 服务：注册自定义动作 ``OurNotesRun``。只在 Agent 进程里导入（导入 maa.agent 会切换到 Agent 服务模式）。"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

from .. import __version__
from .logs import set_ui_debug, setup_logging
from .params import (
    LOCAL_TASKS,
    PARAM_NODE,
    TASKS,
    ParamError,
    device_from_controller,
    flag,
    global_args,
    worker_args,
)
from .update import UpdateCheck
from .worker import run_worker

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
UPDATE = UpdateCheck(ROOT)


@AgentServer.custom_action("OurNotesRun")
class OurNotesRun(CustomAction):
    """``custom_action_param`` 为 ``{"task": "start" | "switch_account" | "repeat" | "clear_lb" | "idle" | "ap" | "records"}``，
    选项在 ``OurNotesParam`` 节点的 attach 里。"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        try:
            param = json.loads(argv.custom_action_param or "{}")
            task = str(param.get("task", "")) if isinstance(param, dict) else ""
            attach = (context.get_node_data(PARAM_NODE) or {}).get("attach") or {}
            set_ui_debug(flag(attach, "debug_log"))
            logger.debug("任务 %s，参数 %s", task, attach)
            if flag(attach, "check_update", default=True):
                UPDATE.start()
            device = {} if task in LOCAL_TASKS else device_from_controller(context.tasker.controller.info)
            args = [*global_args(attach), *worker_args(task, attach, device)]
        except ParamError as e:
            logger.error("%s", e)
            return False
        except ValueError as e:
            logger.error("任务参数不是有效的 JSON：%s", e)
            return False

        cmd = [sys.executable, "-m", "ournotes_auto", *args]
        logger.info("开始%s", TASKS[task])
        logger.debug("启动演奏进程：%s", cmd)
        result = run_worker(cmd, cwd=ROOT, should_stop=lambda: context.tasker.stopping)
        if result.stopped:
            logger.info("已停止")
        elif result.returncode:
            logger.error("%s结束，退出码 %d（详细日志见 data/ournotes.log）", TASKS[task], result.returncode)
        else:
            logger.info("%s完成", TASKS[task])
        UPDATE.report()
        return result.returncode == 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    setup_logging()
    if not argv:
        logger.error("缺少 Agent 连接标识（应由界面启动）")
        return 2
    logger.debug("Agent %s 启动（%s），连接标识 %s", __version__, os.environ.get("PI_CLIENT_NAME", "未知界面"), argv[-1])
    if not AgentServer.start_up(argv[-1]):
        logger.error("Agent 服务启动失败")
        return 1
    AgentServer.join()
    AgentServer.shut_down()
    return 0
