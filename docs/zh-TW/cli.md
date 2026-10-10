# 命令列

不用圖形介面時，從原始碼安裝後用命令列執行。各任務、選項的含義見[使用說明](usage.md)。

## 從原始碼安裝

需要 Python ≥ 3.11。

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
```

### OCR 模型

介面導航用 MaaFramework 的 OCR。下載模型到 `resource/model/ocr/`：

```bash
python tools/fetch_ocr.py
```

模型是 [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) 的 PP-OCRv6 small（Apache-2.0），用 [MaaCommonAssets](https://github.com/MaaXYZ/MaaCommonAssets)（MIT）轉好的 ONNX 版本。腳本會固定版本、驗證檔案，並一併下載兩邊的授權條款。它能讀日文曲名，中英文按鈕也比舊的中文模型讀得準。只手動演奏（`play` 命令）不需要 OCR 模型。

遊戲語言是 한국어（`loop.ocr_model: ko_kr`）時另外要用 PP-OCRv3 韓文辨識模型。第一次用到時自動下載到 `resource/model/ocr/ko_kr/`，也可以用 `python tools/fetch_ocr.py --model ko_kr` 事先下載好。發布包裡不含這個模型。

## 設定

```bash
ournotes-auto init-config          # 產生包含全部預設值的 config.yaml
```

也可以複製 [config.example.yaml](../../config.example.yaml)（只列出常用項目）為 `config.yaml` 再修改。未寫出的項目使用預設值，全部參數與說明見 `ournotes_auto/config.py`。

至少要確認兩項：`device.instance`（MuMu 多開器裡的實例編號）和 `device.mumu_path`（MuMu 安裝目錄）。日服另外把 `device.package` 設成 `com.bushiroad.sirius`（預設是國際服的 `com.bilibili.sirius.official`；模擬器裡裝的是國際服 Google Play 版 `com.bilibili.sirius` 時會自動改用它，不用改）。

## 使用

```bash
# 全自動連續演奏（從任意自由演出相關畫面開始，會自己導航到樂團確認頁）
ournotes-auto run                          # 依設定
ournotes-auto run -n 3 --mode random -d expert   # 隨機選曲、EXPERT、打 3 局後停止
ournotes-auto run --watch-combo --record   # 偵錯：日誌裡列出斷連處的音符；同步失敗時儲存截圖到 debug/sync
ournotes-auto run --until-lb-empty --lb-cost 3             # 清體力：打到 LB 用完
ournotes-auto run --until-lb-empty --wait-lb --lb-cost 3   # 掛機：用完後等 LB 回復到 3 個再接著打，一直執行
ournotes-auto run --until-lb-empty --wait-lb --lb-cost 3 --claim-studio 4   # 掛機，開始時和之後每 4 小時領一次錄音室練習
ournotes-auto run --wait-lb --lb-cost 0 --claim-daily 22:30   # 掛機不等 LB（有 LB 時每局消耗 1 個，用完就消耗 0 接著打），每天 22:30 領一次日常
ournotes-auto run --until-lb-empty --lb-cost 3 --lb-refill 30   # 清體力，LB 不足時用道具裡的飲料補充，最多補 30 個（0 為直到用完）
ournotes-auto run --challenge --mode rotate              # 挑戰演出（活動期間）：每局 200 CP，輪流打每首歌，打到 CP 不夠為止
ournotes-auto run --challenge --challenge-cost 1600 -n 3 # 挑戰演出，每局 1600 CP（報酬 ×8），打 3 局
ournotes-auto run --challenge --mode ap_first           # 挑戰演出：先打 EXPERT 還沒 AP 的歌，都 AP 了再輪流打

# 啟動遊戲並進入主畫面；領取日常報酬（預設除看故事、活動故事外七項全領，--jobs 只做其中幾項）
ournotes-auto start
ournotes-auto switch-account user_123   # 登出目前帳號，從登入紀錄裡選帳號名稱包含 user_123 的帳號登入，然後進入主畫面
ournotes-auto daily
ournotes-auto daily --jobs studio,missions,pass,limited,beginner,tgw,gifts   # 錄音室練習、任務、通行證、期間限定任務、新手任務、T.G.W CARD、禮物盒
ournotes-auto daily --jobs story   # 跳過沒看過的樂團 / 側寫 / 羈絆故事（要明確指定才做）
ournotes-auto daily --jobs event   # 跳過限時活動沒看過的活動故事 / 側寫故事（要明確指定才做）

