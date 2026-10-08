<div align="center">

<img src="docs/ui/icon.png" alt="アイコン" width="128">

# OurNotes-Auto

[![最新バージョン](https://img.shields.io/github/v/release/ChissaQAQ/OurNotes-Auto)](https://github.com/ChissaQAQ/OurNotes-Auto/releases/latest)
[![ダウンロード数](https://img.shields.io/github/downloads/ChissaQAQ/OurNotes-Auto/total)](https://github.com/ChissaQAQ/OurNotes-Auto/releases)
[![Star](https://img.shields.io/github/stars/ChissaQAQ/OurNotes-Auto)](https://github.com/ChissaQAQ/OurNotes-Auto/stargazers)
[![ライセンス](https://img.shields.io/github/license/ChissaQAQ/OurNotes-Auto)](LICENSE)

[简体中文](README.md) | [繁體中文](README.zh-TW.md) | [English](README.en.md) | **日本語** | [한국어](README.ko.md)

</div>

『BanG Dream! Our Notes』（国際版 `com.bilibili.sirius.official`、日本版 `com.bushiroad.sirius`）の自動演奏ツールです。MuMu エミュレーター + Python + [MaaFramework](https://github.com/MaaXYZ/MaaFramework)。

- 譜面サイトから譜面をダウンロードし、判定タイミングに合わせてタッチを送ります。目標は FULL COMBO / ALL PERFECT
- 最初のノーツの落下軌跡を追跡し、判定ラインに届く時刻を外挿して曲全体のタイミングを合わせます（音声には依存しません）
- リザルト画面の FAST/SLOW を読み取り、タイミングのずれを自動で補正します
- フリーライブ画面を自動でナビゲーション：選曲、ライブ開始、リザルトの読み取り、もう一度プレイまで行い、無人で連続演奏できます。ほかに放置周回、AP埋め、デイリー受け取りなどのタスクもあります

本ソフトウェアは無料のオープンソースです。使用前に[利用規約](TERMS_OF_SERVICE.md)（中国語）をお読みください。お金を払って入手した場合は転売されたものです。返金を求め、販売者を通報してください。

## ⚠️ リスクについて

**自動化ツールの使用はゲームの利用規約に違反する可能性が高く、アカウントの警告・制限・BAN につながるおそれがあります。その結果はすべて利用者の自己責任です。**

- 本プロジェクトは学習と技術研究のみを目的としています。Bushiroad、Craft Egg、bilibili およびゲームのいかなる運営元とも無関係であり、その許可や承認も受けていません
- イベントランキングや対戦など、他のプレイヤーに影響する場面では使わないでください。メインアカウントで危険を冒すのも避けてください
- 本ツールが有料アイテム（スター）を自分から消費することはありませんが、画面の認識を誤る可能性はあります。ポップアップと LB の設定に注意してください（[ゲーム内設定](docs/ja/usage.md#ゲーム内設定)を参照）。アイテムのブーストドリンクを使うのは「LB不足時にアイテムで補充」をオンにしたときだけです
- 「現状のまま」提供され、いかなる保証もありません

## クイックスタート

1. [Releases](https://github.com/ChissaQAQ/OurNotes-Auto/releases/latest) から圧縮ファイルを 1 つダウンロードして展開します。Python のインストールは不要です。2 種類の UI は機能が同じなので、どちらか好きなほうを選んでください：

   | 圧縮ファイル | UI | 別途必要なもの |
   |---|---|---|
   | `OurNotes-Auto-<バージョン>-win-x64-MFAA.zip` | [MFAAvalonia](https://github.com/SweetSmellFox/MFAAvalonia) | .NET 10 デスクトップランタイム（同梱の `DependencySetup_依赖库安装_win.bat` でワンクリックでインストール可能） |
   | `OurNotes-Auto-<バージョン>-win-x64-MXU.zip` | [MXU](https://github.com/MistEO/MXU) | WebView2（Windows 10/11 には通常プリインストール済み） |

   どちらも [VC++ 2015–2022 ランタイム](https://aka.ms/vs/17/release/vc_redist.x64.exe)が必要です。
2. `MFAAvalonia.exe` または `mxu.exe` を実行し、初回起動時に表示される説明を最後まで読んでください。
3. 接続設定で「MuMu エミュレーター」と使うインスタンスを選びます（n 番目のインスタンスの adb アドレスは `127.0.0.1:16384+32n`）。
4. タスクにチェックを入れ、オプションを設定して開始します。ゲームはホーム画面かフリーライブの選曲画面で止めておけば大丈夫ですし、開いていなくても構いません。「記録まとめ」以外のタスクは最初にゲームを起動し（すでに開いていればそのまま使います）、タイトル画面・ログインボーナス・お知らせを待ってゲーム内に入ります。

使用前の確認事項：

- MuMu エミュレーター 12、解像度 **1280×720**、フレームレート 60 以上
- 国際版で、ゲーム言語が 简体中文、繁體中文、English、한국어 のいずれかであること（한국어 の場合はタスク設定で「ゲーム言語」を 한국어 にしてください）、または日本版であること（「ゲーム言語」を「日本語（日本版）」にしてください。クライアントは Google Play からインストールする必要があります）
- ゲーム内の「ノーツスピード」は **5.00**（「ランダム GREAT」をオンにするときは必ず維持してください。他のスピードでは押すタイミング全体がずれ、GREAT が GOOD になります）。MV / 背景はオフにするか、暗めで動かない背景に変更。エフェクト・演出レベルは下げることをおすすめします
- インターネットに接続できること：譜面は実行時に譜面サイト `assets.bdon.moe` からダウンロードします

タスク：ゲーム起動、アカウント切替（国際版の bilibili ログイン履歴にあるアカウント。パスワード不要）、繰り返しプレイ、LB消化、放置周回（LB がなくなったら回復を待って続行）、AP埋め、チャレンジライブ（イベント期間中）、デイリー受け取り、記録まとめ。各タスクとオプションの詳細は[使い方](docs/ja/usage.md)、コマンドラインでの使い方は[コマンドライン](docs/ja/cli.md)を参照してください。

## 実測結果と既知の制限

実測（MuMu 6.6.4、Android 15 インスタンス 4 コア 6GB 60 fps、人間らしさ機能はオフ）。EXPERT は各回で測ったゲームのフレームレート別に集計しています：

| ゲームのフレームレート | 回数 | ALL PERFECT | FULL COMBO |
|---|---|---|---|
| 50 fps 以上（スムーズ） | 1075 | 1056（98.2%） | 1060（98.6%） |
| 40〜49 fps | 202 | 187（92.6%） | 190（94.1%） |
| 40 fps 未満 | 126 | 111（88.1%） | 114（90.5%） |

スムーズなときにプレイした EXPERT 48 譜面はすべて ALL PERFECT を達成しており、AP できなかった回も平均 1〜2 判定の取りこぼしで、多くはたまに出る MISS 1 つです。EASY・NORMAL・HARD は別に 154 回で、ALL PERFECT 150 回（97%）、FULL COMBO 152 回です。

既知の制限：

- エミュレーターがカクつく（フレーム落ち、タッチ遅延のばらつき）と、複雑な曲では PERFECT をいくつか落としたり、コンボが途切れたりすることがあります。同期もタッチも、エミュレーターが時間どおりにフレームを出し、時間どおりにタッチを受け取ることを前提にしているため、少し引っかかるだけでずれます。フレームレートが低いと AP 率がはっきり下がります（上の表を参照）。CPU を使う他のプログラムを閉じる、エミュレーターに十分な CPU とメモリを割り当てる、ゲームのエフェクトと演出レベルを下げる、といった対策でかなり改善します
- 対応しているのはフリーライブとチャレンジライブ（イベント期間中）だけです。協力ライブなど他のモードには対応していません
- 曲名の OCR がまれに誤ることがあり、認識に失敗したらその回は諦めて次の曲に変えます
- 最初のノーツが非常に早い曲（画面切り替えが終わる前に最初のノーツが画面に入ってくる曲）は同期に失敗することがあり、その場合は一時停止してその回をリトライします
- 現時点で主に検証しているのは、MuMu + 国際版で、エフェクトと演出レベルを下げた設定です。日本版で検証済みなのはフリーライブ、AP埋め、デイリー受け取りのみです

## 問題の報告

[Issues](https://github.com/ChissaQAQ/OurNotes-Auto/issues) に投稿してください。以下を添えていただけると助かります：

- 何が起きたか、どうなってほしかったか、おおよその時刻（ログは時刻で探します）
- ログ `data/ournotes.log`。毎回の実行の冒頭に本ツールのバージョン、OS、CPU が、接続時にエミュレーターのバージョン、インスタンスの CPU / メモリ / フレームレート、解像度、タッチ方式、ゲームのバージョンが記録されるので、**デバイス情報を別途書く必要はありません**。GUI を使っている場合は `data/agent.log` も添付してください。ファイルが大きすぎる場合は、問題が起きた回の実行冒頭にある「OurNotes-Auto 版本号（…）」の行から、問題が起きた後までを切り出せば十分です
- ログに出てくるスクリーンショット（エラー時に「截图 debug/nav/….png」と書かれます）
- 演奏の問題：曲名、難易度、できればリザルト画面のスクリーンショット
- ログやスクリーンショットにはプレイヤー名やフレンド招待コードが含まれることがあるので、送る前に隠しても構いません

## ライセンス

Copyright (C) 2026 ChissaQAQ

本プロジェクトは [AGPL-3.0](LICENSE)（バージョン 3 のみ）でオープンソース化されており、AGPL-3.0 第 7 条に基づいて 2 つの条項を追加しています：頒布する際は作者の表示と「無料のオープンソース」である旨の表示を残すこと、改変版には改変したことを明記すること。詳しくは[利用規約](TERMS_OF_SERVICE.md)（中国語）の第 2 条を参照してください。本ソフトウェアを使用する際は、利用規約のコミュニティ規範と免責事項にも従う必要があります。

リリースパッケージに含まれるサードパーティのコンポーネントは、それぞれのライセンスで配布されています。[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) を参照してください。

ソフトウェアのアイコン（`docs/ui/icon.png`、`docs/ui/logo.ico`）は『BanG Dream! It's MyGO!!!!!』アニメ公式サイトから取ったもので、著作権は ©BanG Dream! Project に帰属し、AGPL-3.0 の許諾範囲には**含まれません**。利用規約 2.5 を参照してください。

## 謝辞

- [autodori](https://github.com/EvATive7/autodori)：設計の参考（GPLv3）。本プロジェクトはそのコードをコピーしていません
- [bdon.moe](https://bdon.moe/) 譜面サイト、[haneoka.org](https://haneoka.org/) 国際版の曲名データ
- [MaaFramework](https://github.com/MaaXYZ/MaaFramework)、[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) と [MaaCommonAssets](https://github.com/MaaXYZ/MaaCommonAssets)（OCR モデル）
- GUI：[MFAAvalonia](https://github.com/SweetSmellFox/MFAAvalonia)、[MXU](https://github.com/MistEO/MXU)
