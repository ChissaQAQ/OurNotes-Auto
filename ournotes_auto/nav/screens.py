"""按 OCR 结果判断当前画面（坐标均为 1280x720 设计尺寸，实机校准于 MuMu 国际服简中客户端）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

from ..result_reader import OcrItem, _norm, classify_label, find_labels

Rect = tuple[float, float, float, float]  # x, y, w, h


class Screen(StrEnum):
    UNKNOWN = "未知"  # 加载、转场、演奏中等
    HOME = "主界面"
    LIVE_TOP = "演出首页"
    SONG_SELECT = "乐曲选择"
    BAND_CONFIRM = "乐队确认"
    # 挑战演出（部分活动期间开放，从演出首页的「挑战演出」进）：选曲页只有活动指定的几首歌，乐队确认页消耗挑战pt（CP）
    CHALLENGE_SONG_SELECT = "挑战演出-乐曲选择"
    CHALLENGE_BAND_CONFIRM = "挑战演出-乐队确认"
    CP_SETTING = "挑战pt消耗设置"  # 挑战演出乐队确认页「CP 设置」打开的弹窗：每局消耗 200/400/800/1600
    LIVE_OPTIONS = "演出前选项设置"
    LB_SETTING = "LB消耗设置"
    # 用道具/星钻/广告恢复 LB：开了 game.lb_refill 时只在「道具」页用 LIVE BOOST饮料，否则只点取消
    LB_RECOVER = "恢复LIVE BOOST"
    LB_RECOVER_CONFIRM = "恢复LB确认"  # 恢复LIVE BOOST 点 OK 后的「将恢复n点LIVE BOOST。确定要恢复吗？」
    LB_RECOVERED = "LB已恢复"  # 「已恢复LIVE BOOST。」只有「OK」
    SETTINGS = "设置"
    PAUSE = "暂停"
    ABORT_CONFIRM = "终止确认"  # 暂停弹窗点「终止」后的二次确认
    RETRY_CONFIRM = "重试确认"  # 暂停弹窗点「重试」后的二次确认（「要重试并从头开始本次演出吗？」）
    LIVE_END = "LIVE CLEAR"
    ACHIEVEMENT = "达成奖励列表"
    RESULT = "结算"  # 判定数页
    RESULT_REWARD = "结算-奖励"
    RESULT_EXP = "结算-羁绊"  # 有「再次演出」的最后一页
    RESULT_EXP_NEXT = "结算-羁绊（下一步）"  # 活动期间的羁绊页只有「下一步」，后面还有活动结算页
    RESULT_OTHER = "结算-过渡"  # 标题已出现但内容还在动画中
    RANK_UP = "玩家等级提升"  # 结算奖励页上弹出，LB 同时回满
    GRADE_UP = "评级提升"  # 结算页上弹出的「GRADE UP」（最高分评级升段），只有「OK」
    # 结算奖励页之后弹出的「RANK UP」（两位成员的羁绊等级，或「乐队RANK」），只有「OK」，LB 不回满
    BOND_UP = "羁绊/乐队等级提升"
    POPUP = "弹窗"  # 其他底部中央只有「关闭」的弹窗（如最高分评级提升、登录后的公告），关掉即可
    # 看完故事、羁绊升级后的「乐曲解锁」（现在可以选择「曲名」了。）「故事解锁」（视角故事 / 羁绊故事…已解锁。），
    # 只有「关闭」，比一般弹窗靠上
    UNLOCK = "解锁提示"
    # 新增乐曲后回主界面时的全屏演出「追加翻唱乐曲！」（封面、作词作曲），点下方「TAP TO NEXT」继续
    NEW_SONG = "追加乐曲"
    # 每天游戏日期变更时弹出，只能回到标题画面重新登录；之后依次是登录奖励（可能有好几页）、公告、主界面
    DATE_CHANGE = "日期变更"
    TITLE = "标题画面"
    LOGIN_BONUS = "登录奖励"  # 右上角有 SKIP 的登录奖励演出，点空白处继续
    REWARD = "获得奖励"  # 领到东西后的确认弹窗（登录奖励、评级提升奖励等），只有「OK」
    # 别的都认不出、只有「OK」的提示（没有取消、确定、购买、星钻等字样），点 OK 继续（用户 2026-10-03 同意）
    OK_POPUP = "只有 OK 的弹窗"
    # 别的都认不出、右上角有「跳过」/「SKIP」的全屏演出（如重新登录后的角色生日演出，一直循环播放），点跳过
    SKIPPABLE = "可跳过的演出"
    # 打歌、领取日常时碰到的故事（重新登录后，生日演出之后接着放生日故事）：打开右上角的菜单点 SKIP，确认跳过。
    # 故事播放画面 OCR 认不出，看右上角的菜单按钮（导航按像素认，见 StoryMixin._player_menu_button）
    STORY_PLAYER = "故事播放"
    STORY_MENU = "故事播放菜单"  # 右边一列按钮：SKIP / AUTO / 快进 / 终止 ...
    STORY_SKIP = "要跳过故事吗"  # 左取消右跳过
    # B 站 SDK 在标题画面上弹出的「开启消息通知」：只点右上角的 ⓧ（「去开启」会跳到系统的通知设置）
    NOTIFY = "开启消息通知"
    CONNECT_ERROR = "连接失败"  # 「发生网络连接错误。」只有「返回标题画面」，回到标题重新登录
    # 「服务器正在维护中」：只有「返回标题画面」和「官方Discord」，登录时、对局中途都可能弹出。
    # 任务直接失败；挂机隔一阵回到标题画面重新登录看看开服没有
    MAINTENANCE = "维护中"
    # 登录时（游戏更新后）的「数据下载」：「即将下载追加的游戏数据。」左取消右 OK，点 OK 下载（不花钱）
    DATA_DOWNLOAD = "数据下载"
    # 「检测到新版本」：游戏需要进行版本更新，只有「前往商店」（会跳到应用商店），不点，任务直接失败
    UPDATE_REQUIRED = "需要更新游戏"


# 重新登录途中的画面：导航的超时从最后一次看到这些画面算起
RELOGIN_SCREENS = frozenset(
    (Screen.DATE_CHANGE, Screen.TITLE, Screen.LOGIN_BONUS, Screen.CONNECT_ERROR, Screen.DATA_DOWNLOAD)
)

# 画面左上角标题
TITLE_ROI: Rect = (120, 10, 300, 55)
_TITLES = {
    "乐队确认": Screen.BAND_CONFIRM,
    "乐曲选择": Screen.SONG_SELECT,
    "演出首页": Screen.LIVE_TOP,
    "设置": Screen.SETTINGS,
}
# 自由演出的乐曲选择页才有的：顶栏的 HIGH SCORE RATING（常读成 HGHSCORERATNG）和排序「默认」、「随机选曲」
# （筛选面板里也有）、左侧分类按钮。挑战演出的选曲页这些都没有；页面还在淡入时也可能都没读到，
# 所以还要右下角的「确定」和右侧的 HIGH SCORE 已经出来
TOP_BAR_ROI: Rect = (400, 0, 880, 65)
NORMAL_SONG_SELECT_MARKS: tuple[tuple[str, Rect | None], ...] = (("RAT", TOP_BAR_ROI), ("默认", TOP_BAR_ROI), ("随机", None))
SONG_CATEGORY_ROI: Rect = (0, 110, 190, 70)
SONG_CATEGORIES = ("原创", "翻唱", "全部")
SONG_OK_ROI: Rect = (1060, 630, 210, 60)
# 挑战演出乐队确认页：底部「消耗LB」的位置是「CP 设置」（图标读不出，只认「设置」），右上角是 CP 而不是 LB
CP_BUTTON_ROI: Rect = (880, 635, 110, 60)
CP_ICON_ROI: Rect = (990, 10, 50, 40)
# 弹窗优先于底下的页面（按顺序匹配：恢复弹窗可能叠在消耗设置上，终止、重试确认可能叠在暂停上）
_DIALOGS = {
    "服务器正在维护": Screen.MAINTENANCE,
    "需要进行版本更新": Screen.UPDATE_REQUIRED,
    "检测到新版本": Screen.UPDATE_REQUIRED,
    "下载追加的游戏数据": Screen.DATA_DOWNLOAD,
    "开启消息通知": Screen.NOTIFY,
    "发生网络连接错误": Screen.CONNECT_ERROR,
    "日期已变更": Screen.DATE_CHANGE,
    "前往标题画面": Screen.DATE_CHANGE,
    "演出前选项设置": Screen.LIVE_OPTIONS,
    "达成奖励列表": Screen.ACHIEVEMENT,
    # 这两个也包含「恢复L」，要排在前面
    "确定要恢复吗": Screen.LB_RECOVER_CONFIRM,
    "已恢复L": Screen.LB_RECOVERED,
    # 标题「恢复LIVE BOOST」（LIVE 的大小写常读错）；消耗设置弹窗里的「恢复」按钮后面没有字母
    "恢复L": Screen.LB_RECOVER,
    "消耗设置": Screen.LB_SETTING,
    "要终止演出": Screen.ABORT_CONFIRM,
    "要重试并从头开始": Screen.RETRY_CONFIRM,
    "演出已暂停": Screen.PAUSE,
    "要跳过故事吗": Screen.STORY_SKIP,
}
# LIVE CLEAR / LIVE FINISH 大字，OCR 常读成 LVEOLEAR、LVEFINSH
_LIVE_END = re.compile(r"^L.?VE")
LIVE_END_ROI: Rect = (250, 60, 780, 170)
# RANK UP 弹窗：大字常漏掉首字母 R，所以同时认弹窗中间的「玩家等级」小标题
RANK_UP_ROI: Rect = (440, 220, 400, 120)
# 羁绊等级的 RANK UP 大字在更上面，OK 在底部中央
BOND_UP_ROI: Rect = (440, 90, 400, 100)
# 认不出的弹窗：「OK」在中下部，画面上没有这些字才点（确认框、花星钻、招募之类的一律不点）
OK_POPUP_ROI: Rect = (340, 450, 600, 250)
OK_POPUP_BLOCK = ("取消", "确定", "购买", "星钻", "恢复", "使用", "招募", "下载")
# 弹窗底部中央的「关闭」按钮
CLOSE_ROI: Rect = (540, 620, 200, 70)
# 解锁提示：标题在上方中间（任务页左侧也有「乐曲解锁」「主页解锁」分页，不在这个范围），「关闭」在 (640,570)；
# 沉浸式主页解锁（领通行证奖励后回主界面时弹出）的弹窗更大，标题在 (640,80)，「关闭」在 (640,613)
UNLOCK_TITLES = ("乐曲解锁", "故事解锁", "沉浸式主页解锁")
UNLOCK_TITLE_ROI: Rect = (440, 60, 400, 90)
UNLOCK_CLOSE_ROI: Rect = (440, 530, 400, 100)
# 追加乐曲演出：标题在上方中间
NEW_SONG_ROI: Rect = (440, 60, 400, 100)
# 只有「OK」的弹窗顶部的标题：「获得奖励」「领取奖励」（两种弹窗标题高度不同）、GRADE UP 大字
REWARD_ROI: Rect = (440, 10, 400, 160)
# 结算页右下角的「下一步」
NEXT_ROI: Rect = (980, 630, 260, 60)
# 标题画面右上角的 CRIWARE 标志（整页只有它读得稳）；「TAP TO START」读不准，只看那一带有没有字：
# 刚启动时标题画面要加载一阵才出现这行字，这之前点击无效
TITLE_LOGO_ROI: Rect = (1060, 50, 100, 35)
TAP_TO_START_ROI: Rect = (800, 565, 300, 40)
# 登录奖励演出右上角的 SKIP
SKIP_ROI: Rect = (1150, 20, 110, 40)
# 其他全屏演出右上角的「跳过」（挑战演出乐队确认页底部的「跳过 还剩n次」不在这里，绝不能点）
SKIP_CORNER_ROI: Rect = (1050, 0, 230, 90)
SKIP_TEXTS = ("跳过", "SKIP")
PLAYER_MENU_ROI: Rect = (1150, 140, 100, 60)  # 故事播放菜单里的 SKIP
# 加载、下载中的画面认不出，但可能持续很久（下载数据、故事），不算卡住
LOADING_TEXTS = ("LOADING", "下载", "%")

# 乐队确认页左下角的曲名与难度
SONG_TITLE_ROI: Rect = (100, 588, 420, 42)
DIFFICULTY_ROI: Rect = (100, 650, 170, 38)
# 难度右侧「Lv.24」中的数字：只框数字逐格识别（整页检测常把它和 Lv. 拆成噪声或漏掉）
LEVEL_ROI: Rect = (228, 656, 50, 32)
DIFFICULTIES = ("easy", "normal", "hard", "expert")


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def in_roi(item: OcrItem, roi: Rect) -> bool:
    x, y, w, h = roi
    cx, cy = item.x + item.w / 2, item.cy
    return x <= cx <= x + w and y <= cy <= y + h


def find(items: list[OcrItem], text: str, roi: Rect | None = None, exact: bool = False) -> OcrItem | None:
    """第一个包含 ``text``（``exact`` 时为等于）的识别项（忽略空白）。"""
    for it in items:
        t = _compact(it.text)
        if (t == text if exact else text in t) and (roi is None or in_roi(it, roi)):
            return it
    return None


def center(item: OcrItem) -> tuple[int, int]:
    return round(item.x + item.w / 2), round(item.cy)


def skip_button(items: list[OcrItem]) -> OcrItem | None:
    """全屏演出右上角的「跳过」/「SKIP」。"""
    return next((it for text in SKIP_TEXTS if (it := find(items, text, SKIP_CORNER_ROI))), None)


def loading(items: list[OcrItem]) -> bool:
    """加载、下载中的画面（「NOW LOADING」、下载进度）。"""
    return any(text in it.text.upper() for it in items for text in LOADING_TEXTS)


def _setting_dialog(items: list[OcrItem], title: OcrItem) -> Screen:
    """「消耗设置」弹窗是 LIVE BOOST 的还是挑战演出的挑战pt。活动期间 LB 弹窗每行也写着「活动pt」「挑战pt」，
    所以看标题（「挑战pt消耗设置」/「LIVE BOOST消耗设置」，后者常读成「EBCOS消耗设置」之类）
    和只有 LB 弹窗才有的「全部消耗」，都看不出时看单选项前的「CP」。"""
    t = _compact(title.text).upper()
    if "挑战" in t:
        return Screen.CP_SETTING
    if "BOOST" in t or "LIVE" in t or find(items, "全部消耗"):
        return Screen.LB_SETTING
    return Screen.CP_SETTING if find(items, "CP", exact=True) else Screen.LB_SETTING


def classify(items: list[OcrItem]) -> Screen:
    for text, screen in _DIALOGS.items():
        if it := find(items, text):
            return _setting_dialog(items, it) if screen is Screen.LB_SETTING else screen
    for it in items:
        if it.h >= 50 and in_roi(it, LIVE_END_ROI) and _LIVE_END.match(_norm(it.text)):
            return Screen.LIVE_END
    if find(items, "ANKUP", RANK_UP_ROI) or find(items, "玩家等级", RANK_UP_ROI):
        return Screen.RANK_UP
    if find(items, "关闭", CLOSE_ROI, exact=True):
        return Screen.POPUP
    if find(items, "关闭", UNLOCK_CLOSE_ROI, exact=True) and any(
        find(items, title, UNLOCK_TITLE_ROI, exact=True) for title in UNLOCK_TITLES
    ):
        return Screen.UNLOCK
    if (it := find(items, "追加", NEW_SONG_ROI)) and "乐曲" in it.text:
        return Screen.NEW_SONG
    if find(items, "OK", CLOSE_ROI, exact=True):
        if find(items, "奖励", REWARD_ROI):
            return Screen.REWARD
        if find(items, "GRADE", REWARD_ROI):
            return Screen.GRADE_UP
        if find(items, "ANKUP", BOND_UP_ROI) and (find(items, "羁绊等级") or find(items, "乐队RANK")):
            return Screen.BOND_UP
    if find(items, "CRIWARE", TITLE_LOGO_ROI):
        return Screen.TITLE
    if find(items, "SKIP", SKIP_ROI, exact=True):
        return Screen.LOGIN_BONUS
    if find(items, "结算", TITLE_ROI):
        if find(items, "再次演出"):
            return Screen.RESULT_EXP
        if any(classify_label(it.text) == "perfect" for it in items):
            return Screen.RESULT
        if find(items, "玩家等级"):
            return Screen.RESULT_REWARD
        # 「羁绊」常读成「霜绊」
        if find(items, "下一步", NEXT_ROI, exact=True) and (find(items, "详情") or find(items, "绊EXP")):
            return Screen.RESULT_EXP_NEXT
        return Screen.RESULT_OTHER
    for text, screen in _TITLES.items():
        if find(items, text, TITLE_ROI):
            if screen is Screen.SONG_SELECT and _challenge_song_select(items):
                return Screen.CHALLENGE_SONG_SELECT
            if screen is Screen.BAND_CONFIRM and (
                find(items, "设置", CP_BUTTON_ROI, exact=True) or find(items, "CP", CP_ICON_ROI, exact=True)
            ):
                return Screen.CHALLENGE_BAND_CONFIRM
            return screen
    # 主界面没有标题，底部一排入口
    if find(items, "招募", (500, 620, 450, 60)) and find(items, "故事", (500, 620, 450, 60)):
        return Screen.HOME
    if find(items, "SKIP", PLAYER_MENU_ROI, exact=True):
        return Screen.STORY_MENU
    if skip_button(items):
        return Screen.SKIPPABLE
    if find(items, "OK", OK_POPUP_ROI, exact=True) and not any(find(items, text) for text in OK_POPUP_BLOCK):
        return Screen.OK_POPUP
    return Screen.UNKNOWN


def _challenge_song_select(items: list[OcrItem]) -> bool:
    if any(find(items, mark, roi) for mark, roi in NORMAL_SONG_SELECT_MARKS):
        return False
    if any(find(items, cat, SONG_CATEGORY_ROI) for cat in SONG_CATEGORIES):
        return False
    return bool(find(items, "确定", SONG_OK_ROI, exact=True) and find(items, "HIGH"))


# 维护页上的「2026/10/02（周五）11:00~2026/10/02（周五）16:00」
_MAINT_TIME = re.compile(r"(\d{4}/\d{1,2}/\d{1,2})\D*?(\d{1,2}:\d{2})")


def maintenance_period(items: list[OcrItem]) -> str | None:
    """维护页上写的维护时间（如「2026/10/02 11:00 ~ 2026/10/02 16:00」），读不到为 None。"""
    for it in items:
        times = _MAINT_TIME.findall(_compact(it.text))
        if times:
            return " ~ ".join(f"{d} {t}" for d, t in times)
    return None


def title_startable(items: list[OcrItem]) -> bool:
    """标题画面已经出现「TAP TO START」（点击会开始登录）。"""
    return any(in_roi(it, TAP_TO_START_ROI) for it in items)


def parse_level(text: str) -> int | None:
    """乐队确认页的等级数字（LEVEL_ROI 逐格识别的结果）。很窄的 1 常被识别成 T、| 等（21 读成 2T）；
    整格必须只有 1~2 位数字，混进其他字符就不采用（等级只用来给曲名消歧，读不到也无妨）。"""
    t = re.sub(r"[|Il!iT]", "1", _compact(text))
    return int(t) if re.fullmatch(r"\d{1,2}", t) and 1 <= int(t) <= 40 else None


def parse_difficulty(text: str) -> str | None:
    t = _norm(text)
    for d in DIFFICULTIES:
        if d.upper()[:3] in t:
            return d
    return None


def band_confirm_song(items: list[OcrItem]) -> tuple[str, str | None]:
    """乐队确认页上的 (曲名, 难度)。曲名可能被 OCR 拆成几段，按从左到右拼接。"""
    parts = sorted((it for it in items if in_roi(it, SONG_TITLE_ROI)), key=lambda it: it.x)
    title = " ".join(it.text.strip() for it in parts)
    diff = None
    for it in items:
        if in_roi(it, DIFFICULTY_ROI):
            diff = parse_difficulty(it.text) or diff
    return title, diff


def note_speed(items: list[OcrItem]) -> float | None:
    """演出前选项设置弹窗中「节奏图示速度」的数值（位于标签下一行的滑条中央）。"""
    label = find(items, "节奏图示速度")
    if label is None:
        return None
    for it in items:
        if 0 < it.cy - label.cy < 90 and re.fullmatch(r"\d+\.\d{2}", it.text.strip()):
            return float(it.text)
    return None


# LIVE BOOST消耗设置弹窗：左侧一列单选按钮（消耗 3/2/1/0），选中的圆心是白色、未选中是暗红；
# 下方「全部消耗」复选框同理。持有数量「24/99」在弹窗底部中间
LB_RADIO = {3: (317, 126), 2: (317, 181), 1: (317, 237), 0: (317, 293)}
LB_ALL_CHECK = (317, 439)
LB_HELD_ROI: Rect = (560, 545, 180, 40)


def lb_held(items: list[OcrItem]) -> int | None:
    """消耗设置弹窗上的 LB 持有数量（「24/99」中的 24）。"""
    for it in items:
        m = re.fullmatch(r"(\d+)/\d+", _compact(it.text))
        if m and in_roi(it, LB_HELD_ROI):
            return int(m.group(1))
    return None


# 乐队确认页右上角的 LB 持有数「14/10」（可以超过上限），下面一行是下一个 LB 恢复的倒计时「⏱21:11」
# （mm:ss，实时走；持有数到上限后不显示）
LB_BAR_ROI: Rect = (990, 10, 150, 45)
LB_TIMER_ROI: Rect = (1020, 42, 130, 38)


def _digits(text: str) -> str:
    """小号数字常把 1 识别成 ]、| 等，0 识别成 O。"""
    return re.sub(r"[|Il!iT\]\[]", "1", _compact(text)).replace("O", "0").replace("o", "0")


def lb_bar_held(items: list[OcrItem]) -> int | None:
    """乐队确认页顶栏的 LB 持有数量。字很小，1 常被识别成 ]、| 等（14 读成 ]4），也可能整个漏掉，
    读到 0 时应再用消耗设置弹窗（:func:`lb_held`）核对。"""
    for it in items:
        if not in_roi(it, LB_BAR_ROI):
            continue
        m = re.fullmatch(r"(\d{1,3})/\d{1,3}", _digits(it.text))
        if m:
            return int(m.group(1))
    return None


def lb_bar_timer(items: list[OcrItem]) -> int | None:
    """乐队确认页顶栏的 LB 恢复倒计时（秒）。前面的时钟图标有时识别成 ©。"""
    for it in items:
        if not in_roi(it, LB_TIMER_ROI):
            continue
        m = re.search(r"(\d{1,2})[:：;](\d{2})$", _digits(it.text))
        if m and int(m.group(2)) < 60:
            return int(m.group(1)) * 60 + int(m.group(2))
    return None


# 挑战pt消耗设置弹窗：左侧一列单选按钮（每局消耗 200/400/800/1600 CP，奖励等对应 ×1/×2/×4/×8），
# 选中的圆心是白色；底部是持有的 CP「3708」，左「取消」右「OK」
CP_RADIO = {200: (317, 213), 400: (317, 273), 800: (317, 333), 1600: (317, 393)}
CP_HELD_ROI: Rect = (860, 455, 180, 40)
# 挑战演出乐队确认页右上角的 CP 持有数（没有上限，不带「/」）
CP_BAR_ROI: Rect = (1060, 10, 130, 45)


def _cp_value(items: list[OcrItem], roi: Rect) -> int | None:
    for it in items:
        if in_roi(it, roi):
            t = _digits(it.text).replace(",", "")
            if re.fullmatch(r"\d{1,6}", t):
                return int(t)
    return None


def cp_held(items: list[OcrItem]) -> int | None:
    """挑战pt消耗设置弹窗上持有的 CP。"""
    return _cp_value(items, CP_HELD_ROI)


def cp_bar_held(items: list[OcrItem]) -> int | None:
    """挑战演出乐队确认页顶栏持有的 CP（字小可能读错或漏掉，不够一局时应再用弹窗里的 :func:`cp_held` 核对）。"""
    return _cp_value(items, CP_BAR_ROI)


# 挑战演出乐曲选择页：左边是活动指定的几首歌，选中的一行在中间（曲名字大一些），上下各露出两行；
# 点别的行就选中它并滚到中间。列表不循环，选中第一首时上面是空的
CHALLENGE_ROW_X = (250, 540)  # 曲名中心 x 的范围（左边是「Lv.26」、右边是「MV」标志）
CHALLENGE_SELECTED_Y = 324
CHALLENGE_ROW_TAP_X = 420
_CHALLENGE_NOT_TITLE = re.compile(r"\W?L\s*[vV]|[\d\s.|Il!]+$|\W?\w?MV$")


def challenge_rows(items: list[OcrItem]) -> list[tuple[str, float]]:
    """挑战演出乐曲选择页上能看到的各行 (曲名, 中心 y)，从上到下。"""
    rows = []
    for it in items:
        t = _compact(it.text)
        cx = it.x + it.w / 2
        if len(t) < 2 or not CHALLENGE_ROW_X[0] <= cx <= CHALLENGE_ROW_X[1] or not 60 <= it.cy <= 640:
            continue
        if _CHALLENGE_NOT_TITLE.match(t):
            continue
        rows.append((it.text.strip(), it.cy))
    return sorted(rows, key=lambda r: r[1])


def challenge_selected(rows: list[tuple[str, float]]) -> int | None:
    """``rows`` 里选中的那一行（在中间）的下标。"""
    for i, (_, cy) in enumerate(rows):
        if abs(cy - CHALLENGE_SELECTED_Y) < 40:
            return i
    return None


# 乐曲选择页右侧面板：封面下面是大号曲名；HIGH SCORE、RANK 下面是所选难度的通关标记
# （ALL PERFECT / FULL COMBO，所选难度没打出来时没有；切换难度时跟着变）
SELECT_TITLE_ROI: Rect = (735, 405, 540, 65)
CLEAR_MARK_ROI: Rect = (1040, 195, 225, 50)


def select_panel_title(items: list[OcrItem]) -> str | None:
    """乐曲选择页右侧面板上的曲名（选中行上的曲名太长时会截断，面板上是完整的）。"""
    it = max((it for it in items if in_roi(it, SELECT_TITLE_ROI)), key=lambda it: it.w, default=None)
    return None if it is None else it.text.strip()


def all_perfect_mark(items: list[OcrItem]) -> bool:
    """乐曲选择页右侧面板上有 ALL PERFECT 标记：选中的歌在所选难度已经 AP（OCR 会去掉中间的空格）。"""
    return any(in_roi(it, CLEAR_MARK_ROI) and re.search(r"PERFE|ERFECT", _compact(it.text).upper()) for it in items)


def same_title(a: str | None, b: str | None) -> bool:
    """两处读到的曲名是同一首：按较短的长度比较开头（一边可能截断、末尾几个字可能读错），
    或者有一段连续 4 个字相同（选中行上太长的曲名会滚动显示，开头不一定是曲名的开头）。"""
    a, b = _compact(a or ""), _compact(b or "")
    n = min(len(a), len(b))
    if n < 2:
        return bool(a) and a == b
    if SequenceMatcher(None, a[:n], b[:n]).ratio() >= 0.6:
        return True
    return SequenceMatcher(None, a, b, autojunk=False).find_longest_match().size >= min(n, 4)


# 恢复LIVE BOOST 弹窗：左边一列分页「道具」「星钻」「观看广告」，选中的是青绿色、没选中的是深蓝。
# 「道具」页每行一种饮料：名字下面一排 重置 − 「已选/持有」 + 最大；底部是持有数预览「3 ▶ 14」
LB_TAB_ITEMS = (85, 146)
LB_TAB_OTHERS = ((72, 217), (72, 285))  # 星钻、观看广告
LB_PLUS_X = 910
# 小型LIVE BOOST饮料每瓶恢复 1 个，LIVE BOOST饮料 10 个（LIVE 常读成 LIvE、LVE）
LB_DRINK_NAME = re.compile(r"(小型)?L[A-Z]{1,3}BOOST饮料")
LB_DRINK_NAME_X = 600  # 名字在左半边
LB_DRINK_COUNT_X = (720, 860)  # 「已选/持有」的中心 x
LB_PREVIEW_ROI: Rect = (600, 545, 300, 50)
LB_PREVIEW_ARROW_X = 770  # 预览中间 ▶ 的中心 x，左边是恢复前、右边是恢复后


@dataclass(frozen=True)
class LbDrink:
    name: str
    lb: int  # 每瓶恢复的 LB
    chosen: int  # 已选几瓶
    owned: int  # 持有几瓶
    y: float  # 这一行按钮（+）的中心 y


def lb_drinks(items: list[OcrItem]) -> list[LbDrink]:
    """恢复LIVE BOOST「道具」页上认得的饮料，从上到下（名字认不出、数量没读到的行不要）。"""
    drinks = []
    for it in items:
        m = LB_DRINK_NAME.fullmatch(_compact(it.text).upper())
        if not m or it.x + it.w / 2 > LB_DRINK_NAME_X:
            continue
        for c in items:
            n = re.fullmatch(r"(\d+)/(\d+)", _digits(c.text))
            lo, hi = LB_DRINK_COUNT_X
            if n and lo <= c.x + c.w / 2 <= hi and 25 < c.cy - it.cy < 90:
                drinks.append(LbDrink(it.text.strip(), 1 if m.group(1) else 10, int(n[1]), int(n[2]), c.cy))
                break
    return sorted(drinks, key=lambda d: d.y)


def lb_preview(items: list[OcrItem]) -> tuple[int | None, int | None] | None:
    """恢复LIVE BOOST 弹窗底部的（恢复前，恢复后）持有数，没读到的一边为 None，两边都没读到返回 None。
    数字很小，单独一个白色的「1」常常整个漏掉（实机：持有 1 时预览「1 ▶ 3」只读到 3）。"""
    before = after = None
    for it in items:
        if not (in_roi(it, LB_PREVIEW_ROI) and re.fullmatch(r"\d{1,3}", _digits(it.text))):
            continue
        n = int(_digits(it.text))
        cx = it.x + it.w / 2
        if cx < LB_PREVIEW_ARROW_X - 30:
            before = n
        elif cx > LB_PREVIEW_ARROW_X + 30:
            after = n
    if before is None and after is None:
        return None
    return before, after


def lb_recover_amount(items: list[OcrItem]) -> int | None:
    """恢复确认弹窗「将恢复n点LIVE BOOST。」里的 n。"""
    for it in items:
        m = re.search(r"恢复(\d+)点", _compact(it.text))
        if m:
            return int(m.group(1))
    return None


# 结算页判定表：各行中心 y 与数字列（数字右对齐）；总数一列，切换后 FAST、SLOW 两列。
# 格子只框住数字：碰到格子分隔线、表头的 ▼ 会多识别出「|」「1」等字符（333 读成 1333）
RESULT_ROW_Y = {"perfect": 390, "great": 429, "good": 467, "bad": 506, "miss": 545}
RESULT_COL_TOTAL = (930, 100)  # x, w
RESULT_COL_FAST = (874, 72)
RESULT_COL_SLOW = (958, 72)
RESULT_CELL_H = 30
# 分数：大号斜体数字，整屏识别常把开头的 1 读丢（1330139 → 330139）；只框数字、不做检测就读得准。
# 下沿不能碰到下面一行的 HIGH SCORE 数字（y≈234）
RESULT_SCORE_ROI: Rect = (960, 166, 280, 64)
# 最大连击（右下角 COMBO 后面的大号数字）：同样会把开头的 1 读丢（1159 → 159）。上面是 FULL COMBO 等字样
RESULT_COMBO_ROI: Rect = (1120, 516, 112, 48)
# 总数格的备选框法 (dx, dy, dw, dh)：四位数偶尔读错（1188 读成 T188，格子上沿的横线连上了 1），
# 知道谱面音符数时依次换框，直到五项之和对上
RESULT_TOTAL_VARIANTS: tuple[tuple[float, float, float, float], ...] = ((0, 0, 0, 0), (0, -3, 0, 6), (20, 0, -20, 0))


def result_cells(
    items: list[OcrItem], timing: bool, variant: tuple[float, float, float, float] = (0, 0, 0, 0)
) -> dict[str, list[Rect]]:
    """判定表每个数字格的 ROI；行位置优先取识别到的标签（PERFECT 等）的中心。"""
    labels = find_labels(items)
    cols = (RESULT_COL_FAST, RESULT_COL_SLOW) if timing else (RESULT_COL_TOTAL,)
    dx, dy, dw, dh = variant
    cells = {}
    for j, y in RESULT_ROW_Y.items():
        if j in labels and abs(labels[j].cy - y) < RESULT_CELL_H / 2:
            y = labels[j].cy
        cells[j] = [(x + dx, y - RESULT_CELL_H / 2 + dy, w + dw, RESULT_CELL_H + dh) for x, w in cols]
    return cells
