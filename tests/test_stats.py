"""统计与成就：累计数据、成就解锁、三个页面、运行记录。"""

import dataclasses
import json
import logging
import os
import re
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

from ournotes_auto import stats, stats_page
from ournotes_auto.nav.screens import ARENA_BANDS
from ournotes_auto.records import ArenaResult, PlayResult, RecordStore
from ournotes_auto.runner import RunStats
from ournotes_auto.stats import (
    ACHIEVEMENTS,
    PAGE_HTML,
    PAGE_LIVE,
    PAGE_MD,
    PAGE_MXU,
    PAGE_VER,
    RUNNING,
    SESSIONS,
    UNLOCKED,
    VER_MARK,
    Session,
    Tracker,
    compute,
    duration,
    load_sessions,
    next_goals,
    progress_text,
    refresh,
)

T0 = 1790600000.0  # 2026-09-29
DAY = 86400.0
H = 3600.0
A = {a.id: a for a in ACHIEVEMENTS}
N = len(ACHIEVEMENTS)


def play(mid=1, diff="expert", great=0, miss=0, t=0.0, read=True):
    if not read:
        return PlayResult(mid, diff, timestamp=T0 + t)
    return PlayResult(mid, diff, perfect=100, great=great, good=0, bad=0, miss=miss, max_combo=100, score=1000, timestamp=T0 + t)


def store(tmp_path, plays):
    s = RecordStore(tmp_path)
    for p in plays:
        s.append(p)


@pytest.fixture(autouse=True)
def no_client(monkeypatch):
    monkeypatch.delenv("PI_CLIENT_NAME", raising=False)


def test_totals_streak():
    """连续 AP：结算没读全的局不算也不打断，没 AP 的局打断。"""
    plays = [play(t=0), play(t=1), play(t=2, read=False), play(t=3), play(t=4, great=2), play(t=5)]
    t, _ = compute(plays, [])
    assert (t.plays, t.fc, t.ap, t.unread) == (6, 5, 4, 1)
    assert (t.streak, t.best_streak) == (1, 3)
    assert t.notes == 502 and t.perfect == 500 and t.score == 5000 and t.max_combo == 100


def test_totals_days():
    plays = [play(t=0), play(t=1), play(t=DAY), play(t=3 * DAY), play(t=4 * DAY), play(t=5 * DAY)]
    t, _ = compute(plays, [])
    assert len(t.per_day) == 5 and (t.day_streak, t.best_day_streak) == (3, 3)
    assert (t.best_day, t.best_day_date) == (2, date.fromtimestamp(T0))


def test_totals_charts():
    """四个难度都 AP 的歌、打到第几局才第一次 AP。"""
    plays = [play(diff="easy", t=0), play(diff="normal", t=1), play(diff="hard", t=2)]
    plays += [play(great=1, t=3), play(miss=1, t=4), play(t=5), play(t=6), play(2, t=7)]
    t, reached = compute(plays, [])
    assert (len(t.charts), t.charts_ap, t.songs_all_ap, t.late_first_ap, t.max_chart_plays) == (5, 5, 1, 3, 4)
    assert reached["all_diffs"] == reached["late_ap"] == T0 + 5


def test_unlock_times():
    """各成就记的是第一次达成时那局的时间。"""
    t, reached = compute([play(mid, t=mid * 60) for mid in range(10)], [])
    assert reached["first_play"] == reached["first_fc"] == reached["first_ap"] == T0
    assert reached["streak_10"] == reached["charts_10"] == T0 + 540
    assert "plays_100" not in reached and t.charts_ap == 10


def test_estimated_runtime():
    """没有运行记录时按演奏记录估算：相邻两局隔不到 GAP_S 算一直在运行，每段开头那局按典型间隔算。"""
    t, _ = compute([play(t=x) for x in (0, 100, 200, 5000, 5100)], [])
    assert t.run_s == t.estimated_s == 500
    assert t.longest_run_s == 300 and t.runs == 0


def test_runtime_estimated_per_record():
    """按每局判断要不要估算：导入了别处的运行记录（比这边统计功能加入得早）后，这边以前没有运行记录的演奏照样估算；
    估算的一段不跨过运行记录。"""
    s = Session(T0 + 1000, T0 + 1300, "挂机")
    t, _ = compute([play(t=x) for x in (700, 800, 900, 1100, 1400, 1500)], [s])
    assert t.estimated_s == 300 + 200  # 700～900 一段；1400、1500 另起一段（不接 900），开头那局按典型间隔 100 算
    assert t.run_s == 300 + 500 and t.longest_run_s == 300


def test_runtime_from_sessions():
    """第一次运行记录之后按运行记录算，不再估算。"""
    s = Session(T0 + 1000, T0 + 1000 + H, "挂机", idle=True, lb_wait_s=600, skips=2)
    t, _ = compute([play(t=0), play(t=100), play(t=2000)], [s])
    assert (t.run_s, t.estimated_s, t.idle_s, t.lb_wait_s, t.skips, t.runs) == (H + 200, 200, H, 600, 2, 1)
    assert t.plays == 3 and t.longest_run_s == H


def test_runs_merged():
    """挂机为换新版本退出、隔不到 MERGE_S 又开始了，算同一次连续运行。"""
    sessions = [Session(T0, T0 + 7 * H, restart=True), Session(T0 + 7 * H + 60, T0 + 13 * H), Session(T0 + 13 * H + 60, T0 + 15 * H)]
    t, reached = compute([], sessions)
    assert t.runs == 3 and t.run_s == 15 * H - 120
    assert t.longest_run_s == 13 * H
    assert reached["run_12h"] == T0 + 13 * H


def test_runs_not_merged_after_stop():
    """手动停止后很快又开始（MXU 停止再开始，先清体力、挑战演出再挂机）的不算连续运行，
    换新版本退出后隔太久才开始的也不算。"""
    sessions = [
        Session(T0, T0 + 20 * H, "挂机"),
        Session(T0 + 20 * H + 120, T0 + 20 * H + 130, "清体力"),
        Session(T0 + 20 * H + 133, T0 + 20 * H + 400, "挑战演出"),
        Session(T0 + 20 * H + 560, T0 + 43 * H, "挂机", restart=True),
        Session(T0 + 43 * H + 600, T0 + 50 * H, "挂机"),
    ]
    t, reached = compute([], sessions)
    assert t.longest_run_s == 23 * H - 560
    assert "run_12h" in reached and "run_24h" not in reached and "run_48h" not in reached


def test_load_sessions_skips_bad_lines(tmp_path):
    good = json.dumps({"start": T0, "end": T0 + 60, "task": "挂机", "extra": 1})
    lines = ["not json", "[1]", json.dumps({"start": T0}), good, ""]
    (tmp_path / SESSIONS).write_text("\n".join(lines), encoding="utf-8")
    assert load_sessions(tmp_path) == [Session(T0, T0 + 60, "挂机")]
    assert load_sessions(tmp_path / "none") == []


