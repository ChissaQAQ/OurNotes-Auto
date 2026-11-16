"""发布包的组成与自动更新的约定：文件清单、分档规则、interface.json 的转换、锁文件。

打包（tools/install.py、tools/make_update.py）、Agent 和更新器（updater.py）共用。只用标准库：
更新器在 cache/update/runner/ 下用复制出来的嵌入式 Python 单独运行，没有第三方库。

更新分三档（:func:`tier`）：

- ``hot``：只改了演奏进程的代码或文档。没有任务在跑时替换文件，下个任务直接用新代码。
- ``restart``：改了界面启动时读的文件（interface.json、tasks、locales、resource）或 Agent 进程加载的代码（:data:`SHELL`）。
  替换后要重启界面。
- ``full``：运行环境（界面程序、Python、依赖、OCR 模型、打包方式）变了，下载整包替换。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import zipfile
from collections.abc import Iterable, Mapping
from contextlib import suppress
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote

REPO = "ChissaQAQ/OurNotes-Auto"
DOWNLOAD_URL = f"https://github.com/{REPO}/releases/latest"  # 跳转到最新正式发布的，不含草稿和预发布
# 最新正式发布的版本号做成的图片（shields.io）：MXU 设置页的卡片里用，界面打开设置页时自己联网取，不用运行任务
BADGE_URL = f"https://img.shields.io/github/v/release/{REPO}?label={quote('最新版本')}&color=7c5cff"
UPDATE_ASSET = "update.json"  # 每个 Release 附带的更新清单（tools/make_update.py 生成）
RELEASE_FILE = "release.json"  # 发布包根目录里：版本、界面种类、运行环境指纹、文件清单
FORMAT = 1  # release.json / update.json 的格式
LAYOUT = 1  # 打包方式（嵌入式 Python 的 ._pth 等）变了就加 1，旧包只能整包更新
AGENT_EXEC = "./python/python.exe"
AGENT_ARGS = ("./agent/main.py",)
UI_EXE = {"mfaa": "MFAAvalonia.exe", "mxu": "mxu.exe"}
USER_DIRS = frozenset({"config", "data", "cache", "debug"})  # 用户数据和界面的日志、缓存，更新时不动
APP_DIRS = ("agent", "ournotes_auto", "tasks", "locales", "docs", "resource")
APP_TOP_FILES = ("interface.json", "LICENSE", "TERMS_OF_SERVICE.md", "THIRD_PARTY_NOTICES.md", "config.example.yaml")
APP_TOP_PATTERN = re.compile(r"README[^/]*\.md")
MODEL_DIR = "resource/model"  # OCR 模型体积大、很少变，算运行环境（整包更新）
# 随整包附带的谱面（约 7 MB，tools/fetch_bundle.py 生成，程序里是 charts/bdon.py 的 BUNDLE）。只在整包里：不算 app 层
# （自动更新不下载它、也不动它），也不算运行环境（它变了不用整包更新）。安装目录里没有、比谱面站旧都不要紧，
# 缺的、旧的谱面程序照常从谱面站下载
BUNDLE_DIR = "ournotes_auto/charts/bundle"
# Agent 进程加载的本项目文件。MFAA 的 Agent 常驻，这些改了要重启界面才生效；tests/test_release.py 检查 Agent
# 实际导入的模块都在这里。ournotes_auto/__init__.py 只有版本号（测试检查），Agent 用旧的版本号不影响运行，不算。
SHELL = frozenset(
    {
        "agent/main.py",
        "ournotes_auto/agent/__init__.py",
        "ournotes_auto/agent/logs.py",
        "ournotes_auto/agent/server.py",
        "ournotes_auto/agent/update.py",
        "ournotes_auto/agent/worker.py",
        "ournotes_auto/logfile.py",
        "ournotes_auto/release.py",
        "ournotes_auto/sysmem.py",
    }
)
RESTART_PREFIXES = ("tasks/", "locales/", "resource/")  # 界面启动或加载资源时读，另外还有 interface.json

# 更新器和 Agent 之间的约定（相对包根目录）
UPDATE_DIR = "cache/update"
BUSY_DIR = f"{UPDATE_DIR}/busy"  # Agent 在演奏进程运行期间锁住 busy/<pid>.lock
APPLY_LOCK = f"{UPDATE_DIR}/apply.lock"  # 更新器替换文件期间锁住；Agent 启动演奏进程前等它
UPDATER_LOCK = f"{UPDATE_DIR}/updater.lock"  # 更新器运行期间锁住，同时只有一个
STATUS_FILE = f"{UPDATE_DIR}/status.json"  # 更新器的进度，Agent 读了在界面日志里提示
MANIFEST_FILE = f"{UPDATE_DIR}/manifest.json"  # Agent 下载的 update.json（带检查时间），更新器按它更新
FAILED_FILE = f"{UPDATE_DIR}/failed.json"  # 更新失败、已经还原的版本，不再自动重试
AUTO_OFF_FILE = f"{UPDATE_DIR}/auto_off"  # 关掉「检查更新」或「自动更新」时建立，正在等待的更新器看到后退出
CHECK_OFF_FILE = f"{UPDATE_DIR}/check_off"  # 关掉「检查更新」时建立（MXU 设置页的卡片上说明）
CHECK_ERROR_FILE = f"{UPDATE_DIR}/check_error.json"  # 最近一次检查更新没查到的时间和原因；查到了就删掉
CHECK_TTL_S = 600  # 检查更新的结果缓存多久：MXU 每次开始任务都启动新的 Agent，这么久内查过就不再访问 GitHub
UPDATE_CARD = "data/update_mxu.md"  # MXU 设置页「更新」一组里「版本状态」卡片的内容（update_card）
EXIT_UPDATE = 75  # 演奏进程为了热更新在安全点退出的退出码，Agent 看到后用同样的参数重新启动它
EXIT_RERUN = 76  # 挂机在游戏日期变更后退出的退出码，Agent 看到后按顺序重跑挂机前面的任务，再用同样的参数重新启动它
_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?")


def parse_version(text: str | None) -> tuple[int, ...] | None:
    """``v1.2.3`` / ``1.2.3`` → (1, 2, 3)，小修补 ``v1.2.3.1`` → (1, 2, 3, 1)（比 v1.2.3 新、比 v1.2.4 旧）；开发版等其他格式返回 None。

    v0.1.5 及以前的版本只认三段，四段的版本号要等用户更新到 v0.1.6 之后才收得到。
    """
    m = _VERSION.fullmatch(str(text or "").strip())
    return tuple(int(x) for x in m.groups() if x is not None) if m else None


def is_newer(latest: str | None, current: str | None) -> bool:
    new, cur = parse_version(latest), parse_version(current)
    return new is not None and cur is not None and new > cur


# ---------------------------------------------------------------- interface.json


STATS_OPTION = "StatsView"  # MXU 设置页「统计与成就」卡片：没有可选项的多选框，只为显示说明（data/stats_mxu.md）
# MXU 设置页单独一组「拟人化」的全局选项（其余全局选项在「所有任务共用」）
HUMANIZE_OPTIONS = ("HumanBehavior", "HumanGreat", "HumanTiming", "HumanPosition", "DailyHours")
# MXU 设置页单独一组「更新」的全局选项，排在「版本状态」卡片（UPDATE_OPTION）下面
UPDATE_OPTIONS = ("CheckUpdate", "AutoUpdate")
# 「版本状态」卡片：同样是没有可选项的多选框（data/update_mxu.md）。定义在 tasks/common.json 里、只有 MXU 的设置页引用，
# 不像 STATS_OPTION 那样打包时生成：v0.2.0～v0.2.6 算 interface.json 的摘要时只会去掉 STATS_OPTION，
# 打包时再加别的选项，它们整包更新时核对新包的 interface.json 就对不上了
UPDATE_OPTION = "UpdateView"


def render_interface(data: Mapping[str, Any], kind: str, version: str | None = None, agent_exec: str = AGENT_EXEC, agent_args: Iterable[str] = AGENT_ARGS) -> dict:
    """项目的 interface.json → 界面 ``kind``（mfaa / mxu）用的：写版本号和 Agent 启动命令，MXU 另加设置页的
    「统计与成就」卡片和全局选项分组（所有任务共用、拟人化、更新）。"""
    data = dict(data)
    if version:
        data["version"] = version
    data["agent"] = {**data.get("agent", {}), "child_exec": agent_exec, "child_args": list(agent_args)}
    data.pop("setting", None)
    if kind == "mxu":
        # MXU 把全局选项放进设置页的分组；MFAA 2.16.2 读到 setting 会连接卡住
        data["option"] = {
            **data.get("option", {}),
            STATS_OPTION: {"type": "checkbox", "label": f"$opt.{STATS_OPTION}", "description": f"$opt.{STATS_OPTION}.desc", "cases": []},
        }
        shared = [o for o in data["global_option"] if o not in HUMANIZE_OPTIONS and o not in UPDATE_OPTIONS]
        humanize = [o for o in data["global_option"] if o in HUMANIZE_OPTIONS]
        update = [o for o in data["global_option"] if o in UPDATE_OPTIONS]
        data["setting"] = [
            {
                "name": "stats",
                "label": "$setting.stats",
                "description": "$setting.stats.desc",
                "icon": "docs/ui/icon.png",
                "option": [STATS_OPTION],
            },
            {
                "name": "global",
                "label": "$setting.global",
                "description": "$setting.global.desc",
                "option": shared,
            },
        ]
        if humanize:
            data["setting"].append(
                {
                    "name": "humanize",
                    "label": "$setting.humanize",
                    "description": "$setting.humanize.desc",
                    "option": humanize,
                }
            )
        if update:
            data["setting"].append(
                {
                    "name": "update",
                    "label": "$setting.update",
                    "description": "$setting.update.desc",
                    "option": [UPDATE_OPTION, *update],
                }
            )
    return data


def dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=4) + "\n"


def strip_generated(data: Mapping[str, Any]) -> dict:
    """interface.json 去掉打包时写的部分：版本号、Agent 启动命令、MXU 的设置页和「统计与成就」卡片。"""
    data = {k: v for k, v in data.items() if k not in ("version", "setting")}
    data["agent"] = {k: v for k, v in data.get("agent", {}).items() if k not in ("child_exec", "child_args")}
    option = {k: v for k, v in data.pop("option", {}).items() if k != STATS_OPTION}
    if option:
        data["option"] = option
    return data


def interface_digest(data: Mapping[str, Any]) -> str:
    """interface.json 去掉打包时写的部分后的哈希：只改版本号不算改动。"""
    return hashlib.sha256(json.dumps(strip_generated(data), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 文件清单


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_digest(path: Path, rel: str) -> str:
    if rel == "interface.json":
        return interface_digest(json.loads(path.read_text(encoding="utf-8")))
    return sha256_file(path)


def is_app_file(rel: str) -> bool:
    """``rel``（相对包根目录，用 /）是不是 app 层的文件（热更新、重启更新替换的范围）。"""
    parts = rel.split("/")
    if "__pycache__" in parts or rel.endswith(".pyc"):
        return False
    if len(parts) == 1:
        return rel in APP_TOP_FILES or APP_TOP_PATTERN.fullmatch(rel) is not None
    return parts[0] in APP_DIRS and not rel.startswith((MODEL_DIR + "/", BUNDLE_DIR + "/"))


def app_files(root: Path) -> list[str]:
    """包根目录 ``root`` 下 app 层的文件（相对路径，用 /，排好序）。"""
    found = [p.name for p in root.iterdir() if p.is_file() and is_app_file(p.name)]
    for name in APP_DIRS:
        base = root / name
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for f in filenames:
                rel = Path(dirpath, f).relative_to(root).as_posix()
                if is_app_file(rel):
                    found.append(rel)
    return sorted(found)


def manifest_files(root: Path) -> dict[str, str]:
    """app 层文件清单：{相对路径: sha256}（interface.json 用 :func:`interface_digest`）。"""
    return {rel: file_digest(root / rel, rel) for rel in app_files(root)}


def tree_digest(root: Path, rel_dir: str) -> str:
    """``root/rel_dir`` 下全部文件（路径和内容）合起来的哈希；目录不存在时是空串的哈希。"""
    h = hashlib.sha256()
    base = root / rel_dir
    if base.is_dir():
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            h.update(f"{path.relative_to(base).as_posix()}\0{sha256_file(path)}\n".encode("utf-8"))
    return h.hexdigest()


def changed_files(old: Mapping[str, str], new: Mapping[str, str]) -> set[str]:
    """新增、改动、删除的文件。"""
    return {rel for rel, digest in new.items() if old.get(rel) != digest} | (set(old) - set(new))


def zip_name(info: zipfile.ZipInfo) -> str:
    """没有打 UTF-8 标记的中文文件名会被 zipfile 按 cp437 解码，这里还原。"""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("utf-8")
    except UnicodeError:
        return info.filename


def safe_rel(name: str) -> str | None:
    """压缩包里的路径 → 相对路径（用 /）；绝对路径、带 .. 的返回 None。"""
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or ".." in parts or ":" in parts[0]:
        return None
    return "/".join(parts)


def tier(local: Mapping[str, Any], manifest: Mapping[str, Any]) -> str:
    """本地的 release.json + 新版本的 update.json → ``hot`` / ``restart`` / ``full``。"""
    if (manifest.get("runtime") or {}).get(local.get("ui")) != local.get("runtime"):
        return "full"
    changed = changed_files(local.get("files") or {}, manifest.get("files") or {})
    if any(rel == "interface.json" or rel in SHELL or rel.startswith(RESTART_PREFIXES) for rel in changed):
        return "restart"
    return "hot"


# ---------------------------------------------------------------- release.json


def release_info(root: Path, kind: str, version: str, runtime: Mapping[str, Any]) -> dict:
    """组装好的发布包 ``root`` 的 release.json 内容。``top`` 是包根目录下的条目，整包更新时只换这些。"""
    top = {p.name for p in root.iterdir() if p.name not in USER_DIRS} | {RELEASE_FILE}
    return {
        "format": FORMAT,
        "version": version,
        "ui": kind,
        "ui_exe": UI_EXE[kind],
        "runtime": dict(runtime),
        "files": manifest_files(root),
        "top": sorted(top),
    }


def read_release(root: Path) -> dict | None:
    """``root/release.json``；没有（开发环境）或读不了时返回 None。"""
    try:
        data = json.loads((root / RELEASE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict) or data.get("ui") not in UI_EXE:
        return None
    return data


def write_text(path: Path, text: str, tries: int = 20) -> None:
    """先写临时文件再替换，别的进程不会读到写了一半的内容（临时文件名带进程号，多开时不互相覆盖）。
    文件正被别的程序（浏览器、杀毒软件）读着时替换会失败，等一下再试；一直不行就报错，不留临时文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(tries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == tries - 1:
                with suppress(OSError):
                    tmp.unlink()
                raise
            time.sleep(0.05)


def write_json(path: Path, data: Any) -> None:
    write_text(path, dump_json(data))


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- MXU 设置页的「版本状态」卡片

_CARD_STRONG = "color:var(--color-text-primary);font-weight:600"
_MANUAL = "请到发布页手动下载"


def _when(t: Any) -> str:
    """Unix 时间 → ``11-15 14:02``；不是时间时为空串。"""
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(float(t)))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _stamp(data: Mapping[str, Any], key: str) -> float:
    """``data[key]`` 里的 Unix 时间；没有、不是数时为负无穷。"""
    try:
        return float(data[key])
    except (KeyError, TypeError, ValueError):
        return float("-inf")


