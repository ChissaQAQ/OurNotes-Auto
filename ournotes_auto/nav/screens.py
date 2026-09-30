"""按 OCR 结果判断当前画面（坐标均为 1280x720 设计尺寸，实机校准于 MuMu 国际服简中客户端）。"""

from __future__ import annotations

import re
from enum import StrEnum

from ..result_reader import OcrItem, _norm, classify_label, find_labels

Rect = tuple[float, float, float, float]  # x, y, w, h


class Screen(StrEnum):
    UNKNOWN = "未知"  # 加载、转场、演奏中等
    HOME = "主界面"
    LIVE_TOP = "演出首页"
    SONG_SELECT = "乐曲选择"
    BAND_CONFIRM = "乐队确认"
    LIVE_OPTIONS = "演出前选项设置"
    LB_SETTING = "LB消耗设置"
    LB_RECOVER = "恢复LIVE BOOST"  # 用道具/星钻/广告恢复 LB：只能点取消
    SETTINGS = "设置"
    PAUSE = "暂停"
    ABORT_CONFIRM = "终止确认"  # 暂停弹窗点「终止」后的二次确认
    LIVE_END = "LIVE CLEAR"
    ACHIEVEMENT = "达成奖励列表"
    RESULT = "结算"  # 判定数页
    RESULT_REWARD = "结算-奖励"
    RESULT_EXP = "结算-羁绊"  # 有「再次演出」的最后一页
    RESULT_EXP_NEXT = "结算-羁绊（下一步）"  # 活动期间的羁绊页只有「下一步」，后面还有活动结算页
    RESULT_OTHER = "结算-过渡"  # 标题已出现但内容还在动画中
    RANK_UP = "玩家等级提升"  # 结算奖励页上弹出，LB 同时回满
    GRADE_UP = "评级提升"  # 结算页上弹出的「GRADE UP」（最高分评级升段），只有「OK」
    BOND_UP = "羁绊等级提升"  # 结算奖励页之后弹出的「RANK UP」（两位成员的羁绊等级），只有「OK」，LB 不回满
    POPUP = "弹窗"  # 其他底部中央只有「关闭」的弹窗（如最高分评级提升、登录后的公告），关掉即可
    # 看完故事、羁绊升级后的「乐曲解锁」（现在可以选择「曲名」了。）「故事解锁」（视角故事 / 羁绊故事…已解锁。），
    # 只有「关闭」，比一般弹窗靠上
    UNLOCK = "解锁提示"
    # 每天游戏日期变更时弹出，只能回到标题画面重新登录；之后依次是登录奖励（可能有好几页）、公告、主界面
    DATE_CHANGE = "日期变更"
    TITLE = "标题画面"
    LOGIN_BONUS = "登录奖励"  # 右上角有 SKIP 的登录奖励演出，点空白处继续
    REWARD = "获得奖励"  # 领到东西后的确认弹窗（登录奖励、评级提升奖励等），只有「OK」


# 重新登录途中的画面：导航的超时从最后一次看到这些画面算起
RELOGIN_SCREENS = frozenset((Screen.DATE_CHANGE, Screen.TITLE, Screen.LOGIN_BONUS))

# 画面左上角标题
TITLE_ROI: Rect = (120, 10, 300, 55)
_TITLES = {
    "乐队确认": Screen.BAND_CONFIRM,
    "乐曲选择": Screen.SONG_SELECT,
    "演出首页": Screen.LIVE_TOP,
    "设置": Screen.SETTINGS,
}
# 弹窗优先于底下的页面（按顺序匹配：恢复弹窗可能叠在消耗设置上，终止确认可能叠在暂停上）
_DIALOGS = {
    "日期已变更": Screen.DATE_CHANGE,
    "前往标题画面": Screen.DATE_CHANGE,
    "演出前选项设置": Screen.LIVE_OPTIONS,
    "达成奖励列表": Screen.ACHIEVEMENT,
    # 标题「恢复LIVE BOOST」（LIVE 的大小写常读错）；消耗设置弹窗里的「恢复」按钮后面没有字母
    "恢复L": Screen.LB_RECOVER,
    "消耗设置": Screen.LB_SETTING,
    "要终止演出": Screen.ABORT_CONFIRM,
    "演出已暂停": Screen.PAUSE,
}
# LIVE CLEAR / LIVE FINISH 大字，OCR 常读成 LVEOLEAR、LVEFINSH
_LIVE_END = re.compile(r"^L.?VE")
LIVE_END_ROI: Rect = (250, 60, 780, 170)
# RANK UP 弹窗：大字常漏掉首字母 R，所以同时认弹窗中间的「玩家等级」小标题
RANK_UP_ROI: Rect = (440, 220, 400, 120)
# 羁绊等级的 RANK UP 大字在更上面，OK 在底部中央
BOND_UP_ROI: Rect = (440, 90, 400, 100)
# 弹窗底部中央的「关闭」按钮
CLOSE_ROI: Rect = (540, 620, 200, 70)
# 解锁提示：标题在上方中间（任务页左侧也有「乐曲解锁」分页，不在这个范围），「关闭」在 (640,570)
UNLOCK_TITLES = ("乐曲解锁", "故事解锁")
UNLOCK_TITLE_ROI: Rect = (440, 90, 400, 60)
UNLOCK_CLOSE_ROI: Rect = (440, 530, 400, 80)
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


def classify(items: list[OcrItem]) -> Screen:
    for text, screen in _DIALOGS.items():
        if find(items, text):
            return screen
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
    if find(items, "OK", CLOSE_ROI, exact=True):
        if find(items, "奖励", REWARD_ROI):
            return Screen.REWARD
        if find(items, "GRADE", REWARD_ROI):
            return Screen.GRADE_UP
        if find(items, "ANKUP", BOND_UP_ROI) and find(items, "羁绊等级"):
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
            return screen
    # 主界面没有标题，底部一排入口
    if find(items, "招募", (500, 620, 450, 60)) and find(items, "故事", (500, 620, 450, 60)):
        return Screen.HOME
    return Screen.UNKNOWN


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