def test_fun_counters():
    """「趣味」成就看的几种局：只差一个 GREAT / MISS、FAST 和 SLOW 一样多、卡了还 AP、分数末尾 777、回文分数。"""
    def r(**kw):
        base = dict(perfect=100, great=0, good=0, bad=0, miss=0, max_combo=100, score=1000, timestamp=T0)
        return PlayResult(1, "expert", **{**base, **kw})

    plays = [r(great=1), r(miss=1), r(great=1, miss=1), r(great=1, good=1), r(fast=3, slow=3), r(fast=0, slow=0),
             r(stalls=[[1.0, 2.0]]), r(stalls=[[1.0, 2.0]], great=2), r(score=1_234_777), r(score=1_234_321), r(score=12_321)]
    t, _ = compute(plays, [])
    assert (t.one_great, t.one_miss, t.balanced, t.stall_ap, t.lucky, t.palindrome) == (1, 1, 1, 1, 1, 1)


def test_heartbreak_and_first_ap():
    """连续 AP 停在第 99 局（结算没读全的不打断）；第一局就 AP 的谱面、打到第几局才第一次 AP。"""
    plays = [play(t=i) for i in range(99)] + [play(t=99, read=False), play(t=100, great=1)]
    plays += [play(2, t=200, great=1), play(2, t=201, miss=1), play(2, t=202)]
    t, reached = compute(plays, [])
    assert t.heartbreak == 1 and t.best_streak == 99 and "heartbreak_99" in reached
    assert t.first_try_ap == 1 and t.late_first_ap == 3 and "late_ap" in reached


def at(*dt) -> float:
    """本地时间 → play() 的 t。"""
    return datetime(*dt).timestamp() - T0


def test_date_eggs():
    """彩蛋按本地时间：13 号星期五、万圣节、0:00 那一分钟；平常的日子什么都不算。"""
    t, reached = compute([play(t=at(2026, 11, 13, 12)), play(t=at(2026, 10, 31, 0, 0, 30)), play(t=at(2026, 10, 26, 15))], [])
    assert t.eggs == {"friday_13", "halloween", "midnight"}
    assert {"friday_13", "halloween", "midnight"} <= set(reached) and "christmas" not in reached


def test_overlapping_runs():
    """两个打歌任务同时在运行（「分身术」）；一个接一个的不算。"""
    t, _ = compute([], [Session(T0, T0 + H), Session(T0 + H, T0 + 2 * H), Session(T0 + 3 * H, T0 + 4 * H)])
    assert t.overlaps == 0
    t, reached = compute([], [Session(T0, T0 + 2 * H, "挂机"), Session(T0 + H, T0 + 3 * H, "重复刷歌")])
    assert t.overlaps == 1 and "multi_run" in reached


def test_library_from_catalog():
    """曲库：曲名、乐队、等级（不是整数的不要）；有 EXPERT 的歌按乐队数。读不了曲库时为空。"""
    def song(mid, band, levels):
        return SimpleNamespace(music_id=mid, band=band, difficulties=list(levels), level=levels.get, display_title=lambda: f"曲{mid}")

    cat = SimpleNamespace(songs={1: song(1, "A", {"hard": 18, "expert": 25}), 2: song(2, "A", {"expert": None}), 3: song(3, "", {"expert": 27})})
    lib = stats.Library.from_catalog(cat)
    assert lib.titles == {1: "曲1", 2: "曲2", 3: "曲3"} and lib.bands == {1: "A", 2: "A", 3: ""}
    assert lib.levels == {(1, "hard"): 18, (1, "expert"): 25, (3, "expert"): 27}
    assert lib.expert == 2 and lib.band_expert == {"A": 1, "": 1}
    assert lib.title(3) == "曲3" and lib.title(9) == "9"
    assert stats.Library.from_catalog(None) == stats.Library()


def unlocks(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "ournotes_auto.stats" and r.levelno == logging.INFO]


def test_refresh_retroactive_then_incremental(tmp_path, caplog):
    """第一次统计时按以往的记录一下解锁很多个：日志里只报一行；之后新解锁的各报一行。"""
    store(tmp_path, [play(mid, t=mid * 60) for mid in range(9)])
    with caplog.at_level(logging.INFO, logger="ournotes_auto.stats"):
        view = refresh(tmp_path, now=T0 + DAY)
    assert unlocks(caplog) == [f"按以往的演奏记录解锁了 4 个成就（成就 4 / {N}）"]
    assert set(view.unlocked) == {"first_play", "first_fc", "first_ap", "first_try_1"}
    assert json.loads((tmp_path / UNLOCKED).read_text(encoding="utf-8"))["unlocked"]["first_play"] == T0
    assert (tmp_path / PAGE_MD).exists() and (tmp_path / PAGE_MXU).exists()
    assert not list(tmp_path.glob("*.tmp"))

    caplog.clear()
    store(tmp_path, [play(9, t=540)])
    with caplog.at_level(logging.INFO, logger="ournotes_auto.stats"):
        refresh(tmp_path, now=T0 + DAY)
        refresh(tmp_path, now=T0 + DAY)  # 没有新解锁的不再报
    assert unlocks(caplog) == [
        f"★ 解锁成就「渐入佳境」：连续 10 局 ALL PERFECT（成就 6 / {N}）",
        f"★ 解锁成就「初窥曲库」：10 张不同的谱面打出过 AP（成就 6 / {N}）",
    ]


def test_refresh_first_single_unlock(tmp_path, caplog):
    store(tmp_path, [play(read=False)])
    with caplog.at_level(logging.INFO, logger="ournotes_auto.stats"):
        refresh(tmp_path, now=T0)
    assert unlocks(caplog) == [f"★ 解锁成就「初次登台」：用本工具打完第一局（成就 1 / {N}）"]


def test_refresh_new_achievements_batched(tmp_path, caplog):
    """加了新成就后（升级）按以往的记录一下解锁很多个：日志里报一行，带上名字。"""
    store(tmp_path, [play(mid, t=mid * 60) for mid in range(10)] + [play(10, great=1, t=600)])
    (tmp_path / UNLOCKED).write_text(json.dumps({"unlocked": {"first_play": T0}}), encoding="utf-8")
    with caplog.at_level(logging.INFO, logger="ournotes_auto.stats"):
        refresh(tmp_path, now=T0 + DAY)
    assert unlocks(caplog) == [
        "按以往的演奏记录解锁了 6 个成就：「一气呵成」、「完美初体验」、「渐入佳境」、「一发入魂」、「初窥曲库」、「白璧微瑕」"
        f"（成就 7 / {N}）"
    ]


