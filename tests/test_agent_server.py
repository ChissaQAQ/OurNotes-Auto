"""Agent 的导入、启动注册和更新锁失败处理；子进程隔离 MaaFramework 服务模式。"""

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def check(code):
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="replace", check=True, timeout=15)


def test_register_action_at_startup():
    check('''
from unittest.mock import patch
from ournotes_auto.agent import server
assert not server.AgentServer._custom_action_holder
with patch.object(server, "setup_logging"), \
     patch.object(server.AgentServer, "register_custom_action", return_value=True) as register, \
     patch.object(server.AgentServer, "start_up", return_value=True) as start, \
     patch.object(server.AgentServer, "join") as join, \
     patch.object(server.AgentServer, "shut_down") as shutdown:
    assert server.main([]) == 2
    register.assert_not_called()
    assert server.main(["test-id"]) == 0
    assert register.call_args.args[0] == "OurNotesRun"
    assert isinstance(register.call_args.args[1], server.OurNotesRun)
    start.assert_called_once_with("test-id")
    join.assert_called_once()
    shutdown.assert_called_once()
''')


def test_busy_failure_does_not_launch_worker():
    check('''
from types import SimpleNamespace
from unittest.mock import Mock, patch
from ournotes_auto.agent import server
ctx = SimpleNamespace(tasker=SimpleNamespace(stopping=False, controller=SimpleNamespace(info={})), get_node_data=lambda name: {"attach": {}})
update = Mock()
update.busy.side_effect = server.UpdateBusyError("更新尚未完成")
with patch.object(server, "UPDATE", update), patch.object(server, "run_worker") as worker:
    assert server.OurNotesRun().run(ctx, SimpleNamespace(custom_action_param='{"task":"records"}')) is False
    worker.assert_not_called()
    update.report.assert_called_once()
''')