def _update_step(root: Path, check: Mapping[str, Any], status: Mapping[str, Any]) -> str:
    """查到的新版本更新到哪一步了（和任务结束时日志里的提示说法一致，见 agent/update.py 的 report）。"""
    latest, manifest = check["version"], check.get("manifest")
    if (root / AUTO_OFF_FILE).exists():
        return f"没有开「自动更新」，{latest} {_MANUAL}。"
    failed = read_json(root / FAILED_FILE)
    state = status.get("state") if status.get("version") == latest else None
    if state == "failed" or (isinstance(failed, dict) and latest in failed):
        return f"自动更新到 {latest} 失败过，已恢复原版本（详见 data/update.log），{_MANUAL}。"
    if not isinstance(manifest, dict) or _stamp(manifest, "format") > FORMAT:
        return f"{latest} 不能自动更新，{_MANUAL}。"
    if state == "downloading":
        return f"正在后台下载 {latest}。"
    if state == "waiting" and status.get("tier") == "hot":
        return f"{latest} 已下载，没有任务运行时自动换上。"
    if state == "waiting":
        return f"{latest} 已下载，任务都结束、界面空闲 2 分钟后自动重启界面完成更新。"
    if state == "waiting_close":
        return f"{latest} 已下载；开着「启动时自动执行」时不会自动重启界面，关闭界面后自动完成更新。"
    if state == "applying":
        return f"正在换上 {latest}。"
    if state == "error":
        return f"自动更新到 {latest} 暂时没成功（{status.get('error') or '原因不明'}），稍后会重试；也可以到发布页手动下载。"
    return f"{latest} 会在开始任务后自动下载，下载好了自动换上。"