def test_page_version(tmp_path):
    """页面里写上内容的版本，stats_ver.js 写同一个版本（窗口读到版本变了就重新载入）；内容不变版本也不变。"""
    store(tmp_path, [play()])
    refresh(tmp_path, now=T0)
    page = (tmp_path / PAGE_HTML).read_text(encoding="utf-8")
    ver = (tmp_path / PAGE_VER).read_text(encoding="utf-8")
    assert VER_MARK not in page and ver.startswith('statsVer("') and ver.endswith('");\n')
    assert f'"{ver[10:-4]}"' in page and len(ver[10:-4]) == 12
    refresh(tmp_path, now=T0)
    assert (tmp_path / PAGE_VER).read_text(encoding="utf-8") == ver
    store(tmp_path, [play(2, t=60)])
    refresh(tmp_path, now=T0)
    assert (tmp_path / PAGE_VER).read_text(encoding="utf-8") != ver


def test_unlocked_kept(tmp_path):
    """解锁过的成就记录被删了也不丢；时间取最早的。"""
    store(tmp_path, [play(read=False, t=100)])
    (tmp_path / UNLOCKED).write_text(json.dumps({"unlocked": {"first_play": T0 + DAY, "first_ap": T0 - DAY}}), encoding="utf-8")
    view = refresh(tmp_path, now=T0 + DAY)
    assert view.unlocked == {"first_play": T0 + 100, "first_ap": T0 - DAY}
    (tmp_path / "records.jsonl").unlink()
    view = refresh(tmp_path, now=T0 + DAY)
    assert view.unlocked == {"first_play": T0 + 100, "first_ap": T0 - DAY}
    assert "| 完美初体验 | 第一次 ALL PERFECT | " in (tmp_path / PAGE_MD).read_text(encoding="utf-8")


def test_unlocked_file_broken(tmp_path, caplog):
    store(tmp_path, [play()])
    (tmp_path / UNLOCKED).write_text("{", encoding="utf-8")
    with caplog.at_level(logging.INFO, logger="ournotes_auto.stats"):
        view = refresh(tmp_path, now=T0)
    assert len(view.unlocked) == 4 and len(unlocks(caplog)) == 1
    assert json.loads((tmp_path / UNLOCKED).read_text(encoding="utf-8"))["unlocked"] == view.unlocked


def test_pages_empty(tmp_path):
    view = refresh(tmp_path, now=T0)
    md = (tmp_path / PAGE_MD).read_text(encoding="utf-8")
    page = (tmp_path / PAGE_HTML).read_text(encoding="utf-8")
    assert view.unlocked == {} and json.loads((tmp_path / UNLOCKED).read_text(encoding="utf-8")) == {"unlocked": {}}
    assert "还没有演奏记录" in md and "更多数据" not in md and f"成就 0 / {N}" in md
    assert "还没有演奏记录" in page and "运行中" not in page
    assert "还没有" in (tmp_path / PAGE_MXU).read_text(encoding="utf-8")
    assert all(stats.IMPORT_TEXT in text for text in (md, page, (tmp_path / PAGE_MXU).read_text(encoding="utf-8")))  # 换了包：提醒导入


def test_page_backup_dialog(tmp_path):
    """「备份与恢复」对话框：右上角的按钮打开；没有记录时导出按钮不能点，有了写着有多少。页面上的脚本按服务的上传上限
    先查文件大小，请求头、接口和服务对得上；读 stats_ver.js 等时地址带上密钥。"""
    from ournotes_auto import backup, stats_page, stats_server

    refresh(tmp_path, now=T0)
    page = (tmp_path / PAGE_HTML).read_text(encoding="utf-8")
    assert page.count('<dialog class="bk"') == 1 and "data-bk-open" in page
    assert 'data-bk="export" data-empty>' in page and "<span>还没有记录</span>" in page
    assert f"const BK_MAX={backup.MAX_UPLOAD};" in page
    store(tmp_path, [play()])
    refresh(tmp_path, now=T0)
    page = (tmp_path / PAGE_HTML).read_text(encoding="utf-8")
    assert 'data-bk="export">' in page and "<span>1 局演奏</span>" in page and "<span>4 个成就</span>" in page

    js = stats_page.BACKUP_JS
    for header in (stats_server.KEY_HEADER, stats_server.NAME_HEADER, stats_server.INFO_HEADER):
        assert f"'{header}'" in js, header
    assert all(f"call('api/{name}'" in js for name in ("export", "preview", "import"))  # 相对于 /stats.html
    assert "'key='+encodeURIComponent(key)" in stats_page.LIVE_JS


def test_md_per_client(tmp_path, monkeypatch):
    """任务说明：MFAA 放完整统计和打开窗口的链接（有快捷方式时链接到快捷方式），MXU 只放一行总结、指向设置页的卡片
    （只能打开网址，不放路径），命令行给路径。"""
    data = tmp_path / "data"
    store(data, [play()])
    path = (data / PAGE_HTML).resolve()

    def md(client):
        if client:
            monkeypatch.setenv("PI_CLIENT_NAME", client)
        else:
            monkeypatch.delenv("PI_CLIENT_NAME", raising=False)
        refresh(data, now=T0)
        return (data / PAGE_MD).read_text(encoding="utf-8")

    mfaa = md("mfaa")
    assert mfaa.endswith(f"[在浏览器里打开完整页面](<{path}>)\n") and "点一下别的任务再点回来可以刷新" in mfaa
    assert "| 1 局 | 1（100.0%） | 1（100.0%） |" in mfaa
    (tmp_path / "统计与成就.lnk").write_bytes(b"")
    assert md("MFAAvalonia").endswith(f"[打开统计与成就窗口](<{(tmp_path / '统计与成就.lnk').resolve()}>)\n")
    mxu = md("MXU")
    assert "完整页面：" not in mxu and str(tmp_path) not in mxu and "打开设置再回来可以刷新" in mxu
    assert "**累计运行 " in mxu and "「统计与成就」卡片" in mxu and "| 1 局 |" not in mxu
    assert stats.BACKUP_TEXT in mfaa and stats.BACKUP_TEXT in mxu and stats.IMPORT_TEXT not in mxu
    cli = md("")
    assert cli.endswith(f"完整页面：{path}\n") and "MFAA 点一下别的任务再点回来、MXU 打开设置再回来可以刷新" in cli
    assert "| 1 局 | 1（100.0%） | 1（100.0%） |" in cli


def test_mxu_card(tmp_path):
    """MXU 设置页卡片：完整的统计（成就进度、解锁的成就），不放路径。"""
    store(tmp_path, [play()])
    refresh(tmp_path, now=T0)
    card = (tmp_path / PAGE_MXU).read_text(encoding="utf-8")
    assert f"4 / {N}" in card and "完美初体验" in card and str(tmp_path) not in card
    assert stats.BACKUP_TEXT in card and stats.BACKUP_TEXT in (tmp_path / PAGE_HTML).read_text(encoding="utf-8")


