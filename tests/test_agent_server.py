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
    import os, time
    assert 0 <= time.time() - float(os.environ[server.LAUNCHED_ENV]) < 600  # 演奏进程按它判断这次打开程序（#108）
    join.assert_called_once()
    shutdown.assert_called_once()
    update.start_on_launch.side_effect = RuntimeError("boom")  # 查更新出错不影响 Agent
    assert server.main(["test-id"]) == 0
    assert join.call_count == 2
''')


def test_launched_is_ui_start():
    """这次打开界面的时刻取父进程（界面）启动的时刻；父进程读不到、或比 Agent 还晚（PID 被后来的进程占了）时用 Agent 自己的。"""
    check('''
import os, time
from unittest.mock import patch
from ournotes_auto.agent import server
now = time.time()
assert now - 600 < server._started(os.getpid()) <= now
assert server._started(0) is None
times = {os.getpid(): 200.0}
with patch.object(server, "_started", lambda pid: times.get(pid)):
    times[os.getppid()] = 100.0
    assert server._launched() == 100.0
    times[os.getppid()] = 300.0
    assert server._launched() == 200.0
    del times[os.getppid()]
    assert server._launched() == 200.0
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


def test_run_history():
    """记下这次运行里挂机前面的任务（不打歌的、挂机本身不记）；隔了一分钟以上才开始的任务算新的一次运行，
    手动停止、开着重跑的挂机（不限局数，总在最后）结束后清空，限了局数的挂机后面还有任务、不清空。"""
    check('''
from ournotes_auto.agent import params, server
assert server.IDLE_TASKS == params.IDLE_TASKS and set(server.NO_RERUN) <= set(params.TASKS)
now = [1000.0]
h = server.RunHistory(clock=lambda: now[0])
attach = {"song_mode": "random"}
for task in ("start", "records", "clear_lb", "notify_test", "arena_idle"):
    h.begin(task, attach)
    now[0] += 30
    h.end(stopped=False)
    now[0] += 1
attach["song_mode"] = "list"  # 记的是当时的选项
h.begin("idle", {})
assert h.tasks == [("start", {"song_mode": "random"}), ("clear_lb", {"song_mode": "random"})] and h.since == 1000.0
now[0] += 3600
h.end(stopped=False, last=True)  # 出错结束：MFAA 的 Agent 常驻，一分钟内再点开始时前面的任务不重复记
assert h.tasks == []
now[0] += 1
h.begin("clear_lb", {})
assert h.tasks == [("clear_lb", {})] and h.since == now[0]
h.end(stopped=False)
now[0] += server.RUN_GAP_S + 1
h.begin("daily", {})
assert h.tasks == [("daily", {})] and h.since == now[0]
h.end(stopped=True)
assert h.tasks == []
now[0] += 1
h.begin("idle", {})
assert h.tasks == [] and h.since == now[0]
assert server._reruns("idle", {"rerun_daily": True, "max_plays": 0}) and server._reruns("arena_idle", {"rerun_daily": "Yes", "max_plays": "0"})
for task, attach in (
    ("idle", {"rerun_daily": True, "max_plays": "20"}),
    ("idle", {"max_plays": 0}),
    ("idle", {"rerun_daily": True, "max_plays": "x"}),
    ("clear_lb", {"rerun_daily": True, "max_plays": 0}),
):
    assert not server._reruns(task, attach), (task, attach)
''')


RERUN = '''
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch
from ournotes_auto.agent import server
from ournotes_auto.agent.worker import WorkerResult
from ournotes_auto.release import EXIT_RERUN, EXIT_UPDATE
now = [1000.0]
tasker = SimpleNamespace(stopping=False, controller=SimpleNamespace(info={"type": "adb"}))
attaches = {"start": {"touch": "mumu"}, "daily": {"daily_jobs": "all"}, "records": {}, "idle": {"rerun_daily": True}}
current = ["start"]
ctx = SimpleNamespace(tasker=tasker, get_node_data=lambda name: {"attach": attaches[current[0]]})
update = Mock()
update.busy.return_value = nullcontext()
seen = []
results = []

def worker(cmd, cwd, should_stop, should_update):
    seen.append(json.loads(cmd[-1]))
    now[0] += 600
    out = results.pop(0)
    if callable(out):
        out = out()
    return out

def run(task, *outs):
    current[0] = task
    results[:] = outs
    with patch.object(server, "UPDATE", update), patch.object(server, "run_worker", worker), \\
         patch.object(server.sysmem, "memory_status", return_value=None), patch.object(server, "logger") as log:
        ok = server.OurNotesRun().run(ctx, SimpleNamespace(custom_action_param=json.dumps({"task": task})))
    assert not results, results
    msgs = [c.args[0] % c.args[1:] for c in log.mock_calls if c[0] in ("info", "error")]
    now[0] += 5
    return ok, msgs

OK = WorkerResult(0, False, False, False)
FAIL = WorkerResult(1, False, False, False)
RERUN = WorkerResult(EXIT_RERUN, False, False, False)
UPDATED = WorkerResult(EXIT_UPDATE, False, False, False)

def stop():
    tasker.stopping = True
    return WorkerResult(1, True, True, False)

server.HISTORY = server.RunHistory(clock=lambda: now[0])
assert run("start", OK)[0] and run("daily", OK)[0] and run("records", OK)[0]
'''


