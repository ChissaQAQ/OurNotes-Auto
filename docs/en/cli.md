# Command Line

When not using the GUI, install from source and run it from the command line. For what each task and option means, see the [Usage Guide](usage.md).

## Installing from Source

Requires Python ≥ 3.11.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
```

### OCR Models

UI navigation uses MaaFramework's OCR. Download the models to `resource/model/ocr/`:

```bash
python tools/fetch_ocr.py
```

The model is [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)'s PP-OCRv6 small (Apache-2.0), in the ONNX version converted by [MaaCommonAssets](https://github.com/MaaXYZ/MaaCommonAssets) (MIT). The script pins the version, verifies the files, and downloads the licenses of both projects along with them. It can read Japanese song titles, and reads Chinese and English buttons more accurately than the old Chinese model. Manual play alone (the `play` command) doesn't need the OCR models.

When the game language is 한국어 (`loop.ocr_model: ko_kr`), the PP-OCRv3 Korean recognition model is also needed. It's downloaded automatically to `resource/model/ocr/ko_kr/` the first time it's needed, or you can download it in advance with `python tools/fetch_ocr.py --model ko_kr`. The release package doesn't include this model.

## Configuration

```bash
ournotes-auto init-config          # generate a config.yaml containing all default values
```

You can also copy [config.example.yaml](../../config.example.yaml) (which lists only the common items) to `config.yaml` and edit it. Items not written out use their default values; for all parameters and their descriptions, see `ournotes_auto/config.py`.

At least two items need checking: `device.instance` (the instance number in MuMu Multi-Instance Manager) and `device.mumu_path` (the MuMu install directory). For the Japanese server, also set `device.package` to `com.bushiroad.sirius` (the default is the International server's `com.bilibili.sirius.official`; if the emulator has the International server's Google Play version `com.bilibili.sirius` installed instead, it's used automatically, no change needed).

## Usage

```bash
# Fully automatic continuous play (starts from any Free LIVE-related screen and navigates to the Formation screen by itself)
ournotes-auto run                          # as configured
ournotes-auto run -n 3 --mode random -d expert   # random song, EXPERT, stop after 3 rounds
ournotes-auto run --watch-combo --record   # debug: list the notes at combo breaks in the log; save screenshots to debug/sync on sync failure
ournotes-auto run --until-lb-empty --lb-cost 3             # Use Up LB: play until LB runs out
ournotes-auto run --until-lb-empty --wait-lb --lb-cost 3   # AFK Farming: after running out, wait for LB to recover to 3 and continue; runs indefinitely
ournotes-auto run --until-lb-empty --wait-lb --lb-cost 3 --claim-studio 4   # AFK Farming, claiming Studio Practice at the start and every 4 hours after
ournotes-auto run --wait-lb --lb-cost 0 --claim-daily 22:30   # AFK Farming without waiting for LB (uses 1 per round while LB is held, continues at 0 once it runs out), claiming dailies once a day at 22:30
ournotes-auto run --until-lb-empty --lb-cost 3 --lb-refill 30   # Use Up LB, refilling with drinks from Items when LB is low, up to 30 (0 = until they run out)
ournotes-auto run --challenge --mode rotate              # Challenge LIVE (during events): 200 CP per round, rotating through songs until CP runs short
ournotes-auto run --challenge --challenge-cost 1600 -n 3 # Challenge LIVE, 1600 CP per round (rewards ×8), 3 rounds
ournotes-auto run --challenge --mode ap_first           # Challenge LIVE: play EXPERT songs that aren't AP yet first, then rotate once all are AP

# Start the game and enter the Home screen; claim daily rewards (by default, all seven items except stories and event stories; --jobs to do only some of them)
ournotes-auto start
ournotes-auto switch-account user_123   # log out of the current account, log in to the account whose name contains user_123 from the login history, then enter the Home screen
ournotes-auto daily
ournotes-auto daily --jobs studio,missions,pass,limited,beginner,tgw,gifts   # Studio Practice, Missions, Mission Pass, Limited-Time Missions, Beginner Missions, T.G.W CARD, Gift Box
ournotes-auto daily --jobs story   # skip unread Band / Another / Bond Story episodes (only when specified explicitly)
ournotes-auto daily --jobs event   # skip unread Event Story / Another Story episodes of the limited-time event (only when specified explicitly)