def test_mxu_card_button(tmp_path):
    """卡片上「在浏览器里打开」：链接到 MXU 网页服务读的页面，要 target=_blank（MXU 只把这样的链接交给浏览器，
    不然在界面里跳走）。MXU 的端口被占时会悄悄换到后面的端口，每个可能的端口放一个按钮，按钮是从那个端口读的图片，
    读不到的不占位置，按钮之间不能有空白；MXU 设置里关掉了网页服务时不放按钮。"""
    data = tmp_path / "data"
    store(data, [play()])
    refresh(data, now=T0)
    card = (data / PAGE_MXU).read_text(encoding="utf-8")
    image = stats.mxu_button_name(data)
    assert (data / image).is_file()
    for port in range(12701, 12711):
        assert f'<a href="http://127.0.0.1:{port}/api/local-file?path=data/stats.html" target="_blank"' in card
        assert f'<img src="http://127.0.0.1:{port}/api/local-file?path=data/{image}" alt=""' in card
    assert "12711" not in card and card.count("<a ") == 10 and card.count("</a><a ") == 9
    assert "「打开统计窗口」</b>" in card and "\n\n" not in card.strip()
    (tmp_path / stats.MXU_CONFIG).parent.mkdir()
    (tmp_path / stats.MXU_CONFIG).write_text('{"settings": {"webServerEnabled": false}}', encoding="utf-8")
    refresh(data, now=T0)
    card = (data / PAGE_MXU).read_text(encoding="utf-8")
    assert "<a " not in card and "<img" not in card and "「统计与成就」</b>快捷方式" in card


def test_mxu_button_image(tmp_path):
    """按钮图片：能解析的 SVG；文件名带包的记号（两个包不一样）；以前的图片删掉，内容不变时不重写。"""
    data = tmp_path / "a" / "data"
    store(data, [play()])
    (data / "stats_button_deadbeef.svg").write_text("old", encoding="utf-8")
    refresh(data, now=T0)
    name = stats.mxu_button_name(data)
    assert name != stats.mxu_button_name(tmp_path / "b" / "data")
    assert sorted(p.name for p in data.glob("stats_button_*.svg")) == [name]
    svg = ElementTree.fromstring((data / name).read_text(encoding="utf-8"))
    assert svg.get("width") == "136" and "在浏览器里打开" in "".join(svg.itertext())
    mtime = (data / name).stat().st_mtime_ns
    os.utime(data / name, ns=(mtime - 10**9, mtime - 10**9))
    refresh(data, now=T0 + 60)
    assert (data / name).stat().st_mtime_ns == mtime - 10**9


@pytest.mark.parametrize(
    ("config", "port"),
    [
        (None, 12701),
        ('{"settings": {"webServerEnabled": true, "webServerPort": 13000}}', 13000),
        ('{"settings": {"webServerPort": 0}}', 12701),
        ('{"settings": {"webServerPort": "13000"}}', 12701),
        ('{"version": "1.0", "instances": [], "settings": []}', 12701),
        ("[]", 12701),
        ("{", 12701),
        ('{"settings": {"webServerEnabled": false, "webServerPort": 13000}}', None),
    ],
)
def test_mxu_buttons(tmp_path, config, port):
    """端口按 MXU 的配置，没有配置、读不了、端口不对时用默认的（同 MXU），连同后面 9 个（端口被占时 MXU 改用的）；
    关掉了网页服务时没有按钮。"""
    data = tmp_path / "data"
    data.mkdir()
    if config is not None:
        (tmp_path / stats.MXU_CONFIG).parent.mkdir()
        (tmp_path / stats.MXU_CONFIG).write_text(config, encoding="utf-8")
    image = stats.mxu_button_name(data)
    ports = range(port, port + 10) if port else []
    assert stats.mxu_buttons(data) == [
        (f"http://127.0.0.1:{p}/api/local-file?path=data/stats.html", f"http://127.0.0.1:{p}/api/local-file?path=data/{image}")
        for p in ports
    ]


def test_mxu_buttons_last_ports(tmp_path):
    """端口快到头时只到 65535（MXU 也不往后试了）。"""
    data = tmp_path / "data"
    data.mkdir()
    (tmp_path / stats.MXU_CONFIG).parent.mkdir()
    (tmp_path / stats.MXU_CONFIG).write_text('{"settings": {"webServerPort": 65530}}', encoding="utf-8")
    assert [url.split("/")[2] for url, _ in stats.mxu_buttons(data)] == [f"127.0.0.1:{p}" for p in range(65530, 65536)]


def test_mxu_config_name():
    """MXU 的配置文件名跟着 interface.json 的 name（mxu-<name>.json）。"""
    name = json.loads((Path(__file__).resolve().parent.parent / "interface.json").read_text(encoding="utf-8"))["name"]
    assert stats.MXU_CONFIG.name == f"mxu-{name}.json"


def test_html_escapes(tmp_path):
    store(tmp_path, [play()])
    live = Session(T0, T0 + 60, "挂机<1>")
    refresh(tmp_path, lambda mid: "<b>A&B</b>", live, now=T0 + 60)
    page = (tmp_path / PAGE_HTML).read_text(encoding="utf-8")
    assert "&lt;b&gt;A&amp;B&lt;/b&gt;" in page and "<b>A&B" not in page
    assert "挂机&lt;1&gt;" in page and "挂机<1>" not in page and page.count("运行中") == 1
    assert "<title>统计与成就 · Our Notes Auto</title>" in page  # 按标题找已经开着的窗口


def test_md_escape():
    assert stats._md("a|b*c<d>_[e]") == r"a\|b\*c\<d\>\_\[e\]"


def test_texts():
    assert progress_text(A["notes_10m"], 6_144_400) == "614 万 / 1,000 万个"
    assert progress_text(A["hours_500"], 328.14) == "328.1 / 500 小时"
    assert progress_text(A["plays_10000"], 7438) == "7,438 / 10,000 局"
    assert progress_text(A["plays_100"], 250) == "100 / 100 局"
    assert duration(1800) == "30 分钟" and duration(1234.5 * H) == "1,234.5 小时"


def test_texts_not_rounded_up():
    """没达成的不会四舍五入成「10 万 / 10 万个」；59.5 分钟起显示成小时。"""
    assert progress_text(A["notes_100k"], 99_999) == "9 万 / 10 万个"
    assert progress_text(A["notes_10m"], 9_995_000) == "999 万 / 1,000 万个"
    assert progress_text(A["hours_500"], 499.96) == "499.9 / 500 小时"
    assert progress_text(A["hours_10"], 10) == "10.0 / 10 小时"
    assert progress_text(A["plays_100"], 99.6) == "99 / 100 局"
    assert duration(3560) == "59 分钟" and duration(3590) == "1.0 小时"