def test_idle_reruns_earlier_tasks():
    """挂机在日期变更后退出（EXIT_RERUN）：按顺序、用当时的选项重跑前面的任务（不打歌的不跑，出错了接着跑下一个），
    再用同样的选项重新启动挂机，按重跑开始的时刻算下次；手动停止后结束、清空记录。"""
    check(RERUN + '''
ok, msgs = run("idle", RERUN, OK, FAIL, RERUN, OK, OK, stop)
assert ok is False and server.HISTORY.tasks == []
assert [p["task"] for p in seen] == ["start", "daily", "records", "idle", "start", "daily", "idle", "start", "daily", "idle"]
assert [p["attach"] for p in seen[4:6]] == [attaches["start"], attaches["daily"]] and seen[4]["controller"] == {"type": "adb"}
assert [p["rerun_since"] for p in seen if p["task"] == "idle"] == [1000.0, 3415.0, 5215.0]
assert all("rerun_since" not in p for p in seen if p["task"] != "idle")
assert [p.get("continues") for p in seen[3:]] == [None] + [True] * 6  # 重跑的任务、重新启动的挂机接着前面的运行
assert "重跑挂机前面的任务（1/2）" in msgs and msgs.count("重跑挂机前面的任务（2/2）") == 2, msgs
assert any(m.startswith("任务结束，退出码 1") for m in msgs) and msgs[-1] == "已停止", msgs
''')


def test_rerun_stopped():
    """重跑前面的任务时手动停止：不再跑后面的任务、不再启动挂机，清空记录。"""
    check(RERUN + '''
ok, msgs = run("idle", RERUN, stop)
assert ok is False and server.HISTORY.tasks == [] and msgs[-1] == "已停止"
assert [p["task"] for p in seen[3:]] == ["idle", "start"]
''')


def test_idle_without_earlier_tasks():
    """挂机前面没有别的任务（或隔了一分钟以上才开始）：不交 rerun_since，开着开关时说一声；挂机正常结束照常返回。"""
    check(RERUN + '''
now[0] += server.RUN_GAP_S + 1
ok, msgs = run("idle", FAIL)
assert ok is False and "rerun_since" not in seen[-1] and "这次在挂机前面没有运行别的任务，日期变更后不用重跑" in msgs
attaches["idle"] = {}
ok, msgs = run("idle", OK)
assert ok is True and "rerun_since" not in seen[-1] and not any("不用重跑" in m for m in msgs)
assert run("start", RERUN)[0] is False and [p["task"] for p in seen[-1:]] == ["start"]  # 只有挂机会为重跑退出
''')


def test_idle_end_clears_history():
    """开着重跑的挂机出错结束后清空记录：MFAA 的 Agent 常驻，一分钟内再点开始时前面的任务不重复记、不重复重跑；
    限了局数的挂机结束后不清空（后面还有任务）。"""
    check(RERUN + '''
assert run("idle", FAIL)[0] is False and server.HISTORY.tasks == []
since = now[0]
attaches["arena_idle"] = {"rerun_daily": True, "max_plays": "20"}
assert run("start", OK)[0] and run("arena_idle", OK)[0] and server.HISTORY.tasks == [("start", attaches["start"])]
ok, msgs = run("idle", RERUN, OK, stop)
assert [p["task"] for p in seen[3:]] == ["idle", "start", "arena_idle", "idle", "start", "idle"]
assert seen[6]["rerun_since"] == since and "重跑挂机前面的任务（1/1）" in msgs, msgs
''')


def test_rerun_then_hot_update():
    """重跑完重新启动的挂机为热更新退出：等换上新代码，用同样的参数（同一个 rerun_since、接着前面的运行）再启动，不再重跑。"""
    check(RERUN + '''
ok, msgs = run("idle", RERUN, OK, OK, UPDATED, OK)
assert ok is True and update.wait_applied.call_count == 1
assert [p["task"] for p in seen[3:]] == ["idle", "start", "daily", "idle", "idle"]
assert seen[-1] == seen[-2] and seen[-1]["continues"] is True and seen[-1]["rerun_since"] == 3415.0
''')


def test_rerun_update_busy():
    """重跑时有一个任务因为正在更新启动不了：报错，接着跑下一个，再接着挂机。"""
    check(RERUN + '''
update.busy.side_effect = [nullcontext(), server.UpdateBusyError("更新尚未完成"), nullcontext(), nullcontext()]
ok, msgs = run("idle", RERUN, OK, OK)
assert ok is True and [p["task"] for p in seen[3:]] == ["idle", "daily", "idle"]
assert "更新尚未完成" in msgs and "重跑挂机前面的任务（2/2）" in msgs, msgs
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


def test_proxy_password_not_logged():
    """代理地址里的密码（user:pass@host:port）不进 Agent 的日志，交给演奏进程的还是原样。"""
    check('''
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch
from ournotes_auto.agent import server
from ournotes_auto.agent.worker import WorkerResult
attach = {"proxy": "me:pa55w0rd@127.0.0.1:7890"}
ctx = SimpleNamespace(tasker=SimpleNamespace(stopping=False, controller=SimpleNamespace(info={})), get_node_data=lambda name: {"attach": attach})
update = Mock()
update.busy.return_value = nullcontext()
with patch.object(server, "UPDATE", update), patch.object(server, "run_worker", return_value=WorkerResult(0, False, False, False)) as worker, \
     patch.object(server.logger, "debug") as debug:
    assert server.OurNotesRun().run(ctx, SimpleNamespace(custom_action_param='{"task":"start"}')) is True
logged = " ".join(str(c.args[0] % c.args[1:]) for c in debug.call_args_list)
assert "pa55w0rd" not in logged and "网络代理：http://me:***@127.0.0.1:7890" in logged, logged
assert json.loads(worker.call_args.args[0][-1])["attach"]["proxy"] == "me:pa55w0rd@127.0.0.1:7890"
for raw, shown in [("http://me:p@h:1", "http://me:***@h:1"), ("h:7890", "h:7890"), ("http://me@h:1", "http://me@h:1"), (None, None)]:
    assert server._hide_password(raw) == shown, raw
''')
