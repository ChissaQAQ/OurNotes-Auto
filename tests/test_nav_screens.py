"""用实机截图的 OCR 结果（tests/fixtures/screens，由 tools/ocr_dump.py 生成）验证画面判断。"""

import json
from pathlib import Path

import pytest

from ournotes_auto.nav.lang import localize
from ournotes_auto.nav.screens import (
    CP_RADIO,
    RESULT_ROW_Y,
    Screen,
    account_key,
    all_perfect_mark,
    band_confirm_song,
    challenge_rows,
    challenge_selected,
    classify,
    cp_bar_held,
    cp_held,
    lb_bar_held,
    lb_bar_timer,
    lb_drinks,
    lb_held,
    lb_preview,
    lb_recover_amount,
    loading,
    login_expanded,
    login_rows,
    maintenance_period,
    note_speed,
    parse_difficulty,
    parse_level,
    result_cells,
    same_title,
    select_panel_title,
    title_startable,
)
from ournotes_auto.result_reader import OcrItem, classify_label
from ournotes_auto.sources import CHALLENGE_COSTS

FIXTURES = Path(__file__).parent / "fixtures" / "screens"


def load(name: str) -> list[OcrItem]:
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return [OcrItem(x, y, w, h, localize(text)) for x, y, w, h, text in data]


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
        ("band_power", Screen.POPUP),  # 乐队确认页上误点开的「综合能力详情」，左下是「关闭」
        ("live_options", Screen.LIVE_OPTIONS),
        ("live_top", Screen.LIVE_TOP),
        ("live_top_challenge", Screen.LIVE_TOP),  # 活动期间多了「挑战演出」
        ("challenge_song_select", Screen.CHALLENGE_SONG_SELECT),  # 没有分类、筛选、随机，只有活动的几首歌
        ("challenge_song_select_last", Screen.CHALLENGE_SONG_SELECT),
        ("challenge_song_select_ap", Screen.CHALLENGE_SONG_SELECT),  # 选中的歌已 AP（右侧有 ALL PERFECT）
        ("challenge_band_confirm", Screen.CHALLENGE_BAND_CONFIRM),  # 右下是「CP 设置」，顶栏是 CP
        ("challenge_cp_setting", Screen.CP_SETTING),
        ("home", Screen.HOME),
        ("lb_setting", Screen.LB_SETTING),
        ("lb_setting_event", Screen.LB_SETTING),  # 活动期间每行也写着「活动pt」「挑战pt」
        ("lb_recover", Screen.LB_RECOVER),
        ("lb_recover_drinks", Screen.LB_RECOVER),  # 两种 LIVE BOOST饮料都在的「道具」页
        ("lb_recover_held1", Screen.LB_RECOVER),  # 持有 1、选了 2 瓶小型
        ("lb_recover_confirm", Screen.LB_RECOVER_CONFIRM),  # 「将恢复1点LIVE BOOST。确定要恢复吗？」
        ("lb_recovered", Screen.LB_RECOVERED),  # 「已恢复LIVE BOOST。」
        ("pause", Screen.PAUSE),
        ("abort_confirm", Screen.ABORT_CONFIRM),  # 按实机画面手写（标题和右边按钮都是「终止」）
        ("retry_confirm", Screen.RETRY_CONFIRM),  # 暂停菜单点「重试」后的二次确认
        ("settings", Screen.SETTINGS),
        ("rank_up", Screen.RANK_UP),
        ("high_score", Screen.POPUP),
        ("intro_card", Screen.UNKNOWN),
        ("loading", Screen.UNKNOWN),
        ("date_change", Screen.DATE_CHANGE),
        ("title", Screen.TITLE),
        ("title_loading", Screen.TITLE),
        ("title_notify", Screen.NOTIFY),  # 标题画面上 B 站 SDK 的「开启消息通知」
        # 切换账号：标题菜单、用户中心（往下滚出现「注销」）、登录记录（收起 / 展开），都叠在标题画面上
        ("title_menu", Screen.TITLE_MENU),
        ("user_center", Screen.USER_CENTER),
        ("user_center_scrolled", Screen.USER_CENTER),
        ("login_history", Screen.LOGIN_HISTORY),
        ("login_history_expanded", Screen.LOGIN_HISTORY),
        ("connect_error", Screen.CONNECT_ERROR),  # 点 TAP TO START 后连不上服务器
        ("maintenance", Screen.MAINTENANCE),  # 服务器维护中（登录时、对局中途都会弹出）
        ("data_download", Screen.DATA_DOWNLOAD),  # 游戏更新后登录时下载追加数据
        ("update_required", Screen.UPDATE_REQUIRED),  # 检测到新版本：只有「前往商店」
        ("login_bonus_event", Screen.LOGIN_BONUS),
        ("login_bonus", Screen.LOGIN_BONUS),
        ("login_bonus_talk", Screen.LOGIN_BONUS),
        ("reward", Screen.REWARD),
        ("reward_claimed", Screen.REWARD),  # 评级提升奖励，标题在更上面
        ("grade_up", Screen.GRADE_UP),
        ("bond_up", Screen.BOND_UP),  # 羁绊等级的 RANK UP（不是玩家等级）
        ("band_rank_up", Screen.BOND_UP),  # 乐队RANK 的 RANK UP（结算羁绊页之后）
        ("daily_pass_pt", Screen.OK_POPUP),  # 领取日常里另外认（reward_ok），这里当没见过的 OK 弹窗
        ("story_song_unlock", Screen.UNLOCK),  # 乐曲解锁（看完故事）
        ("story_unlock", Screen.UNLOCK),  # 故事解锁：视角故事（看完乐队故事）
        ("bond_story_unlock", Screen.UNLOCK),  # 故事解锁：羁绊故事（结算后羁绊升级）
        ("home_unlock", Screen.UNLOCK),  # 沉浸式主页解锁（领通行证奖励后回主界面）
        ("new_song", Screen.NEW_SONG),  # 回主界面时的「追加翻唱乐曲！」演出
        ("birthday", Screen.SKIPPABLE),  # 重新登录后的角色生日演出，右上角「跳过」
        ("story_skip", Screen.STORY_SKIP),  # 「要跳过故事吗？」（中间的「跳过」不算可跳过的演出）
        ("story_player_menu", Screen.STORY_MENU),
        ("bond_player_menu", Screen.STORY_MENU),
        ("daily_missions", Screen.UNKNOWN),  # 任务页左侧的「乐曲解锁」分页不算
        ("notice", Screen.POPUP),  # 登录后的公告
    ],
)
def test_classify(name, screen):
    assert classify(load(name)) is screen