def update_card(root: Path) -> str | None:
    """MXU 设置页「更新」一组里「版本状态」卡片的内容（打开设置页时读一次）；不是 MXU 的发布包时为 None。

    当前版本、最新版本、上次检查更新的结果、新版本更新到哪一步了。最新版本是一张图片（:data:`BADGE_URL`）：
    界面打开设置页时自己联网取，不用运行任务，取不到时不占位置；关掉「检查更新」后不放这张图（不联网）。
    其余的来自 Agent 和更新器留在 ``cache/update`` 里的文件，Agent 在任务开始、检查完、任务结束时重写，
    更新器在状态变化时重写（换上新版本、界面重开时没有 Agent）。整张卡片是一个 HTML 块，中间不能有空行。"""
    local = read_release(root)
    if local is None or local.get("ui") != "mxu":
        return None
    current = str(local.get("version"))
    check, error, status = (read_json(root / rel) for rel in (MANIFEST_FILE, CHECK_ERROR_FILE, STATUS_FILE))
    check = check if isinstance(check, dict) and isinstance(check.get("version"), str) else {}
    error = error if isinstance(error, dict) else {}
    status = status if isinstance(status, dict) else {}
    newer = is_newer(check.get("version"), current)
    checking = not (root / CHECK_OFF_FILE).exists()
    if not checking:
        rows = ["「检查更新」关着：开始任务时不会检查，这里也不联网取最新版本。"]
    elif _stamp(error, "time") > _stamp(check, "checked_at"):
        rows = [
            f"{_when(error['time'])} 检查更新没成功（{error.get('error') or '原因不明'}）。"
            "浏览器打得开 github.com、这里一直失败时，在「所有任务共用」的「网络代理」里填代理软件的 HTTP 端口。"
        ]
    elif not check:
        rows = ["还没有检查过更新，开始任意任务时会检查。"]
    else:
        found = f"发现新版本 {check['version']}" if newer else "已是最新版本"
        rows = [f"{_when(check.get('checked_at'))} 检查：{found}。"]
    if status.get("state") == "rollback_failed":
        rows.append(f"自动更新到 {status.get('version')} 后还原失败，任务暂时开始不了：请手动恢复备份，或把发布包解压到新目录。")
    elif newer:
        rows.append(_update_step(root, check, status))
    elif status.get("state") == "done" and status.get("version") == current:
        rows.append(f"{_when(status.get('time'))} 已自动更新到 {current}。")
    link = f'<a href="{escape(DOWNLOAD_URL)}" target="_blank" rel="noopener noreferrer"'
    badge = (
        f'{link} title="打开 GitHub 上最新版本的发布页" style="display:inline-flex;line-height:0">'
        f'<img src="{escape(BADGE_URL)}" alt="" style="display:block"></a>'
    )
    hint = f"「最新版本」是打开设置页时联网取的（shields.io，取不到时不显示）。开始任务时检查更新，{CHECK_TTL_S // 60} 分钟内查过的不重复查。"
    head = (
        '<div style="display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px;font-size:13px">'
        f'<span>当前版本 <b style="{_CARD_STRONG}">{escape(current)}</b></span>{badge if checking else ""}</div>'
    )
    foot = (
        f'<div style="font-size:12px;color:var(--color-text-tertiary)">{hint if checking else ""}'
        f'{link} style="color:var(--color-accent);white-space:nowrap">更新内容与手动下载</a></div>'
    )
    body = "\n".join([head, *(f"<div>{escape(row.strip())}</div>" for row in rows), foot])
    return f'<div style="display:flex;flex-direction:column;gap:6px;margin:4px 0 2px;line-height:1.5">\n{body}\n</div>\n'


