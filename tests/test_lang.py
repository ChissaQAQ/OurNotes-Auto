"""游戏切到其他语言时：OCR 文字换成简中说法后，画面判断照常。夹具是繁體中文、English、한국어界面和日服的实机截图。"""

import json
from pathlib import Path

import pytest

from ournotes_auto.nav import daily, song_select, story
from ournotes_auto.nav.lang import localize
from ournotes_auto.nav.ocr import _pick
from ournotes_auto.nav.screens import Screen, band_confirm_song, classify, find, lb_drinks, lb_held, lb_recover_amount
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


def test_daily_en():
    # 第一个通行证的日期读漏了开头的「2」
    assert [(d, done) for d, _, done in daily.pass_banners(load("en_daily_pass"))] == [
        ("026/10/08", True),
        ("2026/10/28", False),
    ]
    assert daily.gift_confirm(load("en_daily_gifts_confirm"))


def test_story_en():
    assert story.story_menu_open(load("en_story_menu_bond"))
    items = load("en_bond_members")
    assert story.story_title(items) == story.BOND_MEMBERS_TITLE
    names = [t for t, _ in story.member_names(items)]
    assert names == ["Anon Chihaya", "Soyo Nagasaki", "Tomori Takamatsu", "Taki Shiina", "Rāna Kaname"]
    items = load("en_bond_pairs")
    assert story.story_title(items) == story.BOND_PAIRS_TITLE
    assert [t for t, _ in story.bond_pairs(items)] == ["Anon&Tomori", "Anon&Rāna", "Anon&Soyo", "Anon&Taki"]
    items = load("en_bond_episodes")
    assert story.bond_popup(items)
    assert [t for t, _ in story.bond_episode_rows(items)] == ["第2话", "第1话"]


def test_lb_held_en():
    assert lb_held(load("en_lb_setting")) == 2  # 「2 / 99」读成「2199」


@pytest.mark.parametrize(
    "main, extra, scores, expected",
    [
        ("れ", "레", (0.9, 0.9), "れ"),  # 假名
        ("", "미션", (0.0, 1.0), "미션"),
        ("0/5", "이5", (0.99, 0.77), "0/5"),
        ("0/1", "이1", (1.0, 0.96), "0/1"),
        ("1", "1회남음", (0.99, 1.0), "1회남음"),
        ("退出登录", "原出끔志", (1.0, 0.32), "退出登录"),  # B 站登录界面还是简中
        ("bilibili", "내i내", (0.94, 0.76), "bilibili"),
        ("引", "리셋", (0.88, 0.91), "리셋"),
    ],
)
def test_pick(main, extra, scores, expected):
    assert _pick(main, extra, *scores) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("확인", "OK"),
        ("취소", "取消"),
        ("일시정지", "暂停"),
        ("구매하기", "购买"),
        ("무매학기", "购买"),
        ("일괄 수령", "一键领取"),
        ("제 3 화", "第3话"),
        ("3일차", "3天"),
        ("1회 남음", "还剩1次"),
        ("라이브 부스트 회복", "恢复LIVE BOOST"),
        ("서버전환", "切换服务器"),
        ("ALL PERFECT", "ALL PERFECT"),
    ],
)
def test_localize_ko(text, expected):
    assert localize(text) == expected


@pytest.mark.parametrize(
    "name, screen",
    [
        ("ko_title", Screen.TITLE),
        ("ko_title_menu", Screen.TITLE_MENU),
        ("ko_user_center", Screen.USER_CENTER),
        ("ko_data_download", Screen.DATA_DOWNLOAD),
        ("ko_home", Screen.HOME),
        ("ko_settings", Screen.SETTINGS),
        ("ko_live_top", Screen.LIVE_TOP),
        ("ko_live_top_challenge", Screen.LIVE_TOP),
        ("ko_song_select", Screen.SONG_SELECT),
        ("ko_band_confirm", Screen.BAND_CONFIRM),
        ("ko_band_power", Screen.POPUP),  # 综合能力详情
        ("ko_challenge_song_select", Screen.CHALLENGE_SONG_SELECT),
        ("ko_challenge_band_confirm", Screen.CHALLENGE_BAND_CONFIRM),
        ("ko_challenge_cp_setting", Screen.CP_SETTING),
        ("ko_lb_setting", Screen.LB_SETTING),
        ("ko_lb_recover", Screen.LB_RECOVER),
        ("ko_lb_recover_selected", Screen.LB_RECOVER),
        ("ko_lb_recover_confirm", Screen.LB_RECOVER_CONFIRM),
        ("ko_live_options", Screen.LIVE_OPTIONS),
        ("ko_pause", Screen.PAUSE),
        ("ko_abort_confirm", Screen.ABORT_CONFIRM),
        ("ko_retry_confirm", Screen.RETRY_CONFIRM),
        ("ko_live_end", Screen.LIVE_END),
        ("ko_result", Screen.RESULT),
        ("ko_result_reward", Screen.RESULT_REWARD),
        ("ko_result_exp_next", Screen.RESULT_EXP_NEXT),
        ("ko_result_event", Screen.RESULT_EXP),
        ("ko_story_player_menu", Screen.STORY_MENU),
        ("ko_story_skip", Screen.STORY_SKIP),
        ("ko_bond_episodes", Screen.POPUP),
    ],
)
def test_classify_ko(name, screen):
    assert classify(load(name)) is screen


