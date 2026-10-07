<div align="center">

<img src="docs/ui/icon.png" alt="圖示" width="128">

# OurNotes-Auto

[![最新版本](https://img.shields.io/github/v/release/ChissaQAQ/OurNotes-Auto)](https://github.com/ChissaQAQ/OurNotes-Auto/releases/latest)
[![下載量](https://img.shields.io/github/downloads/ChissaQAQ/OurNotes-Auto/total)](https://github.com/ChissaQAQ/OurNotes-Auto/releases)
[![Star](https://img.shields.io/github/stars/ChissaQAQ/OurNotes-Auto)](https://github.com/ChissaQAQ/OurNotes-Auto/stargazers)
[![授權條款](https://img.shields.io/github/license/ChissaQAQ/OurNotes-Auto)](LICENSE)

[简体中文](README.md) | **繁體中文** | [English](README.en.md) | [日本語](README.ja.md) | [한국어](README.ko.md)

</div>

《BanG Dream! Our Notes》（國際服 `com.bilibili.sirius.official`、日服 `com.bushiroad.sirius`）自動演奏工具。MuMu 模擬器 + Python + [MaaFramework](https://github.com/MaaXYZ/MaaFramework)。

- 從譜面站下載譜面，依判定時刻傳送觸控，目標是 FULL COMBO / ALL PERFECT
- 追蹤第一個音符的下落軌跡，外推它到達判定線的時刻來對齊整首歌（不依賴音訊）
- 讀取結算頁的 FAST/SLOW，自動修正時間偏移
- 自由演出介面自動導航：選曲、開始演出、讀結算、再來一局，可無人值守連續演奏；另有掛機、AP 補完、領取日常等任務

本軟體免費開源，使用前請閱讀[使用者協議](TERMS_OF_SERVICE.md)（簡體中文）。如果你是花錢買到的，表示被轉賣了，可以要求退款並檢舉賣家。

## ⚠️ 風險聲明

**使用自動化工具很可能違反遊戲的服務條款，可能導致帳號遭到警告、限制或停權。後果由使用者自行承擔。**

- 本專案僅供學習與技術研究。與 Bushiroad、Craft Egg、bilibili 及遊戲的任何營運方無關，也未獲得其授權或認可
- 請勿用於活動排名、競技等影響其他玩家的場合，也不要拿主力帳號冒險
- 本工具不會主動消耗付費道具（星鑽），但介面辨識可能出錯。請留意彈窗與 LB 設定（見[遊戲內設定](docs/zh-TW/usage.md#遊戲內設定)）。只有開啟「LB 不足時用道具補充」時才會使用道具裡的 LIVE BOOST飲料
- 依「現狀」提供，不做任何擔保

## 快速開始

1. 在 [Releases](https://github.com/ChissaQAQ/OurNotes-Auto/releases/latest) 下載一個壓縮檔並解壓縮，不用安裝 Python。兩種介面功能相同，任選一個：

   | 壓縮檔 | 介面 | 另外需要 |
   |---|---|---|
   | `ournotes-auto-<版本>-win-x64-MFAA.zip` | [MFAAvalonia](https://github.com/SweetSmellFox/MFAAvalonia) | .NET 10 桌面執行階段（包裡的 `DependencySetup_依赖库安装_win.bat` 可一鍵安裝） |
   | `ournotes-auto-<版本>-win-x64-MXU.zip` | [MXU](https://github.com/MistEO/MXU) | WebView2（Windows 10/11 通常已內建） |

   兩種都需要 [VC++ 2015–2022 可轉散發套件](https://aka.ms/vs/17/release/vc_redist.x64.exe)。
2. 執行 `MFAAvalonia.exe` 或 `mxu.exe`，先讀完首次開啟時的使用說明。
3. 連線設定裡選「MuMu 模擬器」和要用的實例（第 n 個實例的 adb 位址是 `127.0.0.1:16384+32n`）。
4. 勾選任務、設定選項後開始。遊戲停在主畫面或自由演出選曲頁即可，遊戲沒開也行：除了「紀錄彙總」，其餘任務都會先啟動它（已經開著就直接用），再等過標題畫面、登入獎勵和公告進入遊戲。

使用前確認：

- MuMu 模擬器 12，解析度 **1280×720**，幀率不低於 60
- 國際服，遊戲語言為简体中文、繁體中文、English 或 한국어（한국어要在任務設定裡把「遊戲語言」選成 한국어）；或日服（「遊戲語言」選「日本語（日服）」，用戶端要從 Google Play 安裝）
- 遊戲內「節奏圖示速度」**5.00**（開了「隨機 GREAT」時務必保持，其他流速會讓按鍵整體偏移、GREAT 變 GOOD）；MV / 背景關閉，或換成較暗、不會動的背景；特效、演出等級建議調低
- 能連網：譜面在執行時從譜面站 `assets.bdon.moe` 下載

任務：啟動遊戲、切換帳號（國際服 B 站登入紀錄裡的帳號，不用密碼）、重複刷歌、清體力、掛機（LB 用完後等回復接著打）、AP補完、挑戰演出（活動期間）、領取日常、紀錄彙總。各任務和選項的細節見[使用說明](docs/zh-TW/usage.md)，命令列用法見[命令列](docs/zh-TW/cli.md)。

## 實測效果與已知限制

實測（MuMu 6.6.4、Android 15 實例 4 核心 6GB 60 幀；2026-09-30 起用發布包和開發版打的全部紀錄，不開擬人化）：

| 難度 | 局數 | ALL PERFECT | FULL COMBO |
|---|---|---|---|
| EASY | 18 | 18（100%） | 18 |
| NORMAL | 59 | 56（95%） | 56 |
| HARD | 49 | 49（100%） | 49 |
| EXPERT | 20 | 19（95%） | 19 |
| 合計 | 146 | 142（97%） | 142 |

共 146 局、53 張譜面，EXPERT 13 張全部打出過 ALL PERFECT。沒 AP 的幾局都是模擬器卡頓導致的掉判定。

已知限制：

- 模擬器卡頓（掉幀、觸控延遲抖動）時，複雜的歌可能掉幾個 PERFECT 甚至斷連。同步和觸控都靠模擬器準時出幀、準時收到觸控，卡一下就會偏，所以上面的數字是宿主機不被搶佔時的水準。關掉其他佔用 CPU 的程式、給模擬器分配足夠的 CPU 和記憶體、調低遊戲的特效和演出等級會好很多
- 只支援自由演出和挑戰演出（活動期間）。協力演出等其他模式不支援
- 曲名 OCR 偶爾出錯，辨識失敗時本局放棄，換下一首
- 首音符非常早的曲目（第一個音符在轉場結束前就已進入畫面）可能同步失敗，這時暫停重試本局
- 目前主要在 MuMu + 國際服、調低特效與演出等級的設定下驗證；日服只驗證過自由演出、AP補完和領取日常

## 回報問題

在 [Issues](https://github.com/ChissaQAQ/OurNotes-Auto/issues) 提交，建議附上：

- 發生了什麼、你期望怎樣，以及大概的時間（日誌依時間找）
- 日誌 `data/ournotes.log`。每次執行的開頭記錄了本工具版本、系統、CPU，連線時記錄了模擬器版本、實例的 CPU / 記憶體 / 幀率、解析度、觸控方式和遊戲版本，**不用另外寫裝置資訊**。圖形介面再附上 `data/agent.log`。檔案太大時，從出問題那次執行開頭的「OurNotes-Auto 版本號（…）」一行截到出問題之後即可
- 日誌裡提到的截圖（報錯時會寫「截图 debug/nav/….png」）
- 打歌的問題：曲名、難度，最好有結算頁截圖
- 日誌和截圖裡可能有玩家暱稱、好友邀請碼，傳送前可以先打上馬賽克

## 授權條款

Copyright (C) 2026 ChissaQAQ

本專案依 [AGPL-3.0](LICENSE)（僅第 3 版）開源，另依 AGPL-3.0 第 7 條附加兩項條款：傳播時保留作者署名和「免費開源」聲明，修改版要標明已修改。詳見[使用者協議](TERMS_OF_SERVICE.md)（簡體中文）第 2 條。使用本軟體還需遵守使用者協議裡的社群規範與免責聲明。

發布包裡的第三方元件依各自的授權條款散布，見 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

軟體圖示（`docs/ui/icon.png`、`docs/ui/logo.ico`）取自《BanG Dream! It's MyGO!!!!!》動畫官方網站，著作權歸 ©BanG Dream! Project 所有，**不在** AGPL-3.0 授權範圍內，見使用者協議 2.5。

## 致謝

- [autodori](https://github.com/EvATive7/autodori)：設計參考（GPLv3）。本專案沒有複製其程式碼
- [bdon.moe](https://bdon.moe/) 譜面站，[haneoka.org](https://haneoka.org/) 國際服曲名資料
- [MaaFramework](https://github.com/MaaXYZ/MaaFramework)、[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) 與 [MaaCommonAssets](https://github.com/MaaXYZ/MaaCommonAssets)（OCR 模型）
- 圖形介面：[MFAAvalonia](https://github.com/SweetSmellFox/MFAAvalonia)、[MXU](https://github.com/MistEO/MXU)
