"""备份与恢复：演奏记录（含激奏舞台的）、运行记录、成就和学到的 offset 导出成一个 zip，在新下载的包里导入（合并，不覆盖）。

「统计与成就」窗口里的「备份与恢复」导出时由用户选存到哪里（见 stats_server.py）；命令行导出默认放在
「文档\\OurNotes-Auto」（删掉旧的包也还在），导入时不指定来源就用那里最新的一份。
也能直接从旧的包目录（或它的 data 文件夹）导入。招募截图（``data/recruit``）不在备份里。

导入时这边已有的记录一行不动，只在末尾接上没有的；改动前先把要改的文件复制到 ``data/import_backup``，
写到一半出错就还原。有打歌任务在运行时不导入（它们会同时写演奏记录）。
"""

from __future__ import annotations

import ctypes
import hashlib
import io
import json
import os
import shutil
import time
import zipfile
import zlib
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from . import __version__, release, stats
from .records import parse_arena, parse_result

APP = "OurNotes-Auto"
FORMAT = 1  # 备份的格式，改了就加 1；比自己新的不导入（要先更新）
MANIFEST = "manifest.json"  # 备份里：格式、导出的版本和时间、电脑的记号
RECORDS = "records.jsonl"
ARENA = "arena.jsonl"  # 激奏舞台各局的名次、段位分（旧版本导出的备份里没有）
STATE = "state.json"
OFFSETS = "offsets.json"  # 备份里：state.json 里学到的 offset
OFFSET_KEYS = ("learned_offset_ms", "recent_residuals", "chart_offsets")
GLOBAL_KEYS = ("learned_offset_ms", "recent_residuals")  # 跟电脑、模拟器有关，只在同一台电脑上导入
NAME = "OurNotes-Auto-backup-{}.zip"
PATTERN = "OurNotes-Auto-backup-*.zip"
FALLBACK_DIR = "backup"  # 「文档」里写不了（如开着 Windows 安全中心的「受控文件夹访问」）时放在包目录里
SAFETY_DIR = "import_backup"  # data 里：导入前的数据，留最近 SAFETY_KEEP 次
SAFETY_KEEP = 5
MAX_BYTES = 256 * 1024 * 1024  # 备份里每个文件解压后最多多大：几十万局也不到这么多
MAX_UPLOAD = 128 * 1024 * 1024  # 窗口里选的备份文件最多多大（压缩后的，几十万局也就几十 MB）


class BackupError(Exception):
    """备份 / 导入失败，消息直接显示给用户。"""


@dataclass
class Bundle:
    """一份备份的内容（导入前全部读进内存、检查过）。"""

    records: list[str] = field(default_factory=list)  # records.jsonl 的各行
    arena: list[str] = field(default_factory=list)  # arena.jsonl 的各行
    sessions: list[str] = field(default_factory=list)  # sessions.jsonl 的各行
    unlocked: dict[str, float] = field(default_factory=dict)
    offsets: dict = field(default_factory=dict)  # OFFSET_KEYS 里有的
    machine: str = ""  # 导出时那台电脑的记号（machine_id）
    name: str = ""  # 显示用


@dataclass
class Result:
    """导入了多少。"""

    records: int = 0
    known: int = 0  # 这边已经有的局
    bad: int = 0  # 读不了的行
    arena: int = 0  # 激奏舞台的局（演奏了的同时也算在 records 里）
    sessions: int = 0
    achievements: int = 0
    offsets: bool = False
    changed: bool = False  # 有要改的（包括只是成就的解锁时间改早）
    backup: Path | None = None  # 导入前的数据复制到了哪里；没有改动、只是预览时为 None


def machine_id() -> str:
    """这台电脑的记号（Windows 安装时生成的 MachineGuid 的散列，看不出原值）；读不到为空。"""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            guid = str(winreg.QueryValueEx(key, "MachineGuid")[0])
    except (ImportError, OSError):
        return ""
    return hashlib.sha256(f"ournotes-auto/{guid}".encode()).hexdigest()[:16]


def documents_dir() -> Path:
    """「文档」文件夹（可能被移到别的盘或 OneDrive 里）。"""
    with suppress(AttributeError, OSError):  # 不是 Windows
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:  # CSIDL_PERSONAL
            return Path(buf.value)
    return Path.home() / "Documents"


def backup_dirs(root: Path) -> list[Path]:
    """备份默认放的地方，先试前面的。``root`` 是包目录。"""
    return [documents_dir() / APP, root / FALLBACK_DIR]


def latest(root: Path) -> Path | None:
    """默认位置里最新的备份。"""
    found = [p for d in backup_dirs(root) for p in d.glob(PATTERN) if p.is_file()]
    return max(found, key=lambda p: p.name, default=None)  # 文件名里是导出时间