def write_update_card(root: Path) -> None:
    """写出 :func:`update_card`（内容没变时不写）。卡片只是给人看的，出什么错都不能影响任务和更新。"""
    try:
        text = update_card(root)
        if text is None:
            return
        path = root / UPDATE_CARD
        with suppress(OSError):
            if path.read_text(encoding="utf-8") == text:
                return
        write_text(path, text)
    except Exception:  # noqa: BLE001 - 见上
        pass


# ---------------------------------------------------------------- 锁


class FileLock:
    """锁住一个文件的第一个字节（msvcrt），进程退出时系统自动解锁，不会留下死锁。"""

    def __init__(self, path: Path):
        self.path = path
        self._f = None

    def acquire(self) -> bool:
        """不等待；锁上返回 True。"""
        import msvcrt

        if self._f is not None:
            return True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            f = open(self.path, "a+b")  # noqa: SIM115 - 要一直开着
        except OSError:
            return False
        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            f.close()
            return False
        self._f = f
        return True

    def release(self) -> None:
        import msvcrt

        f, self._f = self._f, None
        if f is None:
            return
        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        f.close()

    @property
    def held(self) -> bool:
        return self._f is not None

    def __enter__(self) -> FileLock:
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def is_locked(path: Path) -> bool:
    """别的进程锁着 ``path``。"""
    if not path.exists():
        return False
    lock = FileLock(path)
    if lock.acquire():
        lock.release()
        return False
    return True


def busy(root: Path) -> bool:
    """有演奏进程在跑（Agent 锁着 busy 目录里的文件）。没锁住的是 Agent 异常退出留下的，顺手删掉。"""
    base = root / BUSY_DIR
    if not base.is_dir():
        return False
    found = False
    for path in base.glob("*.lock"):
        lock = FileLock(path)
        if not lock.acquire():
            found = True
            continue
        lock.release()
        try:
            path.unlink()
        except OSError:
            pass
    return found
