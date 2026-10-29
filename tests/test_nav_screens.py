"""用实机截图的 OCR 结果（tests/fixtures/screens，由 tools/ocr_dump.py 生成）验证画面判断。"""

import json
from pathlib import Path

import pytest

from ournotes_auto.nav.lang import localize
from ournotes_auto.nav.screens import (
    CP_RADIO,
    NOTIFY_LOGO_Y,
    RESULT_ROW_Y,
    Screen,
    account_key,
    all_perfect_mark,
    arena_ap_text,
    arena_ap_total,
    arena_placement,
    arena_result,
    arena_song,
    band_confirm_song,
    challenge_rows,
    challenge_selected,
    classify,
    cp_bar_held,
    cp_held,
    home_talk,
    lb_bar_held,
    lb_all_left,
    lb_all_used,
    lb_bar_timer,
    lb_drinks,
    lb_held,
    lb_preview,
    lb_recover_amount,
    lb_rows,
    loading,
    login_expanded,
    login_rows,
    maintenance_period,
    note_speed,
    parse_difficulty,
    parse_level,
    result_cells,
    same_title,
    sdk_point,
    sdk_rect,
    sdk_scale,
    select_panel_title,
    skip_left,
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
        ("challenge_cp_setting_short", Screen.CP_SETTING),
        ("home", Screen.HOME),
        ("lb_setting", Screen.LB_SETTING),
        ("lb_setting_event", Screen.LB_SETTING),  # 活动期间每行也写着「活动pt」「挑战pt」
        ("lb_setting_4", Screen.LB_SETTING),  # 能选到 4：列表露出 1~4
        ("band_confirm_skip", Screen.BAND_CONFIRM),  # 还能跳过 2 次
        ("skip_confirm", Screen.SKIP_CONFIRM),  # 乐队确认页「跳过」打开的确认弹窗（选 LB 消耗）
        ("skip_result", Screen.SKIP_RESULT),
        ("skip_event_pt", Screen.SKIP_RESULT),  # 活动期间：右下角是「OK」
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
        ("loading_tip_lb", Screen.UNKNOWN),  # 加载小提示「玩家等级提升后，会恢复LIVE BOOST。」（#85）
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
        ("login_history_1440p", Screen.LOGIN_HISTORY),  # 2560x1440 下 SDK 弹窗约 0.75 倍（#54）
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
        # 主界面上点到背景里的角色弹出的对话；故事播放画面也是名牌加台词，但台词更靠左（导航按菜单按钮认）
        ("home_talk", Screen.HOME_TALK),
        ("story_player", Screen.UNKNOWN),
        ("daily_missions", Screen.UNKNOWN),  # 任务页左侧的「乐曲解锁」分页不算
        ("notice", Screen.POPUP),  # 登录后的公告
        # 激奏舞台（夹具里的玩家昵称都去掉了）
        ("arena_top", Screen.ARENA_TOP),
        ("arena_top_lb_timer", Screen.ARENA_TOP),  # LB 没满，右上角有恢复倒计时
        ("arena_match_confirm", Screen.ARENA_MATCH_CONFIRM),
        ("arena_matching", Screen.ARENA_MATCHING),  # 「正在匹配」，中间的转圈图写着 Now Loading
        ("arena_members", Screen.ARENA_MATCHING),  # 成员已确定，等转盘
        ("arena_difficulty", Screen.ARENA_DIFFICULTY),
        ("arena_result", Screen.ARENA_RESULT),
        ("arena_result_4th", Screen.ARENA_RESULT),
        ("arena_ranking", Screen.ARENA_RANKING),  # 「激奏」读成「激秦」
        ("arena_ranking_5th", Screen.ARENA_RANKING),  # 切到了演出记录
        ("arena_rank_up", Screen.ARENA_RANK_UP),  # 和羁绊等级的 RANK UP 一样大，但没有 OK，下面是新旧段位
        ("arena_tier_up", Screen.ARENA_RANK_UP),  # 升到新段位（BEGINNER→BRONZE）
        ("arena_tier_reward", Screen.REWARD),  # 之后的「首次段位达成奖励」
    ],
)
def test_classify(name, screen):
    assert classify(load(name)) is screen