def test_band_confirm_song_ko():
    assert band_confirm_song(load("ko_band_confirm")) == ("これはぼくたちの生存のあらすじ", "expert")
    assert band_confirm_song(load("ko_challenge_band_confirm")) == ("梦我梦中", "expert")


def test_lb_ko():
    drinks = lb_drinks(load("ko_lb_recover"))
    assert [(d.lb, d.owned) for d in drinks] == [(1, 48), (10, 5)]
    assert lb_recover_amount(load("ko_lb_recover_confirm")) == 1
    assert lb_held(load("ko_lb_setting")) == 2


@pytest.mark.parametrize(
    "name, title",
    [
        ("ko_daily_missions", "任务"),
        ("ko_daily_missions_regular", "任务"),
        ("ko_daily_pass", "任务通行证"),
        ("ko_daily_pass_missions", "通行证任务"),
        ("ko_daily_limited", "限定任务"),
        ("ko_daily_beginner", "新手任务"),
        ("ko_daily_gifts", "礼物盒"),
        ("ko_daily_studio", "录音室练习"),
        ("ko_shop", "商店"),
        ("ko_shop_tgw", "商店"),
    ],
)
def test_daily_page_title_ko(name, title):
    assert daily.page_title(load(name)) == title


def test_daily_ko():
    assert [(d, done) for d, _, done in daily.pass_banners(load("ko_daily_pass"))] == [
        ("2026-10-08", True),
        ("2026-10-28", False),
    ]
    assert [t for t, _ in daily.day_tabs(load("ko_daily_beginner"))] == [f"{i}天" for i in range(1, 8)]
    assert daily.reward_ok(load("ko_daily_studio_reward"))
    assert daily.practice_level_up(load("ko_daily_studio_levelup"))
    assert daily.gift_confirm(load("ko_daily_gifts_confirm"))
    items = load("ko_shop_tgw")
    assert daily.free_buys(items) == []
    assert daily.gem_balance(items) == 24810


@pytest.mark.parametrize(
    "name, title",
    [
        ("ko_story_chapters", story.CHAPTERS_TITLE),
        ("ko_story_episodes", story.EPISODES_TITLE),
        ("ko_story_pov", story.EPISODES_TITLE),
        ("ko_bond_members", story.BOND_MEMBERS_TITLE),
        ("ko_bond_pairs", story.BOND_PAIRS_TITLE),
    ],
)
def test_story_title_ko(name, title):
    assert story.story_title(load(name)) == title


def test_story_ko():
    assert story.story_menu_open(load("ko_story_menu"))
    items = load("ko_story_episodes")
    assert story.selected_episode(items) == "第10话"
    assert [t for t, _ in story.episode_cards(items)] == ["第10话", "第11话", "第12话"]
    items = load("ko_story_pov")
    assert story.selected_pov(items) == "미쿠"
    assert [t for t, _ in story.pov_cards(items)] == ["미쿠", "요모기", "치에리"]
    assert len(story.member_names(load("ko_bond_members"))) == 5
    assert [t for t, _ in story.bond_pairs(load("ko_bond_pairs"))] == ["아논&토모리", "아논&라나", "아논&소요", "아논&타키"]
    items = load("ko_bond_episodes")
    assert story.bond_popup(items)
    assert [t for t, _ in story.bond_episode_rows(items)] == ["第2话", "第1话"]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("キャンセル", "取消"),
        ("閉じる", "关闭"),
        ("ライプTOP", "演出首页"),  # 「ブ」读成「プ」
        ("ショッ", "商店"),
        ("一括受け取り", "一键领取"),
        ("あと1回", "还剩1次"),
        ("ライブブースト回復", "恢复LIVE BOOST"),
        ("1日目", "1天"),
        ("燈視点Ver.", "灯视角Ver."),
        ("バンドスト", "乐队故事"),  # 字没读全
        ("ストーリーをスキップしますか？", "要跳过故事吗？"),
        ("もう一回ライブ", "再次演出"),
        ("迷星叫", "迷星叫"),  # 曲名只有汉字，照常转简体
    ],
)
def test_localize_ja(text, expected):
    assert localize(text) == expected


