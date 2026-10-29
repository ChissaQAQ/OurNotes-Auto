"""备份与恢复：导出、导入（合并，这边已有的不动）、坏的备份、打歌任务运行中不导入。"""

import io
import json
import logging
import threading
import time
import zipfile
from dataclasses import asdict

import pytest

from ournotes_auto import backup, release, stats
from ournotes_auto.backup import BackupError, export_bundle, import_bundle, read_source
from ournotes_auto.records import ArenaResult, PlayResult, RecordStore
from ournotes_auto.runner import RunStats
from ournotes_auto.stats import RUNNING, SESSIONS, UNLOCKED, Session, Tracker, load_sessions, session_row

T0 = 1790600000.0  # 2026-09-29
H = 3600.0
STATE = {
    "learned_offset_ms": 4.5,
    "recent_residuals": [0.1, -0.2],
    "chart_offsets": {"1_expert": {"offset_ms": 2.0, "note_speed": 5.0, "residuals": [0.3], "updates": 1}},
    "account": {"com.example": "user_1"},  # 跟账号、这个包有关的不导出
}
OFFSETS = {k: STATE[k] for k in backup.OFFSET_KEYS}
CHART = {"chart_offsets": STATE["chart_offsets"]}


def rec(music_id, t, **kw):
    r = PlayResult(music_id=music_id, difficulty="expert", perfect=500, great=0, good=0, bad=0, miss=0, timestamp=t, **kw)
    return json.dumps(asdict(r), ensure_ascii=False)


def ses(start, hours=1.0, task="挂机"):
    return json.dumps(session_row(Session(start=start, end=start + hours * H, task=task)), ensure_ascii=False)


