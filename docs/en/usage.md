# Usage Guide

Detailed notes beyond the [README](../../README.en.md): task details, in-game settings, auto-correction, humanization, handling of unexpected situations, runtime files and how it works. For command-line usage, see [Command Line](cli.md).

## Requirements

- Windows, MuMu Emulator 12 (this project is tested on MuMu 6.6.x with an Android 15 instance)
- Emulator resolution **1280×720** (other 16:9 resolutions are scaled proportionally but not thoroughly tested), frame rate at least 60 (sync parameters are tuned for 60 fps screenshots)
- Game: International server (package name `com.bilibili.sirius.official`), with the game language set to 简体中文, 繁體中文, English or 한국어. Navigation relies on OCR of on-screen text, so buttons on other servers can't be recognized. For 한국어, set "Game Language" to 한국어: the Korean text recognition model (about 10 MB) is downloaded automatically on the first run and additionally loaded every time after that (both models recognize together, so each recognition is a bit slower); if 한국어 isn't selected, it's neither downloaded nor loaded
- For in-game settings, see [In-Game Settings](#in-game-settings)
- Access to the chart site `assets.bdon.moe` and the song title API `haneoka.org` (update checks additionally access `github.com`, which can be turned off)
- Release package: both UIs need the [VC++ 2015–2022 Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe) (required by MaaFramework); MFAA additionally needs the .NET 10 Desktop Runtime (the bundled `DependencySetup_依赖库安装_win.bat` installs it and the VC++ runtime in one click), and MXU needs WebView2 (usually preinstalled on Windows 10/11). The package bundles the Python runtime, MaaFramework and the OCR model; charts are downloaded from the chart site at runtime. For the licenses of each component, see [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md)
- Running from source: Python ≥ 3.11, see [Command Line](cli.md)

## Connecting the Emulator

In the connection settings, choose "MuMu Emulator", then choose the emulator instance to use. The adb address of MuMu 12 instance n is `127.0.0.1:16384+32n`; for example, instance 0 is 16384 and instance 1 is 16416. Screenshots and touches go through MuMu's interface, and the install directory is derived from the adb path, so only MuMu 12 is supported for now.

## Tasks

The game just needs to be on the Home screen or the Free LIVE Select Song screen; with "Start Game" checked, it doesn't even need to be running.

| Task | What it does |
|---|---|
| Start Game | Launches the game if it isn't running, taps through the title screen, login bonuses and News, and enters the Home screen. Put it before other tasks |
| Switch Account | Logs out of the current bilibili account, logs in to the specified account from the game's login history (no password needed), then enters the Home screen; if already in the game, restarts the game first to get back to the title screen. Enter the account name as shown in the login history; part of it is fine, but it must match only one account. Can only switch to accounts in the login history; log in to a new account manually in the game once first. To rotate between multiple accounts, order the task list as "Switch Account (A) → other tasks → Switch Account (B) → other tasks". Never taps the account deletion option in Account Center or the ⓧ on the right of each login history row. Play records and Records Summary don't distinguish between accounts yet |
| Repeat Play | Plays continuously in Free LIVE, with a choice of song selection (Current Song / Random / Non-AP Songs First / Song List), difficulty, number of rounds and LB per round. With difficulty set to "Highest Difficulty First", "Non-AP Songs First" fills EXPERT through EASY in order, then picks EXPERT songs at random when all are done; other song selection modes play EXPERT |
| Use Up LB | Plays continuously until LIVE BOOST runs out (checks the count held before each round and stops at 0), without using Stars. Leveling up refills LB, so there may be more rounds than expected |
| AFK Farming | Like Use Up LB, plays until LB runs out, then waits on the Formation screen, following the recovery countdown at the top right until LB recovers to the per-round cost, then continues (LB recovers 1 every 30 minutes; at a cost of 3, that's about one round every 90 minutes). With LB per round set to 0 it doesn't wait for LB: checks the count held before each round, uses 1 if there is LB, otherwise continues at 0, and uses LB again once some recovers, playing nonstop. Runs until stopped manually or the round limit is reached; if the date changes while waiting, it logs in again, and during server maintenance it checks every 10 minutes and resumes AFK farming once the server is back. Option "Scheduled Claim" (on by default, every 4 hours): claims Studio Practice once at the start, then every set number of hours (1–12) returns to the Home screen between rounds or while waiting for LB to claim again, then comes back and continues AFK farming (Studio Practice accumulates for up to 12 hours; beyond that, efficiency drops to 30%); if a claim fails, it tries again 30 minutes later. Option "Claim Dailies Each Day" (on by default, 22:30): each day at the set time (the PC's local time), likewise returns to the Home screen to claim Missions, Mission Pass, Limited-Time Missions, Beginner Missions, T.G.W CARD and the Gift Box; if not everything was claimed, tries again 10 minutes later (up to 3 times a day). "Claim Dailies" in the task list only runs once before AFK farming; after crossing into a new day, this option takes care of it. The game's date changes at 23:00 every day, so the claim time must be earlier than 23:00 to get that day's rewards. Doesn't use Stars. It never ends by itself, so put it last in the task list |
| AP Completion | Plays the songs in the selected difficulties (EXPERT / HARD / NORMAL / EASY) that aren't ALL PERFECT yet until they are AP. Temporarily switches the Select Song screen to the "All" category and filters by "NO ALL PERFECT", restoring them after finishing normally |
| Challenge LIVE | Only open during some events (ends with an error otherwise). Enters "CHALLENGE LIVE" from the LIVE top page and plays continuously, spending Challenge Pts (CP) instead of LB each round, until CP isn't enough for another round. The option "Challenge Pts per Round" defaults to 200 (×1, the cheapest, allowing the most rounds); 400 / 800 / 1600 (rewards ×2 / ×4 / ×8) or keeping the in-game setting are also available; before the first round, it selects the cost in the CP settings and verifies the radio button before tapping OK. The song selection "Rotate Through Songs" (default) switches to the next song in the list each round, going back to the first after the last; "Non-AP Songs First" checks song by song before each round whether the selected difficulty is ALL PERFECT and plays non-AP songs first (up to 3 plays per song), then rotates once all are AP (with difficulty set to "Highest Difficulty First", fills EXPERT → HARD → NORMAL → EASY in order); it can also keep playing the currently selected song. Never taps "SKIP" on the Formation screen |
| Claim Dailies | Claims the rewards of Studio Practice (claim), Missions, Pass Missions and the normal PASS tier (one by one when there are several passes), Limited-Time Missions, Beginner Missions, T.G.W CARD and the Gift Box in turn; each can be turned off individually. Only taps a lit "Claim All" and OK on reward popups; for T.G.W CARD, it claims the daily points, the daily reward and items priced "Free" in the Shop's Catalog Shop, never those that cost Stars; it doesn't tap unrecognized popups and stops with an error. Daily missions are only complete after playing songs, so put it after the play tasks. Optional "Read Stories (Skip)" (off by default): skips through unread Band Story, Another Story and Bond Story episodes one by one, unlocking songs and claiming the rewards for finishing them; each episode downloads data the first time (Without Voice; about 30MB for Band Story). Optional "Event Stories (Skip)" (off by default): during a limited-time event, enters the event page via the LIVE top page → "EVENT" and skips through the unread Event Story and Another Story episodes under "Event Story" one by one; this item is skipped when there is no limited-time event |
| Records Summary | Lists this tool's play records in the log, without operating the emulator |

### Refill LB with Items When Low

Repeat Play, Use Up LB, AFK Farming and AP Completion all have the option "Refill LB with Items When Low" (off by default): before each round, if LB held is less than the per-round cost, it goes through the LB "Settings" → "Recovery" and, only on the "Item" tab, uses Boost Drinks to top up just enough for this round (Small +1 first, then +10 if that's not enough); it taps OK only after verifying that the selected quantity, the recovery preview and the numbers on the confirmation popup all match; if it's not on the "Item" tab or anything doesn't match, it taps Cancel and won't refill again this run. Never uses Stars or watches ads.

The sub-option "Max Refill" is the maximum amount of LB refilled in this run; 0 means until the drinks run out. After the drinks run out, it continues as before (Use Up LB ends there, AFK Farming starts waiting for recovery). Requires LB per round to be set to 1–3.

### Other Options

- To adjust parameters beyond the defaults (such as the timing offset), copy [config.example.yaml](../../config.example.yaml) to `config.yaml` in the extracted folder and edit it. Options in the UI take precedence over it.
- By default, the UI log only shows progress and results. With the global option "Debug Log" on, sync, chart, touch and other details are shown too; the full log is always written to `data/ournotes.log`.
- The global option "Check for Updates" is on by default: whenever a task finishes, if there's a newly released version on GitHub, the log shows the download link. It doesn't download automatically; to update, download the new archive manually.

## In-Game Settings

| Setting | Recommended | Notes |
|---|---|---|
| Notes Speed | **5.00** (required) | The sync parameter `play.sync.tau_s = 0.835` was measured at 5.00. Before each round, the speed is read from the Pre-LIVE Option Settings popup and checked; a mismatch only produces a warning in the log and doesn't stop anything: other speeds can still sync, but press timing is shifted overall. Change it back to 5.00, or run `calibrate motion` to re-measure (also see the speed requirement in the [Humanization](#humanization-optional-off-by-default) section) |
| MV / background | Off, or a dark, static background (required) | At the start, it waits for the screen to settle before tracking the first note. With a constantly moving background, it starts tracking after at most 4 seconds, and a bright background can also bleed in, making sync fail or be inaccurate |
| Bar line display (Details → notes settings) | Either on or off | When tracking the first note at the start, bar lines spanning the lanes are recognized and skipped. Version 0.1.3 and earlier mistake a bar line that falls before the first note for the first note, so the whole round is early and life quickly hits zero; turn it off when using those versions |
| Effect and performance levels | Not enforced; turning them down is recommended on a weaker PC | It only looks at the screen for the first few seconds (when there are no hit effects yet), and colored effects are filtered out; after that it plays from the chart and only watches the pause button at the top right. But so far it has only been tested with them turned down: with high effects, bright white opening effects may bleed into the tracking area, or full-screen performances may cover the pause button for more than 2 seconds in a row and be taken as leaving the play screen, stopping touches. Turning them down also makes the emulator frame rate steadier |
| LB "Settings" on the Formation screen | Set automatically from the config key `game.lb_cost` (0–3) | Before the first round, it opens the popup, selects the value and verifies it; the game remembers it. 0 consumes no LB (LIVE START shows "n ▸ n"). When LB is short, the game consumes all that's left; when "LIVE Boost Recover" (Item/Star/ad) pops up, it only taps Cancel and that round switches to 0 (with `game.lb_refill` on, it first refills with Boost Drinks from Items, see [above](#refill-lb-with-items-when-low)). Leave it empty (null) to keep the in-game setting |

After switching PCs or emulator instances or reinstalling, play one or two songs on a low difficulty first and check the log for sync failures or speed warnings. Screenshot and touch latency differ between PCs, but there's no need to tune anything manually: auto-correction adjusts the timing offset round by round, by at most 1ms per round (see [Timing Offset and Auto-Correction](#timing-offset-and-auto-correction)).

## Timing Offset and Auto-Correction

`play.offset_ms` is the global timing offset; a positive value presses later overall, compensating for latency from screenshots, input and rendering. On my MuMu setup, about -16ms centers the judgments.

After each round, the program reads FAST/SLOW from the result screen. More FAST pushes it later, more SLOW pulls it earlier, and the learned offset is saved to `data/state.json` (added on top of `offset_ms` in the config). In the following cases it only records and doesn't correct: unstable execution (many late batches), or too many GOOD/BAD/MISS (most likely synced to the wrong note).

## Humanization (Optional, Off by Default)

Makes results less uniform. In the UI it's among the global options; on the command line, change it with `--set`:

| Config key | UI option | Effect |
|---|---|---|
| `play.touch.great_ratio` | Random GREAT (1% / 3% / 5%) | At this ratio of the total number of judgments, picks plain taps with no other notes right before or after and hits them 64–69ms early or late as GREAT (when a round's sync error is large, some of them may become PERFECT or GOOD; GOOD doesn't break the combo). Has no effect when the song mode is `ap` / `ap_first`. When on, auto-correction ignores the FAST/SLOW of GREATs |
| `play.touch.jitter_ms` | Timing Drift (±8 / ±16ms) | Press timing drifts slowly over time (up to ± the set value), plus independent jitter of up to ±3ms per gesture. Capped at 20ms |
| `play.touch.position_jitter` | Random Touch Position | Touch points shift randomly sideways within the note width and move slightly up and down. They never move into the judgment area of an adjacent tap-type note (otherwise the press might be claimed by it), and a held long note never moves into the judgment area of another long note held at the same time. A value of 0–1 sets the amplitude; turning on the UI switch means 1 |

Example: `ournotes-auto --set play.touch.great_ratio=0.03 --set play.touch.jitter_ms=8 --set play.touch.position_jitter=1 run`

With "Random GREAT" on, keep the in-game Notes Speed at the calibrated value (default 5.00). The GREAT window is ±83ms,
and deliberate GREATs land 64–69ms off, leaving only a dozen or so milliseconds of margin for the overall offset: if the speed isn't the one used for calibration,
the offset may exceed that margin, and late GREATs become GOODs (the combo is kept, but the result is wrong).
The faster the speed, the shorter the first-note tracking time (about 1.8 seconds at 5.00, about 0.45 seconds at 10.00), and the harder it is to average out the sync error within a round,
so this margin is tightest at 10.00.

## Unexpected Situations

- If the pause button at the top right isn't visible for `play.guard_lost_s` seconds in a row during play (default 2; paused, popup, crash), touches stop immediately, the round is void, and navigation finds its way back. Set it to 0 to disable this check
- When first-note sync fails or life drops to 0, it pauses and taps "Retry" to restart the round, without wasting LB
- When the screen stays unrecognized, it checks the game process every 30 seconds; if the game has crashed, it restarts it via adb and logs in again from the title screen. At most 2 consecutive restarts; the count resets after getting back to Home
- Unknown popups with only "OK" (and none of the words Cancel, Confirm, Purchase, Star and the like on them) get OK tapped to continue; the popup text is written to the log and a screenshot is saved to `debug/nav`
- Full-screen performances with "Skip" at the top right (such as character birthday performances) get Skip tapped; for a story that plays right after, it opens the menu at the top right and taps SKIP
- Fallback: if an unrecognized screen doesn't change at all for 60 seconds, or keeps moving but stays unrecognized for 150 seconds (loading and downloading don't count), it restarts the game and logs in again, sharing the restart count above with crashes; it only stops if still stuck after the restarts are used up
- After running for a dozen or so hours straight, the game gets slower and slower (from 60fps down to the 30s), and sync fails or the whole song is off. During continuous play, if the frame rate measured over the last 5 syncs (median) drops below `loop.min_fps` (default 50, 0 = no check), it restarts the game before switching songs; if it's still that low after restarting (the PC itself can't keep up, or the emulator has a frame rate cap), it won't restart for this reason again
- Server maintenance (the "server is under maintenance" page): AFK Farming taps the return-to-title button every 10 minutes to log in again and check, resuming once the server is back; other tasks fail immediately
- Game updates: the "Download data" prompt at login (additional game data) gets OK tapped automatically to download; the "new version detected" prompt (requiring an app update from the app store) isn't tapped, the task fails immediately, and AFK Farming stops too; update the game yourself and then start again

## Runtime Files

| Path | Contents |
|---|---|
| `data/ournotes.log` | Detailed log (including debug level, with version, PC and emulator info). When over 5MB, it's renamed to `ournotes.old.log` at the next start |
| `data/agent.log` | GUI Agent log: task parameters, launched commands, update checks |
| `data/records.jsonl` | Result of each round (judgment counts, FAST/SLOW, offset, sync error) |
| `data/state.json` | Offset learned by auto-correction |
| `cache/charts/` | Chart and song index cache |
| `cache/update.json` | Update check result (cached for 6 hours) |
| `debug/nav/` | Screenshots from navigation errors and contradictory result readings |
| `debug/sync/` | First-note tracking frames saved by `play --record` |
| `debug/combo/` | Combo count screenshots saved by `--watch-combo --record`; when a combo break is detected, they're saved automatically to `breaks/` (only the latest 20 are kept) |

## How It Works (Brief)

1. **Navigation**: screenshot → OCR → determine the current screen from text and positions → tap buttons. Buttons are located precisely by their text first, falling back to fixed coordinates
2. **Song recognition**: OCR reads the song title on the Formation screen, which is fuzzy-matched against the multilingual song titles from the chart site and the International server to get the musicId
3. **Sync**: after tapping start, it waits for the screen to settle, detects the first note appearing above its lane and tracks its leading edge. As the note approaches the judgment line it follows `y - y_h ∝ exp(t/τ)`; after fitting, the arrival time is extrapolated, and the song's zero point is derived from it
4. **Play**: the chart is converted into touch gestures (tap, flick, long, trace), touches are injected via minitouch (MuMu IPC as the fallback), and a high-precision timer sends them on time. Following the game's judgment table, it keeps adjacent notes from "claiming" each other's touch points: neighbors flicking sideways at the same time get staggered start points, and upward flicks / traces that would be claimed by a later tap are pressed early
5. **Results**: reads the judgment counts and FAST/SLOW, records the result and corrects the offset