@pytest.mark.parametrize("extra", ["取消", "确定", "购买", "消耗星钻"])
def test_ok_popup_with_risky_text_is_not_tapped(extra):
    """有取消、确定、购买、星钻之类字样的弹窗不算只有 OK 的提示（不会去点）。"""
    items = [OcrItem(560, 300, 160, 30, "要继续吗？"), OcrItem(616, 645, 48, 31, "OK"), OcrItem(400, 645, 80, 31, extra)]
    assert classify(items) is Screen.UNKNOWN
    assert classify(items[:2]) is Screen.OK_POPUP


def test_loading():
    assert loading([OcrItem(1000, 650, 200, 30, "NOW LOADING")])
    assert loading([OcrItem(560, 400, 160, 30, "下载中…45%")])
    assert not loading(load("birthday"))


def test_title_startable():
    assert title_startable(load("title"))
    assert not title_startable(load("title_loading"))  # 刚启动，TAP TO START 还没出现


def test_login_rows():
    """登录记录的账号名：收起时只有选中的一行，展开后按最近登录从上到下；「登录」按钮不算。"""
    collapsed = load("login_history")
    assert [it.text for it in login_rows(collapsed)] == ["user_1234567890"]
    assert not login_expanded(collapsed)
    expanded = load("login_history_expanded")
    assert [it.text for it in login_rows(expanded)] == ["user_98765432100", "user_1234567890"]
    assert login_expanded(expanded)
    assert account_key(" User_１２３ ") == "user_123"


def test_maintenance_period():
    assert maintenance_period(load("maintenance")) == "2026/10/02 11:00 ~ 2026/10/02 16:00"
    assert maintenance_period(load("title")) is None


def test_band_confirm_song():
    assert band_confirm_song(load("band_confirm")) == ("迷星叫", "expert")
    assert band_confirm_song(load("band_confirm_avemujica")) == ("AveMujica", "expert")
    assert band_confirm_song(load("band_confirm_lb_timer")) == ("无路矢", "expert")  # 繁体字都转成了简体


def test_challenge_band_confirm_song():
    assert band_confirm_song(load("challenge_band_confirm")) == ("梦我梦中", "expert")


def test_setting_dialog_title():
    """标题读错、没有「全部消耗」时：有「CP」的是挑战pt消耗设置。"""
    cp = [it for it in load("challenge_cp_setting") if "挑战" not in it.text]
    assert classify([*cp, OcrItem(536, 104, 205, 32, "消耗设置")]) is Screen.CP_SETTING
    lb = [it for it in load("lb_setting_event") if it.text != "全部消耗"]
    assert classify(lb) is Screen.LB_SETTING  # 标题里有 LIVEBOOST
    lb = [it for it in lb if "消耗设置" not in it.text]
    assert classify([*lb, OcrItem(482, 19, 310, 27, "EBCOS消耗设置")]) is Screen.LB_SETTING


