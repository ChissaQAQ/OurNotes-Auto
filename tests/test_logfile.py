"""日志文件与反馈问题用的设备信息。"""

from __future__ import annotations

import json
import logging
import subprocess

from ournotes_auto import logfile
from ournotes_auto.config import DeviceConfig
from ournotes_auto.device import adb
from ournotes_auto.device.mumu import describe_mumu


def test_file_handler_has_date(tmp_path):
    fh = logfile.file_handler(tmp_path / "data" / "x.log")
    try:
        record = logging.LogRecord("a", logging.INFO, __file__, 1, "msg", None, None)
        line = fh.format(record)
    finally:
        fh.close()
    assert fh.level == logging.DEBUG
    date, time_, *_ = line.split()
    assert len(date) == 10 and date[4] == date[7] == "-"
    assert line.endswith("I a: msg")


def test_file_handler_rolls_big_log(tmp_path, monkeypatch):
    monkeypatch.setattr(logfile, "LOG_MAX_BYTES", 10)
    log = tmp_path / "x.log"
    (tmp_path / "x.old.log").write_text("older", encoding="utf-8")
    log.write_text("0123456789abc", encoding="utf-8")
    logfile.file_handler(log).close()
    assert (tmp_path / "x.old.log").read_text(encoding="utf-8") == "0123456789abc"
    assert log.read_text(encoding="utf-8") == ""

    log.write_text("small", encoding="utf-8")
    logfile.file_handler(log).close()
    assert log.read_text(encoding="utf-8") == "small"


def _write(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_describe_mumu(tmp_path):
    _write(tmp_path / "configs" / "install_config.json", {"product": {"version": "6.6.4.0"}})
    vm = tmp_path / "vms" / "MuMuPlayer-15.0-1" / "configs"
    _write(vm / "vm_config.json", {"vm": {"cpu": "4", "memory": "6"}})
    _write(vm / "customer_config.json", {"setting": {"frame_setting": {"desired_framerate": "60"}}})
    _write(tmp_path / "vms" / "MuMuPlayer-12.0-0" / "configs" / "vm_config.json", {"vm": {"cpu": "2"}})

    cfg = DeviceConfig(mumu_path=str(tmp_path), instance=1)
    assert describe_mumu(cfg) == ["MuMu 6.6.4.0", "CPU 4 核", "内存 6 GB", "帧率 60"]
    assert describe_mumu(DeviceConfig(mumu_path=str(tmp_path), instance=0)) == ["MuMu 6.6.4.0", "CPU 2 核"]
    assert describe_mumu(DeviceConfig(mumu_path=str(tmp_path / "nope"), instance=1)) == []


def test_device_versions(monkeypatch):
    out = b"15\n    versionName=1.0.1\n"

    def runner(cfg, timeout_s):
        def run(*args, check=True):
            return subprocess.CompletedProcess(args, 0, stdout=out, stderr=b"")

        return "127.0.0.1:16416", run

    monkeypatch.setattr(adb, "_runner", runner)
    assert adb.versions(DeviceConfig()) == ("15", "1.0.1")
    out = b""  # 没装游戏、adb 没输出
    assert adb.versions(DeviceConfig()) == ("", "")