def test_texts_percent_and_yi():
    """按完成了百分之几的、按亿显示的分数。"""
    assert progress_text(A["songs_all"], 42.7) == "42%"
    assert progress_text(A["score_1e10"], 3.56e9) == "35 亿 / 100 亿分"
    assert progress_text(A["score_1e9"], 2.5e8) == "2 亿 / 10 亿分"


def test_hidden_achievements(tmp_path, monkeypatch):
    """隐藏成就解锁前名字、条件都不露，只显示提示；没写提示的显示「达成后显示」；解锁后照常显示。"""
    monkeypatch.setenv("PI_CLIENT_NAME", "mfaa")
    store(tmp_path, [play(), play(2, great=1, t=60)])
    view = refresh(tmp_path, now=T0)
    assert "one_great" in view.unlocked and "same_chart_100" not in view.unlocked
    page = (tmp_path / PAGE_HTML).read_text(encoding="utf-8")
    md = (tmp_path / PAGE_MD).read_text(encoding="utf-8")
    card = (tmp_path / PAGE_MXU).read_text(encoding="utf-8")
    for text in (page, md, card):
        assert "单曲循环" not in text and "同一张谱面演奏 100 局" not in text and "白璧微瑕" in text
    assert "隐藏成就：反复打同一张谱面" in page
    assert "| ??? | 隐藏成就：反复打同一张谱面 | - |" in md and "| 白璧微瑕 | 只差一个 GREAT 就 ALL PERFECT |" in md
    assert "???：隐藏成就：反复打同一张谱面" in card
    assert stats.secret_text(dataclasses.replace(A["same_chart_100"], hint="")) == "隐藏成就：达成后显示"


def test_page_charts_by_song(tmp_path):
    """谱面页一首歌一组（<tbody>）：四个难度在同一行，打过 / 没 AP 的难度记在 data-p / data-n 上给筛选用；
    曲库里知道等级、没打过的难度也显示，曲库里没打过的歌另外列出来。"""
    store(tmp_path, [play(1, "expert"), play(1, "hard", great=2, t=60), play(2, "special", t=120), play(1, "expert", miss=1, t=180)])
    lib = stats.Library({1: "歌一", 2: "歌二", 3: "歌三"}, {1: "A", 2: "B", 3: "C"}, {(1, "easy"): 5, (1, "expert"): 25, (3, "expert"): 26})
    refresh(tmp_path, now=T0 + H, library=lib)
    page = (tmp_path / PAGE_HTML).read_text(encoding="utf-8")
    bodies = re.findall(r'<tbody data-m="(\d+)" data-k="([^"]*)" data-p="([^"]*)" data-n="([^"]*)">', page)
    assert bodies == [("1", "歌一 1 a", "23", "2"), ("2", "歌二 2 b", "x", "")]  # 最近打过的在前
    song = page[page.index('<tbody data-m="1"'):page.index("</tbody>", page.index('<tbody data-m="1"'))]
    assert song.count('class="dc ') == 4 and len(re.findall(r'class="dcard[ "]', song)) == 3  # EASY（没打过）、HARD、EXPERT
    assert '<span class="lv d0 none">5</span>' in song and '<span class="lv d3 ap">25</span>' in song
    assert "差 2" in song and "1 / 3 张 AP" in song
    assert "曲库里还没打过的 1 首" in page and "歌三" in page
    assert '显示 2 / 2 首' in page


def test_page_runs_paged(tmp_path):
    """运行记录分页：运行超过一页时有翻页条；每局都放进页面的数据里（新的在前），曲名转义后放进 <script> 也安全。"""
    n = stats.PAGE_SIZE + 3
    store(tmp_path, [play(1, t=i * 60) for i in range(n)])
    (tmp_path / SESSIONS).write_text("".join(json.dumps({"start": T0 + i * H, "end": T0 + i * H + 60}) + "\n" for i in range(n)), encoding="utf-8")
    refresh(tmp_path, lambda mid: "</script><b>", now=T0 + DAY)
    page = (tmp_path / PAGE_HTML).read_text(encoding="utf-8")
    assert re.search(rf'data-pager="sp" data-ps="{stats.RUNS_PAGE_SIZE}" data-n="{n}" data-u="次">', page)
    assert re.search(rf'data-pager="pp" data-ps="{stats.PAGE_SIZE}" data-n="{n}" data-u="局">', page)
    assert page.count('<tbody data-pg="sp">') == 1 and "</script><b>" not in page
    raw = page[page.index('id="plays-data">') + 16:]
    data = json.loads(raw[:raw.index("</script>")])
    assert data["t"] == {"1": "</script><b>"} and data["d"][3] == "expert" and len(data["r"]) == n
    assert data["r"][0] == [round(T0 + (n - 1) * 60), 1, 3, 100, 0, 0, 0, 0, 2, 1000]
    assert '<div class="seg" data-pm' not in page and 'id="arena-data"' not in page  # 没打过激奏舞台：没有子页签


def test_page_runs_paged_smaller(tmp_path):
    """运行记录每页比「全部演奏」少：二三十次运行就要翻页，不是全部列出来；同样多的局数「全部演奏」还在一页里。"""
    n = stats.RUNS_PAGE_SIZE + 1
    assert n <= stats.PAGE_SIZE
    store(tmp_path, [play(1, t=i * 60) for i in range(n)])
    (tmp_path / SESSIONS).write_text("".join(json.dumps({"start": T0 + i * H, "end": T0 + i * H + 60}) + "\n" for i in range(n)), encoding="utf-8")
    refresh(tmp_path, lambda mid: "歌", now=T0 + DAY)
    page = (tmp_path / PAGE_HTML).read_text(encoding="utf-8")
    assert re.search(rf'data-pager="sp" data-ps="{stats.RUNS_PAGE_SIZE}" data-n="{n}" data-u="次">.*?第 1 / 2 页', page)
    assert re.search(r'data-pager="pp"[^>]* hidden>', page)


def arena(band, placement, gain, total, lb, title, mid, played, t, score=None, counts=(None,) * 5):
    p, g, gd, b, m = counts
    return ArenaResult(band=band, placement=placement, ap_gain=gain, ap_total=total, lb_cost=lb, title=title, music_id=mid,
                       played=played, score=score, perfect=p, great=g, good=gd, bad=b, miss=m, timestamp=T0 + t)


def test_arena_plays():
    """激奏舞台演奏了的每一局对应同一首歌、时间不晚于它（差 30 秒以内）的最近一条演奏记录；没演奏的局不对应。"""
    records = [play(1, t=0), play(1, t=100), play(2, t=200), play(1, t=300), play(3, t=400)]
    games = [
        arena("mygo", 1, 10, 100, 0, "歌一", 1, True, 100),
        arena("mygo", 2, 5, 105, 0, "歌二", 2, True, 203),  # 以前先记演奏、再记对局
        arena("mygo", 3, 0, 105, 0, "歌一", 1, True, 340),  # 晚了 40 秒：不是 t=300 那局
        arena("mygo", 4, -5, 100, 0, "歌三", 3, False, 400),
        arena("mygo", 5, -10, 90, 0, "?", None, False, 500),
    ]
    assert stats.arena_plays(records, games) == {1, 2}
    assert stats.arena_plays(records, []) == set()


