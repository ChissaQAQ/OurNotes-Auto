# ournotes-auto

《BanG Dream! Our Notes》（国际服，包名 `com.bilibili.sirius.official`）自动演奏工具。MuMu 模拟器 + Python + [MaaFramework](https://github.com/MaaXYZ/MaaFramework)。

- 从谱面站下载谱面，按判定时刻发送触控，目标是 FULL COMBO / ALL PERFECT
- 跟踪第一个音符的下落轨迹，外推它到达判定线的时刻来对齐整首歌（不依赖音频）
- 读取结算页的 FAST/SLOW，自动修正时间偏移
- 自由演出界面自动导航：选曲、开始演出、读结算、再来一局，可无人值守连续演奏

## ⚠️ 风险声明

**使用自动化工具很可能违反游戏的服务条款，可能导致账号被警告、限制或封禁。后果由使用者自行承担。**

- 本项目仅供学习与技术研究。与 Bushiroad、Craft Egg、bilibili 及游戏的任何运营方无关，也未获得其授权或认可
- 请勿用于活动排名、竞技等影响其他玩家的场合，也不要拿主力账号冒险
- 本工具不会主动消耗付费道具，但界面识别可能出错。请留意弹窗与 LB 设置（见下文）
- 按「现状」提供，不做任何担保

## 环境要求

- Windows，MuMu 模拟器 12（本项目在 MuMu 6.6.x、Android 15 实例上测试）
- 模拟器分辨率 **1280×720**（其他 16:9 分辨率会按比例换算，但未充分测试）
- Python ≥ 3.11
- 能访问谱面站 `assets.bdon.moe` 与曲名接口 `haneoka.org`

## 图形界面（发布包）

普通使用推荐这种方式，不用装 Python。在 Releases 页下载一个压缩包解压。两种界面功能相同，任选一个：

| 压缩包 | 界面 | 另外需要 |
|---|---|---|
| `ournotes-auto-<版本>-win-x64-MFAA.zip` | [MFAAvalonia](https://github.com/SweetSmellFox/MFAAvalonia) | .NET 10 桌面运行时。包里的 `DependencySetup_依赖库安装_win.bat` 可一键安装它和 VC++ 运行库 |
| `ournotes-auto-<版本>-win-x64-MXU.zip` | [MXU](https://github.com/MistEO/MXU) | WebView2（Windows 10/11 一般自带） |

两种都需要 [VC++ 2015–2022 运行库](https://aka.ms/vs/17/release/vc_redist.x64.exe)（MaaFramework 依赖）。包里自带 Python 运行时、MaaFramework 与 OCR 模型，谱面在运行时从谱面站下载。各组件的许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

1. 运行 `MFAAvalonia.exe` 或 `mxu.exe`，先读完首次打开时的使用说明。
2. 连接设置里选「MuMu 模拟器」，再选要用的模拟器实例。MuMu 12 第 n 个实例的 adb 地址是 `127.0.0.1:16384+32n`，例如第 0 个是 16384，第 1 个是 16416。截图和触控走 MuMu 的接口，安装目录由 adb 路径推出，所以目前只支持 MuMu 12。
3. 勾选任务、设置选项后开始。游戏停在主界面或自由演出选曲页即可；勾选「启动游戏」时，游戏没开也行。

| 任务 | 作用 |
|---|---|
| 启动游戏 | 游戏没在运行时启动它，点掉标题画面、登录奖励和公告，进入主界面。放在其他任务前面 |
| 重复刷歌 | 在自由演出里连续演奏，可选选曲方式（当前曲 / 随机 / 优先未 AP / 指定歌单）、难度、局数、每局消耗的 LB |
| 清体力 | 连续演奏到 LIVE BOOST 用完（每局前看持有数，为 0 就结束），不用恢复道具或星钻。升级会回满 LB，所以局数可能比预计的多 |
| AP补完 | 把所选难度（EXPERT / HARD / NORMAL / EASY）里还没 ALL PERFECT 的歌打到 AP。会临时把选曲页切到「全部」分类、筛选「未ALL PERFECT」，正常结束后改回原样 |
| 领取日常 | 依次领取录音室练习（收获）、任务、通行证任务与 PASS 普通档、限定任务、新手任务、礼物盒的奖励，每项可单独关掉。只点亮着的「一键领取」和奖励弹窗的 OK；不认得的弹窗不点，报错停下。打完歌后每日任务才满，所以排在演奏任务后面。可选「看故事（跳过）」（默认关）：把没看过的乐队故事、视角故事、羁绊故事逐话跳过，解锁乐曲、领看完的奖励，每话第一次看要下载数据（无语音，乐队故事约 30MB） |
| 记录汇总 | 在日志里列出本工具的演奏记录，不操作模拟器 |

需要调整默认值以外的参数（如时间偏移）时，把 [config.example.yaml](config.example.yaml) 复制为解压目录里的 `config.yaml` 再修改。界面里的选项优先于它。

## 从源码安装（命令行）

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
```

### OCR 模型

界面导航用 MaaFramework 的 OCR。下载模型到 `resource/model/ocr/`：

```bash
python tools/fetch_ocr.py
```

模型是 [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) 的 PP-OCRv6 small（Apache-2.0），用 [MaaCommonAssets](https://github.com/MaaXYZ/MaaCommonAssets)（MIT）转好的 ONNX 版本。脚本固定版本、校验文件，并一起下载两边的许可证。它能读日文曲名，中英文按钮也比旧的中文模型读得准。只手动演奏（`play` 命令）不需要 OCR 模型。

## 游戏内设置

| 设置 | 建议 | 说明 |
|---|---|---|
| 节奏图示速度 | **5.00** | 同步参数 `play.sync.tau_s = 0.835` 对应 5.00。改了流速就要重新执行 `calibrate motion` |
| MV / 背景 | 关闭，或调成较暗的轻量背景 | 背景一直在动会干扰首音符跟踪 |
| 乐队确认页「消耗LB」 | 用配置项 `game.lb_cost`（0~3）自动设置 | 第一局前自动打开弹窗选好并核对，游戏会记住。0 不消耗 LB（LIVE START 上显示「n ▸ n」）。LB 不够时游戏会消耗剩余全部；弹出「恢复LIVE BOOST」（道具/星钻/广告）时只点取消，该局改为 0。留空（null）则不改游戏里的设置 |

## 配置

```bash
ournotes-auto init-config          # 生成包含全部默认值的 config.yaml
```

也可以复制 [config.example.yaml](config.example.yaml)（只列出常用项）为 `config.yaml` 再修改。未写出的项使用默认值，全部参数与说明见 `ournotes_auto/config.py`。

至少要确认两项：`device.instance`（MuMu 多开器里的实例编号）和 `device.mumu_path`（MuMu 安装目录）。

## 使用

```bash
# 全自动连续演奏（从任意自由演出相关画面开始，会自己导航到乐队确认页）
ournotes-auto run                          # 按配置
ournotes-auto run -n 3 --mode random -d expert   # 随机选曲、EXPERT、打 3 局后停止
ournotes-auto run --watch-combo --record   # 调试：日志里列出断连处的音符；同步失败时保存截图到 debug/sync

# 启动游戏并进入主界面；领取日常奖励（默认除看故事外六项全领，--jobs 只做其中几项）
ournotes-auto start
ournotes-auto daily
ournotes-auto daily --jobs studio,missions,pass,limited,beginner,gifts   # 录音室练习、任务、通行证、限定任务、新手任务、礼物盒
ournotes-auto daily --jobs story   # 跳过没看过的乐队 / 视角 / 羁绊故事（要写明才做）

# 汇总本地演奏记录（总局数、AP 过的谱面、还没 AP 的谱面、最近几局）
ournotes-auto records [--recent 10]

# 识别当前画面（调试导航）
ournotes-auto look [--save] [--ocr]

# 手动模式：停在乐队确认页，点 LIVE START 并演奏指定曲目
ournotes-auto play "迷星叫" -d expert --tap 1140,648 --tap 782,612

# 谱面
ournotes-auto charts search [曲名]
ournotes-auto charts show 100026 -d expert
ournotes-auto charts prefetch              # 预先下载全部谱面

# 截图并叠加判定线/轨道线，核对几何参数
ournotes-auto screenshot --overlay

# 修改流速后重新测量音符运动参数（停在乐队确认页，--tap 同 play）
ournotes-auto calibrate motion "迷星叫" --tap 1140,648 --tap 782,612
```

也可以用 `python -m ournotes_auto ...` 运行。Windows 终端乱码时设置环境变量 `PYTHONIOENCODING=utf-8`。按 Ctrl+C 停止。

`run` 的选曲模式（`loop.song_mode`）：
- `current`：一直打当前选中的曲目
- `random`：每局结束后点「随机选曲」。第一次换歌前会把选曲页的「游玩状况」筛选改回「不指定」，因为随机选曲只在筛选后的列表里抽。
- `ap`：全曲 AP 补完。把分类切到「全部」，按 `loop.ap_difficulties` 的顺序对每个难度筛选「未ALL PERFECT」，然后随机抽歌来打。
  - 同一首歌打了 `loop.ap_max_attempts` 次还没 AP，就不再打它。
  - 未解锁或认不出的歌直接重抽。
  - 正常结束时把筛选和分类改回原样；中途停止时不会改回。
  - 例：`ournotes-auto run --mode ap --ap-difficulties expert,hard --lb-cost 0`
- `ap_first`：所选难度优先打没 AP 的歌（抽歌规则同 `ap`），没有了就改回「不指定」随机选曲。
- `list`：按歌单（`--songs` / `loop.song_list`）依次打，打完一轮从头再来。
  - 每项是曲目 ID 或曲名（任意语言，模糊匹配），可以加 `@难度`，不加就用 `-d` 的难度；用逗号、分号或换行分隔。同名的不同版本只能用 ID 区分。
  - 把分类切到「全部」后在列表里边滚边认封面找歌，第一次找一首可能要二三十秒。未解锁、找不到的歌跳过。
  - 例：`ournotes-auto run --mode list --songs "100010, 碧天伴走@hard" -n 4`

单独选中一首歌（停在乐曲选择页）：`ournotes-auto select 碧天伴走 [--category 全部]`。

### 时间偏移与自动修正

`play.offset_ms` 是全局时间偏移，正值表示整体更晚按下，用来补偿截图、输入、渲染带来的延迟。本机 MuMu 实测约 -16ms 时判定居中。

每局结束后，程序读取结算页的 FAST/SLOW。FAST 多就往后推，SLOW 多就往前拉，把学到的偏移存进 `data/state.json`（与配置里的 `offset_ms` 叠加）。以下情况只记录、不修正：执行不稳定（迟到的批次多），或者 GOOD/BAD/MISS 过多（多半同步到了错误的音符）。

### 拟人化（可选，默认关闭）

让结果不那么整齐。界面里在全局选项中，命令行用 `--set` 修改：

| 配置项 | 界面选项 | 作用 |
|---|---|---|
| `play.touch.great_ratio` | 随机 GREAT（1% / 3% / 5%） | 按判定总数的这个比例，挑前后没有别的音符挨着的普通点击，提前或延后 64~69ms 打成 GREAT（单局同步误差偏大时其中一部分会变成 PERFECT 或 GOOD，GOOD 不断连击）。选曲方式为 `ap` / `ap_first` 时不生效。开启后自动修正偏移时不计 GREAT 的 FAST/SLOW |
| `play.touch.jitter_ms` | 时机偏移（±8 / ±16ms） | 按下时刻随时间缓慢漂移（最多 ± 设定值），另加每个手势最多 ±3ms 的独立抖动。上限 20ms |
| `play.touch.position_jitter` | 触控位置随机 | 触控点在音符宽度内随机横移，并上下小幅挪动。不会挪进旁边点击类音符的判定区（否则按下可能被它认领），按住的长条也不会挪进同时按住的另一个长条的判定区。取值 0~1 为幅度，界面开关打开时为 1 |

例：`ournotes-auto --set play.touch.great_ratio=0.03 --set play.touch.jitter_ms=8 --set play.touch.position_jitter=1 run`

### 意外情况

- 演奏中连续 `play.guard_lost_s` 秒（默认 2）看不到右上角的暂停按钮（被暂停、弹窗、闪退），就立刻停止触控，本局作废，由导航重新找回画面。设为 0 关闭这项检查
- 画面一直认不出时，每 30 秒检查一次游戏进程；游戏闪退了就经 adb 重新启动，从标题画面重新登录。连续重启最多 2 次，回到主页后重新计数

## 运行时文件

| 路径 | 内容 |
|---|---|
| `data/ournotes.log` | 详细日志（含调试级别） |
| `data/records.jsonl` | 每局结果（判定数、FAST/SLOW、偏移、同步误差） |
| `data/state.json` | 自动修正学到的偏移 |
| `cache/charts/` | 谱面与曲目索引缓存 |
| `debug/nav/` | 导航出错、结算读数矛盾时的截图 |
| `debug/sync/` | `play --record` 保存的首音符跟踪画面 |
| `debug/combo/` | `--watch-combo --record` 保存的连击数截图；检测到断连时自动保存到 `breaks/`（只留最新 20 个） |

## 工作原理（简述）

1. **导航**：截图 → OCR → 按文字与位置判断当前画面 → 点击按钮。按钮优先按文字精确定位，找不到时用固定坐标
2. **识曲**：乐队确认页 OCR 出曲名，模糊匹配谱面站与国际服的多语言曲名，得到 musicId
3. **同步**：点击开始后，等画面静止，检测第一个音符出现在它所在轨道的上方，跟踪它的前沿。音符逼近判定线满足 `y - y_h ∝ exp(t/τ)`，拟合后外推到达时刻，由此推出歌曲的零点
4. **演奏**：谱面转换成触控手势（点击、滑动、长条、追踪），经 minitouch 注入触控（备选 MuMu IPC），高精度计时器按时发送。按游戏的判定表避开相邻音符互相「认领」触点：同时横滑的邻居错开起点，会被之后的点击认领的上划 / 追踪提前按下
5. **结算**：读取判定数与 FAST/SLOW，记录结果并修正偏移

## 已知限制

- 只支持自由演出。协力演出、活动等其他模式不支持
- 曲名 OCR 偶尔出错，识别失败时本局放弃，换下一首
- 首音符非常早的曲目（第一个音符在转场结束前就已进入画面）可能同步失败，本局会放弃
- 目前只在 MuMu + 国际服上验证过

## 许可证

本项目按 [GPL-3.0](LICENSE)（或更新版本）发布。发布包里的第三方组件按各自的许可证分发，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 致谢

- [autodori](https://github.com/EvATive7/autodori)：设计参考（GPLv3）。本项目没有复制其代码
- [bdon.moe](https://bdon.moe/) 谱面站，[haneoka.org](https://haneoka.org/) 国际服曲名数据
- [MaaFramework](https://github.com/MaaXYZ/MaaFramework)、[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) 与 [MaaCommonAssets](https://github.com/MaaXYZ/MaaCommonAssets)（OCR 模型）
- 图形界面：[MFAAvalonia](https://github.com/SweetSmellFox/MFAAvalonia)、[MXU](https://github.com/MistEO/MXU)
