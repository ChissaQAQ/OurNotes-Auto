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
     patch.object(server, "UPDATE") as update, \
     patch.object(server, "_ensure_stats_page") as ensure, \
     patch.object(server, "_ensure_stats_card") as card, \
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
    ensure.assert_called_once()  # 不在仓库目录下写 data/stats.md
    card.assert_called_once()
    update.start_on_launch.assert_called_once()  # MFAA 打开界面时查更新
    join.assert_called_once()
    shutdown.assert_called_once()
    update.start_on_launch.side_effect = RuntimeError("boom")  # 查更新出错不影响 Agent
    assert server.main(["test-id"]) == 0
    assert join.call_count == 2
''')


def test_stats_page_placeholder():
    """任务「打开统计窗口」的说明和 MXU 设置页的统计卡片还没有时先放一句提示；已有的不动。"""
    check('''
import tempfile
from pathlib import Path
from ournotes_auto.agent import server
with tempfile.TemporaryDirectory() as d:
    data = Path(d) / "data"
    server._ensure_stats_page(data)
    assert (data / "stats.md").read_text(encoding="utf-8").startswith("统计页还没有生成：")
    assert (data / "stats_mxu.md").read_text(encoding="utf-8").startswith("还没有统计：")
    (data / "stats.md").write_text("x", encoding="utf-8")
    (data / "stats_mxu.md").unlink()
    server._ensure_stats_page(data)
    assert (data / "stats.md").read_text(encoding="utf-8") == "x"
    assert (data / "stats_mxu.md").read_text(encoding="utf-8").startswith("还没有统计：")
''')


def test_stats_card_added_after_update():
    """从没有统计卡片的版本更新过来（更新器用旧的 release.py 生成 interface.json）：Agent 启动时给 MXU 补上卡片，
    用户加的字段、版本号、Agent 启动命令不变；已经有的、MFAA、开发目录都不动。"""
    check('''
import json, tempfile
from pathlib import Path
from ournotes_auto import release
from ournotes_auto.agent import server
src = json.loads((server.ROOT / "interface.json").read_text(encoding="utf-8"))
old = release.render_interface(src, "mxu", "v0.1.6.6")
old["option"].pop(release.STATS_OPTION)
old["setting"] = old["setting"][1:]  # 旧版本只有全局选项一组
old["github"] = "https://example.com"
old["agent"]["child_exec"] = "python/python.exe"
with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    iface = root / "interface.json"
    server._ensure_stats_card(root)  # 没有 release.json：开发目录
    assert not iface.exists()
    for ui in ("mfaa", "mxu"):
        release.write_json(root / release.RELEASE_FILE, {"ui": ui, "files": {}})
        release.write_json(iface, old)
        server._ensure_stats_card(root)
    new = json.loads(iface.read_text(encoding="utf-8"))
    assert [s["name"] for s in new["setting"]] == ["stats", "global"] and release.STATS_OPTION in new["option"]
    assert (new["version"], new["github"], new["agent"]["child_exec"]) == ("v0.1.6.6", "https://example.com", "python/python.exe")
    assert release.interface_digest(new) == release.interface_digest(old)  # 不算改了文件
    text = iface.read_text(encoding="utf-8")
    server._ensure_stats_card(root)
    assert iface.read_text(encoding="utf-8") == text
    release.write_json(root / release.RELEASE_FILE, {"ui": "mfaa", "files": {}})
    release.write_json(iface, old)
    server._ensure_stats_card(root)
    assert json.loads(iface.read_text(encoding="utf-8")) == old
    release.write_json(root / release.RELEASE_FILE, {"ui": "mxu", "files": {}})
    iface.write_text("{", encoding="utf-8")
    server._ensure_stats_card(root)  # 读不了：不动、不报错
    assert iface.read_text(encoding="utf-8") == "{"
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


def test_worker_out_of_memory_message():
    """演奏进程分配不到内存退出（#94）或退出时电脑的内存快用完了：说清楚是内存不够；正常结束、停止不提。"""
    check('''
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch
from ournotes_auto import sysmem
from ournotes_auto.agent import server
from ournotes_auto.agent.worker import WorkerResult
GB = sysmem.GB
PLENTY = sysmem.MemoryStatus(16 * GB, 8 * GB, 32 * GB, 16 * GB)
LOW = sysmem.MemoryStatus(16 * GB, GB // 10, 32 * GB, GB // 2)
ctx = SimpleNamespace(tasker=SimpleNamespace(stopping=False, controller=SimpleNamespace(info={})), get_node_data=lambda name: {"attach": {}})
update = Mock()
update.busy.return_value = nullcontext()

def errors(result, status):
    with patch.object(server, "UPDATE", update), patch.object(server, "run_worker", return_value=result), \
         patch.object(server.sysmem, "memory_status", return_value=status), patch.object(server.logger, "error") as err:
        ok = server.OurNotesRun().run(ctx, SimpleNamespace(custom_action_param='{"task":"idle"}'))
    assert ok is (result.returncode == 0)
    return [c.args[0] % c.args[1:] for c in err.call_args_list]

msgs = errors(WorkerResult(1, False, False, True), PLENTY)
assert msgs[0].startswith("任务结束，退出码 1")
assert msgs[1].startswith("电脑内存不足，演奏进程分配不到内存退出了（内存可用 8.0/16.0 GB，提交 16.0/32.0 GB）：请关掉占内存多的程序"), msgs
assert errors(WorkerResult(1, False, False, True), None)[1].startswith("电脑内存不足，演奏进程分配不到内存退出了：")
assert errors(WorkerResult(1, False, False, False), LOW)[1].startswith("电脑内存快用完了，出错可能与此有关（内存可用 0.1/16.0 GB，提交 31.5/32.0 GB）：")
assert len(errors(WorkerResult(1, False, False, False), PLENTY)) == 1
assert len(errors(WorkerResult(1, False, False, False), None)) == 1
assert errors(WorkerResult(0, False, False, False), LOW) == []
assert errors(WorkerResult(1, True, True, True), LOW) == []  # 停止时被结束
''')


def test_notify_target_not_logged():
    """推送地址等于密码：Agent 的日志里记参数、启动命令时换成 ***，交给演奏进程的还是原样。"""
    check('''
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch
from ournotes_auto.agent import server
from ournotes_auto.agent.worker import WorkerResult
attach = {"notify_channel": "ntfy", "notify_target": "secret-topic_42", "notify_events": "all", "proxy": ""}
ctx = SimpleNamespace(tasker=SimpleNamespace(stopping=False, controller=SimpleNamespace(info={})), get_node_data=lambda name: {"attach": attach})
update = Mock()
update.busy.return_value = nullcontext()
with patch.object(server, "UPDATE", update), patch.object(server, "run_worker", return_value=WorkerResult(0, False, False, False)) as worker, \
     patch.object(server.logger, "debug") as debug:
    assert server.OurNotesRun().run(ctx, SimpleNamespace(custom_action_param='{"task":"notify_test"}')) is True
logged = " ".join(str(c.args[0] % c.args[1:]) for c in debug.call_args_list)
assert "notify_target" in logged and "***" in logged and "secret-topic_42" not in logged, logged
assert json.loads(worker.call_args.args[0][-1])["attach"]["notify_target"] == "secret-topic_42"
assert server._masked({"notify_target": "", "a": 1}) == {"notify_target": "", "a": 1}
''')
