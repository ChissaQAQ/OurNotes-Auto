# コマンドライン

GUI を使わない場合は、ソースからインストールしてコマンドラインで実行します。各タスク・オプションの意味は[使い方](usage.md)を参照してください。

## ソースからのインストール

Python ≥ 3.11 が必要です。

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
```

### OCR モデル

画面のナビゲーションには MaaFramework の OCR を使います。モデルを `resource/model/ocr/` にダウンロードします：

```bash
python tools/fetch_ocr.py
```

モデルは [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) の PP-OCRv6 small（Apache-2.0）を、[MaaCommonAssets](https://github.com/MaaXYZ/MaaCommonAssets)（MIT）で変換した ONNX 版です。スクリプトはバージョンを固定してファイルを検証し、両方のライセンスも一緒にダウンロードします。このモデルは日本語の曲名も読め、中国語・英語のボタンも旧来の中国語モデルより正確に読めます。手動演奏（`play` コマンド）だけなら OCR モデルは不要です。

ゲーム言語が 한국어（`loop.ocr_model: ko_kr`）の場合は、別途 PP-OCRv3 の韓国語認識モデルを使います。初めて使うときに `resource/model/ocr/ko_kr/` へ自動でダウンロードされます。`python tools/fetch_ocr.py --model ko_kr` で事前にダウンロードしておくこともできます。リリースパッケージにはこのモデルは含まれていません。

## 設定

```bash
ournotes-auto init-config          # すべてのデフォルト値を含む config.yaml を生成
```

[config.example.yaml](../../config.example.yaml)（よく使う項目だけを記載）を `config.yaml` にコピーして編集してもかまいません。書かれていない項目はデフォルト値になります。すべてのパラメータと説明は `ournotes_auto/config.py` を参照してください。

少なくとも次の 2 項目は確認してください：`device.instance`（MuMu のマルチインスタンス管理ツールでのインスタンス番号）と `device.mumu_path`（MuMu のインストール先）。日本版ではさらに `device.package` を `com.bushiroad.sirius` にしてください（既定値は国際版の `com.bilibili.sirius.official`。エミュレーターに入っているのが国際版の Google Play 版 `com.bilibili.sirius` なら自動でそちらを使うので、変更は不要です）。

## 使用方法

```bash
# 全自動の連続演奏（フリーライブ関連のどの画面からでも開始でき、バンド確認画面まで自動で移動します）
ournotes-auto run                          # 設定どおり
ournotes-auto run -n 3 --mode random -d expert   # ランダム選曲、EXPERT、3 回プレイして停止
ournotes-auto run --watch-combo --record   # デバッグ：コンボが途切れた箇所のノーツをログに出力。同期失敗時はスクリーンショットを debug/sync に保存
ournotes-auto run --until-lb-empty --lb-cost 3             # LB消化：LB がなくなるまでプレイ
ournotes-auto run --until-lb-empty --wait-lb --lb-cost 3   # 放置周回：なくなったら LB が 3 回復するのを待って続行し、ずっと実行
ournotes-auto run --until-lb-empty --wait-lb --lb-cost 3 --claim-studio 4   # 放置周回、開始時とその後 4 時間ごとにスタジオ練習を回収
ournotes-auto run --wait-lb --lb-cost 0 --claim-daily 22:30   # LB を待たない放置周回（LB があれば 1 回に 1 消費、なくなったら消費 0 で続行）、毎日 22:30 にデイリーを受け取り
ournotes-auto run --until-lb-empty --lb-cost 3 --lb-refill 30   # LB消化、LB 不足時はアイテムのドリンクで補充、最大 30 まで（0 でなくなるまで）
ournotes-auto run --challenge --mode rotate              # チャレンジライブ（イベント期間中）：1 回 200 CP、全曲を順番にプレイ、CP が足りなくなるまで
ournotes-auto run --challenge --challenge-cost 1600 -n 3 # チャレンジライブ、1 回 1600 CP（報酬 ×8）、3 回プレイ
ournotes-auto run --challenge --mode ap_first           # チャレンジライブ：EXPERT でまだ AP していない曲を先にプレイし、すべて AP したら順番にプレイ

# ゲームを起動してホーム画面に入る。デイリー報酬を受け取る（デフォルトではストーリー閲覧・イベントストーリー以外の 7 項目をすべて受け取り、--jobs で一部だけ実行）
ournotes-auto start
ournotes-auto switch-account user_123   # 現在のアカウントからログアウトし、ログイン履歴からアカウント名に user_123 を含むアカウントを選んでログインし、ホーム画面に入る
ournotes-auto daily
ournotes-auto daily --jobs studio,missions,pass,limited,beginner,tgw,gifts   # スタジオ練習、ミッション、パス、期間限定ミッション、ビギナーミッション、T.G.W CARD、プレゼントボックス
ournotes-auto daily --jobs story   # 未読のバンド / アナザー / 絆ストーリーをスキップ（明示したときだけ実行）
ournotes-auto daily --jobs event   # 期間限定イベントの未読のイベントストーリー / アナザーストーリーをスキップ（明示したときだけ実行）