def test_home_talk():
    """主界面角色对话：背景虚化读不出字，上面读出字来（主界面的顶栏等）就不是。"""
    talk = load("home_talk")
    assert home_talk(talk)
    assert not home_talk([*talk, OcrItem(130, 40, 120, 24, "Lv.120")])
    assert not home_talk([it for it in talk if it.y > 520])  # 没有名牌


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


def test_login_rows_scaled_sdk():
    """2560x1440 时 SDK 弹窗比按比例放大的小（#54）：账号名照样认得出。"""
    items = load("login_history_1440p")
    assert [it.text for it in login_rows(items)] == ["user_1234567890"]
    assert not login_expanded(items)


def test_sdk_scale():
    """缩放比按 bilibili 标志算：1280x720 的截图是 1；2560x1440 约 0.75，换算后和截图上量的位置差不多。"""
    assert sdk_scale(load("login_history")) == pytest.approx(1, abs=0.01)
    assert sdk_scale(load("user_center")) == pytest.approx(1, abs=0.02)
    assert sdk_scale(load("title_notify"), NOTIFY_LOGO_Y) == pytest.approx(1, abs=0.01)
    assert sdk_scale(load("title")) == 1  # 没有标志
    s = sdk_scale(load("login_history_1440p"))
    assert s == pytest.approx(0.745, abs=0.005)
    assert sdk_point((640, 497), s) == pytest.approx((640, 462), abs=2)  # 截图上「登录」在 (639,461)
    assert sdk_point((798, 305), s) == pytest.approx((759, 317), abs=2)  # ∨ 在 (759,317)
    assert sdk_rect((500, 265, 270, 310), s) == pytest.approx((535.7, 289.2, 201.1, 230.9), abs=0.1)


@pytest.mark.parametrize("name", ["login_history", "login_history_expanded", "login_history_1440p"])
def test_account_named_bilibili(name):
    """账号名带 bilibili（在标志下面）：不当成标志。"""
    items = load(name)
    renamed = [OcrItem(it.x, it.y, it.w, it.h, it.text.replace("user_", "bilibili_")) for it in items]
    assert classify(renamed) is Screen.LOGIN_HISTORY
    assert sdk_scale(renamed) == sdk_scale(items)
    assert [it.text for it in login_rows(renamed)] == [it.text.replace("user_", "bilibili_") for it in login_rows(items)]


def test_maintenance_period():
    assert maintenance_period(load("maintenance")) == "2026/10/02 11:00 ~ 2026/10/02 16:00"
    assert maintenance_period(load("title")) is None


def test_band_confirm_song():
    assert band_confirm_song(load("band_confirm")) == ("迷星叫", "expert")
    assert band_confirm_song(load("band_confirm_avemujica")) == ("AveMujica", "expert")
    assert band_confirm_song(load("band_confirm_lb_timer")) == ("无路矢", "expert")  # 繁体字都转成了简体


def test_arena_song():
    assert arena_song(load("arena_difficulty")) == ("端程山", 24)
    assert arena_song(load("arena_matching")) == ("", None)


def test_arena_result():
    """结果弹窗「结果 2nd AP+25」→ 名次、段位分增减，下面是加减后的段位分；排名页左边是大号的名次。"""
    items = load("arena_result")
    assert arena_result(items) == (2, 25) and arena_ap_total(items) == 72
    items = load("arena_result_4th")
    assert arena_result(items) == (4, 10) and arena_ap_total(items) == 82
    assert arena_result(load("arena_ranking")) == (None, None)
    assert arena_placement(load("arena_ranking")) == 2
    assert arena_placement(load("arena_ranking_5th")) == 5  # 右边的「2nd3rd4th」连成一项也不会认错
    assert arena_placement(load("arena_result")) is None


