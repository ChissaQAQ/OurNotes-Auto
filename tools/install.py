"""组装通用界面（MFAAvalonia 或 MXU）的运行目录，默认 install/mfaa 或 install/mxu。

开发用——resource/tasks/docs 以目录联接指向项目，Agent 用项目的 .venv，改代码或资源不用重装::

    python tools/install.py --ui MFAAvalonia-v2.16.2-win-x64.zip --dev
    python tools/install.py --ui MXU-win-x86_64-v2.6.1.zip --dev

发布用——全部复制，带嵌入式 Python 与依赖，把输出目录打包即可分发::

    python tools/install.py --ui MFAAvalonia-...-win-x64.zip \\
        --python python-3.14.3-embed-amd64.zip --version v0.1.0

界面用的 MaaFramework 动态库取当前 Python 环境里 maafw 包的那一份（MFAA 自带的会被替换），保证界面和 Agent 版本一致。
发布模式的 pip 依赖用运行本脚本的 Python 安装，它的版本要和嵌入式 Python 一致；依赖版本按 tools/constraints.txt 固定。
OCR 模型不在仓库里，组装前先运行 tools/fetch_ocr.py。
MFAA 与 MXU 的用户配置都放在各自目录的 config/ 下，所以两者分开组装、分开发布。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RID = "win-x64"
PY_DIR = "python"  # 发布包里嵌入式 Python 的目录
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc")
# 界面种类：(识别用的可执行文件, MaaFramework 动态库目录, 不复制的顶层文件或目录)
UIS = {
    "mfaa": ("MFAAvalonia.exe", f"runtimes/{RID}/native", {"runtimes"}),
    "mxu": ("mxu.exe", "maafw", {"mxu.pdb", "README.md"}),
}
RENAME = {"LICENSE": "LICENSE-{kind}"}  # 界面自带的许可证改名保留，不和本项目的 LICENSE 冲突
ICON = ROOT / "docs" / "ui" / "logo.ico"  # 由 tools/make_icon.py 生成


def _remove(path: Path) -> None:
    if os.path.isjunction(path) or path.is_symlink():
        os.unlink(path) if path.is_file() else os.rmdir(path)
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _zip_name(info: zipfile.ZipInfo) -> str:
    """没有打 UTF-8 标记的中文文件名会被 zipfile 按 cp437 解码，这里还原。"""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("utf-8")
    except UnicodeError:
        return info.filename


def ui_kind(src: Path) -> str:
    names = {p.name for p in src.iterdir()} if src.is_dir() else {n.split("/")[0] for n in zipfile.ZipFile(src).namelist()}
    for kind, (exe, _, _) in UIS.items():
        if exe in names:
            return kind
    sys.exit(f"{src} 里既没有 MFAAvalonia.exe 也没有 mxu.exe")


def _target(out: Path, kind: str, name: str) -> Path | None:
    top, _, rest = name.partition("/")
    if top in UIS[kind][2]:
        return None
    if not rest and top in RENAME:
        top = RENAME[top].format(kind=kind.upper())
    return out / top / rest if rest else out / top


def install_ui(src: Path, out: Path, kind: str) -> None:
    """复制界面程序，跳过它自带的 MaaFramework 和调试符号等。"""
    if src.is_dir():
        for p in src.rglob("*"):
            dst = _target(out, kind, p.relative_to(src).as_posix())
            if dst is not None and p.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)
        return
    with zipfile.ZipFile(src) as zf:
        for info in zf.infolist():
            dst = _target(out, kind, _zip_name(info))
            if info.is_dir() or dst is None:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as f, open(dst, "wb") as g:
                shutil.copyfileobj(f, g)


def install_maafw(out: Path, kind: str) -> str:
    """把 maafw 包里的动态库放到界面找原生库的位置，返回 maafw 版本。"""
    spec = importlib.util.find_spec("maa")
    if spec is None or not spec.submodule_search_locations:
        sys.exit("当前 Python 环境没有安装 maafw")
    bin_dir = Path(spec.submodule_search_locations[0]) / "bin"
    native = out / UIS[kind][1]
    _remove(native)
    shutil.copytree(bin_dir, native, ignore=shutil.ignore_patterns("plugins"))
    from importlib.metadata import version

    return version("maafw")


def place(src: Path, dst: Path, link: bool) -> None:
    _remove(dst)
    if link:
        import _winapi

        dst.parent.mkdir(parents=True, exist_ok=True)
        _winapi.CreateJunction(str(src), str(dst))
    elif src.is_dir():
        shutil.copytree(src, dst, ignore=SKIP)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def install_icon(out: Path, kind: str) -> None:
    """MFAA 从 Assets/logo.ico 读窗口和托盘图标；MXU 用 interface.json 的 icon，不用放文件。"""
    if kind == "mfaa":
        place(ICON, out / "Assets" / "logo.ico", link=False)


def install_python(embed_zip: Path, out: Path) -> None:
    """解压嵌入式 Python，打开 site-packages 与项目根目录的导入，再装依赖。"""
    py = out / PY_DIR
    _remove(py)
    with zipfile.ZipFile(embed_zip) as zf:
        zf.extractall(py)
    pth = next(py.glob("python*._pth"))
    lines = [x for x in pth.read_text().splitlines() if x.strip() and x.strip() != "#import site"]
    pth.write_text("\n".join([*lines, "Lib/site-packages", "..", "import site", ""]))
    embed_ver = pth.stem.removeprefix("python")  # 如 "314"
    host_ver = f"{sys.version_info.major}{sys.version_info.minor}"
    if embed_ver != host_ver:
        sys.exit(f"嵌入式 Python 版本（{embed_ver}）和当前 Python（{host_ver}）不一致，依赖的二进制包装不上")
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-compile", "--disable-pip-version-check",
         "--target", str(py / "Lib" / "site-packages"), "-c", str(ROOT / "tools" / "constraints.txt"), *deps],
        check=True,
    )


def write_interface(out: Path, kind: str, version: str | None, agent_exec: str, agent_args: list[str]) -> None:
    data = json.loads((ROOT / "interface.json").read_text(encoding="utf-8"))
    if version:
        data["version"] = version
    data["agent"]["child_exec"] = agent_exec
    data["agent"]["child_args"] = agent_args
    if kind == "mxu":
        # MXU 只在设置页「任务设置」里编辑 setting 分组列出的全局选项；
        # MFAA 2.16.2 读到 setting 会在后台线程建界面项而出错，连接卡住，所以只给 MXU 加
        data["setting"] = [
            {
                "name": "global",
                "label": "所有任务共用",
                "description": "触控方式等对所有任务生效的设置",
                "option": data["global_option"],
            }
        ]
    (out / "interface.json").write_text(json.dumps(data, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--ui", type=Path, required=True, help="MFAAvalonia 或 MXU 的发布包（zip）或解压后的目录")
    p.add_argument("--out", type=Path, help="输出目录，默认 install/mfaa 或 install/mxu")
    p.add_argument("--dev", action="store_true", help="开发模式：目录联接到项目，Agent 用项目的 .venv")
    p.add_argument("--python", type=Path, help="发布模式：嵌入式 Python 的 zip（python-3.x.y-embed-amd64.zip）")
    p.add_argument("--version", help="写进 interface.json 的版本号")
    args = p.parse_args()
    if not args.dev and not args.python:
        p.error("发布模式需要 --python")
    ocr_dir = ROOT / "resource" / "model" / "ocr"
    ocr_files = (
        *(f"{d}{f}" for d in ("", "ko_kr/") for f in ("det.onnx", "rec.onnx", "keys.txt")),
        "LICENSE-MaaCommonAssets.txt",
        "LICENSE-PaddleOCR.txt",
    )
    if not args.dev and not all((ocr_dir / f).is_file() for f in ocr_files):
        sys.exit(f"{ocr_dir} 里没有 OCR 模型，先运行 python tools/fetch_ocr.py")
    kind = ui_kind(args.ui)
    out: Path = (args.out or ROOT / "install" / kind).resolve()
    out.mkdir(parents=True, exist_ok=True)

    install_ui(args.ui, out, kind)
    maafw = install_maafw(out, kind)
    for name in ("resource", "tasks", "docs"):
        place(ROOT / name, out / name, link=args.dev)
    install_icon(out, kind)
    if args.dev:
        write_interface(out, kind, args.version, str(ROOT / ".venv" / "Scripts" / "python.exe"), [str(ROOT / "agent" / "main.py")])
    else:
        for name in ("agent", "ournotes_auto", "README.md", "LICENSE", "TERMS_OF_SERVICE.md", "THIRD_PARTY_NOTICES.md", "config.example.yaml"):
            if (ROOT / name).exists():
                place(ROOT / name, out / name, link=False)
        install_python(args.python, out)
        write_interface(out, kind, args.version, f"./{PY_DIR}/python.exe", ["./agent/main.py"])
    print(f"已组装 {kind} 到 {out}（MaaFramework {maafw}，{'开发' if args.dev else '发布'}模式）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