def test_cp_held():
    assert cp_held(load("challenge_cp_setting")) == 3708
    assert cp_bar_held(load("challenge_band_confirm")) == 3708
    assert cp_bar_held(load("band_confirm")) is None  # 自由演出的顶栏是 LB（「14/10」）
    assert cp_held(load("lb_setting")) is None
    assert cp_held(load("lb_setting_event")) is None
    assert cp_bar_held([OcrItem(1100, 19, 70, 29, "12,800")]) == 12800
    assert cp_bar_held([OcrItem(1100, 19, 70, 29, "37O8")]) == 3708  # O 读成 0
    assert cp_bar_held([OcrItem(1100, 19, 70, 29, "CP")]) is None


def test_cp_radio_matches_costs():
    assert tuple(CP_RADIO) == CHALLENGE_COSTS


def test_challenge_rows():
    rows = challenge_rows(load("challenge_song_select"))
    assert [t for t, _ in rows] == ["梦我梦中", "これはぼくたちの生存のあらすじ", "オリオンをなぞる"]  # 不含等级、MV
    assert challenge_selected(rows) == 0
    rows = challenge_rows(load("challenge_song_select_last"))  # 选中最后一首，列表滚到底
    assert [t for t, _ in rows][-1] == "オリオンをなぞる"
    assert challenge_selected(rows) == 2
    assert challenge_selected([("梦我梦中", 110.0), ("オリオンをなぞる", 212.0)]) is None
    rows = challenge_rows(load("challenge_song_select_ap"))  # 选中中间那首，选中行的曲名截断了
    assert [t for t, _ in rows] == ["梦我梦中", "これはぼくたちの生存の", "オリオンをなぞる"]
    assert challenge_selected(rows) == 1


@pytest.mark.parametrize(
    "name, title, ap",
    [
        ("challenge_song_select", "梦我梦中", False),  # 没打过：HIGH SCORE 0
        ("challenge_song_select_last", "オリオンをなぞる", False),
        ("challenge_song_select_ap", "これはぼくたちの生存のあらて", True),  # 面板上是完整曲名（末尾读错）
    ],
)
def test_select_panel(name, title, ap):
    items = load(name)
    assert select_panel_title(items) == title
    assert all_perfect_mark(items) is ap
    rows = challenge_rows(items)
    assert same_title(rows[challenge_selected(rows)][0], title)


def test_same_title():
    assert same_title("これはぼくたちの生存の", "これはぼくたちの生存のあらすじ")  # 选中行截断
    assert same_title("梦我梦中", "梦我梦中 ")
    assert same_title(";ぼくたちの生存のあらす", "これはぼくたちの生存のあらて")  # 选中行的曲名在滚动
    assert same_title("の生存のあらすじこれは", "これはぼくたちの生存のあらすじ")
    assert not same_title("梦我梦中", "オリオンをなぞる")
    assert not same_title("梦我梦中", None) and not same_title("", "")
    assert same_title("R", "R") and not same_title("R", "Rubato")


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
    assert lb_held(load("lb_setting_event")) == 8
    assert lb_held(load("lb_recover")) is None


def test_lb_drinks():
    small, big = lb_drinks(load("lb_recover_drinks"))
    assert (small.name, small.lb, small.chosen, small.owned) == ("小型LIVE BOOST饮料", 1, 0, 24)
    assert (big.name, big.lb, big.chosen, big.owned) == ("LIVE BOOST饮料", 10, 0, 5)
    assert small.y < big.y
    (only,) = lb_drinks(load("lb_recover"))  # OCR 把 V 认成小写 v 也要认出来
    assert (only.lb, only.chosen, only.owned) == (1, 0, 12)
    assert lb_drinks(load("lb_setting")) == []
    small, big = lb_drinks(load("lb_recover_held1"))  # 选了 2 瓶小型
    assert (small.chosen, small.owned, big.chosen, big.owned) == (2, 22, 0, 5)
    # 数量不在名字那一行（离得太远）的不算
    assert lb_drinks([OcrItem(392, 144, 232, 26, "小型LIVE BOOST饮料"), OcrItem(768, 400, 70, 45, "0/24")]) == []


def test_lb_preview_and_amount():
    assert lb_preview(load("lb_recover_drinks")) == (3, 3)
    assert lb_preview(load("lb_recover")) == (24, 24)
    assert lb_preview(load("lb_setting")) is None
    assert lb_preview(load("lb_recover_held1")) == (None, 3)  # 持有 1 选了 2 瓶：「1 ▶ 3」左边的 1 漏读
    assert lb_preview([OcrItem(760, 566, 20, 17, "|")]) is None  # ▶ 读成 | 也不算数字
    assert lb_recover_amount(load("lb_recover_confirm")) == 1
    assert lb_recover_amount(load("lb_recovered")) is None


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


def test_home_with_one_entry_unreadable():
    """背景亮的时候底部入口个别字读不出（实机上漏过「招募」），认出两个就算主界面。"""
    row = [("商店", 690), ("故事", 804), ("乐队", 916)]
    assert classify([OcrItem(x - 20, 650, 40, 20, text) for text, x in row]) is Screen.HOME
    assert classify([OcrItem(784, 650, 40, 20, "故事")]) is Screen.UNKNOWN