@pytest.mark.parametrize(
    "text, result",
    [
        ("结果 5th AP-5", (5, -5)),
        ("结果 5th AP一5", (5, -5)),  # 减号读成「一」
        ("结果 1st AP+3O", (1, 30)),
        ("结果 3rd", (3, None)),  # 段位分没读出来
        ("结果", (None, None)),
    ],
)
def test_arena_result_text(text, result):
    assert arena_result([OcrItem(581, 284, 217, 28, text)]) == result
    assert arena_result([OcrItem(581, 584, 217, 28, text)]) == (None, None)  # 不在弹窗标题的位置


@pytest.mark.parametrize(
    "text, value", [("8", 8), ("72", 72), ("1O5", 105), ("]2", 12), ("", None), ("AP", None), ("+8", None), ("123456", None)]
)
def test_arena_ap_text(text, value):
    """结果弹窗上段位分的直接识别结果：小号数字的 1、0 常读成 ]、O。"""
    assert arena_ap_text(text) == value


def test_arena_top_lb():
    """激奏舞台首页右上角的 LB 持有数、恢复倒计时和乐队确认页的在同一个位置。"""
    assert lb_bar_held(load("arena_top")) == 10 and lb_bar_timer(load("arena_top")) is None
    assert lb_bar_held(load("arena_top_lb_timer")) == 9 and lb_bar_timer(load("arena_top_lb_timer")) == 932


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
    assert cp_held(load("challenge_cp_setting_short")) == 798  # 选了 1600，提示「挑战pt不足。」
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
    assert lb_held(load("lb_setting_4")) == 4
    assert lb_held(load("skip_confirm")) == 1
    assert lb_held(load("lb_recover")) is None


@pytest.mark.parametrize(
    "name, x, rows",
    [
        ("lb_setting", 317, {0: 292, 1: 236, 2: 180, 3: 124}),
        ("lb_setting_event", 317, {0: 292, 1: 236, 2: 180, 3: 124}),
        ("lb_setting_4", 315, {1: 292, 2: 236, 3: 180, 4: 124}),  # 拖过的列表：「1」很小，读偏了也按其他几行推
        ("skip_confirm", 311, {0: 292, 1: 236, 2: 180, 3: 124}),  # 跳过确认弹窗靠左一点
    ],
)
def test_lb_rows(name, x, rows):
    got = lb_rows(load(name))
    assert abs(got.x - x) <= 2 and got.y == rows


def test_lb_rows_missing():
    assert lb_rows(load("lb_recover")) is None
    # 漏读一行照样推得出整列
    items = [it for it in load("lb_setting") if it.text != "2"]
    assert lb_rows(items).y == {0: 292, 1: 236, 2: 180, 3: 124}


def test_lb_rows_misaligned():
    """只读到两行、又对不上整列的位置（读偏、其实不是同一个列表）：按全部消耗数算，不能抛错（设置 LB 消耗时会中断）。"""
    items = [OcrItem(430, 200, 0, 0, "0"), OcrItem(430, 300, 0, 0, "1")]
    rows = lb_rows(items)
    assert rows is not None and rows.x == 307
    assert rows.y == {0: 278, 1: 222, 2: 166, 3: 110}


def test_lb_all_used_and_skip_left():
    assert lb_all_used(load("lb_setting")) and lb_all_used(load("lb_setting_4"))
    assert not lb_all_used(load("lb_setting_event")) and not lb_all_used(load("skip_confirm"))
    assert skip_left(load("band_confirm_skip")) == 2
    assert skip_left(load("skip_confirm")) == 2
    assert skip_left(load("band_confirm")) == 0
    assert skip_left(load("lb_setting")) is None


def test_lb_all_left():
    """「全部消耗」复选框上面的「今日剩余1次」；用完了、持有不够、别的语言、商店里的「今日剩余1次」都不算。"""
    assert lb_all_left(load("lb_setting_event")) == 1
    assert lb_all_left(load("ja_lb_setting")) == 1
    for name in ("lb_setting", "lb_setting_4", "en_lb_setting", "ko_lb_setting", "zh_hant_lb_setting", "skip_confirm", "shop_tgw_free"):
        assert lb_all_left(load(name)) is None, name


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