def make(data, records=(), sessions=(), unlocked=None, state=None):
    data.mkdir(parents=True, exist_ok=True)
    if records:
        (data / "records.jsonl").write_text("".join(r + "\n" for r in records), encoding="utf-8")
    if sessions:
        (data / SESSIONS).write_text("".join(s + "\n" for s in sessions), encoding="utf-8")
    if unlocked is not None:
        (data / UNLOCKED).write_text(json.dumps({"unlocked": unlocked}), encoding="utf-8")
    if state is not None:
        (data / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return data


def bundle(**kw):
    return backup.Bundle(**{"name": "test.zip", **kw})


def files(data):
    return {p.name: p.read_bytes() for p in data.iterdir() if p.is_file()}


@pytest.fixture
def docs(tmp_path, monkeypatch):
    """「文档」换成临时目录，电脑的记号固定；返回备份默认放的文件夹。"""
    monkeypatch.setattr(backup, "documents_dir", lambda: tmp_path / "docs")
    monkeypatch.setattr(backup, "machine_id", lambda: "pc-1")
    return tmp_path / "docs" / backup.APP


def test_round_trip(tmp_path, docs):
    """旧的包导出、新的包导入：演奏记录、运行记录、成就、学到的 offset 都回来；再导入一次不改动。"""
    records = [rec(1, T0), rec(2, T0 + 60, fast=3, slow=1), rec(1, T0 + 120)]
    old = make(tmp_path / "old" / "data", records, [ses(T0 - H), ses(T0 + H, task="重复刷歌")], {"fc_1": T0}, STATE)
    path, b = export_bundle(old, tmp_path / "old")
    assert path.parent == docs and (len(b.records), len(b.sessions), len(b.unlocked)) == (3, 2, 1)
    assert backup.latest(tmp_path / "new") == path

    new = tmp_path / "new" / "data"
    result = import_bundle(new, read_source(path, new))
    assert (result.records, result.known, result.bad, result.sessions, result.achievements, result.offsets) == (3, 0, 0, 2, 1, True)
    assert (new / "records.jsonl").read_text(encoding="utf-8") == (old / "records.jsonl").read_text(encoding="utf-8")
    assert load_sessions(new) == load_sessions(old)
    assert stats.load_unlocked(new / UNLOCKED) == {"fc_1": T0}
    assert json.loads((new / "state.json").read_text(encoding="utf-8")) == OFFSETS
    assert RecordStore(new).learned_offset_ms == 4.5
    assert result.backup.parent == new / backup.SAFETY_DIR
    assert not (new / stats.IMPORT_LOCK).exists()

    before = files(new)
    again = import_bundle(new, read_source(path, new))
    assert (again.records, again.known, again.sessions, again.achievements, again.offsets, again.backup) == (0, 3, 0, 0, False, None)
    assert files(new) == before


def arena(band, t, **kw):
    return json.dumps(asdict(ArenaResult(band=band, placement=1, ap_gain=10, title="春日影", timestamp=t, **kw)), ensure_ascii=False)


def test_arena_round_trip(tmp_path, docs):
    """激奏舞台的记录（arena.jsonl）一起导出、导入，这边有的不再加、读不了的行不要；旧版本导出的备份里没有也照常导入。"""
    old_root = tmp_path / "old"
    old = make(old_root / "data", [rec(1, T0)])
    (old / backup.ARENA).write_text(arena("mygo", T0) + "\n{坏的\n" + arena("avemujica", T0 + 60) + "\n", encoding="utf-8")
    path, b = export_bundle(old, old_root)
    assert len(b.arena) == 2
    with zipfile.ZipFile(path) as z:
        assert backup.ARENA in z.namelist()

    new = make(tmp_path / "new" / "data")
    (new / backup.ARENA).write_text(arena("mygo", T0) + "\n", encoding="utf-8")
    result = import_bundle(new, read_source(path, new))
    assert (result.records, result.arena) == (1, 1)
    assert [r.band for r in RecordStore(new).arena_history(limit=None)] == ["mygo", "avemujica"]
    assert import_bundle(new, read_source(path, new)).backup is None
    assert import_bundle(new, read_source(old_root, new)).backup is None  # 直接选旧的包目录也读 arena.jsonl

    only = make(tmp_path / "only" / "data")  # 只打过激奏舞台、没读到结算页
    (only / backup.ARENA).write_text(arena("mewtype", T0 + 120) + "\n", encoding="utf-8")
    assert import_bundle(new, read_source(only, new)).arena == 1

    manifest = json.dumps({"app": backup.APP, "format": backup.FORMAT, "version": "0.2.0"})
    older = _zip(tmp_path / "v020.zip", {backup.MANIFEST: manifest, "records.jsonl": rec(2, T0) + "\n"})
    result = import_bundle(new, read_source(older, new))
    assert (result.records, result.arena) == (1, 0) and read_source(older, new).arena == []


def test_import_keeps_existing_lines(tmp_path, docs):
    """这边已有的行一字不动（包括读不了的行、结尾没有换行），只在末尾接上没有的；新版本加的字段原样留着。"""
    new = make(tmp_path / "data")
    original = rec(1, T0) + "\n{坏的行\n" + rec(2, T0 + 60)
    (new / "records.jsonl").write_text(original, encoding="utf-8")
    newer = json.dumps({**json.loads(rec(3, T0 - 60)), "new_field": [1, 2]})
    result = import_bundle(new, bundle(records=[rec(2, T0 + 60), newer, "坏的", rec(4, T0 + 30), rec(4, T0 + 30)]))
    assert (result.records, result.known, result.bad) == (2, 2, 1)
    assert (new / "records.jsonl").read_text(encoding="utf-8") == f"{original}\n{newer}\n{rec(4, T0 + 30)}\n"
    assert [r.music_id for r in RecordStore(new).history(limit=None)] == [3, 1, 4, 2]  # 按时间排
    assert (result.backup / "records.jsonl").read_text(encoding="utf-8") == original


def test_import_sessions(tmp_path, docs):
    """运行记录：这边有的（包括正在运行、还没补记的）不再加；备份里同一次运行有几条时留结束得最晚的。"""
    new = make(tmp_path / "data", sessions=[ses(T0)])
    (new / RUNNING).mkdir()
    (new / RUNNING / "1-0.json").write_text(ses(T0 + 5 * H, task="重复刷歌"), encoding="utf-8")
    b = bundle(sessions=[ses(T0), ses(T0 + 5 * H, task="重复刷歌"), ses(T0 + 2 * H), ses(T0 + 2 * H, 1.5), ses(T0 + 2 * H, 0.5), "{"])
    assert import_bundle(new, b).sessions == 1
    first, added = (new / SESSIONS).read_text(encoding="utf-8").splitlines()
    assert first == ses(T0) and json.loads(added)["end"] == T0 + 3.5 * H


def test_import_achievements(tmp_path, docs):
    """成就合在一起，解锁时间按早的。"""
    new = make(tmp_path / "data", unlocked={"a": T0 + 10, "b": T0})
    assert import_bundle(new, bundle(unlocked={"a": T0, "c": T0 + 5})).achievements == 1
    assert stats.load_unlocked(new / UNLOCKED) == {"a": T0, "b": T0, "c": T0 + 5}
    result = import_bundle(new, bundle(unlocked={"b": T0 - 10}))  # 只是解锁时间更早
    assert result.achievements == 0 and result.backup is not None
    assert stats.load_unlocked(new / UNLOCKED)["b"] == T0 - 10


@pytest.mark.parametrize(
    ("machine", "state", "taken"),
    [
        ("pc-1", None, OFFSETS),  # 同一台电脑上新下载的包：都导入
        ("pc-1", {"account": {"a": "b"}}, OFFSETS),  # 别的项不动
        ("pc-2", {}, CHART),  # 别的电脑：只导入按谱面的
        ("", {}, CHART),  # 不知道是哪台电脑导出的
        ("pc-1", {"learned_offset_ms": 1.0}, {}),  # 这边已经学过：不动
        ("pc-1", {"chart_offsets": {}}, {}),
    ],
)
def test_import_offsets(tmp_path, docs, machine, state, taken):
    """学到的 offset 只在这边还没学过时导入；全局的跟电脑、模拟器有关，只在同一台电脑上导入。"""
    new = make(tmp_path / "data", state=state)
    assert import_bundle(new, bundle(offsets=OFFSETS, machine=machine)).offsets == bool(taken)
    after = json.loads((new / "state.json").read_text(encoding="utf-8")) if (new / "state.json").exists() else None
    assert after == ({**(state or {}), **taken} if taken else state)


def test_import_offsets_broken_state(tmp_path, docs):
    """这边的 state.json 读不了：不动它（打歌时会重置）。"""
    new = make(tmp_path / "data")
    (new / "state.json").write_text("{", encoding="utf-8")
    assert not import_bundle(new, bundle(offsets=OFFSETS, machine="pc-1", records=[rec(1, T0)])).offsets
    assert (new / "state.json").read_text(encoding="utf-8") == "{"


def test_no_import_while_task_running(tmp_path, docs):
    """有打歌任务在运行时不导入（它们会同时写演奏记录），什么都不改；结束后能导入。没锁着的是进程被强行结束留下的，不算。"""
    new = make(tmp_path / "data")
    tracker = Tracker(new, "挂机", clock=lambda: T0)
    tracker.start()
    b = bundle(records=[rec(1, T0 - H)])
    try:
        with pytest.raises(BackupError, match="有打歌任务正在运行"):
            import_bundle(new, b)
        assert not (new / "records.jsonl").exists() and not (new / backup.SAFETY_DIR).exists()
    finally:
        tracker.finish(RunStats())
    (new / RUNNING).mkdir(exist_ok=True)
    (new / RUNNING / "9-0.lock").write_bytes(b"")
    assert import_bundle(new, b).records == 1


def test_import_lock(tmp_path, docs, monkeypatch, caplog):
    """导入期间锁着 import.lock：另一个导入不做；打歌任务开始时等它，等太久就不等了。"""
    new = make(tmp_path / "data")
    lock = release.FileLock(new / stats.IMPORT_LOCK)
    assert lock.acquire()
    monkeypatch.setattr(stats, "IMPORT_WAIT_S", 0.3)
    try:
        with pytest.raises(BackupError, match="正在导入另一份备份"):
            import_bundle(new, bundle(records=[rec(1, T0)]))
        t = time.monotonic()
        tracker = Tracker(new, "挂机", clock=lambda: T0)
        with caplog.at_level(logging.INFO, logger="ournotes_auto.stats"):
            tracker.start()
        assert time.monotonic() - t >= 0.3 and "不等了" in caplog.text
        tracker.finish(RunStats())
    finally:
        lock.release()


def test_tracker_waits_for_import(tmp_path, monkeypatch, caplog):
    """打歌任务开始时正在导入：导入完成后马上开始。"""
    new = make(tmp_path / "data")
    lock = release.FileLock(new / stats.IMPORT_LOCK)
    assert lock.acquire()
    monkeypatch.setattr(stats, "IMPORT_WAIT_S", 10.0)
    threading.Timer(0.3, lock.release).start()
    t = time.monotonic()
    tracker = Tracker(new, "挂机", clock=lambda: T0)
    with caplog.at_level(logging.INFO, logger="ournotes_auto.stats"):
        tracker.start()
    assert 0.3 <= time.monotonic() - t < 5
    assert "导入完成后开始" in caplog.text and "不等了" not in caplog.text
    tracker.finish(RunStats())


def test_import_safety_copies(tmp_path, docs, monkeypatch):
    """每次导入前先把要改的文件复制一份，留最近 5 次；同一秒里导入了好多次也是（删掉的名字不再用，按次序删）。"""
    monkeypatch.setattr(backup.time, "strftime", lambda fmt, *args: "20261026-120000")
    new = make(tmp_path / "data", [rec(0, T0)])
    made = [import_bundle(new, bundle(records=[rec(i, T0 + i)])).backup for i in range(1, 13)]
    assert [p.name for p in made[::5]] == ["20261026-120000", "20261026-120000-6", "20261026-120000-11"]
    assert set((new / backup.SAFETY_DIR).iterdir()) == set(made[-5:])
    assert (made[-5] / "records.jsonl").read_text(encoding="utf-8").splitlines() == [rec(i, T0 + i) for i in range(8)]


def test_import_write_failure_restores(tmp_path, docs, monkeypatch):
    """写到一半出错：已经写了的还原（原来没有的删掉），报错，不留锁。"""
    new = make(tmp_path / "data", sessions=[ses(T0)])
    original = (new / SESSIONS).read_bytes()
    write, calls = release.write_text, []

    def flaky(path, text):
        calls.append(path.name)
        if len(calls) == 2:
            raise PermissionError("文件被占用")
        write(path, text)

    monkeypatch.setattr(release, "write_text", flaky)
    with pytest.raises(BackupError, match="已还原"):
        import_bundle(new, bundle(records=[rec(1, T0)], sessions=[ses(T0 + H)]))
    assert calls == ["records.jsonl", SESSIONS]
    assert not (new / "records.jsonl").exists() and (new / SESSIONS).read_bytes() == original
    assert not (new / stats.IMPORT_LOCK).exists()


def _zip(path, members):
    with zipfile.ZipFile(path, "w") as z:
        for name, text in members.items():
            z.writestr(name, text)
    return path


def test_rejects_bad_backups(tmp_path, docs):
    """别的程序的 zip、新版本导出的、坏了的都不导入，说清楚原因；备份里多出来的文件不管。"""
    new = tmp_path / "data"
    manifest = json.dumps({"app": backup.APP, "format": backup.FORMAT + 1, "version": "9.9"})
    cases = [
        (_zip(tmp_path / "newer.zip", {backup.MANIFEST: manifest}), "新版本（v9.9）导出的"),
        (_zip(tmp_path / "other.zip", {"records.jsonl": rec(1, T0)}), "不是 OurNotes-Auto 的备份"),
        (_zip(tmp_path / "maa.zip", {backup.MANIFEST: json.dumps({"app": "MAA"})}), "不是 OurNotes-Auto 的备份"),
        (_zip(tmp_path / "broken.zip", {backup.MANIFEST: "{"}), "不是 OurNotes-Auto 的备份"),
    ]
    (tmp_path / "text.zip").write_text("这不是 zip", encoding="utf-8")
    cases.append((tmp_path / "text.zip", "已经损坏"))

    old = make(tmp_path / "old", [rec(i, T0 + i) for i in range(20)])
    path, _ = export_bundle(old, tmp_path)
    raw = bytearray(path.read_bytes())
    (tmp_path / "cut.zip").write_bytes(raw[: len(raw) // 2])
    cases.append((tmp_path / "cut.zip", "已经损坏"))
    with zipfile.ZipFile(path) as z:
        info = z.getinfo("records.jsonl")
    start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra) + 20  # 压缩数据中间
    raw[start : start + 8] = bytes(b ^ 0xFF for b in raw[start : start + 8])
    (tmp_path / "flipped.zip").write_bytes(raw)
    cases.append((tmp_path / "flipped.zip", "已经损坏"))
    cases.append((tmp_path / "nope.zip", "找不到"))
    for src, match in cases:
        with pytest.raises(BackupError, match=match):
            read_source(src, new)
    assert not new.exists()

    with zipfile.ZipFile(path, "a") as z:
        z.writestr("../evil.txt", "x")
        z.writestr("extra/new.json", "{}")
    assert len(read_source(path, new).records) == 20


def test_import_from_old_package_dir(tmp_path, docs):
    """直接选旧的包目录或它的 data 文件夹也行（不用先导出，算同一台电脑），旧的包里没补记的运行也算上；
    选到这个包自己的不导入。"""
    old_root = tmp_path / "old"
    old = make(old_root / "data", [rec(1, T0)], unlocked={"a": T0}, state=STATE)
    (old / RUNNING).mkdir()
    (old / RUNNING / "1-0.json").write_text(ses(T0 + H), encoding="utf-8")  # 挂机时被强行结束，还没补记
    new_root = tmp_path / "new"
    new = new_root / "data"
    for src in (old_root, old):
        b = read_source(src, new)
        assert (len(b.records), len(b.sessions), b.unlocked, b.machine) == (1, 1, {"a": T0}, "pc-1")
    result = import_bundle(new, read_source(old_root, new))
    assert (result.records, result.sessions, result.achievements, result.offsets) == (1, 1, 1, True)
    assert json.loads((new / "state.json").read_text(encoding="utf-8")) == OFFSETS
    for src in (new_root, new):
        with pytest.raises(BackupError, match="这个包自己的记录"):
            read_source(src, new)
    (tmp_path / "empty").mkdir()
    with pytest.raises(BackupError, match="没有演奏记录"):
        read_source(tmp_path / "empty", new)


def test_export(tmp_path, docs):
    """没有记录时不导出；「文档」里写不了时放在包目录的 backup 里；默认位置里取最新的一份。"""
    root = tmp_path / "pkg"
    data = make(root / "data")
    with pytest.raises(BackupError, match="还没有演奏记录"):
        export_bundle(data, root)
    make(data, [rec(1, T0)])
    (tmp_path / "docs").write_text("不是文件夹", encoding="utf-8")
    path, _ = export_bundle(data, root, now=T0)
    assert path.parent == root / backup.FALLBACK_DIR and backup.latest(root) == path
    assert not list(path.parent.glob("*.part"))
    with zipfile.ZipFile(path) as z:
        assert z.namelist() == [backup.MANIFEST, "records.jsonl", SESSIONS, UNLOCKED]  # 没学过 offset
        manifest = json.loads(z.read(backup.MANIFEST))
    assert (manifest["app"], manifest["format"], manifest["exported"], manifest["machine"]) == (backup.APP, backup.FORMAT, round(T0), "pc-1")

    (tmp_path / "docs").unlink()
    newer, _ = export_bundle(data, root, now=T0 + 60)
    assert newer.parent == docs and backup.latest(root) == newer
    other = tmp_path / "out"
    assert export_bundle(data, root, other, now=T0)[0].parent == other


def test_window_export_preview(tmp_path, docs):
    """窗口里导出成内存里的 zip、读选的文件的内容；预览什么都不写，数和真的导入一样，导入过再预览没有改动。"""
    old = make(tmp_path / "old", [rec(1, T0), rec(2, T0 + 60)], [ses(T0 - H)], {"fc_1": T0}, STATE)
    name, blob, b = backup.export_bytes(old, now=T0)
    assert name == backup.NAME.format(time.strftime("%Y%m%d-%H%M%S", time.localtime(T0)))
    assert (len(b.records), len(b.sessions), len(b.unlocked)) == (2, 1, 1)
    got = backup.read_zip(io.BytesIO(blob), "选的.zip")
    assert (got.name, got.records, got.sessions, got.unlocked, got.machine) == ("选的.zip", b.records, b.sessions, b.unlocked, "pc-1")

    new = make(tmp_path / "new", [rec(1, T0)], unlocked={"fc_1": T0 - 60})
    before = files(new)
    p = backup.preview(new, got)
    assert (p.records, p.known, p.sessions, p.achievements, p.offsets, p.changed, p.backup) == (1, 1, 1, 0, True, True, None)
    assert files(new) == before and not (new / backup.SAFETY_DIR).exists()
    r = import_bundle(new, got)
    assert (r.records, r.known, r.sessions, r.achievements, r.offsets, r.changed) == (1, 1, 1, 0, True, True)
    assert r.backup is not None and r.backup.is_dir()
    again = backup.preview(new, got)
    assert (again.records, again.known, again.changed) == (0, 2, False)

    with pytest.raises(BackupError, match="坏的.zip 不是 OurNotes-Auto 的备份"):
        backup.read_zip(io.BytesIO(b"PK not a zip"), "坏的.zip")
    with pytest.raises(BackupError, match="还没有演奏记录"):
        backup.export_bytes(make(tmp_path / "empty"))


@pytest.fixture
def cli_main(monkeypatch):
    """调 cli.main：不读曲目目录（不联网），跑完撤掉加上的日志处理器。"""
    from ournotes_auto import cli

    root, added = logging.getLogger(), []
    level, setup = root.level, cli._setup_logging

    def setup_logging(*args, **kw):
        before = list(root.handlers)
        setup(*args, **kw)
        added.extend(h for h in root.handlers if h not in before)

    monkeypatch.setattr(cli, "_setup_logging", setup_logging)
    monkeypatch.setattr(cli, "_library", lambda cfg: stats.Library())
    yield cli.main
    for handler in added:
        root.removeHandler(handler)
        handler.close()
    root.setLevel(level)


def test_cli(tmp_path, docs, monkeypatch, cli_main, capsys):
    """命令行：在旧的包里导出，在新的包里导入（不指定时用最新的备份）。"""
    old_root, new_root = tmp_path / "old", tmp_path / "new"
    make(old_root / "data", [rec(1, T0), rec(2, T0 + 60)], [ses(T0)])
    new_root.mkdir()
    monkeypatch.chdir(old_root)
    assert cli_main(["backup", "export"]) == 0
    [path] = docs.glob(backup.PATTERN)
    monkeypatch.chdir(new_root)
    assert cli_main(["backup", "import"]) == 0
    assert len(RecordStore(new_root / "data").history(limit=None)) == 2
    assert cli_main(["backup", "import", str(path)]) == 0
    err = capsys.readouterr().err
    assert "已从 " + path.name + " 导入2 局演奏记录、1 次运行记录" in err and "这边都有了" in err
    assert cli_main(["backup", "import", "nope.zip"]) == 1
    path.unlink()
    assert cli_main(["backup", "import"]) == 1
    assert "没找到备份" in capsys.readouterr().err
