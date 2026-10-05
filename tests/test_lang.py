"""游戏切到其他语言时：OCR 文字换成简中说法后，画面判断照常。夹具是繁體中文、English 界面的实机截图。"""

import json
from pathlib import Path

import pytest

from ournotes_auto.nav.lang import localize
from ournotes_auto.nav.screens import Screen, band_confirm_song, classify
from ournotes_auto.result_reader import OcrItem

FIXTURES = Path(__file__).parent / "fixtures" / "screens"


def load(name: str) -> list[OcrItem]:
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return [OcrItem(x, y, w, h, localize(text)) for x, y, w, h, text in data]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("選擇樂曲", "乐曲选择"),
        ("選擇樂團故事章節", "乐队故事章节选择"),
        ("演出TOP", "演出首页"),
        ("轉蛋", "招募"),
        ("LIVE BOOST回復", "恢复LIVE BOOST"),
        ("確定要中斷演出並返回上一個畫面嗎？", "确定要终止演出并返回上一个画面吗？"),
        ("繼續", "继续"),
        ("乐曲选择", "乐曲选择"),  # 简中原样不动
    ],
)
def test_localize_zh_hant(text, expected):
    assert localize(text) == expected


@pytest.mark.parametrize(
    "name, screen",
    [
        ("zh_hant_home", Screen.HOME),
        ("zh_hant_live_top", Screen.LIVE_TOP),
        ("zh_hant_song_select", Screen.SONG_SELECT),
        ("zh_hant_band_confirm", Screen.BAND_CONFIRM),
        ("zh_hant_live_options", Screen.LIVE_OPTIONS),
        ("zh_hant_lb_setting", Screen.LB_SETTING),
        ("zh_hant_lb_recover", Screen.LB_RECOVER),
        ("zh_hant_pause", Screen.PAUSE),
        ("zh_hant_abort_confirm", Screen.ABORT_CONFIRM),
        ("zh_hant_retry_confirm", Screen.RETRY_CONFIRM),
        ("zh_hant_result", Screen.RESULT),
        ("zh_hant_result_next", Screen.RESULT_EXP_NEXT),  # 繁中每页结算都是「繼續」
        ("zh_hant_title_menu", Screen.TITLE_MENU),
        ("zh_hant_user_center", Screen.USER_CENTER),
        ("zh_hant_data_download", Screen.DATA_DOWNLOAD),
    ],
)
def test_classify_zh_hant(name, screen):
    assert classify(load(name)) is screen


def test_band_confirm_song_zh_hant():
    assert band_confirm_song(load("zh_hant_band_confirm")) == ("唱", "expert")


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Cancel", "取消"),
        ("Normal", "常规"),  # 任务的分页
        ("NORMAL", "NORMAL"),  # 难度，大小写不同不换
        ("Select Song", "乐曲选择"),
        ("SelectSong", "乐曲选择"),  # OCR 丢了空格
        ("Abort LIVE and Return to Previous Screen?", "要终止演出 and Return to Previous Screen?"),
        ("There are no gifts available to claim.", "There are 没有可领取的礼物."),
        ("3Day", "3天"),
        ("Chapter 11", "第11话"),
        ("Raika Point of view ver.", "Raika视角Ver."),
        ("Star×120", "星钻×120"),  # 认不出的弹窗上有价格就不点 OK
        ("Clear NORMAL of the song", "Clear NORMAL of the song"),
    ],
)
def test_localize_en(text, expected):
    assert localize(text) == expected


@pytest.mark.parametrize(
    "name, screen",
    [
        ("en_title", Screen.TITLE),
        ("en_data_download", Screen.DATA_DOWNLOAD),
        ("en_home", Screen.HOME),
        ("en_live_top", Screen.LIVE_TOP),
        ("en_song_select", Screen.SONG_SELECT),
        ("en_band_confirm", Screen.BAND_CONFIRM),
        ("en_lb_setting", Screen.LB_SETTING),
        ("en_lb_recover", Screen.LB_RECOVER),
        ("en_settings", Screen.SETTINGS),
        ("en_live_options", Screen.LIVE_OPTIONS),
        ("en_pause", Screen.PAUSE),
        ("en_abort_confirm", Screen.ABORT_CONFIRM),
        ("en_retry_confirm", Screen.RETRY_CONFIRM),
        ("en_live_end", Screen.LIVE_END),
        ("en_result", Screen.RESULT),
        ("en_result_reward", Screen.RESULT_REWARD),
        ("en_result_next", Screen.RESULT_EXP_NEXT),
        ("en_result_exp", Screen.RESULT_EXP),
        ("en_title_menu", Screen.TITLE_MENU),
        ("en_story_menu", Screen.STORY_MENU),
        ("en_story_skip", Screen.STORY_SKIP),
    ],
)
def test_classify_en(name, screen):
    assert classify(load(name)) is screen


def test_band_confirm_song_en():
    assert band_confirm_song(load("en_band_confirm")) == ("唱", "expert")