def test_page_arena_tab(tmp_path):
    """打过激奏舞台：「全部演奏」分普通演奏、激奏舞台两个子页签，激奏舞台打的局不列在普通演奏里（总数照常算）；
    激奏舞台每局的乐队、曲目、名次、段位分、LB、判定数、结果、分数都放进页面的数据里，上面有对局数、名次和各乐队段位分的汇总。"""
    store(tmp_path, [play(1, t=0), play(2, t=60, great=3), play(1, t=120)])
    s = RecordStore(tmp_path)
    s.append_arena(arena("mygo", 2, 15, 1015, 0, "歌二", 2, True, 60, 1000, (100, 3, 0, 0, 0)))
    s.append_arena(arena("mygo", 1, 20, 1035, -1, "読めない", None, False, 600, 900))
    s.append_arena(arena("avemujica", 5, -8, None, 3, "歌一", 1, True, 900))
    view = refresh(tmp_path, now=T0 + DAY, library=stats.Library({1: "歌一", 2: "歌二"}))
    assert view.totals.plays == 3 and [r.timestamp for r in view.plays] == [T0 + 120, T0]
    page = (tmp_path / PAGE_HTML).read_text(encoding="utf-8")
    assert '<button data-v="free">普通演奏<i>2</i></button><button data-v="arena">激奏舞台<i>3</i></button>' in page

    def data(key):
        mark = f'id="{key}-data">'
        raw = page[page.index(mark) + len(mark):]
        return json.loads(raw[:raw.index("</script>")])

    assert len(data("plays")["r"]) == 2
    d = data("arena")
    assert d["b"]["mygo"] == "MyGO!!!!!" and d["t"] == {"1": "歌一", "2": "歌二"}
    assert d["r"] == [
        [round(T0 + 900), "avemujica", 1, 5, -8, None, 3, None, None, None, None, None, 3, None],
        [round(T0 + 600), "mygo", "読めない", 1, 20, 1035, -1, None, None, None, None, None, 4, 900],
        [round(T0 + 60), "mygo", 2, 2, 15, 1015, 0, 100, 3, 0, 0, 0, 1, 1000],
    ]
    strip = page[page.index('<div data-pv="arena"'):page.index('id="arena"')]
    assert '<div class="fv">3<small>局</small></div><div class="fs">演奏了 2 局</div>' in strip
    assert '<div class="fv">2.67</div><div class="fs">第一名 1 局（33%）</div>' in strip
    assert '<div class="fk">MyGO!!!!!</div><div class="fv">1,035<small>分</small></div><div class="fs">2 局 · 平均第 1.5 名</div>' in strip
    assert '<div class="fk">Ave Mujica</div><div class="fv">-</div><div class="fs">1 局 · 平均第 5.0 名</div>' in strip
    assert "夢限大MewType" not in strip


def test_page_arena_only(tmp_path):
    """只打过激奏舞台：普通演奏的子页签里说还没有。"""
    store(tmp_path, [play(2, t=60)])
    RecordStore(tmp_path).append_arena(arena("mygo", 2, 15, 1015, 0, "歌二", 2, True, 60, 1000, (100, 0, 0, 0, 0)))
    view = refresh(tmp_path, now=T0 + DAY)
    assert view.plays == [] and len(view.arena) == 1
    page = (tmp_path / PAGE_HTML).read_text(encoding="utf-8")
    assert "还没有普通演奏" in page and 'id="plays-data"' not in page and 'id="arena-data"' in page


def test_band_names():
    """统计页上乐队的名字和设置里的一样。"""
    zh = json.loads((Path(__file__).resolve().parent.parent / "locales" / "zh_cn.json").read_text(encoding="utf-8"))
    assert stats_page.BAND_NAMES == {b: zh[f"opt.ArenaBand.{b}"] for b in ARENA_BANDS}


def test_page_runs_one_page(tmp_path):
    """不到一页时不显示翻页条。"""
    store(tmp_path, [play()])
    refresh(tmp_path, now=T0)
    page = (tmp_path / PAGE_HTML).read_text(encoding="utf-8")
    assert re.search(r'data-pager="pp"[^>]* hidden>', page)


def test_next_goals_skip_hidden_and_unlocked(tmp_path):
    store(tmp_path, [play(mid, t=mid * 60) for mid in range(9)])
    view = refresh(tmp_path, now=T0)
    goals = next_goals(view)
    assert {a.id for a in goals[:2]} == {"streak_10", "charts_10"} and len(goals) == 3
    assert not any(a.hidden or a.id in view.unlocked for a in goals)


def running(tmp_path):
    return sorted(p.name for p in (tmp_path / RUNNING).iterdir())


def test_tracker_lifecycle(tmp_path):
    """开始时写 running/<id>.json 并锁住 <id>.lock，每局后更新，结束时并进 sessions.jsonl；
    统计页上正在运行的这次也算时长。"""
    now = [T0]
    tracker = Tracker(tmp_path, "挂机", True, clock=lambda: now[0])
    tracker.start()
    assert running(tmp_path) == [tracker.file.name, tracker.file.with_suffix(".lock").name]
    current = json.loads(tracker.file.read_text(encoding="utf-8"))
    assert (current["task"], current["idle"], current["start"], current["end"]) == ("挂机", True, T0, T0)
    assert not (tmp_path / SESSIONS).exists() and (tmp_path / PAGE_MD).exists()

    now[0] += 600
    tracker.update(RunStats(plays=2, full_combo=2, all_perfect=1, lb_wait_s=123.456))
    current = json.loads(tracker.file.read_text(encoding="utf-8"))
    assert (current["plays"], current["ap"], current["lb_wait_s"], current["end"]) == (2, 1, 123.5, T0 + 600)
    assert "| 10 分钟 |" in (tmp_path / PAGE_MD).read_text(encoding="utf-8")
    assert "运行中" in (tmp_path / PAGE_HTML).read_text(encoding="utf-8")

    now[0] += 600
    tracker.finish(RunStats(plays=3, full_combo=3, all_perfect=2, skips=1))
    assert running(tmp_path) == []
    [s] = load_sessions(tmp_path)
    assert (s.task, s.idle, s.plays, s.ap, s.skips, s.seconds, s.restart) == ("挂机", True, 3, 2, 1, 1200, False)
    assert "| 20 分钟 |" in (tmp_path / PAGE_MD).read_text(encoding="utf-8")
    assert "运行中" not in (tmp_path / PAGE_HTML).read_text(encoding="utf-8")


