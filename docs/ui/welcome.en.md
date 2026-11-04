# What's New in v0.2.4.1

- When MuMu screenshots fail (the game crashed, the emulator froze, etc.), it now retries and then reconnects, and carries on if that works; if it still fails, the log explains that MuMu can't be connected or its screenshots keep failing and asks you to restart MuMu, instead of reporting an unexpected error with a long traceback (#119)
- A few charts whose opening notes cover both sides of the lanes (such as 潜在表明 EXPERT) can't be checked for bar lines during sync, so with the game's "Bar line display" on the whole round runs early and life hits zero. When life hits zero on such a chart, the log now suggests turning "Bar line display" off (#123)

Full release notes: https://github.com/ChissaQAQ/OurNotes-Auto/releases/tag/v0.2.4.1

# What's New in v0.2.4

- New global option "Finish Current Song on Stop" (off by default, #115): when on, clicking Stop during a song waits until that song finishes, its result is recorded and the result screen is left before stopping (up to 10 minutes), so the LB (Challenge Pts in CHALLENGE LIVE) already spent on it isn't wasted. When no song is being played (choosing songs, waiting for LB, Arena matchmaking, claiming dailies, etc.) it still stops right away; to stop right away while this is on, exit the UI (minimizing to the tray doesn't count)

Full release notes: https://github.com/ChissaQAQ/OurNotes-Auto/releases/tag/v0.2.4

# What's New in v0.2.3.3

- Flick notes (including flicks at the start and end of long notes) used to land twenty-some milliseconds later than taps, so results leaned SLOW. Flicks now press and swipe 24 ms early by default (#114). Auto-correction had pulled the overall offset a bit earlier to make up for the late flicks; it moves back by itself after a few rounds, so there's no need to change any settings
- With the game's "Bar line display" on, a few charts whose first notes cover both sides of the lanes (such as 壱雫空 EXPERT) mistook a bar line for the first note, so the whole round was about 1 second early and life quickly hit zero. Fixed (#120)

Full release notes: https://github.com/ChissaQAQ/OurNotes-Auto/releases/tag/v0.2.3.3

# What's New in v0.2.3.2

- Arena: when a 5-minute matching attempt finds no match (maybe nobody is playing at this hour), it no longer counts as a failure, and the log counts these separately. If there's still no match 1 hour after the first miss, it goes back to the Home screen and ends the task (as a normal finish), and the tasks after it run (#113). If it's already in a matching room but the round doesn't start, that still counts as a failure
- Arena: when the "Pre-LIVE Option Settings" popup (「ライブ前オプション設定」 on the Japanese server, #111) appears after tapping Ready, it now taps "LIVE" like in Free LIVE (「ライブ」 on the Japanese server) instead of waiting for the popup to go away; if the button text can't be recognized, it waits as before

Full release notes: https://github.com/ChissaQAQ/OurNotes-Auto/releases/tag/v0.2.3.2

# What's New in v0.2.3.1

- Arena now recognizes the "match failed" (no opponent found) and "match error" popups, taps Close and matches again, instead of not recognizing them and restarting the game after two and a half minutes (#113, #116). Each attempt matches for up to 5 minutes; if there's still no match, it counts as one failure and the log says how many times matching failed. If it's already in a matching room (members gathered, can't cancel) by then, it waits up to 90 more seconds but doesn't start a new match
- Japanese-server Arena: the 「ライブ前オプション設定」 popup that appears after tapping 「準備完了」 is no longer canceled (canceling withdraws your ready, #111); 「難易度選択」 and 「準備完了」 are now recognized by their text
- If the International song list has never been downloaded and a download has already failed since you opened the program, later tasks try only once (#108). The log explains how this affects song recognition and what to do
- Retries when adb can't connect to the emulator. When other programs have used up this PC's network ports and adb can't start, the log names the program with the most connections
- An adb error while restarting the game now counts as one failure and the task continues (it still stops after 5 failures in a row), instead of stopping the whole task right away
- Challenge LIVE: when the selected song is already AP (the "ALL" badge left of the title), switching songs and checking for AP no longer go wrong

Full release notes: https://github.com/ChissaQAQ/OurNotes-Auto/releases/tag/v0.2.3.1

# What's New in v0.2.3

> **Note**: The Arena (new in v0.2.1) hasn't been fully tested yet, and it hasn't actually been played on the Japanese server. You may run into all kinds of problems. Please report problems in the GitHub Issues.

- New option "Rerun Earlier Tasks Each Day" for AFK Farming and AFK Farming (Arena) (off by default): after the game's daily reset, the tasks before AFK Farming (such as Start Game, Claim Dailies, Use Up LB) run again in the same order with the same options, then AFK Farming continues
- Arena now identifies the song by the jacket on the right of the difficulty page first, and falls back to the title only when the jacket can't be recognized (#107): long titles and titles with symbols no longer rely on text recognition alone
- Arena now uses at least ±16ms of "Humanize: Timing Drift" (even when it's set to off), so you no longer get far more JUSTs than a human player would (#110). Expect fewer JUSTs after updating; this is intentional
- Fixed the Japanese-server Arena getting stuck on 「マッチング確認」 when the 撃奏 rotation is on COMBO or JUST (#111). If the difficulty page isn't recognized and the round starts anyway two rounds in a row, the task now stops instead of wasting LB
- Less waiting when the chart site can't be reached (#108): with an old cache it tries only once, and after a failure it uses the old cache and skips missing jackets until the next time you open the program. The log explains why the connection failed

Full release notes: https://github.com/ChissaQAQ/OurNotes-Auto/releases/tag/v0.2.3

---

# ⚠️ Read Before Use

This software is developed by ChissaQAQ and is free and open source under AGPL-3.0. Source code: https://github.com/ChissaQAQ/OurNotes-Auto

If you paid for it, it was resold to you: you can ask for a refund and report the seller. Using this software means you agree to the Terms of Service (`TERMS_OF_SERVICE.md` in the release package, Chinese only).

**Using automation tools very likely violates the game's terms of service and may get your account warned, restricted or banned. You bear all consequences yourself.**

- This project is for learning and technical research only. It is not affiliated with Bushiroad, Craft Egg, bilibili or any operator of the game, nor authorized or endorsed by them
- Do not use it where it affects other players, such as event rankings or competitive play, and don't risk your main account
- This tool won't deliberately spend paid items, but screen recognition can make mistakes. Keep an eye on popups and LB settings
- Provided "as is", without any warranty

## Requirements

- MuMu Emulator 12, resolution 1280×720, frame rate at least 60
- International server, with the game language set to 简体中文, 繁體中文, English or 한국어 (for 한국어, set "Game Language" in the task settings to 한국어; navigation relies on recognizing on-screen text, so buttons on other servers can't be recognized); or the Japanese server (set "Game Language" to "日本語 (Japanese server)"; the client must be installed from Google Play, and Switch Account is International server only)
- Keep the in-game "Notes Speed" at 5.00. It's checked before each round; a mismatch only produces a warning in the log, and press timing will be shifted overall. With "Random GREAT" on, the shift turns GREATs into GOODs, so be sure to keep 5.00 (other speeds need recalibrating from the command line first)
- Turn off the in-game MV / background, or switch to a dark, static background. A constantly moving background makes sync at the start fail or be inaccurate
- Effect and performance levels aren't enforced, but so far it has only been tested with them turned down. On a weaker PC, turning them down is recommended for a steadier emulator frame rate
- After switching PCs or reinstalling, play one or two songs on a low difficulty first and check the log for sync failures or speed warnings
- If the game isn't open, check "Start Game" (put it first); other tasks start from the Home screen, the Free LIVE Select Song screen or the LIVE Result screen
- "AP Completion" temporarily changes the category and filter on the Select Song screen and restores them after finishing normally. If stopped midway they aren't restored; you can change them back manually, and the next "Random" song selection will also reset the play status filter to "No Preference" automatically