# 彙總本機演奏紀錄（總局數、AP 過的譜面、還沒 AP 的譜面、最近幾局）
ournotes-auto records [--recent 10]
ournotes-auto records --clear-chart-offsets   # 清除按譜面學到的 offset（全域學習值不變）

# 辨識目前畫面（偵錯導航）
ournotes-auto look [--save] [--ocr]

# 手動模式：停在樂團確認頁，點 LIVE START 並演奏指定曲目
ournotes-auto play "迷星叫" -d expert --tap 1140,648 --tap 782,612

# 譜面
ournotes-auto charts search [曲名]
ournotes-auto charts show 100026 -d expert
ournotes-auto charts prefetch              # 預先下載全部譜面

# 截圖並疊加判定線/軌道線，核對幾何參數
ournotes-auto screenshot --overlay

# 修改流速後重新測量音符運動參數（停在樂團確認頁，--tap 同 play）
ournotes-auto calibrate motion "迷星叫" --tap 1140,648 --tap 782,612
```

也可以用 `python -m ournotes_auto ...` 執行。Windows 終端機出現亂碼時設定環境變數 `PYTHONIOENCODING=utf-8`。按 Ctrl+C 停止。

### 選曲模式

`run` 的選曲模式（`loop.song_mode`）：

- `current`：一直打目前選中的曲目
- `random`：每局結束後點「隨機」。第一次換歌前會把選曲頁的「遊玩狀況」篩選改回「不指定」，因為隨機選曲只在篩選後的列表裡抽。
- `ap`：全曲 AP 補完。把分類切到「全部」，依 `loop.ap_difficulties` 的順序對每個難度篩選「未ALL PERFECT」，然後隨機抽歌來打。
  - 同一首歌打了 `loop.ap_max_attempts` 次還沒 AP，就不再打它。
  - 未解鎖或認不出的歌直接重抽。
  - 正常結束時把篩選和分類改回原樣；中途停止時不會改回。
  - 例：`ournotes-auto run --mode ap --ap-difficulties expert,hard --lb-cost 0`
- `ap_first`：所選難度優先打沒 AP 的歌（抽歌規則同 `ap`），沒有了就改回「不指定」隨機選曲。
  - 要補多個難度時寫 `loop.ap_first_difficulties`（如 `expert,hard,normal,easy`），依順序補完再依 `-d` 的難度隨機。介面裡難度選「優先高難度」就是這樣。
  - 例：`ournotes-auto --set loop.ap_first_difficulties=expert,hard,normal,easy run --mode ap_first -d expert --until-lb-empty --lb-cost 3`
- `list`：依歌單（`--songs` / `loop.song_list`）順序打，打完一輪從頭再來。
  - 每項是曲目 ID 或曲名（任意語言，模糊比對），可以加 `@難度`，不加就用 `-d` 的難度；用逗號、分號或換行分隔。同名的不同版本只能用 ID 區分。
  - 把分類切到「全部」後在列表裡邊捲動邊辨認封面找歌，第一次找一首可能要二三十秒。未解鎖、找不到的歌跳過。
  - 例：`ournotes-auto run --mode list --songs "100010, 碧天伴走@hard" -n 4`
- `rotate`：只用於挑戰演出（`--challenge` / `loop.challenge`），每局結束後在挑戰演出的「選擇樂曲」頁選中下一首，最後一首之後回到第一首。挑戰演出只能用 `current`、`rotate` 和 `ap_first`。
- 挑戰演出的 `ap_first`：挑戰演出的「選擇樂曲」頁沒有篩選和隨機選曲，改為從選中的歌往下逐首看右側面板有沒有 ALL PERFECT 標記，打第一首沒 AP 的（同一首打 `loop.ap_max_attempts` 次還沒 AP 就不再打）；`loop.ap_first_difficulties` 的用法同上，都補完了就依 `-d` 的難度輪流打每首歌。

挑戰演出（`--challenge`）每局消耗 `game.challenge_cost`（`--challenge-cost`，200 / 400 / 800 / 1600，預設 200；null 為不改遊戲裡的設定）挑戰pt，不消耗 LB，打到 CP 不夠一局為止，所以不能和 `--until-lb-empty`、`--wait-lb`、`--lb-refill` 一起用。

單獨選中一首歌（停在「選擇樂曲」頁）：`ournotes-auto select 碧天伴走 [--category 全部]`。

### 結束代碼

0 正常結束；1 任務失敗（一局都沒打成、導航出錯、伺服器維護等）；2 設定或執行環境有問題；130 按了 Ctrl+C。