def test_tracker_restart_for_update(tmp_path):
    """挂机为换新版本退出时运行记录记上 restart，接着重新开始的那次算同一次连续运行。"""
    now = [T0]
    a = Tracker(tmp_path, "挂机", True, clock=lambda: now[0])
    a.start()
    now[0] += 7 * H
    a.update(RunStats(plays=100, restart=True))  # 还没结束的不算
    assert json.loads(a.file.read_text(encoding="utf-8"))["restart"] is False
    a.finish(RunStats(plays=100, restart=True))
    now[0] += 60
    b = Tracker(tmp_path, "挂机", True, clock=lambda: now[0])
    b.start()
    now[0] += 6 * H
    b.finish(RunStats(plays=80))
    assert [s.restart for s in load_sessions(tmp_path)] == [True, False]
    assert refresh(tmp_path, now=now[0]).totals.longest_run_s == 13 * H + 60


def test_tracker_recovers_stale_session(tmp_path):
    """上次进程被强行结束留下的（锁没人锁着）：按最后一次更新补进运行记录。"""
    run = tmp_path / RUNNING
    run.mkdir()
    (run / "1-0.json").write_text(json.dumps({"start": T0, "end": T0 + H, "task": "重复刷歌", "plays": 5}), encoding="utf-8")
    (run / "1-0.lock").write_bytes(b"")
    tracker = Tracker(tmp_path, "挂机", clock=lambda: T0 + 2 * H)
    tracker.start()
    [stale] = load_sessions(tmp_path)
    assert (stale.task, stale.plays, stale.seconds) == ("重复刷歌", 5, H)
    assert running(tmp_path) == [tracker.file.name, tracker.file.with_suffix(".lock").name]

    (run / "1-1.json").write_text("{", encoding="utf-8")  # 坏了就不要了
    Tracker(tmp_path, "挂机", clock=lambda: T0 + 3 * H).start()
    assert len(load_sessions(tmp_path)) == 1 and not (run / "1-1.json").exists()


def test_tracker_two_instances(tmp_path):
    """多开共用 data：后开始的不会把正在运行的当成没正常结束的补记；统计页上两个都算时长、都标「运行中」，
    「统计与成就」任务（不带 live）也算上正在运行的。"""
    now = [T0]
    a = Tracker(tmp_path, "挂机", True, clock=lambda: now[0])
    a.start()
    now[0] += 5 * H
    a.update(RunStats(plays=100))
    b = Tracker(tmp_path, "重复刷歌", clock=lambda: now[0])
    b.start()
    assert not (tmp_path / SESSIONS).exists()
    now[0] += 5 * H
    a.update(RunStats(plays=200))
    b.update(RunStats(plays=50))
    view = refresh(tmp_path, now=now[0])
    assert view.totals.run_s == 15 * H and len(view.live) == 2
    assert (tmp_path / PAGE_HTML).read_text(encoding="utf-8").count("运行中") == 2
    a.finish(RunStats(plays=200))
    b.finish(RunStats(plays=50))
    assert [(s.task, s.seconds) for s in load_sessions(tmp_path)] == [("挂机", 10 * H), ("重复刷歌", 5 * H)]
    view = refresh(tmp_path, now=now[0])
    assert view.totals.run_s == 15 * H and view.totals.runs == 2 and view.live == []


def live_rows(tmp_path):
    """``stats_live.js`` 里的各项。"""
    text = (tmp_path / PAGE_LIVE).read_text(encoding="utf-8")
    assert text.startswith("statsLive(") and text.endswith(");\n")
    return json.loads(text[len("statsLive(") : -len(");\n")])


def test_tracker_status(tmp_path):
    """正在做什么写进 running/<id>.json 和 stats_live.js（统计页就地更新，不重新统计）：还在做同一件事时
    从开始做的时候算，换了一件从头算；还没开始、已经结束的不写。"""
    now = [T0]
    tracker = Tracker(tmp_path, "挂机", True, clock=lambda: now[0])
    tracker.status("select")
    assert not (tmp_path / RUNNING).exists() and not (tmp_path / PAGE_LIVE).exists()
    tracker.start()
    key = f"{T0:.1f}"
    assert live_rows(tmp_path) == [{"key": key, "upd": T0}]
    ver = (tmp_path / PAGE_VER).read_text(encoding="utf-8")

    now[0] += 60
    tracker.status("lb", "持有 0 个，每局消耗 3 个", eta=T0 + 400.04)
    now[0] += 60
    tracker.status("lb", "持有 1 个，每局消耗 3 个", eta=T0 + 400.04, daily=None)
    lb = {"kind": "lb", "detail": "持有 1 个，每局消耗 3 个", "since": T0 + 60, "eta": T0 + 400.0}
    assert live_rows(tmp_path) == [{**lb, "key": key, "upd": T0 + 120}]
    current = json.loads(tracker.file.read_text(encoding="utf-8"))
    assert current["now"] == lb and current["end"] == T0 + 120  # 进程被强行结束时补记到这里
    assert (tmp_path / PAGE_VER).read_text(encoding="utf-8") == ver

    now[0] += 60
    tracker.status("play", "詩超絆 · EXPERT 25")
    assert live_rows(tmp_path) == [{"kind": "play", "detail": "詩超絆 · EXPERT 25", "since": T0 + 180, "key": key, "upd": T0 + 180}]
    tracker.finish(RunStats(plays=1))
    assert live_rows(tmp_path) == []
    tracker.status("select")
    assert live_rows(tmp_path) == [] and running(tmp_path) == []


def test_live_skips_stopped(tmp_path):
    """进程被强行结束留下的（锁没人锁着）不算正在运行。"""
    tracker = Tracker(tmp_path, "重复刷歌", clock=lambda: T0 + 2 * H)
    tracker.start()
    row = {"start": T0, "end": T0 + H, "task": "挂机", "now": {"kind": "play", "detail": "", "since": T0}}
    (tmp_path / RUNNING / "1-0.json").write_text(json.dumps(row), encoding="utf-8")
    (tmp_path / RUNNING / "1-0.lock").write_bytes(b"")
    assert [r["key"] for r in stats.write_live(tmp_path)] == [f"{T0 + 2 * H:.1f}"]


