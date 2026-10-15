"""由两个发布包生成自动更新用的文件：app 层的压缩包和更新清单 update.json，和发布包一起上传到 Release::

    python tools/make_update.py dist/OurNotes-Auto-v0.1.5-win-x64-MFAA.zip dist/OurNotes-Auto-v0.1.5-win-x64-MXU.zip --out dist

update.json 写新版本的文件清单、两个界面的运行环境指纹和各个压缩包的大小与 sha256。用户那边的 Agent 下载它，
和本地的 release.json 比较决定热更新、重启更新还是整包更新（ournotes_auto/release.py）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ournotes_auto import release  # noqa: E402


def read_package(path: Path) -> tuple[dict, dict[str, bytes]]:
    """发布包 → (release.json, {app 层文件: 内容})，并核对文件和清单一致。"""
    files: dict[str, bytes] = {}
    info = None
    with zipfile.ZipFile(path) as zf:
        for item in zf.infolist():
            rel = release.safe_rel(release.zip_name(item))
            if item.is_dir() or rel is None:
                continue
            if rel == release.RELEASE_FILE:
                info = json.loads(zf.read(item).decode("utf-8"))
            elif release.is_app_file(rel):
                files[rel] = zf.read(item)
    if info is None:
        sys.exit(f"{path} 里没有 {release.RELEASE_FILE}，是不是用旧版 tools/install.py 组装的")
    digests = {
        rel: release.interface_digest(json.loads(data.decode("utf-8"))) if rel == "interface.json" else hashlib.sha256(data).hexdigest()
        for rel, data in files.items()
    }
    if digests != info["files"]:
        bad = sorted(release.changed_files(info["files"], digests))
        sys.exit(f"{path} 的文件和 {release.RELEASE_FILE} 对不上：{', '.join(bad[:10])}")
    return info, files


def asset(path: Path) -> dict:
    return {"name": path.name, "size": path.stat().st_size, "sha256": release.sha256_file(path)}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("packages", type=Path, nargs="+", help="tools/install.py 组装后打的发布包（zip），每种界面一个")
    p.add_argument("--out", type=Path, required=True, help="输出目录")
    p.add_argument("--repo", default=release.REPO, help="GitHub 仓库（owner/name）")
    p.add_argument("--base-url", help="压缩包的下载地址前缀（测试用，默认是 Release 的下载地址）")
    args = p.parse_args()

    packages = {}
    for path in args.packages:
        info, files = read_package(path)
        if info["ui"] in packages:
            sys.exit(f"有两个 {info['ui']} 的发布包")
        packages[info["ui"]] = (path, info, files)
    infos = [info for _, info, _ in packages.values()]
    version = infos[0]["version"]
    if release.parse_version(version) is None:
        sys.exit(f"版本号 {version!r} 不是 vX.Y.Z 或 vX.Y.Z.W")
    for info in infos[1:]:
        if info["version"] != version or info["files"] != infos[0]["files"]:
            sys.exit("几个发布包的版本或 app 层文件不一致")

    args.out.mkdir(parents=True, exist_ok=True)
    app = args.out / f"OurNotes-Auto-{version}-app.zip"
    _, _, files = next(iter(packages.values()))
    with zipfile.ZipFile(app, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in sorted(files):
            zf.writestr(rel, files[rel])
    manifest = {
        "format": release.FORMAT,
        "version": version,
        "url": f"https://github.com/{args.repo}/releases/tag/{version}",
        "base_url": args.base_url or f"https://github.com/{args.repo}/releases/download/{version}/",
        "app": asset(app),
        "full": {kind: asset(path) for kind, (path, _, _) in sorted(packages.items())},
        "runtime": {kind: info["runtime"] for kind, (_, info, _) in sorted(packages.items())},
        "files": infos[0]["files"],
    }
    release.write_json(args.out / release.UPDATE_ASSET, manifest)
    print(f"已生成 {app.name} 和 {release.UPDATE_ASSET}（{version}，{len(files)} 个文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
