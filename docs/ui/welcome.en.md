# What's New in v0.2.2.1

- Arena on the Japanese server (#105): the 「撃奏アリーナ」 entry on the Live top page and the Arena top page are now recognized. The other Arena pages aren't adapted to Japanese yet; when their text can't be recognized, the page is identified by language-independent content (where the row of "LUCK" sits, LIVE START, OK, difficulty names, placements). Languages that are already supported, such as Simplified Chinese, are still recognized by their text as before. The Arena hasn't actually been played on the Japanese server yet, so please report any problems

Full release notes: https://github.com/ChissaQAQ/OurNotes-Auto/releases/tag/v0.2.2.1

# What's New in v0.2.2

> **Note**: The Arena (new in v0.2.1) and this version's fix for the song intro have not been fully tested yet. You may run into all kinds of problems. Please report problems in the GitHub Issues.

- Fixed the song intro added in game 1.1.0 (stage lights) being mistaken for notes, which made sync fail, fooled the retry too, and occasionally shifted a whole song early (#102): the intro is now recognized and it keeps waiting for the first note
- Arena: fixed getting stuck on the zh-Hant 「配對確認」 (match confirmation) screen (#100, thanks @bfs2021) and on rank-up screens it couldn't recognize (#101, Gold and above)
- Fixed tasks failing right away with "Refill LB with Items When Low" on and "LB per Round" set to 0 (#100): it now just doesn't refill and plays as usual

Full release notes: https://github.com/ChissaQAQ/OurNotes-Auto/releases/tag/v0.2.2

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