def test_page_live_status(tmp_path):
    """统计页上正在运行的那条：页面生成时先写好在做什么（读不到 stats_live.js 时也有），之后由脚本就地更新；
    还没报上来在做什么的先不显示这一行；太久没更新的（进程被强行结束）标出来，时间停在最后一次更新。"""
    now = [T0]
    tracker = Tracker(tmp_path, "挂机", True, clock=lambda: now[0])
    tracker.start()
    page = (tmp_path / PAGE_HTML).read_text(encoding="utf-8")
    assert f'data-key="{T0:.1f}"' in page and '<div class="lnow" hidden>' in page
    assert "LIVE_CFG=" in page and "stats_live.js" in page

    now[0] += 600
    tracker.status("lb", "持有 0 个，每局消耗 3 个", eta=T0 + 780)
    refresh(tmp_path, now=T0 + 630)
    assert "刚开始 · 下一个 LB 2:30 后恢复" in (tmp_path / PAGE_HTML).read_text(encoding="utf-8")
    refresh(tmp_path, now=T0 + 720)
    page = (tmp_path / PAGE_HTML).read_text(encoding="utf-8")
    assert '<div class="live" ' in page and '<div class="lnow">' in page
    assert "等 LB 恢复" in page and "持有 0 个，每局消耗 3 个" in page and "已 2 分钟 · 下一个 LB 1:00 后恢复" in page
    assert "已进行<b>12 分钟</b>" in page

    refresh(tmp_path, now=T0 + 600 + 2 * H)
    page = (tmp_path / PAGE_HTML).read_text(encoding="utf-8")
    [line] = re.findall(r'<div class="lnow">(.*?)</div>', page)
    assert '<div class="live stale" ' in page and "超过 120 分钟没有更新，可能已经停止" in line and "后恢复" not in line
    assert "已进行<b>10 分钟</b>" in page


def test_live_kinds():
    """runner 报的每种「正在做什么」统计页上都有名字，图标都有。"""
    from ournotes_auto import runner, stats_page

    kinds = set(re.findall(r'self\._status\("(\w+)"', Path(runner.__file__).read_text(encoding="utf-8")))
    assert len(kinds) == 8 and kinds <= set(stats_page.LIVE_KINDS)
    assert all(f'id="{icon}"' in stats_page.SPRITE for _, icon in stats_page.LIVE_KINDS.values())


def test_sessions_deduped(tmp_path):
    """同一次运行记了两遍（结束时并进了 sessions.jsonl，但删 running 里的文件失败、下次开始又补记）只算一次。"""
    row = {"start": T0, "end": T0 + H, "task": "挂机", "plays": 5}
    (tmp_path / SESSIONS).write_text(json.dumps(row) + "\n", encoding="utf-8")
    (tmp_path / RUNNING).mkdir()
    (tmp_path / RUNNING / "1-0.json").write_text(json.dumps(row), encoding="utf-8")
    Tracker(tmp_path, "重复刷歌", clock=lambda: T0 + 2 * H).start()
    assert len(load_sessions(tmp_path)) == 2
    view = refresh(tmp_path, now=T0 + 2 * H)
    assert view.totals.runs == 2 and view.totals.run_s == H  # 挂机那次 + 正在运行的（0 秒）


def test_tracker_errors_do_not_propagate(tmp_path, monkeypatch, caplog):
    """统计出错不影响打歌：只记一次警告，之后的只记调试日志；运行记录照样记下。"""

    def broken(*args, **kwargs):
        raise OSError("磁盘满了")

    monkeypatch.setattr(stats, "refresh", broken)
    tracker = Tracker(tmp_path)
    with caplog.at_level(logging.DEBUG, logger="ournotes_auto.stats"):
        tracker.start()
        tracker.update(RunStats(plays=1))
        tracker.update(None)
        tracker.finish(RunStats(plays=1))
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == ["更新统计页失败（不影响演奏）：磁盘满了"]
    assert [s.plays for s in load_sessions(tmp_path)] == [1]


def test_tracker_report(tmp_path, caplog):
    """打歌任务结束时在日志里报一行战报；一局没打的不报。"""
    store(tmp_path, [play(), play(t=60, great=1)])
    now = [T0]
    tracker = Tracker(tmp_path, "挂机", clock=lambda: now[0])
    tracker.start()
    now[0] += 1800
    with caplog.at_level(logging.INFO, logger="ournotes_auto.stats"):
        tracker.finish(RunStats(plays=2, full_combo=2, all_perfect=1))
    assert unlocks(caplog)[-1] == f"本次运行 30 分钟；累计运行 30 分钟、演奏 2 局（AP 1），成就 5 / {N}"

    caplog.clear()
    idle = Tracker(tmp_path, "挂机", clock=lambda: now[0])
    idle.start()
    with caplog.at_level(logging.INFO, logger="ournotes_auto.stats"):
        idle.finish(RunStats())
    assert not any(m.startswith("本次运行") for m in unlocks(caplog))


def test_show_stats(tmp_path, monkeypatch, caplog):
    """任务「打开统计窗口」：日志里显示累计时长和成就，从界面运行时打开统计窗口；出错只记警告。"""
    from ournotes_auto import cli

    monkeypatch.chdir(tmp_path)
    store(tmp_path / "data", [play()])
    opened, lines, how = [], [], ["window"]
    monkeypatch.setattr(stats, "open_page", lambda page: opened.append(page) or how[0])
    cli._show_stats(stats.Library(), lines.append, open_page=True)
    page = (tmp_path / "data" / PAGE_HTML).resolve()
    assert lines[0].startswith("累计运行 ") and lines[1].startswith(f"成就 4 / {N}，最近解锁：")
    assert opened == [page] and lines[-1] == "已打开统计窗口（打完一局自动刷新）"
    how[0] = "focused"
    cli._show_stats(stats.Library(), lines.append, open_page=True)
    assert lines[-1] == "统计窗口已经开着，已切到前面"
    how[0] = "browser"
    cli._show_stats(stats.Library(), lines.append, open_page=True)
    assert lines[-1] == f"已在浏览器里打开统计页：{page}" and len(opened) == 3
    opened.clear()

    lines.clear()
    cli._show_stats(stats.Library(), lines.append, open_page=False)
    assert lines[-1] == f"完整的统计页：{page}" and opened == []

    lines.clear()
    monkeypatch.setattr(stats, "refresh", lambda *a, **k: 1 / 0)
    cli._show_stats(stats.Library(), lines.append, open_page=True)
    assert lines == [] and "更新统计页失败" in caplog.text


def test_stats_window_command(tmp_path, monkeypatch):
    """快捷方式运行的 stats-window：页面已有时先打开窗口再统计（窗口自己刷新），还没有时统计完再打开。"""
    from ournotes_auto import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_library", lambda cfg: stats.Library())
    calls = []
    real = stats.refresh
    monkeypatch.setattr(stats, "open_page", lambda page: calls.append(("open", page.exists())) or "window")
    monkeypatch.setattr(stats, "refresh", lambda *a, **k: calls.append(("refresh",)) or real(*a, **k))
    assert cli.cmd_stats_window(None, None) == 0
    assert calls == [("refresh",), ("open", True)]
    calls.clear()
    assert cli.cmd_stats_window(None, None) == 0
    assert calls == [("open", True), ("refresh",)]
    assert cli.build_parser().parse_args(["stats-window"]).func is cli.cmd_stats_window
