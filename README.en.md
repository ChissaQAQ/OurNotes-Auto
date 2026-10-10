<div align="center">

<img src="docs/ui/icon.png" alt="Icon" width="128">

# OurNotes-Auto

[![Latest Release](https://img.shields.io/github/v/release/ChissaQAQ/OurNotes-Auto)](https://github.com/ChissaQAQ/OurNotes-Auto/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/ChissaQAQ/OurNotes-Auto/total)](https://github.com/ChissaQAQ/OurNotes-Auto/releases)
[![Stars](https://img.shields.io/github/stars/ChissaQAQ/OurNotes-Auto)](https://github.com/ChissaQAQ/OurNotes-Auto/stargazers)
[![License](https://img.shields.io/github/license/ChissaQAQ/OurNotes-Auto)](LICENSE)

[简体中文](README.md) | [繁體中文](README.zh-TW.md) | **English** | [日本語](README.ja.md) | [한국어](README.ko.md)

</div>

An auto-play tool for *BanG Dream! Our Notes* (International server `com.bilibili.sirius.official` (Google Play version `com.bilibili.sirius`), Japanese server `com.bushiroad.sirius`). MuMu Emulator + Python + [MaaFramework](https://github.com/MaaXYZ/MaaFramework).

- Downloads charts from a chart site and sends touches at the judgment times, aiming for FULL COMBO / ALL PERFECT
- Tracks the falling path of the first note and extrapolates when it reaches the judgment line to align the whole song (no audio needed)
- Reads FAST/SLOW from the result screen and automatically corrects the timing offset
- Automatic navigation in Free LIVE: selects songs, starts the LIVE, reads the results and plays again, for unattended continuous play; there are also tasks such as AFK Farming, AP Completion and Claim Dailies

This software is free and open source; please read the [Terms of Service](TERMS_OF_SERVICE.md) (Chinese) before use. If you paid for it, it was resold to you: you can ask for a refund and report the seller.

## ⚠️ Risk Disclaimer

**Using automation tools very likely violates the game's terms of service and may get your account warned, restricted or banned. You bear all consequences yourself.**

- This project is for learning and technical research only. It is not affiliated with Bushiroad, Craft Egg, bilibili or any operator of the game, nor authorized or endorsed by them
- Do not use it where it affects other players, such as event rankings or competitive play, and don't risk your main account
- This tool won't deliberately spend paid items (Stars), but screen recognition can make mistakes. Keep an eye on popups and LB settings (see [In-Game Settings](docs/en/usage.md#in-game-settings)). Boost Drinks from Items are used only when "Refill LB with Items When Low" is on
- Provided "as is", without any warranty

## Quick Start

1. Download an archive from [Releases](https://github.com/ChissaQAQ/OurNotes-Auto/releases/latest) and extract it; no need to install Python. Both UIs have the same features, so pick either one:

   | Archive | UI | Also requires |
   |---|---|---|
   | `OurNotes-Auto-<version>-win-x64-MFAA.zip` | [MFAAvalonia](https://github.com/SweetSmellFox/MFAAvalonia) | .NET 10 Desktop Runtime (the bundled `DependencySetup_依赖库安装_win.bat` installs it in one click) |
   | `OurNotes-Auto-<version>-win-x64-MXU.zip` | [MXU](https://github.com/MistEO/MXU) | WebView2 (usually preinstalled on Windows 10/11) |

   Both require the [VC++ 2015–2022 Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe).
2. Run `MFAAvalonia.exe` or `mxu.exe`, and read through the instructions shown on first launch.
3. In the connection settings, choose "MuMu Emulator" and the instance to use (the adb address of instance n is `127.0.0.1:16384+32n`).
4. Check the tasks, set the options and start. The game just needs to be on the Home screen or the Free LIVE Select Song screen, and it doesn't even need to be running: every task except "Records Summary" starts it first (if it is already running, that instance is used), then waits through the title screen, login bonus and notices to get into the game.

Before use, make sure of the following:

- MuMu Emulator 12, resolution **1280×720**, frame rate at least 60
- International server, with the game language set to 简体中文, 繁體中文, English or 한국어 (for 한국어, set "Game Language" to 한국어 in the task settings); or the Japanese server (set "Game Language" to "日本語 (Japanese server)"; the client must be installed from Google Play)
- In-game "Notes Speed" at **5.00** (be sure to keep it when "Random GREAT" is on; other speeds shift all presses and turn GREATs into GOODs); MV / background off, or switched to a dark, static background; turning down effect and performance levels is recommended
- Internet access: charts are downloaded at runtime from the chart site `assets.bdon.moe`

Tasks: Start Game, Switch Account (accounts in the International server's bilibili login history, no password needed), Repeat Play, Use Up LB, AFK Farming (waits for LB to recover after running out, then keeps playing), AP Completion, Challenge LIVE (during events), Claim Dailies, Records Summary. For details on each task and option, see the [Usage Guide](docs/en/usage.md); for command-line usage, see [Command Line](docs/en/cli.md).

## Measured Results and Known Limitations

Measured on MuMu 6.6.4 with an Android 15 instance (4 cores, 6GB, 60 fps), humanization off. EXPERT rounds are grouped by the game frame rate measured in each round:

| Game frame rate | Rounds | ALL PERFECT | FULL COMBO |
|---|---|---|---|
| 50 fps or more (smooth) | 1075 | 1056 (98.2%) | 1060 (98.6%) |
| 40–49 fps | 202 | 187 (92.6%) | 190 (94.1%) |
| Below 40 fps | 126 | 111 (88.1%) | 114 (90.5%) |

All 48 EXPERT charts played while running smoothly have been ALL PERFECT at least once; the non-AP rounds missed only 1–2 judgments on average, usually a single MISS. EASY, NORMAL and HARD add another 154 rounds: 150 ALL PERFECT (97%) and 152 FULL COMBO.

Known limitations:

- When the emulator stutters (dropped frames, jittery touch latency), complex songs may lose a few PERFECTs or even break the combo. Both sync and touch rely on the emulator producing frames and receiving touches on time, so any hiccup causes drift, and the AP rate drops noticeably at low frame rates (see the table above). Closing other CPU-hungry programs, giving the emulator enough CPU and memory, and lowering the game's effect and performance levels helps a lot
- Only Free LIVE and Challenge LIVE (during events) are supported. Co-op LIVE and other modes are not
- Song title OCR occasionally fails; when recognition fails, that round is abandoned and the next song is tried
- Songs whose first note comes very early (already on screen before the transition ends) may fail to sync; in that case the round is paused and retried
- So far mainly verified with MuMu + International server, with effect and performance levels turned down; on the Japanese server only Free LIVE, AP Completion and Claim Dailies have been verified

## Reporting Issues

Submit them at [Issues](https://github.com/ChissaQAQ/OurNotes-Auto/issues), ideally with:

- What happened, what you expected, and roughly when (logs are searched by time)
- The log `data/ournotes.log`. The start of each run records the tool version, OS and CPU, and connecting records the emulator version, the instance's CPU / memory / frame rate, resolution, touch method and game version, so **there's no need to write down device info separately**. For the GUI, also attach `data/agent.log`. If the file is too large, cut from the "OurNotes-Auto <version>（…）" line at the start of the run where the problem happened to just after the problem
- Screenshots mentioned in the log (on errors it writes "截图 debug/nav/….png")
- For play problems: the song title and difficulty, ideally with a screenshot of the result screen
- Logs and screenshots may contain player nicknames and friend invite codes; you may want to blur them before posting

## License

Copyright (C) 2026 ChissaQAQ

This project is open source under [AGPL-3.0](LICENSE) (version 3 only), with two additional terms under Section 7 of AGPL-3.0: keep the author attribution and the "free and open source" notice when distributing, and mark modified versions as modified. See Section 2 of the [Terms of Service](TERMS_OF_SERVICE.md) (Chinese) for details. Using this software also requires following the community guidelines and disclaimer in the Terms of Service.

Third-party components in the release package are distributed under their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

The software icon (`docs/ui/icon.png`, `docs/ui/logo.ico`) is taken from the official website of the *BanG Dream! It's MyGO!!!!!* anime. Its copyright belongs to ©BanG Dream! Project, and it is **not** covered by the AGPL-3.0 license; see Section 2.5 of the Terms of Service.

## Acknowledgements

- [autodori](https://github.com/EvATive7/autodori): design reference (GPLv3). This project does not copy its code
- [bdon.moe](https://bdon.moe/) chart site, [haneoka.org](https://haneoka.org/) International server song title data
- [MaaFramework](https://github.com/MaaXYZ/MaaFramework), [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) and [MaaCommonAssets](https://github.com/MaaXYZ/MaaCommonAssets) (OCR models)
- GUI: [MFAAvalonia](https://github.com/SweetSmellFox/MFAAvalonia), [MXU](https://github.com/MistEO/MXU)
