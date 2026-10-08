<div align="center">

<img src="docs/ui/icon.png" alt="图标" width="128">

# OurNotes-Auto

[![最新版本](https://img.shields.io/github/v/release/ChissaQAQ/OurNotes-Auto)](https://github.com/ChissaQAQ/OurNotes-Auto/releases/latest)
[![下载量](https://img.shields.io/github/downloads/ChissaQAQ/OurNotes-Auto/total)](https://github.com/ChissaQAQ/OurNotes-Auto/releases)
[![Star](https://img.shields.io/github/stars/ChissaQAQ/OurNotes-Auto)](https://github.com/ChissaQAQ/OurNotes-Auto/stargazers)
[![许可证](https://img.shields.io/github/license/ChissaQAQ/OurNotes-Auto)](LICENSE)

**简体中文** | [繁體中文](README.zh-TW.md) | [English](README.en.md) | [日本語](README.ja.md) | [한국어](README.ko.md)

</div>

《BanG Dream! Our Notes》（国际服 `com.bilibili.sirius.official`、日服 `com.bushiroad.sirius`）自动演奏工具。MuMu 模拟器 + Python + [MaaFramework](https://github.com/MaaXYZ/MaaFramework)。

- 从谱面站下载谱面，按判定时刻发送触控，目标是 FULL COMBO / ALL PERFECT
- 跟踪第一个音符的下落轨迹，外推它到达判定线的时刻来对齐整首歌（不依赖音频）
- 读取结算页的 FAST/SLOW，自动修正时间偏移
- 自由演出界面自动导航：选曲、开始演出、读结算、再来一局，可无人值守连续演奏；另有挂机、AP 补完、领取日常等任务

本软件免费开源，使用前请阅读[用户协议](TERMS_OF_SERVICE.md)。如果你是花钱买到的，说明被倒卖了，可以要求退款并举报卖家。

## ⚠️ 风险声明

**使用自动化工具很可能违反游戏的服务条款，可能导致账号被警告、限制或封禁。后果由使用者自行承担。**

- 本项目仅供学习与技术研究。与 Bushiroad、Craft Egg、bilibili 及游戏的任何运营方无关，也未获得其授权或认可
- 请勿用于活动排名、竞技等影响其他玩家的场合，也不要拿主力账号冒险
- 本工具不会主动消耗付费道具（星钻），但界面识别可能出错。请留意弹窗与 LB 设置（见[游戏内设置](docs/使用说明.md#游戏内设置)）。只有打开「LB 不足时用道具补充」时才会使用道具里的 LIVE BOOST饮料
- 按「现状」提供，不做任何担保

## 快速开始

1. 在 [Releases](https://github.com/ChissaQAQ/OurNotes-Auto/releases/latest) 下载一个压缩包解压，不用装 Python。两种界面功能相同，任选一个：

   | 压缩包 | 界面 | 另外需要 |
   |---|---|---|
   | `OurNotes-Auto-<版本>-win-x64-MFAA.zip` | [MFAAvalonia](https://github.com/SweetSmellFox/MFAAvalonia) | .NET 10 桌面运行时（包里的 `DependencySetup_依赖库安装_win.bat` 可一键安装） |
   | `OurNotes-Auto-<版本>-win-x64-MXU.zip` | [MXU](https://github.com/MistEO/MXU) | WebView2（Windows 10/11 一般自带） |

   两种都需要 [VC++ 2015–2022 运行库](https://aka.ms/vs/17/release/vc_redist.x64.exe)。
2. 运行 `MFAAvalonia.exe` 或 `mxu.exe`，先读完首次打开时的使用说明。
3. 连接设置里选「MuMu 模拟器」和要用的实例（第 n 个实例的 adb 地址是 `127.0.0.1:16384+32n`）。
4. 勾选任务、设置选项后开始。游戏停在主界面或自由演出选曲页即可，游戏没开也行：除了「记录汇总」，其余任务都会先启动它（已经开着就直接用），再等过标题画面、登录奖励和公告进入游戏。

使用前确认：

- MuMu 模拟器 12，分辨率 **1280×720**，帧率不低于 60
- 国际服，游戏语言为简体中文、繁體中文、English 或 한국어（한국어要在任务设置里把「游戏语言」选成 한국어）；或日服（「游戏语言」选「日本語（日服）」，客户端要从 Google Play 安装）
- 游戏内「节奏图示速度」**5.00**（开了「随机 GREAT」时务必保持，其他流速会让按键整体偏移、GREAT 变 GOOD）；MV / 背景关闭，或换成较暗、不动的背景；特效、演出等级建议调低
- 能联网：谱面运行时从谱面站 `assets.bdon.moe` 下载

任务：启动游戏、切换账号（国际服 B 站登录记录里的账号，不用密码）、重复刷歌、清体力、挂机（LB 用完后等恢复接着打）、AP补完、挑战演出（活动期间）、领取日常、记录汇总。各任务和选项的细节见[使用说明](docs/使用说明.md)，命令行用法见[命令行](docs/命令行.md)。

## 实测效果与已知限制

实测（MuMu 6.6.4、Android 15 实例 4 核 6GB 60 帧，不开拟人化）。EXPERT 按每局测到的游戏帧率分开统计：

| 游戏帧率 | 局数 | ALL PERFECT | FULL COMBO |
|---|---|---|---|
| 50 帧以上（流畅） | 1075 | 1056（98.2%） | 1060（98.6%） |
| 40~49 帧 | 202 | 187（92.6%） | 190（94.1%） |
| 不到 40 帧 | 126 | 111（88.1%） | 114（90.5%） |

流畅时打过的 48 张 EXPERT 谱面都打出过 ALL PERFECT，没 AP 的局平均只差 1~2 个判定，多是偶尔一个 MISS。EASY、NORMAL、HARD 另有 154 局，ALL PERFECT 150 局（97%），FULL COMBO 152 局。

已知限制：

- 模拟器卡顿（掉帧、触控延迟抖动）时，复杂的歌可能丢几个 PERFECT 甚至断连。同步和触控都靠模拟器按时出帧、按时收触控，卡一下就会偏，所以帧率低时 AP 率会明显下降（见上表）。关掉别的占 CPU 的程序、给模拟器分配足够的 CPU 和内存、调低游戏的特效和演出等级会好很多
- 只支持自由演出和挑战演出（活动期间）。协力演出等其他模式不支持
- 曲名 OCR 偶尔出错，识别失败时本局放弃，换下一首
- 首音符非常早的曲目（第一个音符在转场结束前就已进入画面）可能同步失败，这时暂停重试本局
- 目前主要在 MuMu + 国际服、调低特效与演出等级的设置下验证；日服只验证过自由演出、AP补完和领取日常

## 反馈问题

在 [Issues](https://github.com/ChissaQAQ/OurNotes-Auto/issues) 提交，建议附上：

- 发生了什么、你期望怎样，以及大概的时间（日志按时间找）
- 日志 `data/ournotes.log`。每次运行的开头记了本工具版本、系统、CPU，连接时记了模拟器版本、实例的 CPU / 内存 / 帧率、分辨率、触控方式和游戏版本，**不用另外写设备信息**。图形界面再附上 `data/agent.log`。文件太大时，从出问题那次运行开头的「OurNotes-Auto 版本号（…）」一行截到出问题之后即可
- 日志里提到的截图（报错时会写「截图 debug/nav/….png」）
- 打歌的问题：曲名、难度，最好有结算页截图
- 日志和截图里可能有玩家昵称、好友邀请码，发之前可以打码

## 许可证

Copyright (C) 2026 ChissaQAQ

本项目按 [AGPL-3.0](LICENSE)（仅第 3 版）开源，另按 AGPL-3.0 第 7 条附加两项条款：传播时保留作者署名和「免费开源」声明，修改版要标明已修改。详见[用户协议](TERMS_OF_SERVICE.md)第 2 条。使用本软件还需遵守用户协议里的社区规范与免责声明。

发布包里的第三方组件按各自的许可证分发，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

软件图标（`docs/ui/icon.png`、`docs/ui/logo.ico`）取自《BanG Dream! It's MyGO!!!!!》动画官网，版权归 ©BanG Dream! Project，**不在** AGPL-3.0 授权范围内，见用户协议 2.5。

## 致谢

- [autodori](https://github.com/EvATive7/autodori)：设计参考（GPLv3）。本项目没有复制其代码
- [bdon.moe](https://bdon.moe/) 谱面站，[haneoka.org](https://haneoka.org/) 国际服曲名数据
- [MaaFramework](https://github.com/MaaXYZ/MaaFramework)、[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) 与 [MaaCommonAssets](https://github.com/MaaXYZ/MaaCommonAssets)（OCR 模型）
- 图形界面：[MFAAvalonia](https://github.com/SweetSmellFox/MFAAvalonia)、[MXU](https://github.com/MistEO/MXU)
