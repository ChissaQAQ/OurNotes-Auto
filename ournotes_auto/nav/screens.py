"""按 OCR 结果判断当前画面（坐标均为 1280x720 设计尺寸，实机校准于 MuMu 国际服简中客户端）。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum
from statistics import median

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
    # 激奏舞台（从演出首页的「激奏舞台」进，多人对局）：首页「LIVE START」→ 匹配确认 → 正在匹配 → 匹配房间
    # （成员到齐后转盘定歌）→ 难度选择（点 EXPERT、准备完毕）→ 演奏 → 结果弹窗、排名页（先后不定）→ 普通的结算页 → 首页
    ARENA_TOP = "激奏舞台"  # 首页：右上角 LB，下面「消耗LB」「LIVE START」，乐队图标右边 ▶▶ 换乐队
    ARENA_MATCH_CONFIRM = "激奏舞台-匹配确认"  # 左取消右 OK
    ARENA_MATCHING = "激奏舞台-匹配中"  # 「正在匹配」弹窗、匹配房间（等成员到齐）
    ARENA_DIFFICULTY = "激奏舞台-难度选择"
    ARENA_RESULT = "激奏舞台-结果"  # 演奏后弹出的「结果 3rd AP+15」（名次、段位分），下方「下一步」
    ARENA_RANKING = "激奏舞台-排名"  # 各玩家的名次、分数（「演出记录」切到判定数），右下「下一步」
    ARENA_RANK_UP = "激奏舞台-段位提升"  # 「RANK UP」大字和新旧段位，没有按钮，点一下继续
    LIVE_OPTIONS = "演出前选项设置"
    LB_SETTING = "LB消耗设置"
    # 用道具/星钻/广告恢复 LB：开了 game.lb_refill 时只在「道具」页用 LIVE BOOST饮料，否则只点取消
    LB_RECOVER = "恢复LIVE BOOST"
    LB_RECOVER_CONFIRM = "恢复LB确认"  # 恢复LIVE BOOST 点 OK 后的「将恢复n点LIVE BOOST。确定要恢复吗？」
    LB_RECOVERED = "LB已恢复"  # 「已恢复LIVE BOOST。」只有「OK」
    # 乐队确认页「跳过」（开了 loop.use_skip 才点）打开的「确认跳过」：和消耗设置弹窗一样选消耗，左「关闭」右「跳过」
    SKIP_CONFIRM = "确认跳过"
    # 跳过之后的结果页（玩家、成员经验，右下「下一步」）和活动pt页（右下「OK」），之后回到乐队确认页
    SKIP_RESULT = "跳过结果"
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
    # 别的都认不出、只有「OK」的提示（没有取消、确定、购买、星钻等字样），点 OK 继续
    OK_POPUP = "只有 OK 的弹窗"
    # 别的都认不出、右上角有「跳过」/「SKIP」的全屏演出（如重新登录后的角色生日演出，一直循环播放），点跳过
    SKIPPABLE = "可跳过的演出"
    # 打歌、领取日常时碰到的故事（重新登录后，生日演出之后接着放生日故事）：打开右上角的菜单点 SKIP，确认跳过。
    # 故事播放画面 OCR 认不出，看右上角的菜单按钮（导航按像素认，见 StoryMixin._player_menu_button）
    STORY_PLAYER = "故事播放"
    STORY_MENU = "故事播放菜单"  # 右边一列按钮：SKIP / AUTO / 快进 / 终止 ...
    STORY_SKIP = "要跳过故事吗"  # 左取消右跳过
    # 主界面上点到角色弹出的对话（背景虚化、角色站在前面，下面是名牌和一句台词），点对话框关掉。
    # 一直在动（Live2D），按停住的画面恢复不了；左侧一列没有入口的地方点下去就是这个
    HOME_TALK = "主界面角色对话"
    # B 站 SDK 在标题画面上弹出的「开启消息通知」：只点右上角的 ⓧ（「去开启」会跳到系统的通知设置）
    NOTIFY = "开启消息通知"
    # Android 13 起游戏第一次启动时系统问「要允许…向您发送通知吗」（日服遇到过）：点「不允许」
    NOTIFY_PERMISSION = "通知权限"
    CONNECT_ERROR = "连接失败"  # 「发生网络连接错误。」只有「返回标题画面」，回到标题重新登录
    # 「服务器正在维护中」：只有「返回标题画面」和「官方Discord」，登录时、对局中途都可能弹出。
    # 任务直接失败；挂机隔一阵回到标题画面重新登录看看开服没有
    MAINTENANCE = "维护中"
    # 登录时（游戏更新后）的「数据下载」：「即将下载追加的游戏数据。」左取消右 OK，点 OK 下载（不花钱）
    DATA_DOWNLOAD = "数据下载"
    # 「检测到新版本」：游戏需要进行版本更新，只有「前往商店」（会跳到应用商店），不点，任务直接失败
    UPDATE_REQUIRED = "需要更新游戏"
    # 切换账号（见 AccountMixin）：标题画面右上角 ☰ 打开的菜单 → B 站 SDK 的用户中心 → 退出登录后的登录记录
    TITLE_MENU = "标题菜单"  # 公告 / 选择语言 / 用户中心 / 一键下载 / 切换服务器 / 清除缓存 / 模式设置，底部「关闭」
    USER_CENTER = "用户中心"  # UID、修改密码、退出登录等；往下滚还有「注销」（删除账号），绝不能点
    LOGIN_HISTORY = "登录记录"  # 记住的账号（点「登录」不用密码），展开后每行右边的 ⓧ 会删掉这条记录，绝不能点


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
    "激奏舞台": Screen.ARENA_TOP,
}
# 故事的各个页面（左上角是返回、主页按钮）
CHAPTERS_TITLE = "乐队故事章节选择"
EPISODES_TITLE = "乐队故事话数选择"
BOND_MEMBERS_TITLE = "羁绊故事"
BOND_PAIRS_TITLE = "羁绊故事选择"
EVENT_EPISODES_TITLE = "活动故事话数选择"
STORY_TITLES = (CHAPTERS_TITLE, EPISODES_TITLE, BOND_MEMBERS_TITLE, BOND_PAIRS_TITLE, EVENT_EPISODES_TITLE)
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
    "发送通知": Screen.NOTIFY_PERMISSION,
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
    "确认跳过": Screen.SKIP_CONFIRM,
    "要终止演出": Screen.ABORT_CONFIRM,
    "要重试并从头开始": Screen.RETRY_CONFIRM,
    "演出已暂停": Screen.PAUSE,
    "要跳过故事吗": Screen.STORY_SKIP,
}
# 加载画面中间有随机小提示（如「玩家等级提升后，会恢复LIVE BOOST。」，小标题「玩家等级」），会被当成弹窗、RANK UP：
# 加载中只认可能叠在上面的这几种，别的都算加载
_LOADING_DIALOGS = frozenset(
    (
        Screen.MAINTENANCE,
        Screen.UPDATE_REQUIRED,
        Screen.DATA_DOWNLOAD,
        Screen.NOTIFY,
        Screen.NOTIFY_PERMISSION,
        Screen.CONNECT_ERROR,
        Screen.DATE_CHANGE,
    )
)
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
POWER_TITLE_ROI: Rect = (440, 20, 400, 60)
BOTTOM_LEFT_ROI: Rect = (360, 610, 280, 80)
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
# 主界面底部一排入口的文字（右边的「演出」在大图标上，不在这个范围）
HOME_ENTRIES = ("招募", "商店", "故事", "乐队")
HOME_ENTRIES_ROI: Rect = (500, 620, 450, 60)
# 标题菜单的按钮（三个都在才算，别的页面不会同时有）
TITLE_MENU_MARKS = ("用户中心", "切换服务器", "清除缓存")
# B 站 SDK 的弹窗（用户中心、登录记录、开启消息通知）是安卓原生界面，按 dp 排版、在画面正中：分辨率或 DPI
# 和 1280x720 时不同，整个弹窗以画面中心为原点缩放（2560x1440 下约 0.75 倍），不跟游戏画面按比例放大。
# 下面这些都是 1280x720 下量的，用之前按 sdk_scale 换算（sdk_point / sdk_rect）；
# 缩放比按顶部 bilibili 标志离画面中心的高度算，标志中心的高度见 SDK_LOGO_Y / NOTIFY_LOGO_Y
SDK_LOGO_ROI: Rect = (440, 0, 400, 360)
SDK_LOGO_Y = 166.0  # 用户中心、登录记录
NOTIFY_LOGO_Y = 173.0  # 开启消息通知（弹窗矮一些）
# 标志下面的标题（「用户中心」「登录记录」）
SDK_TITLE_ROI: Rect = (480, 180, 320, 70)
# 登录记录：账号名下面一行是「上次登录：n分钟前」。收起时只有选中的一行（名字在 (610,292)），
# 展开后按最近登录排列，每行约 84px，能看到三行左右
LOGIN_ROWS_ROI: Rect = (500, 265, 270, 310)
LAST_LOGIN = "上次登录"
# 标题画面右上角的 CRIWARE 标志（整页只有它读得稳）；「TAP TO START」读不准，只看那一带有没有字：
# 刚启动时标题画面要加载一阵才出现这行字，这之前点击无效
TITLE_LOGO_ROI: Rect = (1060, 50, 100, 35)
TAP_TO_START_ROI: Rect = (800, 565, 300, 40)
# 登录奖励演出右上角的 SKIP
SKIP_ROI: Rect = (1150, 20, 110, 40)
# 其他全屏演出右上角的「跳过」（挑战演出乐队确认页底部的「跳过 还剩n次」不在这里，绝不能点）
SKIP_CORNER_ROI: Rect = (1050, 0, 230, 90)
SKIP_TEXTS = ("跳过", "SKIP")
# 乐队确认页底部的「跳过」，上面一行是剩余次数「还剩2次」（跳过确认弹窗右下的「跳过」也在这两个范围里）
SKIP_BTN_ROI: Rect = (720, 640, 120, 50)
SKIP_LEFT_ROI: Rect = (680, 590, 160, 45)
PLAYER_MENU_ROI: Rect = (1150, 140, 100, 60)  # 故事播放菜单里的 SKIP
# 主界面角色对话：名牌在 (368,506)，台词在下面的方框里、左边 x≈372；虚化的背景读不出字，这条线以上什么都没有。
# 故事播放画面也是名牌加台词，但台词从 x≈312 开始（导航还会先按右上角的菜单按钮认故事播放）
HOME_TALK_NAME_ROI: Rect = (300, 475, 160, 60)
HOME_TALK_NAME_MAX = 8  # 名牌上只有角色名
HOME_TALK_LINE_ROI: Rect = (355, 525, 600, 125)
HOME_TALK_LINE_LEFT = (355, 395)
HOME_TALK_TOP = 470
# 加载、下载中的画面认不出，但可能持续很久（下载数据、故事），不算卡住
LOADING_TEXTS = ("LOADING", "下载", "%")

# 激奏舞台的乐队（loop.arena_band），按首页 ▶▶ 切换的顺序
ARENA_BANDS = ("mygo", "avemujica", "mewtype", "millsage", "dumbrock")
# 首页左上角标题「激奏舞台」之外，下面这些至少要有一个（说明弹窗等别的页面标题也可能是激奏舞台）
ARENA_TOP_MARKS = ("LIVE", "消耗LB", "全部消耗", "交换所")
TOP_CENTER_ROI: Rect = (440, 10, 400, 60)  # 顶部正中的标题：匹配确认、难度选择
ARENA_MATCHING_ROI: Rect = (440, 90, 400, 60)  # 「正在匹配」弹窗的标题
ARENA_RESULT_ROI: Rect = (440, 270, 500, 60)  # 结果弹窗的「结果 3rd AP+15」
ARENA_AP_ROI: Rect = (690, 425, 90, 45)  # 结果弹窗上加分后的段位分
ARENA_TIER_ROI: Rect = (400, 490, 480, 50)  # 段位提升：左右两个段位名
ARENA_RECORD_ROI: Rect = (720, 630, 250, 60)  # 排名页底部的「演出记录」
ARENA_PLACE_ROI: Rect = (20, 330, 200, 150)  # 排名页左边自己的名次（大号的「3rd」）
ARENA_SONG_ROI: Rect = (780, 385, 490, 50)  # 难度选择页右边封面下的曲名
# 难度选择页右边转盘定下的那首歌的大封面（#107，要贴边：裁小到 280 就认不出了）。匹配确认页上的封面是自己带的歌，不是这首
ARENA_JACKET_ROI: Rect = (855, 81, 300, 300)
ARENA_LEVEL_ROI: Rect = (1140, 510, 130, 45)  # 难度选择页 EXPERT 上面的等级数字
MATCH_OK_ROI: Rect = (640, 630, 300, 60)  # 匹配确认：左取消右 OK
# 激奏舞台的页面认不出时（还没适配的语言，#105）按不分语言的特征猜（见 arena_guess）：首页、匹配确认、难度选择、
# 排名页上各有一排撃奏（别的页面都没有），各页这一排的高度不同
ARENA_GEKISOU_ROWS = (
    (658, Screen.ARENA_TOP),
    (320, Screen.ARENA_MATCH_CONFIRM),
    (488, Screen.ARENA_DIFFICULTY),
    (561, Screen.ARENA_RANKING),
)
# 撃奏的种类按时间轮换（#111）：「LUCK」「COMBO」「JUST」都有可能
ARENA_GEKISOU = re.compile(r"LUCK|C[O0]MB[O0]|JUST")
ARENA_ROW_DY = 20  # 中心高度差这么多以内算同一排
ARENA_START_ROI: Rect = (1000, 610, 280, 60)  # 首页右下的 LIVE START
ARENA_DIFFICULTY_Y = 555  # 难度选择页 EASY … EXPERT 那一排
ARENA_PT_Y = 576  # 排名页各玩家的「220pt」那一排
# 有这些字时不猜（可能叠着别的弹窗）。匹配确认上有「取消」「确定要开始吗」、首页上有「消耗LB」，所以不用 OK_POPUP_BLOCK
ARENA_GUESS_BLOCK = ("购买", "星钻", "恢复", "使用", "招募", "下载", "兑换", "跳过", "解锁", "升级")

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


def story_title(items: list[OcrItem]) -> str | None:
    for title in STORY_TITLES:
        if find(items, title, TITLE_ROI, exact=True):
            return title
    return None


def sdk_logo(items: list[OcrItem]) -> OcrItem | None:
    """B 站 SDK 弹窗顶部的 bilibili 标志。取最上面的：登录记录里的账号名也可能带 bilibili（在标志下面）。"""
    logos = [it for it in items if "bilibili" in _compact(it.text) and in_roi(it, SDK_LOGO_ROI)]
    return min(logos, key=lambda it: it.cy, default=None)


def sdk_scale(items: list[OcrItem], logo_y: float = SDK_LOGO_Y) -> float:
    """B 站 SDK 弹窗相对 1280x720 时的缩放比（见 SDK_LOGO_ROI 上面的说明）；没认出 bilibili 标志时为 1。"""
    logo = sdk_logo(items)
    return (360 - logo.cy) / (360 - logo_y) if logo is not None else 1.0


def sdk_point(point: tuple[float, float], scale: float) -> tuple[int, int]:
    """1280x720 下量的 SDK 弹窗里的点，按缩放比 ``scale`` 换算到当前画面。"""
    return round(640 + (point[0] - 640) * scale), round(360 + (point[1] - 360) * scale)


def sdk_rect(rect: Rect, scale: float) -> Rect:
    x, y, w, h = rect
    return 640 + (x - 640) * scale, 360 + (y - 360) * scale, w * scale, h * scale


def skip_button(items: list[OcrItem], roi: Rect = SKIP_CORNER_ROI) -> OcrItem | None:
    """全屏演出右上角（或 ``roi`` 里）的「跳过」/「SKIP」。"""
    return next((it for text in SKIP_TEXTS if (it := find(items, text, roi))), None)


def skip_left(items: list[OcrItem]) -> int | None:
    """乐队确认页、跳过确认弹窗上还能跳过几次（「还剩2次」，English 的「x2」「Left」读成两项），读不到为 None。"""
    text = "".join(_compact(it.text) for it in sorted(items, key=lambda i: i.x) if in_roi(it, SKIP_LEFT_ROI))
    m = re.search(r"\d+", text)
    return int(m[0]) if m else None


def next_button(items: list[OcrItem]) -> OcrItem | None:
    """结算页右下角的「下一步」（繁中界面是「繼續」）。"""
    return find(items, "下一步", NEXT_ROI, exact=True) or find(items, "继续", NEXT_ROI, exact=True)


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
    now_loading = any("LOADING" in it.text.upper() for it in items)
    for text, screen in _DIALOGS.items():
        if now_loading and screen not in _LOADING_DIALOGS:
            continue
        # 恢复弹窗的标题在左上角（别处的「恢复LIVE BOOST」是说明文字）
        if it := find(items, text, TITLE_ROI if screen is Screen.LB_RECOVER else None):
            return _setting_dialog(items, it) if screen is Screen.LB_SETTING else screen
    if now_loading:
        # 激奏舞台「正在匹配」中间的转圈图写着 Now Loading
        return Screen.ARENA_MATCHING if find(items, "正在匹配", ARENA_MATCHING_ROI) else Screen.UNKNOWN
    # 跳过确认的标题别的语言没见过：左下「关闭」、右下「跳过」，中间有「全部消耗」
    if (
        find(items, "全部消耗", exact=True)
        and find(items, "关闭", BOTTOM_LEFT_ROI, exact=True)
        and skip_button(items, SKIP_BTN_ROI)
    ):
        return Screen.SKIP_CONFIRM
    # 韩文界面的标题「라이브 부스트 회복」有时拆成两项读出来，按左边的分页认
    if find(items, "道具", LB_TABS_ROI, exact=True) and find(items, "观看广告", LB_TABS_ROI, exact=True):
        return Screen.LB_RECOVER
    # 登录记录、用户中心叠在标题画面上（底下的 CRIWARE 还读得到），要先认
    if sdk_logo(items):
        title_roi = sdk_rect(SDK_TITLE_ROI, sdk_scale(items))
        if find(items, "登录记录", title_roi):
            return Screen.LOGIN_HISTORY
        if find(items, "用户中心", title_roi):
            return Screen.USER_CENTER
    if all(find(items, text) for text in TITLE_MENU_MARKS):
        return Screen.TITLE_MENU
    for it in items:
        if it.h >= 50 and in_roi(it, LIVE_END_ROI) and _LIVE_END.match(_norm(it.text)):
            return Screen.LIVE_END
    if (arena := _arena(items)) is not None:
        return arena
    if find(items, "ANKUP", RANK_UP_ROI) or find(items, "玩家等级", RANK_UP_ROI):
        return Screen.RANK_UP
    if find(items, "关闭", CLOSE_ROI, exact=True):
        return Screen.POPUP
    # 乐队确认页上点到综合能力打开的「综合能力详情」：左下「关闭」、右下「乐队强化」
    if find(items, "综合能力详情", POWER_TITLE_ROI) and find(items, "关闭", BOTTOM_LEFT_ROI, exact=True):
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
    if find(items, "跳过结果", TITLE_ROI):
        return Screen.SKIP_RESULT
    if find(items, "结算", TITLE_ROI):
        if find(items, "再次演出"):
            return Screen.RESULT_EXP
        if any(classify_label(it.text) == "perfect" for it in items):
            return Screen.RESULT
        if find(items, "玩家等级"):
            return Screen.RESULT_REWARD
        # 「羁绊」常读成「霜绊」
        # 繁中界面上每一页都是「繼續」，最后一页也认成这个（同样点右下角）
        if next_button(items) and (find(items, "详情") or find(items, "绊EXP")):
            return Screen.RESULT_EXP_NEXT
        return Screen.RESULT_OTHER
    for text, screen in _TITLES.items():
        if find(items, text, TITLE_ROI):
            if screen is Screen.ARENA_TOP and not any(find(items, mark) for mark in ARENA_TOP_MARKS):
                continue
            if screen is Screen.SONG_SELECT and _challenge_song_select(items):
                return Screen.CHALLENGE_SONG_SELECT
            # English 界面上「消耗LB」按钮也叫「Settings」，但上面有「Use All x1 Left」（全部消耗剩余n次）
            if screen is Screen.BAND_CONFIRM and (
                (find(items, "设置", CP_BUTTON_ROI, exact=True) and not find(items, "全部消耗"))
                or find(items, "CP", CP_ICON_ROI, exact=True)
            ):
                return Screen.CHALLENGE_BAND_CONFIRM
            return screen
    # 主界面没有标题，底部一排入口：认出两个就算（背景亮的时候个别字会读不出来）
    if sum(find(items, text, HOME_ENTRIES_ROI) is not None for text in HOME_ENTRIES) >= 2:
        return Screen.HOME
    if find(items, "SKIP", PLAYER_MENU_ROI, exact=True):
        return Screen.STORY_MENU
    if skip_button(items):
        return Screen.SKIPPABLE
    if find(items, "OK", OK_POPUP_ROI, exact=True) and not any(find(items, text) for text in OK_POPUP_BLOCK):
        return Screen.OK_POPUP
    if home_talk(items):
        return Screen.HOME_TALK
    return Screen.UNKNOWN


def home_talk(items: list[OcrItem]) -> bool:
    """主界面上点到角色弹出的对话（见 HOME_TALK_NAME_ROI）。"""
    if any(it.cy < HOME_TALK_TOP for it in items):
        return False
    name = any(in_roi(it, HOME_TALK_NAME_ROI) and len(_compact(it.text)) <= HOME_TALK_NAME_MAX for it in items)
    left, right = HOME_TALK_LINE_LEFT
    return name and any(in_roi(it, HOME_TALK_LINE_ROI) and left <= it.x <= right for it in items)


def _arena(items: list[OcrItem]) -> Screen | None:
    """激奏舞台对局途中的画面（首页按左上角标题认，见 _TITLES），都不是时为 None。"""
    if find(items, "匹配确认", TOP_CENTER_ROI):
        return Screen.ARENA_MATCH_CONFIRM
    if find(items, "正在匹配", ARENA_MATCHING_ROI) or (
        find(items, "匹配", TITLE_ROI, exact=True) and find(items, "表情", exact=True)
    ):
        return Screen.ARENA_MATCHING
    if find(items, "难度选择", TOP_CENTER_ROI):
        return Screen.ARENA_DIFFICULTY
    if any(in_roi(it, ARENA_RESULT_ROI) and _compact(it.text).startswith("结果") for it in items):
        return Screen.ARENA_RESULT
    # 「激奏BESTPERFORMER」的前两个字常读错（激秦、激春）
    if not find(items, "结算", TITLE_ROI) and (
        find(items, "演出记录", ARENA_RECORD_ROI) or any("PERFORMER" in it.text.upper() for it in items)
    ):
        return Screen.ARENA_RANKING
    # 段位提升的 RANK UP 大字和羁绊等级的一样高，但没有 OK，下面是新旧两个段位名
    if (
        find(items, "ANKUP", BOND_UP_ROI)
        and not find(items, "OK", CLOSE_ROI, exact=True)
        and not any(find(items, text) for text in ("羁绊等级", "乐队RANK", "玩家等级"))
        and sum(in_roi(it, ARENA_TIER_ROI) for it in items) >= 2
    ):
        return Screen.ARENA_RANK_UP
    # 激奏舞台的说明（第一次进来时也可能弹出）：左「关闭」右「下一步」
    if find(items, "前往帮助") and find(items, "关闭", exact=True):
        return Screen.POPUP
    return None


def arena_guess(items: list[OcrItem]) -> Screen | None:
    """认不出的画面猜是激奏舞台的哪一页，不像时为 None。只在激奏舞台任务里、:func:`classify` 认不出时用
    （见 ``GameNavigator.look``），简中等适配了的语言照常按标题认，不走这里。

    还没适配的语言（#105）里标题、按钮上的字认不出，只看不分语言的：撃奏（LUCK / COMBO / JUST）那一排的高度，
    再加一处这一页才有的（首页的 LIVE START、匹配确认的 OK、难度选择的 EASY … EXPERT、排名页的名次或「220pt」）；
    结果弹窗看「2nd AP+25」（见 :func:`arena_result`）。
    """
    if any(find(items, text) for text in ARENA_GUESS_BLOCK):
        return None
    if any(in_roi(it, ARENA_RESULT_ROI) and _ARENA_RESULT_ANY.search(_digits(it.text)) for it in items):
        return Screen.ARENA_RESULT

    def row(y: float, match) -> int:
        return sum(1 for it in items if abs(it.cy - y) <= ARENA_ROW_DY and match(_norm(it.text)))

    def page(screen: Screen) -> bool:
        if screen is Screen.ARENA_TOP:
            return any(in_roi(it, ARENA_START_ROI) and re.search("LIVE|START", _norm(it.text)) for it in items)
        if screen is Screen.ARENA_MATCH_CONFIRM:
            return find(items, "OK", MATCH_OK_ROI, exact=True) is not None
        if screen is Screen.ARENA_DIFFICULTY:
            return row(ARENA_DIFFICULTY_Y, parse_difficulty) >= 2
        return arena_placement(items) is not None or row(ARENA_PT_Y, lambda t: re.fullmatch(r"\d+PT", t)) >= 2

    return next((s for y, s in ARENA_GEKISOU_ROWS if row(y, ARENA_GEKISOU.search) >= 2 and page(s)), None)


def _challenge_song_select(items: list[OcrItem]) -> bool:
    if any(find(items, mark, roi) for mark, roi in NORMAL_SONG_SELECT_MARKS):
        return False
    if any(find(items, cat, SONG_CATEGORY_ROI) for cat in SONG_CATEGORIES):
        return False
    # 韩文界面的「확인」统一换成了 OK
    ok = find(items, "确定", SONG_OK_ROI, exact=True) or find(items, "OK", SONG_OK_ROI, exact=True)
    return bool(ok and find(items, "HIGH"))


# 维护页上的「2026/10/02（周五）11:00~2026/10/02（周五）16:00」
_MAINT_TIME = re.compile(r"(\d{4}/\d{1,2}/\d{1,2})\D*?(\d{1,2}:\d{2})")


def maintenance_period(items: list[OcrItem]) -> str | None:
    """维护页上写的维护时间（如「2026/10/02 11:00 ~ 2026/10/02 16:00」），读不到为 None。"""
    for it in items:
        times = _MAINT_TIME.findall(_compact(it.text))
        if times:
            return " ~ ".join(f"{d} {t}" for d, t in times)
    return None


def login_rows(items: list[OcrItem]) -> list[OcrItem]:
    """登录记录里的账号名（从上到下）：下面紧跟着「上次登录」的那一项。"""
    s = sdk_scale(items)
    roi = sdk_rect(LOGIN_ROWS_ROI, s)
    lasts = [it for it in items if LAST_LOGIN in it.text]
    rows = [
        it
        for it in items
        if LAST_LOGIN not in it.text
        and in_roi(it, roi)
        and any(abs(last.x - it.x) < 40 * s and 15 * s < last.cy - it.cy < 50 * s for last in lasts)
    ]
    return sorted(rows, key=lambda it: it.cy)


def login_expanded(items: list[OcrItem]) -> bool:
    """登录记录的账号列表展开了（盖住了下面的「登录」「切换其他账号」）。"""
    return find(items, "切换其他账号") is None


def account_key(text: str) -> str:
    """比较账号名用：全角转半角、忽略大小写和空白。"""
    return _compact(unicodedata.normalize("NFKC", text)).lower()


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


def arena_song(items: list[OcrItem]) -> tuple[str, int | None]:
    """激奏舞台难度选择页上转盘定下的 (曲名, EXPERT 等级)。"""
    parts = sorted((it for it in items if in_roi(it, ARENA_SONG_ROI)), key=lambda it: it.x)
    title = " ".join(it.text.strip() for it in parts)
    levels = [parse_level(it.text) for it in items if in_roi(it, ARENA_LEVEL_ROI)]
    return title, next((lv for lv in levels if lv is not None), None)


_ARENA_RESULT = re.compile(r"结果([1-5])\D*?AP([+\-－−一—]?)(\d{1,4})$")
# 还没适配的语言（#105）：「结果」认不出，只认后面的「3rd AP+15」
_ARENA_RESULT_ANY = re.compile(r"(?<!\d)([1-5])(?:st|nd|rd|th)\D*?AP([+\-－−一—]?)(\d{1,4})$", re.I)


def arena_result(items: list[OcrItem]) -> tuple[int | None, int | None]:
    """激奏舞台结果弹窗「结果 3rd AP+15」→ (名次, 段位分增减)；读不出的为 None。
    没有「结果」开头的一项时（还没适配的语言）认「3rd AP+15」。"""
    for it in items:
        text = _digits(it.text)
        if not in_roi(it, ARENA_RESULT_ROI) or not text.startswith("结果"):
            continue
        if m := _ARENA_RESULT.match(text):
            sign = -1 if m.group(2) not in ("", "+") else 1
            return int(m.group(1)), sign * int(m.group(3))
        place = re.match(r"结果([1-5])", text)
        return (int(place.group(1)) if place else None), None
    for it in items:
        if in_roi(it, ARENA_RESULT_ROI) and (m := _ARENA_RESULT_ANY.search(_digits(it.text))):
            sign = -1 if m.group(2) not in ("", "+") else 1
            return int(m.group(1)), sign * int(m.group(3))
    return None, None


def arena_ap_total(items: list[OcrItem]) -> int | None:
    """激奏舞台结果弹窗上加减之后的段位分。一位数常检测不到，这时用 :func:`arena_ap_text` 读直接识别的结果。"""
    for it in items:
        if in_roi(it, ARENA_AP_ROI) and (value := arena_ap_text(it.text)) is not None:
            return value
    return None


def arena_ap_text(text: str) -> int | None:
    """段位分的识别文字 → 数字（最多 5 位），不是数字时为 None。"""
    return int(digits) if re.fullmatch(r"\d{1,5}", digits := _digits(text)) else None


def arena_placement(items: list[OcrItem]) -> int | None:
    """激奏舞台排名页左边自己的名次（大号的「3rd」），结果弹窗没读到名次时用。"""
    for it in items:
        if in_roi(it, ARENA_PLACE_ROI) and (m := re.fullmatch(r"([1-5])(st|nd|rd|th)", _digits(it.text), re.I)):
            return int(m.group(1))
    return None


def note_speed(items: list[OcrItem]) -> float | None:
    """演出前选项设置弹窗中「节奏图示速度」的数值（位于标签下一行的滑条中央）。"""
    label = find(items, "节奏图示速度")
    if label is None:
        return None
    for it in items:
        if 0 < it.cy - label.cy < 90 and re.fullmatch(r"\d+\.\d{2}", it.text.strip()):
            return float(it.text)
    return None


# LIVE BOOST消耗设置弹窗（乐队确认页「跳过」打开的跳过确认弹窗也一样）：左侧一列消耗数和单选按钮，从上到下
# 最大消耗 … 1、0，每行 56px，同时只露出 4 行（T.G.W CARD 10 级解锁 4、14 级解锁 5，这时要上下拖才看得全）。
# 单选按钮在消耗数左边 LB_RADIO_DX（两种弹窗、几种布局的位置不同，按读到的消耗数算），选中的圆心是白色、
# 未选中是暗红；下方「全部消耗」复选框同理（每天能用几次看月卡，消耗持有的全部，最多 10）。持有数量「24/99」在弹窗底部中间
LB_MAX_COST = 5
LB_ROW_STEP = 56
LB_ROW_X = (415, 460)  # 消耗数中心 x 的范围（右边是奖励倍数，下面「全部消耗」那行的数字在 LB_ROWS_Y 外面）
LB_ROWS_Y = (110, 306)  # 完整露出的行中心 y 的范围
LB_RADIO_DX = -123
LB_ALL_CHECK = (317, 439)
SKIP_ALL_CHECK = (316, 434)  # 跳过确认弹窗的「全部消耗」
# 上下拖消耗数列表：从上往下拖露出更大的消耗数，反过来露出 0
LB_LIST_DRAG = ((640, 150), (640, 290))
LB_HELD_ROI: Rect = (560, 545, 180, 40)
LB_ALL_LEFT_ROI: Rect = (300, 380, 160, 40)  # 「全部消耗」复选框上面的「今日剩余1次」（中心约 (378, 403)）


@dataclass(frozen=True)
class LbRows:
    x: int  # 单选按钮中心 x
    y: dict[int, int]  # 消耗数 → 这一行单选按钮中心 y（只有完整露出的行）


def lb_rows(items: list[OcrItem]) -> LbRows | None:
    """消耗设置 / 跳过确认弹窗里露出的各行。消耗数是单个数字，很小的「1」偶尔读偏、整个漏掉：按读到的几个数字
    推出整列的位置（离大多数差太远的不要），漏读的行也能算出来。一个都没读到时为 None。"""
    digits = []
    for it in items:
        t, cx = _digits(it.text), it.x + it.w / 2
        if (
            re.fullmatch(f"[0-{LB_MAX_COST}]", t)
            and LB_ROW_X[0] <= cx <= LB_ROW_X[1]
            and LB_ROWS_Y[0] - 15 <= it.cy <= LB_ROWS_Y[1] + 15
        ):
            digits.append((int(t), cx, it.cy))
    if not digits:
        return None
    base = median(cy + LB_ROW_STEP * v for v, _, cy in digits)  # 消耗 0 那一行的 y
    near = [cx for v, cx, cy in digits if abs(cy + LB_ROW_STEP * v - base) <= 12]  # 离大多数差太远的不要
    x = median(near or [cx for _, cx, _ in digits])  # 只有两行、错位时可能一个都不剩：按全部算，别抛错
    rows = {v: round(base - LB_ROW_STEP * v) for v in range(LB_MAX_COST + 1)}
    return LbRows(round(x) + LB_RADIO_DX, {v: y for v, y in rows.items() if LB_ROWS_Y[0] <= y <= LB_ROWS_Y[1]})


def lb_all_used(items: list[OcrItem]) -> bool:
    """「全部消耗」下面写着今天的次数已用完（「今日次数已用完。」，没用完时是「今日剩余1次」；别的语言没见过，认不出时由勾选不上兜底）。"""
    return find(items, "已用完") is not None


def lb_all_left(items: list[OcrItem]) -> int | None:
    """「全部消耗」复选框上面写的今天还能用几次（「今日剩余1次」，日文「本日あと1回」；用完时这里没有字），
    没读到为 None（English、한국어、繁體中文的还没见过）。"""
    for it in items:
        m = re.search(r"今日剩[余餘](\d+)次|本日あと(\d+)回", _digits(it.text))
        if m and in_roi(it, LB_ALL_LEFT_ROI):
            return int(m.group(1) or m.group(2))
    return None


def lb_held(items: list[OcrItem]) -> int | None:
    """消耗设置弹窗上的 LB 持有数量（「24/99」中的 24）。English 界面上是「2 / 99」，斜杠常读成「1」（「2199」）。"""
    for it in items:
        m = re.fullmatch(r"(\d+)/\d+|(\d{1,2})1(99)", _compact(it.text))
        if m and in_roi(it, LB_HELD_ROI):
            return int(m.group(1) or m.group(2))
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
LB_TABS_ROI: Rect = (40, 110, 200, 200)
LB_TAB_OTHERS = ((72, 217), (72, 285))  # 星钻、观看广告
LB_PLUS_X = 910
# 小型LIVE BOOST饮料每瓶恢复 1 个，LIVE BOOST饮料 10 个（LIVE 常读成 LIvE、LVE）；
# 韩文是「미니 부스트 드링크」「부스트 드링크」（OCR 常把后一个拆成两项，同一行挨着的拼起来认）
LB_DRINK_NAME = re.compile(r"(小型|미니)?(L[A-Z]{1,3}BOOST饮料|부스트'?드링크)")
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


def _join_rows(items: list[OcrItem], gap: float = 20) -> list[OcrItem]:
    """同一行、左右挨着（间隔、重叠不超过 ``gap``）的识别项拼成一项。"""
    out: list[OcrItem] = []
    for it in sorted(items, key=lambda i: i.x):
        prev = next((o for o in out if abs(o.cy - it.cy) < 8 and -gap <= it.x - (o.x + o.w) <= gap), None)
        if prev is None:
            out.append(it)
            continue
        y, bottom = min(prev.y, it.y), max(prev.y + prev.h, it.y + it.h)
        out[out.index(prev)] = OcrItem(prev.x, y, it.x + it.w - prev.x, bottom - y, prev.text + it.text)
    return out


def lb_drinks(items: list[OcrItem]) -> list[LbDrink]:
    """恢复LIVE BOOST「道具」页上认得的饮料，从上到下（名字认不出、数量没读到的行不要）。"""
    drinks = []
    for it in _join_rows([i for i in items if i.x + i.w / 2 <= LB_DRINK_NAME_X]):
        m = LB_DRINK_NAME.fullmatch(_compact(it.text).upper())
        if not m:
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
# 数字右对齐（右沿 x≈1226），「1」比别的数字窄：7 位里没有 1 时（3438301）整串最宽，左沿伸出原来的框（x=960）
# 就读丢开头一到三位（438301、5463）。左边只有背景，留够余量；也不能太宽，杂点读成 1 会连进分数
# 下沿不能碰到下面一行的 HIGH SCORE 数字（y≈234）
RESULT_SCORE_ROI: Rect = (900, 166, 340, 64)
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
