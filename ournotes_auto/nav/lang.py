"""游戏的其他语言：把 OCR 读到的文字换成简中界面上的说法，画面识别、按钮查找都只按简中写。

繁體中文：先逐字转成简体，再把用词和简中不同的地方换掉（如「選擇樂曲」→「选择乐曲」→「乐曲选择」）。
English：整项是某个短词（忽略空白，大小写要对上：「Normal」是分页、「NORMAL」是难度）才换成简中的词；长一些的说法包含就换（如标题、确认框里的句子）。
"""

from __future__ import annotations

import re

from .t2s import SIMP, TRAD

_T2S = str.maketrans(TRAD, SIMP)

# 繁中界面（已转成简体字）的用词 → 简中界面的用词：先换词，再换整句
ZH_HANT_WORDS = {
    "乐团": "乐队",
    "设定": "设置",
    "回复": "恢复",
    "报酬": "奖励",
    "资料": "数据",
    "伺服器": "服务器",
    "快取": "缓存",
    "中断": "终止",
    "开放": "解锁",
    "侧写故事": "视角故事",
}
ZH_HANT = {
    "选择乐曲": "乐曲选择",
    "选择乐队故事章节": "乐队故事章节选择",
    "选择乐队故事话数": "乐队故事话数选择",
    "选择羁绊故事": "羁绊故事选择",
    "演出TOP": "演出首页",
    "预设": "默认",
    "转蛋": "招募",
    "全消耗": "全部消耗",
    "一并领取": "一键领取",
    "期间限定任务": "限定任务",
    "已暂停演出": "演出已暂停",
}

# 词序和简中不同的：「LIVE BOOST恢复」→「恢复LIVE BOOST」（LIVE BOOST 常读错，按 BOOST 认）
ZH_HANT_RULES = ((re.compile(r"^(.*BOOST)\s*恢复$"), r"恢复\1"),)

# 长的先换，免得被其中较短的词截断
_PHRASES = sorted(ZH_HANT.items(), key=lambda kv: -len(kv[0]))

# English 界面：整项等于这些（按钮、分页、页面标题）才换，大小写要一致
EN_WORDS = {
    "Cancel": "取消",
    "Close": "关闭",
    "Confirm": "确定",
    "GACHA": "招募",
    "STORY": "故事",
    "LIVE": "演出首页",  # 演出首页的标题；演出前选项设置的「演出」按钮也是它
    "Select Song": "乐曲选择",
    "Formation": "乐队确认",
    "Options": "设置",
    "Settings": "设置",  # 乐队确认页的「消耗LB」按钮
    "Default": "默认",
    "Random": "随机选曲",
    "All": "全部",
    "Reset": "重置",
    "No Preference": "不指定",
    "Not Cleared": "未完成",
    "Cleared": "已完成",
    "No SS Rank": "未达成SS",
    "SS Rank": "已达成SS",
    "NO FULL COMBO": "未FULLCOMBO",
    "NO ALL PERFECT": "未ALLPERFECT",
    "Recovery": "恢复",
    "LIVE Boost Recover": "恢复LIVE BOOST",
    "Small Boost Drink": "小型LIVE BOOST饮料",
    "Boost Drink": "LIVE BOOST饮料",
    "Item": "道具",
    "Star": "星钻",
    "Pause": "暂停",
    "Paused": "演出已暂停",
    "Quit": "终止",
    "Retry": "重试",
    "Resume": "继续",
    "Result": "结算",
    "Next": "下一步",
    "Player Rank": "玩家等级",
    "Details": "详情",
    "Bond EXP": "羁绊EXP",
    "Studio Practice": "录音室练习",
    "Missions": "任务",
    "Claim All": "一键领取",
    "Daily": "每日",
    "Normal": "常规",
    "Song Unlock": "乐曲解锁",
    "Home Unlock": "主页解锁",
    "Claim Rewards": "领取奖励",
    "Mission Pass": "任务通行证",
    "Pass Missions": "通行证任务",
    "Immersive Home Unlocked": "沉浸式主页解锁",
    "Song Unlocked": "乐曲解锁",
    "Story Unlocked": "故事解锁",
    "OBTAINED": "获得奖励",  # 「REWARDS OBTAINED」常读成两行
    "Limited-Time Mission": "限定任务",
    "Beginner Mission": "新手任务",
    "Shop": "商店",
    "Catalog Shop": "专享商品目录",
    "Free": "免费",
    "Gift Box": "礼物盒",
    "Unclaimed": "未领取",
    "Claim History": "领取记录",
    "Claim Points": "领取积分",  # T.G.W CARD 页
    "Claim": "领取",
    "Daily Reward": "每日奖励",
    "Band Story": "乐队故事",
    "Bond Story": "羁绊故事",
    "CHALLENGE LIVE": "挑战演出",
    "Menu": "菜单",  # 标题画面右上角 ☰
    "News": "公告",
    "Language": "选择语言",
    "Account Center": "用户中心",
    "Download All": "一键下载",
    "Switch Server": "切换服务器",
    "Cache Clear": "清除缓存",
    "Mode Settings": "模式设置",
    "Skip": "跳过",
    "Select Band Story Chapter": "乐队故事章节选择",
    "Select Band Story Episode": "乐队故事话数选择",
    "Another Story": "视角故事",
    "View Story": "观看故事",
    "Without Voice": "无语音",
    "Ikka Dumb Rock!": "一家Dumb Rock!",
}
# 包含就换（长的先换）
EN_PHRASES = {
    "Cancel": "取消",
    "Purchase": "购买",
    "Buy": "购买",
    "Recover": "恢复",
    "Download": "下载",
    "Download data": "下载追加的游戏数据",
    "Consumption Setting": "消耗设置",
    "Consume All": "全部消耗",
    "Use All": "全部消耗",
    "Pre-LIVE Option Settings": "演出前选项设置",
    "Notes Speed": "节奏图示速度",
    "Abort LIVE": "要终止演出",
    "Retry and start": "要重试并从头开始",
    "One More LIVE": "再次演出",
    "Rewards Obtained": "获得奖励",
    "Reward Obtained": "获得奖励",
    "Practice Lv": "练习Lv",
    "Purchase Complete": "购买完成",
    "no gifts available to claim": "没有可领取的礼物",
    "no rewards available to claim": "没有可领取的奖励",
    "download the voice data": "要下载语音数据",
    "skip the story": "要跳过故事吗",
}


