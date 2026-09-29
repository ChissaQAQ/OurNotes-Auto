"""给通用界面预置「固定设备」的配置：只连指定的 adb 地址。

电脑上还连着别的 adb 设备（比如手机）时，界面第一次打开会扫描设备并选中列表里的第一个，
连接失败时还可能 kill-server。先写好配置再打开界面::

    python tools/ui_config.py install/mfaa --serial 127.0.0.1:16416 --mumu D:\\MuMuPlayer --index 1
    python tools/ui_config.py install/mxu --serial 127.0.0.1:16416

MFAA 只写一个实例，关掉指纹匹配、失败时自动检测和 adb 重启；任务选项按 interface 的默认值写好
（MFAA 自己补的一律取第一个 case）。手动用直接打开、勾任务、点开始。
无人值守（Windows 计划任务等）用 ``--run-on-open`` 生成配置：打开界面就执行勾选的任务，完了关掉界面。
2.16.2 的命令行参数实测都不能用：
- ``--autostart`` 跳过了读取保存的设备，开始时提示「未选择连接目标」，什么也不做。
- ``-i`` 切了配置，执行任务的却还是原来的页签（实测 ``-i auto`` 跑了 default 的任务）。
- 多个实例时，后台加载其他实例会扫描设备并覆盖保存的设备（kill-server 的替换也随之丢掉），所以只写一个实例。
  单实例实测只读取保存的设备，不扫描。
- 别点「刷新」、别切控制器、别新建实例，这些也会扫描。

MXU 只能固定地址：点「开始」时仍会扫描（MuMuManager 没返回实例时会探测所有 adb 设备），
MaaFramework 重连时的 kill-server 也关不掉。界面新增任务后用 ``--update`` 更新 MXU 的配置：
MXU 只给新任务标「新」，不会自己加进已有的任务列表。

已有配置时不覆盖，除非加 --force（MXU 可以用 --update）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

INSTANCE_SAFE = {
    "RememberAdb": True,
    "UseFingerprintMatching": False,
    "AutoDetectOnConnectionFailed": False,
    "AutoConnectAfterRefresh": False,
    "AllowAdbRestart": False,
    "AllowAdbHardRestart": False,
    "RetryOnDisconnected": False,
}
# 实例文件里没有的键会回退到全局配置：以后在界面里新建的实例也按安全值。
# 新手引导会盖住界面（无人值守时没人关），和 MXU 的 onboardingCompleted 一样跳过
GLOBAL = {
    "EnableCheckVersion": False,
    "EnableAutoUpdateResource": False,
    "EnableAutoUpdateMFA": False,
    "UI.HasCompletedFirstUseTutorial": True,
    **INSTANCE_SAFE,
}
DEFAULT_TASKS = ("启动游戏",)


def _load(ui_dir: Path) -> tuple[dict, list[dict], dict[str, dict]]:
    """界面目录里的 interface.json、全部任务与全部选项（含 import 的文件）。"""
    data = json.loads((ui_dir / "interface.json").read_text(encoding="utf-8"))
    tasks = list(data.get("task", []))
    specs = dict(data.get("option", {}))
    for rel in data.get("import", []):
        part = json.loads((ui_dir / rel).read_text(encoding="utf-8"))
        tasks += part.get("task", [])
        specs.update(part.get("option", {}))
    return data, tasks, specs


def load_interface(ui_dir: Path) -> tuple[dict, dict[str, str]]:
    """界面目录里的 interface.json 与全部任务（含 import 的文件）：任务名 → entry。"""
    data, tasks, _ = _load(ui_dir)
    return data, {t["name"]: t["entry"] for t in tasks}


def mfaa_options(tasks: list[dict], specs: dict[str, dict]) -> dict[str, list[dict]]:
    """每个任务的选项按 default_case / 输入框默认值写成 MFAA 的格式。
    MFAA 2.16.2 自己给任务补选项时一律取第一个 case，不看 default_case（「看故事」会被默认打开）。"""
    out = {}
    for t in tasks:
        items = []
        for name in t.get("option", []):
            spec = specs[name]
            if spec.get("type") == "input":
                items.append({"name": name, "index": 0, "data": {i["name"]: i.get("default", "") for i in spec["inputs"]}})
            else:
                cases = [c["name"] for c in spec["cases"]]
                items.append({"name": name, "index": cases.index(spec["default_case"]) if "default_case" in spec else 0})
        out[t["name"]] = items
    return out


def js_hash(text: str) -> str:
    """MXU 判断欢迎页是否变化用的 hash（WelcomeDialog.tsx 的 simpleHash：按 UTF-16 码元的 32 位 hash，36 进制）。"""
    h = 0
    raw = text.encode("utf-16-le")
    for i in range(0, len(raw), 2):
        h = ((h << 5) - h + int.from_bytes(raw[i : i + 2], "little")) & 0xFFFFFFFF
    if h >= 1 << 31:
        h -= 1 << 32
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    n, out = abs(h), ""
    while True:
        n, r = divmod(n, 36)
        out = digits[r] + out
        if not n:
            break
    return "-" + out if h < 0 else out


def _write(path: Path, data: dict, force: bool) -> None:
    if path.exists() and not force:
        sys.exit(f"{path} 已存在，加 --force 覆盖")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写入 {path}")


def mfaa(ui_dir: Path, iface: dict, tasks: dict[str, str], args) -> None:
    extras = {}
    if args.mumu:
        extras["extras"] = {"mumu": {"enable": True, "path": args.mumu, "index": args.index}}
    device = {
        "Name": args.name,
        "AdbPath": args.adb,
        "AdbSerial": args.serial,
        "ScreencapMethods": "EmulatorExtras" if args.mumu else "Default",
        "InputMethods": "Default",
        "Config": json.dumps({**extras, "command": {"KillServer": ["{ADB}", "version"]}}, ensure_ascii=False),
    }

    checked = _checked(tasks, args.tasks)
    _, task_list, specs = _load(ui_dir)
    options = mfaa_options(task_list, specs)
    instance = {
        "InstanceName": "默认",
        "CurrentController": "Adb",
        "CurrentControllerName": iface["controller"][0]["name"],
        "Resource": iface["resource"][0]["name"],
        **INSTANCE_SAFE,
        # 「启动前：启动脚本」「完成后：关闭界面」；手动用时都是「无」
        "BeforeTask": "StartupScriptOnly" if args.run_on_open else "None",
        "AfterTask": "CloseMFA" if args.run_on_open else "None",
        "SoftwarePath": "",
        # 启动时的设备扫描按这里的多开号挑设备
        "EmulatorConfig": f"-v {args.index}" if args.mumu else "",
        "Prescript": "",
        "Post-script": "",
        "IncludeInGlobalStart": False,
        "AdbDevice": device,
        # 界面按 name + entry 认任务，对不上的会被删掉；default_check 就是勾选状态
        "TaskItems": [
            {"name": t, "entry": e, "default_check": t in checked, **({"option": options[t]} if options.get(t) else {})}
            for t, e in tasks.items()
        ],
    }
    _write(
        ui_dir / "appsettings.json",
        {
            "Instances.List": "default",
            "Instances.Order": "default",
            "Instances.LastActive": "default",
            "Instances.LastActiveName": "默认",
            "NoAutoStart": "False",
            "GlobalStartEnabled": "False",
            "HelpImproveSoftware": "False",
        },
        args.force,
    )
    _write(ui_dir / "config" / "config.json", GLOBAL, args.force)
    _write(ui_dir / "config" / "instances" / "default.json", instance, args.force)
    # 实例目录里的每个文件都会被当成实例加载
    for extra in (ui_dir / "config" / "instances").glob("*.json"):
        if extra.stem != "default":
            print(f"注意：{extra} 也会作为实例加载，不需要的话删掉")


def _checked(tasks: dict[str, str], spec: str | None) -> set[str]:
    checked = set(spec.split(",")) if spec else set(DEFAULT_TASKS)
    unknown = checked - set(tasks)
    if unknown:
        sys.exit(f"没有这些任务：{'、'.join(sorted(unknown))}（可选：{'、'.join(tasks)}）")
    return checked


# 每次都强制的设置：内置 Web 服务能远程启动任务，桌面界面用不到
MXU_SAFE = {"helpImproveSoftware": False, "webServerEnabled": False, "onboardingCompleted": True}


def mxu_update(config: dict, iface: dict, tasks: dict[str, str], args) -> dict:
    """在已有的 MXU 配置上更新：保留用户的任务顺序、勾选和选项，补上界面新增的任务（不勾），
    重新固定设备地址、强制安全设置。MXU 自己只给新任务标「新」，不会加进已有实例的任务列表。"""
    checked = _checked(tasks, args.tasks) if args.tasks else None
    for inst in config.get("instances", []):
        items = [t for t in inst.get("tasks", []) if t.get("taskName") in tasks]
        have = {t["taskName"] for t in items}
        ids = {t.get("id") for t in items}
        for name in tasks:
            if name not in have:
                n = len(ids)
                while f"t{n}" in ids:
                    n += 1
                ids.add(f"t{n}")
                items.append({"id": f"t{n}", "taskName": name, "enabled": False, "optionValues": {}})
                print(f"{inst.get('name')}：加入任务 {name}")
        if checked is not None:
            for t in items:
                t["enabled"] = t["taskName"] in checked
        inst["tasks"] = items
        device = inst.setdefault("savedDevice", {})
        device["adbDeviceAddress"] = args.serial
        device["adbDeviceName"] = device.get("adbDeviceName") or args.name
        inst["controllerName"] = iface["controller"][0]["name"]
        inst["resourceName"] = iface["resource"][0]["name"]
    config.setdefault("settings", {}).update(MXU_SAFE)
    config["interfaceTaskSnapshot"] = list(tasks)
    config.pop("newTaskNames", None)
    return config


def mxu(ui_dir: Path, iface: dict, tasks: dict[str, str], args) -> None:
    path = ui_dir / "config" / f"mxu-{iface['name']}.json"
    welcome = ui_dir / iface.get("welcome", "")
    welcome_hash = js_hash(welcome.read_text(encoding="utf-8")) if iface.get("welcome") and welcome.is_file() else None
    if args.update and path.exists():
        config = mxu_update(json.loads(path.read_text(encoding="utf-8")), iface, tasks, args)
        if welcome_hash:
            config["settings"]["welcomeShownHash"] = welcome_hash
        _write(path, config, True)
        return
    checked = _checked(tasks, args.tasks)
    settings = {"theme": "system", "language": "zh-CN", **MXU_SAFE, "autoRunOnLaunch": False}
    if welcome_hash:
        settings["welcomeShownHash"] = welcome_hash
    config = {
        "version": "1.0",
        "instances": [
            {
                "id": "ournotes",
                "name": args.name,
                "controllerName": iface["controller"][0]["name"],
                "resourceName": iface["resource"][0]["name"],
                # 名字必须非空，否则当作没有保存设备、直接连扫描到的第一个；匹配时地址优先
                "savedDevice": {"adbDeviceName": args.name, "adbDeviceAddress": args.serial},
                "tasks": [
                    {"id": f"t{i}", "taskName": t, "enabled": t in checked, "optionValues": {}}
                    for i, t in enumerate(tasks)
                ],
            }
        ],
        "settings": settings,
        "lastActiveInstanceId": "ournotes",
        "presetInitialized": True,
        "interfaceTaskSnapshot": list(tasks),
    }
    _write(path, config, args.force or args.update)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("ui_dir", type=Path, help="界面目录（install/mfaa 或 install/mxu）")
    p.add_argument("--serial", required=True, help="adb 地址，如 127.0.0.1:16416")
    p.add_argument("--mumu", help="MuMu 安装目录（启用 MuMu 截图扩展）")
    p.add_argument("--index", type=int, default=0, help="MuMu 实例序号")
    p.add_argument("--adb", help="adb.exe 路径，默认用 MuMu 自带的")
    p.add_argument("--name", default="MuMu", help="设备显示名")
    p.add_argument("--tasks", help="勾选的任务，逗号分隔；新建配置默认只勾「启动游戏」，--update 时不给就不改勾选")
    p.add_argument("--run-on-open", action="store_true", help="MFAA 打开就执行勾选的任务，完成后关闭")
    p.add_argument("--force", action="store_true", help="覆盖已有配置")
    p.add_argument("--update", action="store_true", help="MXU：在已有配置上补新任务、重新固定设备，保留勾选和选项")
    args = p.parse_args()
    ui_dir: Path = args.ui_dir.resolve()
    iface, tasks = load_interface(ui_dir)
    if (ui_dir / "MFAAvalonia.exe").exists():
        if not args.adb:
            if not args.mumu:
                p.error("MFAA 需要 --adb 或 --mumu")
            args.adb = str(Path(args.mumu) / "nx_main" / "adb.exe")
        mfaa(ui_dir, iface, tasks, args)
    elif (ui_dir / "mxu.exe").exists():
        mxu(ui_dir, iface, tasks, args)
    else:
        sys.exit(f"{ui_dir} 里既没有 MFAAvalonia.exe 也没有 mxu.exe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
