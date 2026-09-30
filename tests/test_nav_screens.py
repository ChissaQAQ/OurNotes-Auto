"""用实机截图的 OCR 结果（tests/fixtures/screens，由 tools/ocr_dump.py 生成）验证画面判断。"""

import json
from pathlib import Path

import pytest

from ournotes_auto.nav.screens import (
    RESULT_ROW_Y,
    Screen,
    band_confirm_song,
    classify,
    lb_bar_held,
    lb_bar_timer,
    lb_held,
    note_speed,
    parse_difficulty,
    parse_level,
    result_cells,
    title_startable,
)
from ournotes_auto.result_reader import OcrItem, classify_label

FIXTURES = Path(__file__).parent / "fixtures" / "screens"


def load(name: str) -> list[OcrItem]:
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return [OcrItem(x, y, w, h, text) for x, y, w, h, text in data]


@pytest.mark.parametrize(
    "name, screen",
    [
        ("result", Screen.RESULT),
        ("result_timing", Screen.RESULT),
        ("result_reward", Screen.RESULT_REWARD),
        ("result_exp", Screen.RESULT_EXP),
        ("result_exp_next", Screen.RESULT_EXP_NEXT),  # 活动期间的羁绊页只有「下一步」
        ("result_event", Screen.RESULT_EXP),  # 活动结算页（活动pt），也有「再次演出」
        ("event_story_unlock", Screen.UNLOCK),  # 故事解锁：活动故事
        ("event_pt_reward", Screen.REWARD),  # 获得活动pt达成奖励
        ("live_clear", Screen.LIVE_END),
        ("live_finish", Screen.LIVE_END),
        ("achievement", Screen.ACHIEVEMENT),
        ("song_select", Screen.SONG_SELECT),
        ("band_confirm", Screen.BAND_CONFIRM),
        ("band_confirm_avemujica", Screen.BAND_CONFIRM),
        ("band_confirm_lb_timer", Screen.BAND_CONFIRM),  # LB 没满，顶栏有恢复倒计时
        ("live_options", Screen.LIVE_OPTIONS),
        ("live_top", Screen.LIVE_TOP),
        ("home", Screen.HOME),
        ("lb_setting", Screen.LB_SETTING),
        ("lb_recover", Screen.LB_RECOVER),
        ("pause", Screen.PAUSE),
        ("abort_confirm", Screen.ABORT_CONFIRM),  # 按实机画面手写（标题和右边按钮都是「终止」）
        ("settings", Screen.SETTINGS),
        ("rank_up", Screen.RANK_UP),
        ("high_score", Screen.POPUP),
        ("intro_card", Screen.UNKNOWN),
        ("loading", Screen.UNKNOWN),
        ("date_change", Screen.DATE_CHANGE),
        ("title", Screen.TITLE),
        ("title_loading", Screen.TITLE),
        ("login_bonus_event", Screen.LOGIN_BONUS),
        ("login_bonus", Screen.LOGIN_BONUS),
        ("login_bonus_talk", Screen.LOGIN_BONUS),
        ("reward", Screen.REWARD),
        ("reward_claimed", Screen.REWARD),  # 评级提升奖励，标题在更上面
        ("grade_up", Screen.GRADE_UP),
        ("bond_up", Screen.BOND_UP),  # 羁绊等级的 RANK UP（不是玩家等级）
        ("story_song_unlock", Screen.UNLOCK),  # 乐曲解锁（看完故事）
        ("story_unlock", Screen.UNLOCK),  # 故事解锁：视角故事（看完乐队故事）
        ("bond_story_unlock", Screen.UNLOCK),  # 故事解锁：羁绊故事（结算后羁绊升级）
        ("daily_missions", Screen.UNKNOWN),  # 任务页左侧的「乐曲解锁」分页不算
        ("notice", Screen.POPUP),  # 登录后的公告
    ],
)
def test_classify(name, screen):
    assert classify(load(name)) is screen


def test_title_startable():
    assert title_startable(load("title"))
    assert not title_startable(load("title_loading"))  # 刚启动，TAP TO START 还没出现


def test_band_confirm_song():
    assert band_confirm_song(load("band_confirm")) == ("迷星叫", "expert")
    assert band_confirm_song(load("band_confirm_avemujica")) == ("AveMujica", "expert")
    assert band_confirm_song(load("band_confirm_lb_timer")) == ("無路矢", "expert")


def test_parse_difficulty_tolerates_ocr_noise():
    assert parse_difficulty("今EASY") == "easy"
    assert parse_difficulty("NORMA[") == "normal"
    assert parse_difficulty("EXPERT") == "expert"
    assert parse_difficulty("Lv.25") is None


@pytest.mark.parametrize(
    "text, level",
    [("24", 24), ("6", 6), ("2T", 21), ("2|", 21), ("1l", 11), ("", None), ("t.24(", None), ("241", None), ("0", None)],
)
def test_parse_level(text, level):
    assert parse_level(text) == level


def test_note_speed():
    assert note_speed(load("live_options")) == 5.0
    assert note_speed(load("band_confirm")) is None


def test_lb_held():
    assert lb_held(load("lb_setting")) == 14
    assert lb_held(load("lb_recover")) is None


@pytest.mark.parametrize("text, held", [("]4/10", 14), ("0/10", 0), ("O/10", 0), ("10/10", 10), ("09:09", None)])
def test_lb_bar_held(text, held):
    assert lb_bar_held(load("band_confirm")) == 14  # 夹具里是 ]4/10
    assert lb_bar_held([OcrItem(1056, 25, 45, 20, text)]) == held
    assert lb_bar_held([OcrItem(650, 563, 67, 24, "0/99")]) is None  # 消耗设置弹窗上的持有数不算


@pytest.mark.parametrize(
    "text, left",
    [("15:24", 924), ("©21:11", 1271), ("O9:5]", 591), ("29:59", 1799), ("6/10", None), ("12:75", None), ("", None)],
)
def test_lb_bar_timer(text, left):
    assert lb_bar_timer(load("band_confirm_lb_timer")) == 924
    assert lb_bar_held(load("band_confirm_lb_timer")) == 6  # 倒计时在持有数下面一行，不会混淆
    assert lb_bar_timer(load("band_confirm")) is None  # 持有数到上限后不显示
    assert lb_bar_timer([OcrItem(1034, 46, 75, 29, text)]) == left
    assert lb_bar_timer([OcrItem(1054, 21, 50, 26, "21:11")]) is None  # 持有数那一行


def test_result_cells_follow_labels():
    assert classify_label("G00D") == "good"
    items = load("result_timing")
    cells = result_cells(items, timing=True)
    assert set(cells) == set(RESULT_ROW_Y)
    for j, (fast, slow) in cells.items():
        # FAST 列在 SLOW 列左边，同一行
        assert fast[0] < slow[0] and fast[1] == slow[1]
        assert abs(fast[1] + fast[3] / 2 - RESULT_ROW_Y[j]) < 5
    assert all(len(v) == 1 for v in result_cells(items, timing=False).values())