# Summarize local play records (total rounds, charts AP'd, charts not yet AP, recent rounds)
ournotes-auto records [--recent 10]
ournotes-auto records --clear-chart-offsets   # clear the per-chart learned offsets (the global learned offset stays)

# Recognize the current screen (for debugging navigation)
ournotes-auto look [--save] [--ocr]

# Manual mode: on the Formation screen, tap LIVE START and play the specified song
ournotes-auto play "迷星叫" -d expert --tap 1140,648 --tap 782,612

# Charts
ournotes-auto charts search [曲名]
ournotes-auto charts show 100026 -d expert
ournotes-auto charts prefetch              # download all charts in advance

# Take a screenshot with the judgment line / lane lines overlaid to check geometry parameters
ournotes-auto screenshot --overlay

# Re-measure note motion parameters after changing the speed (on the Formation screen; --tap as in play)
ournotes-auto calibrate motion "迷星叫" --tap 1140,648 --tap 782,612
```

You can also run it with `python -m ournotes_auto ...`. If the Windows terminal shows garbled text, set the environment variable `PYTHONIOENCODING=utf-8`. Press Ctrl+C to stop.

### Song Selection Modes

Song selection modes for `run` (`loop.song_mode`):

- `current`: always plays the currently selected song
- `random`: taps "Random" after each round. Before the first song change, it resets the play status filter on the Select Song screen to "No Preference", because Random only picks from the filtered list.
- `ap`: AP completion across all songs. Switches the category to "All", filters each difficulty by "NO ALL PERFECT" in the order of `loop.ap_difficulties`, then picks songs at random to play.
  - A song that still isn't AP after `loop.ap_max_attempts` plays is no longer played.
  - Locked or unrecognized songs are simply re-picked.
  - On normal completion, the filter and category are restored; if stopped midway, they aren't.
  - Example: `ournotes-auto run --mode ap --ap-difficulties expert,hard --lb-cost 0`
- `ap_first`: plays songs in the selected difficulty that aren't AP yet first (same picking rules as `ap`); when there are none left, resets to "No Preference" and selects songs at random.
  - To fill multiple difficulties, set `loop.ap_first_difficulties` (e.g. `expert,hard,normal,easy`); they're filled in order, then songs are picked at random at the `-d` difficulty. This is what "Highest Difficulty First" in the UI does.
  - Example: `ournotes-auto --set loop.ap_first_difficulties=expert,hard,normal,easy run --mode ap_first -d expert --until-lb-empty --lb-cost 3`
- `list`: plays the song list (`--songs` / `loop.song_list`) in order, starting over after each pass.
  - Each item is a song ID or title (any language, fuzzy-matched), optionally followed by `@difficulty`; without it, the `-d` difficulty is used. Separate items with commas, semicolons or newlines. Different versions with the same title can only be told apart by ID.
  - After switching the category to "All", it finds each song by scrolling the list while matching jackets; the first search for a song may take 20–30 seconds. Locked or unfound songs are skipped.
  - Example: `ournotes-auto run --mode list --songs "100010, 碧天伴走@hard" -n 4`
- `rotate`: only for Challenge LIVE (`--challenge` / `loop.challenge`); after each round, selects the next song on the Challenge LIVE song selection screen, going back to the first after the last. Challenge LIVE can only use `current`, `rotate` and `ap_first`.
- `ap_first` in Challenge LIVE: the Challenge LIVE song selection screen has no filter or random selection, so instead it checks song by song, downward from the selected one, whether the right-side panel has the ALL PERFECT mark, and plays the first song that isn't AP (a song that still isn't AP after `loop.ap_max_attempts` plays is no longer played); `loop.ap_first_difficulties` works as above, and once all are done it rotates through the songs at the `-d` difficulty.

Challenge LIVE (`--challenge`) spends `game.challenge_cost` (`--challenge-cost`, 200 / 400 / 800 / 1600, default 200; null keeps the in-game setting) Challenge Pts per round, uses no LB, and plays until CP isn't enough for another round, so it can't be combined with `--until-lb-empty`, `--wait-lb` or `--lb-refill`.

To select a single song (stopping on the Select Song screen): `ournotes-auto select 碧天伴走 [--category 全部]`.

### Exit Codes

0 finished normally; 1 task failed (not a single round completed, navigation error, server maintenance, etc.); 2 problem with the configuration or runtime environment; 130 Ctrl+C was pressed.