def _lines(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def _record_key(line: str) -> tuple | None:
    """一局的记号（曲目、难度、时间）；读不了的行为 None。"""
    try:
        r = parse_result(line)
        key = (r.music_id, r.difficulty, round(r.timestamp, 3))
        hash(key)
    except (TypeError, ValueError, AttributeError):
        return None
    return key


def _arena_key(line: str) -> tuple | None:
    """激奏舞台一局的记号（时间、乐队、曲名）；读不了的行为 None。"""
    try:
        r = parse_arena(line)
        key = (round(r.timestamp, 3), r.band, r.title)
        hash(key)
    except (TypeError, ValueError, AttributeError):
        return None
    return key


def _session_key(line: str) -> tuple | None:
    try:
        s = stats.parse_session(json.loads(line))
        if s is None:
            return None
        key = stats.session_key(s)
        hash(key)
    except (TypeError, ValueError):
        return None
    return key


def _offsets(state) -> dict:
    """学到的 offset 那几项（类型不对的不要）。"""
    if not isinstance(state, dict):
        return {}
    out = {}
    if isinstance(state.get("learned_offset_ms"), (int, float)) and not isinstance(state["learned_offset_ms"], bool):
        out["learned_offset_ms"] = state["learned_offset_ms"]
    if isinstance(state.get("recent_residuals"), list):
        out["recent_residuals"] = state["recent_residuals"]
    if isinstance(state.get("chart_offsets"), dict):
        out["chart_offsets"] = {k: v for k, v in state["chart_offsets"].items() if isinstance(v, dict)}
    return out


def _read_state(path: Path):
    """``state.json`` 的内容；没有为 {}，读不了为 None。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None


def read_dir(data: Path) -> Bundle:
    """一个 data 文件夹里的记录。正在运行、或进程被强行结束还没补记的运行（``data/running``）也算上。"""
    sessions = _lines(data / stats.SESSIONS)
    sessions += [json.dumps(stats.session_row(s), ensure_ascii=False) for _, s in stats.running_files(data) if s is not None]
    return Bundle(
        records=[line for line in _lines(data / RECORDS) if _record_key(line) is not None],
        arena=[line for line in _lines(data / ARENA) if _arena_key(line) is not None],
        sessions=sessions,
        unlocked=stats.load_unlocked(data / stats.UNLOCKED) or {},
        offsets=_offsets(_read_state(data / STATE)),
        machine=machine_id(),
        name=str(data),
    )


def _prepare(data: Path, now: float | None) -> tuple[Bundle, dict, str]:
    """要导出的内容、manifest 和文件名。"""
    b = read_dir(data)
    if not (b.records or b.arena or b.sessions or b.unlocked):
        raise BackupError("还没有演奏记录，不用备份")
    t = time.time() if now is None else now
    manifest = {"format": FORMAT, "app": APP, "version": __version__, "exported": round(t), "machine": b.machine}
    return b, manifest, NAME.format(time.strftime("%Y%m%d-%H%M%S", time.localtime(t)))


def export_bytes(data: Path, now: float | None = None) -> tuple[str, bytes, Bundle]:
    """导出成内存里的 zip（窗口里的「导出」按钮用，存到哪里由用户在浏览器里选），返回 (文件名, 内容, 导出的记录)。"""
    b, manifest, name = _prepare(data, now)
    buf = io.BytesIO()
    _fill_zip(buf, manifest, b)
    return name, buf.getvalue(), b


def export_bundle(data: Path, root: Path, out_dir: Path | None = None, now: float | None = None) -> tuple[Path, Bundle]:
    """把 ``data`` 里的记录导出成备份，返回 (备份文件, 内容)。``out_dir`` 没给时放在默认位置（见 :func:`backup_dirs`）。"""
    b, manifest, name = _prepare(data, now)
    error: OSError | None = None
    for d in [out_dir] if out_dir is not None else backup_dirs(root):
        try:
            return _write_zip(d / name, manifest, b), b
        except OSError as e:
            error = e
    raise BackupError(f"写不了备份文件：{error}")


def _write_zip(path: Path, manifest: dict, b: Bundle) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")  # 写完再改名：中途出错不会留下坏的备份
    try:
        _fill_zip(part, manifest, b)
        os.replace(part, path)
    except BaseException:
        with suppress(OSError):
            part.unlink()
        raise
    return path


def _fill_zip(target: Path | BinaryIO, manifest: dict, b: Bundle) -> None:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
        z.writestr(RECORDS, "".join(line + "\n" for line in b.records))
        if b.arena:
            z.writestr(ARENA, "".join(line + "\n" for line in b.arena))
        z.writestr(stats.SESSIONS, "".join(line + "\n" for line in b.sessions))
        z.writestr(stats.UNLOCKED, json.dumps({"unlocked": b.unlocked}, ensure_ascii=False, indent=2))
        if b.offsets:
            z.writestr(OFFSETS, json.dumps(b.offsets, ensure_ascii=False, indent=2))


def _member(z: zipfile.ZipFile, name: str) -> str | None:
    """备份里的一个文件（没有为 None）。zip 自带校验，内容坏了读的时候会报错。"""
    try:
        info = z.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_BYTES:
        raise BackupError(f"备份里的 {name} 太大，不是正常的备份")
    with z.open(info) as f:
        data = f.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise BackupError(f"备份里的 {name} 太大，不是正常的备份")
    return data.decode("utf-8")


def read_zip(src: Path | BinaryIO, name: str = "") -> Bundle:
    """读一份导出的备份。``src`` 是文件，或者窗口里选的文件的内容（这时 ``name`` 是显示用的文件名）。"""
    name = name or Path(src).name
    try:
        with zipfile.ZipFile(src) as z:
            try:
                manifest = json.loads(_member(z, MANIFEST) or "null")
            except ValueError:
                manifest = None
            if not isinstance(manifest, dict) or manifest.get("app") != APP:
                raise BackupError(f"{name} 不是 OurNotes-Auto 的备份")
            fmt = manifest.get("format")
            if not isinstance(fmt, int) or fmt > FORMAT:
                raise BackupError(f"{name} 是新版本（v{manifest.get('version', '?')}）导出的，先把 OurNotes-Auto 更新到这个版本或更新的版本再导入")
            records, arena, sessions, unlocked, offsets = (
                _member(z, n) for n in (RECORDS, ARENA, stats.SESSIONS, stats.UNLOCKED, OFFSETS)
            )
    except (zipfile.BadZipFile, zlib.error, UnicodeDecodeError, EOFError, NotImplementedError) as e:
        raise BackupError(f"{name} 不是 OurNotes-Auto 的备份，或者已经损坏（{e}）") from e
    try:
        offsets_data = json.loads(offsets) if offsets else {}
    except ValueError:
        offsets_data = {}
    split = lambda text: [line.strip() for line in (text or "").splitlines() if line.strip()]  # noqa: E731
    return Bundle(
        records=split(records),
        arena=split(arena),
        sessions=split(sessions),
        unlocked=(stats.parse_unlocked(unlocked) if unlocked else None) or {},
        offsets=_offsets(offsets_data),
        machine=str(manifest.get("machine") or ""),
        name=name,
    )


def read_source(src: Path, data: Path) -> Bundle:
    """``src`` 是导出的备份文件、旧的包目录或它的 data 文件夹。"""
    if src.is_dir():
        source = src / "data" if (src / "data").is_dir() else src
        if source.resolve() == data.resolve():
            raise BackupError("选的是这个包自己的记录，不用导入")
        if not any((source / n).exists() for n in (RECORDS, ARENA, stats.SESSIONS, stats.UNLOCKED)):
            raise BackupError(f"{src} 里没有演奏记录：要选导出的备份文件、旧的包目录或它的 data 文件夹")
        return read_dir(source)  # 从这台电脑上的文件夹导入：算同一台电脑
    if not src.is_file():
        raise BackupError(f"找不到 {src}")
    return read_zip(src)


def _check_running(data: Path) -> None:
    if any(release.is_locked(p) for p in (data / stats.RUNNING).glob("*.lock")):
        raise BackupError("有打歌任务正在运行，先停止再导入（导入要改演奏记录）")


def preview(data: Path, b: Bundle) -> Result:
    """导入的话会导入多少（什么都不写，``backup`` 为 None）。有打歌任务在运行时同 :func:`import_bundle` 报错。"""
    _check_running(data)
    return _merge(data, b)[0]


def import_bundle(data: Path, b: Bundle) -> Result:
    """把备份合并进 ``data``。正在导入别的、有打歌任务在运行时不导入。"""
    lock = release.FileLock(data / stats.IMPORT_LOCK)
    if not lock.acquire():
        raise BackupError("正在导入另一份备份，等它完成再试")
    try:
        # 先锁上再看：打歌任务开始时先锁住自己的文件再等导入（见 stats.Tracker），两边不会都以为对方没在运行
        _check_running(data)
        result, files = _merge(data, b)
        if files:
            result.backup = _write(data, files)
        return result
    finally:
        lock.release()
        with suppress(OSError):
            (data / stats.IMPORT_LOCK).unlink()


def _merge(data: Path, b: Bundle) -> tuple[Result, dict[str, str]]:
    """算出导入后的各文件内容（只有要改的），这时还不写。"""
    result, files = Result(), {}

    text = _text(data / RECORDS)
    have = {k for k in map(_record_key, text.splitlines()) if k is not None}
    new = []
    for line in b.records:
        key = _record_key(line)
        if key is None:
            result.bad += 1
        elif key in have:
            result.known += 1
        else:
            have.add(key)
            new.append(line)
    if new:
        files[RECORDS] = _append(text, new)
        result.records = len(new)

    text = _text(data / ARENA)
    have = {k for k in map(_arena_key, text.splitlines()) if k is not None}
    new = []
    for line in b.arena:
        key = _arena_key(line)
        if key is not None and key not in have:
            have.add(key)
            new.append(line)
    if new:
        files[ARENA] = _append(text, new)
        result.arena = len(new)

    text = _text(data / stats.SESSIONS)
    have = {stats.session_key(s) for s in stats.load_sessions(data)}
    have |= {stats.session_key(s) for _, s in stats.running_files(data) if s is not None}
    pending: dict[tuple, tuple[float, str]] = {}  # 备份里同一次运行有几条时（补记和结束时的记录）留最后的
    for line in b.sessions:
        key = _session_key(line)
        if key is None or key in have:
            continue
        end = json.loads(line).get("end", 0)
        end = end if isinstance(end, (int, float)) else 0
        if key not in pending or end >= pending[key][0]:
            pending[key] = (end, line)
    if pending:
        files[stats.SESSIONS] = _append(text, [line for _, line in pending.values()])
        result.sessions = len(pending)

    stored = stats.load_unlocked(data / stats.UNLOCKED)
    unlocked = dict(stored or {})
    for k, v in b.unlocked.items():
        if k not in unlocked:
            result.achievements += 1
        if v < unlocked.get(k, v + 1):  # 解锁时间按早的
            unlocked[k] = v
    if unlocked != (stored or {}):
        files[stats.UNLOCKED] = json.dumps({"unlocked": unlocked}, ensure_ascii=False, indent=2)

    # 学到的 offset：只在这边还没学过时导入；全局的跟电脑、模拟器有关，只在同一台电脑上导入
    state = _read_state(data / STATE)
    if isinstance(state, dict) and not any(k in state for k in ("learned_offset_ms", "chart_offsets")):
        same = bool(b.machine) and b.machine == machine_id()
        take = {k: v for k, v in b.offsets.items() if k not in GLOBAL_KEYS or same}
        if take:
            files[STATE] = json.dumps({**state, **take}, ensure_ascii=False, indent=2)
            result.offsets = True
    result.changed = bool(files)
    return result, files


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _append(text: str, lines: list[str]) -> str:
    """原来的内容一字不动，后面接上新的行。"""
    if text and not text.endswith("\n"):
        text += "\n"
    return text + "".join(line + "\n" for line in lines)


def _write(data: Path, files: dict[str, str]) -> Path:
    """先把要改的文件复制一份，再逐个写；写到一半出错就还原。返回复制到的目录。"""
    base = data / SAFETY_DIR
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dirs = [p for p in base.iterdir() if p.is_dir()] if base.is_dir() else []
    n = max((k for s, k in (_order(p) for p in dirs) if s == stamp), default=0) + 1  # 删掉的名字不再用
    backup = base / (stamp if n == 1 else f"{stamp}-{n}")
    try:
        backup.mkdir(parents=True)
        for name in files:
            if (data / name).exists():
                shutil.copy2(data / name, backup / name)
    except OSError as e:
        raise BackupError(f"导入前备份现有记录失败，没有导入：{e}") from e
    done = []
    try:
        for name, text in files.items():
            release.write_text(data / name, text)
            done.append(name)
    except OSError as e:
        for name in done:
            with suppress(OSError):
                if (backup / name).exists():
                    shutil.copy2(backup / name, data / name)
                else:
                    (data / name).unlink()
        raise BackupError(f"写入失败，已还原（{e}）") from e
    for old in sorted((p for p in base.iterdir() if p.is_dir()), key=_order)[:-SAFETY_KEEP]:
        if old != backup:  # 电脑时间往回调过时，这次的可能排在前面
            shutil.rmtree(old, ignore_errors=True)
    return backup


def _order(path: Path) -> tuple[str, int]:
    """导入前的数据的目录名 ``<时间>`` 或 ``<时间>-<n>``（同一秒里的第 n 次）→ 先后。"""
    day, _, rest = path.name.partition("-")
    sec, _, n = rest.partition("-")
    return f"{day}-{sec}", int(n) if n.isdigit() else 1
