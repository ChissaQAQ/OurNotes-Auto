"""游戏的其他语言：把 OCR 读到的文字换成简中界面上的说法，画面识别、按钮查找都只按简中写。

繁體中文：先逐字转成简体，再把用词和简中不同的地方换掉（如「選擇樂曲」→「选择乐曲」→「乐曲选择」）。
English：整项是某个短词（忽略空白，大小写要对上：「Normal」是分页、「NORMAL」是难度）才换成简中的词；长一些的说法包含就换（如标题、确认框里的句子）。
한국어：和 English 一样按短词、说法换，另有一些按正则换（「제 3 화」→「第3话」）；韩文要用韩文 OCR 模型才读得出（见 ocr.py）。
日本語（日服）：整项是某个短词才换；有假名时再按正则、说法换（日服的字常读得不全，如「バンドスト」）；
  「1日目」「燈視点Ver.」这类只有汉字的说法总是按正则换，剩下的汉字和繁中一样转成简体。
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
    "Don't allow": "不允许",  # 系统问要不要允许发送通知（Android 是英文时）
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
    "SHOP": "商店",  # 主界面底部的入口
    "BAND": "乐队",
    "Catalog Shop": "专享商品目录",
    "Free": "免费",
    "Gift Box": "礼物盒",
    "Claim Gifts": "领取礼物",
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
    "Select Event Story Episode": "活动故事话数选择",
    "Event Story": "活动故事",
    "EVENT": "活动",
    "Select Bond Story": "羁绊故事选择",
    "Another Story": "视角故事",
    "View Story": "观看故事",
    "Without Voice": "无语音",
    "Ikka Dumb Rock!": "一家Dumb Rock!",
}
# 包含就换（长的先换）
EN_PHRASES = {
    "claim all gifts at once": "一键领取礼物",
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
    "send you notifications": "发送通知",
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

# 한국어 界面（OCR 用韩文模型，见 ocr._pick）：整项等于这些才换；字间的空格 OCR 常丢，比较时忽略
KO_WORDS = {
    "확인": "OK",  # 弹窗的 OK 和选曲页、章节选择的「确定」都是它（导航找不到「确定」时点默认位置）
    "취소": "取消",
    "닫기": "关闭",
    "뽑기": "招募",
    "라이브": "演出",  # 主界面的入口、演出前选项设置的「演出」按钮
    "기본": "默认",
    "랜덤": "随机选曲",
    "회복": "恢复",
    "설정": "设置",
    "결과": "结算",
    "다음": "下一步",
    "상세": "详情",
    "홈": "主页",
    "일시정지": "暂停",
    "중단": "终止",
    "중탄": "终止",  # 终止确认的按钮常读成「중탄」
    "재시도": "重试",
    "계속": "继续",
    "아이템": "道具",
    "아이렘": "道具",  # 「템」常读成「렘」
    "리셋": "重置",
    "리엘": "重置",
    "최대": "最大",
    "광고시청": "观看广告",
    "미션": "任务",
    "미션패스": "任务通行证",
    "패스미션": "通行证任务",
    "기간한정미션": "限定任务",
    "초보자미션": "新手任务",
    "스튜디오연습": "录音室练习",
    "선물함": "礼物盒",
    "데일리": "每日",
    "일반": "常规",
    "곡해제": "乐曲解锁",
    "홈해제": "主页解锁",
    "일괄수령": "一键领取",
    "수령": "领取",
    "수령완료": "已领取",
    "포인트수령": "领取积分",
    "미수령": "未领取",
    "수령기록": "领取记录",
    "구매하기": "购买",
    # 标题菜单
    "메뉴": "菜单",
    "공지사항": "公告",
    "언어변경": "选择语言",
    "언어변겸": "选择语言",
    "고객지원": "用户中心",
    "일괄다운로드": "一键下载",
    "서버전환": "切换服务器",
    "캐시삭제": "清除缓存",
    "모드설정": "模式设置",
    "보이스없음": "无语音",
    "보이스있음": "有语音",
    "해제조건": "解锁条件",
}
# 包含就换（长的先换）：韩文和中文一样不分词，「밴드 스토리」→「乐队故事」这样逐段换
KO_PHRASES = {
    "추가게임데이터": "下载追加的游戏数据",
    "라이브TOP": "演出首页",
    "곡선택": "乐曲选择",
    "밴드확인": "乐队确认",
    "프리라이브": "自由演出",
    "챌린지라이브": "挑战演出",
    "챌린지": "挑战",
    "라이브부스트": "LIVE BOOST",
    "소모설정": "消耗设置",
    "전체소모": "全部消耗",
    "LB소모": "消耗LB",
    "라이브전설정": "演出前选项设置",
    "노트속도": "节奏图示速度",
    "스토리": "故事",
    "밴드": "乐队",
    "상점": "商店",
    "전체": "全部",
    "스킵": "跳过",
    "스타": "星钻",
    "구매": "购买",
    "무료": "免费",
    "다운로드": "下载",
    "달성보상": "达成奖励",
    "플레이어랭크": "玩家等级",
    "보상": "奖励",
    "이벤트": "活动",
    "인연": "羁绊",
    "종합력상세": "综合能力详情",
    "챕터선택": "章节选择",
    "챌터선택": "章节选择",  # 「챕」常读成「챌」
    "화선택": "话数选择",
    "스토리보기": "观看故事",
    "스토리선택": "故事选择",  # 「인연 스토리 선택」
    "어나더스토리": "视角故事",
    "데일리보상": "每日奖励",
    "보상획득": "获得奖励",
    "선물수령": "领取礼物",
    "선물을일괄로수령": "一键领取礼物",
    "구매완료": "购买完成",
    "일시정지되었습니다": "演出已暂停",
    "라이브를중단": "要终止演出",
    "재시도하여라이브를처음부터": "要重试并从头开始",
}
# 韩文模型常读错的几处（混着英文字母的按钮、个别字形相近的字）
KO_RULES = (
    (re.compile(r"^라이브\s*(TOP|[7T].?)$"), "演出首页"),  # 「라이브 TOP」常读成「라이브7아」
    (re.compile(r"^.?B\s*소모$"), "消耗LB"),  # 「LB 소모」常读成「느B소모」
    (re.compile(r"^라이[브부]\s*전\s*설정$"), "演出前选项设置"),  # 「브」常读成「부」
    (re.compile(r"^스[킵킴]$"), "跳过"),
    (re.compile(r"^한\s*\S\s*더\s*라이브$"), "再次演出"),  # 「한 번 더 라이브」，「번」常读成「뻔」
    (re.compile(r"^(라이[브부]\s*부스트|.*BOOST)\s*회복$"), "恢复LIVE BOOST"),  # 「라이브 부스트 회복」
    (re.compile(r"^[구무]매\s*[하학]기$"), "购买"),  # 商店的「구매하기」
    (re.compile(r"^T?\.?[Gg]?\.?[Ww]\.?\s*카드$"), "T.G.W CARD"),  # 「T.G.W 카드」常读成「gw카드」「Tw카드」
    (re.compile(r"^연습\s*(?:Lv\.?|[니느])\.?$"), "练习Lv"),  # 录音室练习的 LEVEL UP：「연습 Lv」常读成「연습니」
    (re.compile(r"^패스\s*(?:pt|\S)?\s*획득$"), "获得通行证pt"),  # 「패스 pt 획득」常读成「패스만획득」「패스0획득」
    (re.compile(r"^제\s*(\d+)\s*화$"), r"第\1话"),
    (re.compile(r"^(\d+)\s*[일월]차$"), r"\1天"),  # 限定任务、新手任务右侧的「1일차」
    # 视角故事：简介里「라이카 시점 Ver.」，卡片上名字下面一行「시점 Ver.」（常读成「점ver」「럼Ver」）
    (re.compile(r"^(.+?)\s*시점\s*(?:[Vv]e|%).*$"), r"\1视角Ver."),
    (re.compile(r"^.?[점럼]\s*[Vv]e.*$"), "视角Ver."),
    (re.compile(r"^보이스\s*데이터를\s*다운로드.*$"), "要下载语音数据，并观看故事吗？"),
    (re.compile(r"^스토리를\s*스[킵킴].*$"), "要跳过故事吗？"),
    # 恢复确认「LIVE BOOST를 1 회복합니다」「계속하시겠습니까?」；恢复后的提示没见过，按「회복되었습니다」认
    (re.compile(r"^.*(?:부스트|BOOST)\s*를\s*(\d+)\s*회복\s*합니다.*$"), r"将恢复\1点LIVE BOOST。确定要恢复吗？"),
    (re.compile(r"^.*(?:부스트|BOOST).*회복\s*되었습니다.*$"), "已恢复LIVE BOOST。"),
    (re.compile(r"^(\d+)\s*회\s*남음$"), r"还剩\1次"),
)

# 日服（日本語）：整项等于这些才换（忽略空白）；只有汉字的词也放在这里（免得把曲名、简中里的字换掉）
JA_WORDS = {
    "キャンセル": "取消",
    "閉じる": "关闭",
    "次へ": "下一步",
    "決定": "确定",
    "ガチャ": "招募",
    "ストーリー": "故事",
    "バンド": "乐队",
    "ライブ": "演出",  # 主界面的入口、演出前选项设置的「演出」按钮
    "ライプ": "演出",
    "ライブTOP": "演出首页",
    "楽曲選択": "乐曲选择",
    "バンド確認": "乐队确认",
    "オプション": "设置",
    "デフォルト": "默认",
    "ランダム": "随机选曲",
    "オリジナル": "原创",
    "カバー": "翻唱",
    "すべて": "全部",
    # 乐曲选择页的筛选面板
    "リセット": "重置",
    "お気に入り": "收藏",
    "難易度": "难度",
    "楽曲レベル": "乐曲等级",
    "プレイ状況": "游玩状况",
    "楽曲タグ": "乐曲标签",
    "ライブ演出": "演出效果",
    "指定なし": "不指定",
    "未クリア": "未完成",
    "未スコアSS": "未达成SS",
    "クリア": "已完成",
    "スコアSS": "已达成SS",
    "フリーライブ": "自由演出",
    "チャレンジライブ": "挑战演出",
    "イベント": "活动",
    "総合力": "综合力",
    "LB消費": "消耗LB",
    "スキップ": "跳过",
    "回復": "恢复",
    "アイテム": "道具",
    "スター": "星钻",
    "CM視聴": "观看广告",
    "一時停止": "暂停",
    "時停止": "暂停",
    "中断": "终止",
    "リトライ": "重试",
    "再開": "继续",
    "リザルト": "结算",
    "詳細": "详情",
    "メニュー": "菜单",
    "お知らせ": "公告",
    "ホーム": "主页",
    "許可しない": "不允许",  # 系统问要不要允许发送通知（Android 是日文时）
    "ミッション": "任务",
    "デイリー": "每日",
    "ノーマル": "常规",
    "楽曲解放": "乐曲解锁",
    "ホーム解放": "主页解锁",
    "一括受取": "一键领取",
    "一括受け取り": "一键领取",
    "受取": "领取",
    "未受取": "未领取",
    "受取履歴": "领取记录",
    "ミッションパス": "任务通行证",
    "パスミッション": "通行证任务",
    "初心者ミッション": "新手任务",
    "期間限定ミッション": "限定任务",
    "スタジオ練習": "录音室练习",
    "友達招待": "好友邀请",
    "交換所": "交换所",
    "プレゼントボックス": "礼物盒",
    "プレゼント受け取り": "领取礼物",
    "無料": "免费",
    "年齢確認": "年龄确认",
    "バンドストーリー": "乐队故事",
    "絆ストーリー": "羁绊故事",
    "バンドストーリー章選択": "乐队故事章节选择",
    "バンドストーリー話選択": "乐队故事话数选择",
    "絆ストーリー選択": "羁绊故事选择",
    "イベントストーリー": "活动故事",
    "イベントストーリー話選択": "活动故事话数选择",
    "アナザーストーリー": "视角故事",
    "ストーリーを見る": "观看故事",
    "解放条件": "解锁条件",
    "ヘルプへ": "前往帮助",
    "ボイスなし": "无语音",
    "ボイスあり": "有语音",
    "プレイヤーランク": "玩家等级",
    "総合力詳細": "综合能力详情",
    "ストーリー解放": "故事解锁",
}
# 包含就换（长的先换）
JA_PHRASES = {
    "ライブブースト": "LIVE BOOST",
    "消費設定": "消耗设置",
    "全消費": "全部消耗",
    "プチブーストドリンク": "小型LIVE BOOST饮料",
    "ブーストドリンク": "LIVE BOOST饮料",
    "ライブ前オプション設定": "演出前选项设置",
    "ノーツ速度": "节奏图示速度",
    "ライブを一時停止しました": "演出已暂停",
    "ライブを中断して": "要终止演出",
    "リトライしてライブを最初から": "要重试并从头开始",
    "報酬受け取り": "领取奖励",
    "報酬獲得": "获得奖励",
    "プレゼントをまとめて受け取ります": "一键领取礼物",
    "受け取れるプレゼントがありません": "没有可领取的礼物",
    "受け取れる報酬がありません": "没有可领取的奖励",
    "絞り込み条件に該当する楽曲がありません": "没有符合筛选条件的乐曲",
    "選択できる楽曲がありません": "没有可以随机选择的乐曲",  # 随机选曲没得选时的提示条
    "購入完了": "购买完成",
    "購入": "购买",
    "練習Lv": "练习Lv",
    "ボイスデータをダウンロード": "要下载语音数据",
    "ストーリーをスキップしますか": "要跳过故事吗",
    "追加データ": "下载追加的游戏数据",
    "ダウンロード": "下载",
    "メンテナンス中": "服务器正在维护",
    "新しいバージョン": "检测到新版本",
    "日付が変更": "日期已变更",
    "日付が変わりました": "日期已变更",
    "タイトルに戻る": "返回标题画面",
    "タイトルへ戻る": "返回标题画面",
    "通信エラー": "发生网络连接错误",
    "達成報酬一覧": "达成奖励列表",
    "通知の送信を許可": "发送通知",
    "バンドランク": "乐队RANK",
    "絆ランク": "羁绊等级",
}
# OCR 常读错、截断的几处
JA_RULES = (
    (re.compile(r"^ライ[ブプ]\s*TOP$"), "演出首页"),
    (re.compile(r"^ショッ[プブ]?$"), "商店"),  # 主界面底部的「ショップ」常读成「ショッブ」
    (re.compile(r"^.{0,2}ツ\s*速度$"), "节奏图示速度"),  # 「ノーツ速度」常读成「一ツ速度」
    (re.compile(r"^あと\s*(\d+)\s*回$"), r"还剩\1次"),
    (re.compile(r"^(?:ライ[ブプ]\s*ブースト|.*BOOST)\s*回復$"), "恢复LIVE BOOST"),  # 「ライブブースト回復」
    # 恢复确认、恢复后的提示（没见过，照韩文界面的说法认）
    (re.compile(r"^.*(?:ブースト|BOOST)\s*を\s*(\d+)\s*回復.*$"), r"将恢复\1点LIVE BOOST。确定要恢复吗？"),
    (re.compile(r"^.*(?:ブースト|BOOST).*回復しました.*$"), "已恢复LIVE BOOST。"),
    (re.compile(r"^パス\s*pt\s*獲得$"), "获得通行证pt"),
    (re.compile(r"^もう\s*一\s*[回度]\s*ライ[ブプ]$"), "再次演出"),  # 结算最后一页的「もう一回ライブ」
    # 故事菜单的按钮文字常截断（「バンドスト」「絆ストーリ」）
    (re.compile(r"^バンドスト(?:ーリ?ー?)?$"), "乐队故事"),
    (re.compile(r"^絆スト(?:ーリ?ー?)?$"), "羁绊故事"),
    (re.compile(r"^特別スト(?:ーリ?ー?)?$"), "特别故事"),
)
# 没有假名也要换的
JA_KANJI_RULES = (
    (re.compile(r"^(\d+)\s*日目$"), r"\1天"),  # 限定任务、新手任务右侧的「1日目」
    (re.compile(r"[視見]点\s*Ver"), "视角Ver"),  # 视角故事卡片上「燈視点Ver.」（「視」常读成「見」）
)
# 平假名、片假名（日文汉字和繁中、简中分不开，所以只有汉字的只按整项换）
KANA = re.compile(r"[\u3040-\u30ff]")

HANGUL = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7a3]")
_EN_WORDS = {_en_key(src): dst for src, dst in EN_WORDS.items()}
_JA_WORDS = {_en_key(src): dst for src, dst in JA_WORDS.items()}
_JA_PHRASES = [(_en_pattern(src), dst) for src, dst in sorted(JA_PHRASES.items(), key=lambda kv: -len(kv[0]))]
_KO_WORDS = {_en_key(src): dst for src, dst in KO_WORDS.items()}
_KO_PHRASES = [(_en_pattern(src), dst) for src, dst in sorted(KO_PHRASES.items(), key=lambda kv: -len(kv[0]))]
_EN_PHRASES = [(_en_pattern(src), dst) for src, dst in sorted(EN_PHRASES.items(), key=lambda kv: -len(kv[0]))]


def to_simplified(text: str) -> str:
    """逐字把繁体字转成简体字（曲名里的日文汉字也会转，如「無路矢」→「无路矢」，所以曲目目录也要这样转了再比）。"""
    return text.translate(_T2S)


def localize(text: str) -> str:
    """OCR 读到的一段文字 → 简中界面上的说法（本来就是简中的原样返回）。"""
    if (word := _EN_WORDS.get(_en_key(text))) is not None:
        return word
    if (word := _JA_WORDS.get(_en_key(text))) is not None:
        return word
    if KANA.search(text):
        for pattern, repl in JA_RULES:
            if pattern.search(text):
                return pattern.sub(repl, text)
        for pattern, dst in _JA_PHRASES:
            text = pattern.sub(dst, text)
    elif HANGUL.search(text):
        if (word := _KO_WORDS.get(_en_key(text))) is not None:
            return word
        for pattern, repl in KO_RULES:
            if pattern.search(text):
                return pattern.sub(repl, text)
        for pattern, dst in _KO_PHRASES:
            text = pattern.sub(dst, text)
    if re.search(r"[A-Za-z]", text):
        for pattern, dst in _EN_PHRASES:
            text = pattern.sub(dst, text)
        for pattern, repl in EN_RULES:
            text = pattern.sub(repl, text)
    for pattern, repl in JA_KANJI_RULES:
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