# ローカルの演奏記録をまとめる（総プレイ回数、AP した譜面、まだ AP していない譜面、直近の数回）
ournotes-auto records [--recent 10]
ournotes-auto records --clear-chart-offsets   # 譜面ごとに学習したオフセットを消す（全体の学習値はそのまま）

# 現在の画面を認識（ナビゲーションのデバッグ）
ournotes-auto look [--save] [--ocr]

# 手動モード：バンド確認画面で止めておき、LIVE START を押して指定した曲を演奏
ournotes-auto play "迷星叫" -d expert --tap 1140,648 --tap 782,612

# 譜面
ournotes-auto charts search [曲名]
ournotes-auto charts show 100026 -d expert
ournotes-auto charts prefetch              # すべての譜面を事前にダウンロード

# スクリーンショットに判定ライン / レーンの線を重ねて、ジオメトリのパラメータを確認
ournotes-auto screenshot --overlay

# スピードを変えた後にノーツの動きのパラメータを測り直す（バンド確認画面で止めておく。--tap は play と同じ）
ournotes-auto calibrate motion "迷星叫" --tap 1140,648 --tap 782,612
```

`python -m ournotes_auto ...` で実行することもできます。Windows のターミナルで文字化けするときは、環境変数 `PYTHONIOENCODING=utf-8` を設定してください。Ctrl+C で停止します。

### 選曲モード

`run` の選曲モード（`loop.song_mode`）：

- `current`：現在選択中の曲をプレイし続けます
- `random`：毎回終了後に「ランダム」を押します。最初に曲を変える前に、選曲画面の「プレイ状況」の絞り込みを「指定なし」（No Preference）に戻します。ランダム選曲は絞り込み後の一覧からしか選ばないためです。
- `ap`：全曲の AP埋め。カテゴリを「すべて」に切り替え、`loop.ap_difficulties` の順に各難易度を「ALL PERFECT未達成」（NO ALL PERFECT）で絞り込み、ランダムに曲を選んでプレイします。
  - 同じ曲を `loop.ap_max_attempts` 回プレイしても AP できなければ、その曲はもうプレイしません。
  - 未解放の曲や認識できない曲は、すぐに選び直します。
  - 正常に終了したときは絞り込みとカテゴリを元に戻します。途中で止めたときは戻しません。
  - 例：`ournotes-auto run --mode ap --ap-difficulties expert,hard --lb-cost 0`
- `ap_first`：選んだ難易度で未 AP の曲を優先してプレイし（曲の選び方は `ap` と同じ）、なくなったら「指定なし」に戻してランダムに選曲します。
  - 複数の難易度を埋めるときは `loop.ap_first_difficulties`（例：`expert,hard,normal,easy`）を指定すると、その順に埋め終わってから `-d` の難易度でランダムにプレイします。UI で難易度を「高難易度優先」にしたときはこの動作になります。
  - 例：`ournotes-auto --set loop.ap_first_difficulties=expert,hard,normal,easy run --mode ap_first -d expert --until-lb-empty --lb-cost 3`
- `list`：曲リスト（`--songs` / `loop.song_list`）の順にプレイし、一巡したら最初に戻ります。
  - 各項目は楽曲 ID または曲名（任意の言語、あいまい一致）で、`@難易度` を付けられます。付けなければ `-d` の難易度を使います。カンマ、セミコロン、改行で区切ります。同名の別バージョンは ID でしか区別できません。
  - カテゴリを「すべて」に切り替えた後、一覧をスクロールしながらジャケットを認識して曲を探すため、1 曲目を探すのに 20〜30 秒かかることがあります。未解放の曲や見つからない曲はスキップします。
  - 例：`ournotes-auto run --mode list --songs "100010, 碧天伴走@hard" -n 4`
- `rotate`：チャレンジライブ（`--challenge` / `loop.challenge`）専用です。毎回終了後にチャレンジライブの楽曲選択画面で次の曲を選び、最後の曲の後は最初の曲に戻ります。チャレンジライブで使えるのは `current`、`rotate`、`ap_first` だけです。
- チャレンジライブの `ap_first`：チャレンジライブの楽曲選択画面には絞り込みもランダム選曲もないため、代わりに選択中の曲から下へ 1 曲ずつ、右側のパネルに ALL PERFECT のマークがあるかを確認し、最初に見つかった未 AP の曲をプレイします（同じ曲を `loop.ap_max_attempts` 回プレイしても AP できなければ、もうプレイしません）。`loop.ap_first_difficulties` の使い方は上と同じで、すべて埋め終わったら `-d` の難易度で全曲を順番にプレイします。

チャレンジライブ（`--challenge`）は 1 回ごとに `game.challenge_cost`（`--challenge-cost`、200 / 400 / 800 / 1600、デフォルト 200。null ならゲーム内の設定を変更しない）のチャレンジpt を消費し、LB は消費せず、CP が 1 回分に足りなくなるまでプレイします。そのため `--until-lb-empty`、`--wait-lb`、`--lb-refill` とは併用できません。

1 曲だけを選択する（楽曲選択画面で止めておく）：`ournotes-auto select 碧天伴走 [--category 全部]`。

### 終了コード

0 正常終了。1 タスク失敗（1 回もプレイできなかった、ナビゲーションのエラー、サーバーメンテナンスなど）。2 設定または実行環境に問題がある。130 Ctrl+C が押された。