@pytest.mark.parametrize(
    "name, screen",
    [
        ("ja_title", Screen.TITLE),
        ("ja_notify_permission", Screen.NOTIFY_PERMISSION),  # 系统问要不要允许发送通知
        ("ja_home", Screen.HOME),
        ("ja_story_menu", Screen.HOME),  # 主界面上展开的故事菜单
        ("ja_settings", Screen.SETTINGS),
        ("ja_live_top", Screen.LIVE_TOP),
        ("ja_song_select", Screen.SONG_SELECT),
        ("ja_band_confirm", Screen.BAND_CONFIRM),
        ("ja_lb_setting", Screen.LB_SETTING),
        ("ja_lb_recover", Screen.LB_RECOVER),
        ("ja_live_options", Screen.LIVE_OPTIONS),
        ("ja_pause", Screen.PAUSE),
        ("ja_abort_confirm", Screen.ABORT_CONFIRM),
        ("ja_retry_confirm", Screen.RETRY_CONFIRM),
        ("ja_result_event", Screen.RESULT_EXP),  # 活动结算：「もう一回ライブ」
        ("ja_story_player_menu", Screen.STORY_MENU),
        ("ja_story_skip", Screen.STORY_SKIP),
        ("ja_daily_reward", Screen.REWARD),
        ("ja_daily_gifts_reward", Screen.REWARD),
        ("ja_daily_missions_claimed", Screen.REWARD),
        ("ja_story_reward", Screen.REWARD),
        ("ja_daily_pass_pt", Screen.OK_POPUP),  # 获得通行证pt
        ("ja_shop_age_check", Screen.UNKNOWN),  # 年龄确认：不点
    ],
)
def test_classify_ja(name, screen):
    assert classify(load(name)) is screen


def test_band_confirm_song_ja():
    assert band_confirm_song(load("ja_band_confirm")) == ("迷星叫", "expert")


def test_lb_ja():
    assert lb_drinks(load("ja_lb_recover")) == []  # 没有饮料
    assert lb_held(load("ja_lb_setting")) == 10


def test_song_select_ja():
    items = load("ja_song_filter")
    assert song_select.filter_open(items)
    assert song_select.song_category(items) == "全部"
    for text in ("EASY", "NORMAL", "HARD", "EXPERT"):
        assert song_select.filter_option(items, text)
    items = load("ja_song_filter_status")
    for text in song_select.STATUS_OPTIONS.values():
        assert song_select.filter_option(items, text), text
    assert not song_select.filter_open(load("ja_song_select"))
    assert song_select.list_empty(load("ja_song_select_empty"))
    assert find(load("ja_song_select_no_random"), song_select.NO_RANDOM_TEXT)
    assert song_select.song_locked(load("ja_song_select_locked"))
    assert not song_select.song_locked(load("ja_song_select"))


@pytest.mark.parametrize(
    "name, title",
    [
        ("ja_daily_missions", "任务"),
        ("ja_daily_missions_regular", "任务"),
        ("ja_daily_pass", "任务通行证"),
        ("ja_daily_pass_missions", "通行证任务"),
        ("ja_daily_beginner", "新手任务"),
        ("ja_daily_gifts", "礼物盒"),
    ],
)
def test_daily_page_title_ja(name, title):
    assert daily.page_title(load(name)) == title


def test_daily_ja():
    assert [(d, done) for d, _, done in daily.pass_banners(load("ja_daily_pass"))] == [
        ("2026/10/08", True),
        ("2026/10/28", False),
    ]
    # 选中第二个时截止时间拆成了「2026/10」「/28」「13:59」
    assert [(d, done) for d, _, done in daily.pass_banners(load("ja_daily_pass_second"))] == [
        ("2026/10/08", False),
        ("2026/10", True),
    ]
    assert [t for t, _ in daily.day_tabs(load("ja_daily_beginner"))] == ["1天"]
    assert daily.reward_ok(load("ja_daily_missions_claimed"))
    assert daily.reward_ok(load("ja_daily_pass_pt"))
    assert daily.reward_ok(load("ja_story_reward"))
    assert daily.gift_confirm(load("ja_daily_gifts_confirm"))
    assert daily.reward_ok(load("ja_shop_age_check")) is None


@pytest.mark.parametrize(
    "name, title",
    [
        ("ja_story_chapters", story.CHAPTERS_TITLE),
        ("ja_story_episodes", story.EPISODES_TITLE),
        ("ja_story_pov", story.EPISODES_TITLE),
        ("ja_bond_members", story.BOND_MEMBERS_TITLE),
    ],
)
def test_story_title_ja(name, title):
    assert story.story_title(load(name)) == title


def test_story_ja():
    assert story.story_menu_open(load("ja_story_menu"))
    items = load("ja_story_episodes")
    assert story.selected_episode(items) == "第1话"
    assert [t for t, _ in story.episode_cards(items)] == ["第1话", "第2话"]
    items = load("ja_story_pov")
    assert story.selected_pov(items) == "灯"
    assert [t for t, _ in story.pov_cards(items)] == ["灯", "爱音", "楽奈"]
    assert len(story.member_names(load("ja_bond_members"))) == 5
    assert story.event_story_button(load("ja_event_page"))