def _en_key(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _en_pattern(src: str) -> re.Pattern:
    """忽略大小写；词中间的空白可有可无（OCR 常把空格丢掉）。"""
    return re.compile(r"\s*".join(map(re.escape, src.replace(" ", ""))), re.IGNORECASE)


EN_RULES = (
    (re.compile(r"^(\d+)\s*Days?$"), r"\1天"),  # 限定任务、新手任务右侧的「1Day」
    # 认不出的弹窗看到这些字就不点 OK（见 screens.OK_POPUP_BLOCK）：价格「Star×120」、「Use」、「Gacha」
    (re.compile(r"\bStar\b"), "星钻"),
    (re.compile(r"\bUse\b"), "使用"),
    (re.compile(r"\bGacha\b"), "招募"),
    (re.compile(r"^Chapter\s*(\d+)$"), r"第\1话"),  # 话数选择的「Chapter 11」
    (re.compile(r"\s*Point\s*of\s*view\s*ver\.?$", re.IGNORECASE), "视角Ver."),  # 「Raika Point of view ver.」
)

_EN_WORDS = {_en_key(src): dst for src, dst in EN_WORDS.items()}
_EN_PHRASES = [(_en_pattern(src), dst) for src, dst in sorted(EN_PHRASES.items(), key=lambda kv: -len(kv[0]))]


def to_simplified(text: str) -> str:
    """逐字把繁体字转成简体字（曲名里的日文汉字也会转，如「無路矢」→「无路矢」，所以曲目目录也要这样转了再比）。"""
    return text.translate(_T2S)


def localize(text: str) -> str:
    """OCR 读到的一段文字 → 简中界面上的说法（本来就是简中的原样返回）。"""
    if (word := _EN_WORDS.get(_en_key(text))) is not None:
        return word
    if re.search(r"[A-Za-z]", text):
        for pattern, dst in _EN_PHRASES:
            text = pattern.sub(dst, text)
        for pattern, repl in EN_RULES:
            text = pattern.sub(repl, text)
    text = to_simplified(text)
    for src, dst in ZH_HANT_WORDS.items():
        text = text.replace(src, dst)
    for src, dst in _PHRASES:
        if src in text:
            text = text.replace(src, dst)
    for pattern, repl in ZH_HANT_RULES:
        text = pattern.sub(repl, text)
    return text
