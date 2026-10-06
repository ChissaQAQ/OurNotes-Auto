# 명령줄

GUI를 쓰지 않을 때는 소스에서 설치한 뒤 명령줄로 실행합니다. 각 작업과 옵션의 의미는 [사용 설명서](usage.md)를 참고하세요.

## 소스에서 설치

Python ≥ 3.11이 필요합니다.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
```

### OCR 모델

UI 내비게이션에는 MaaFramework의 OCR을 사용합니다. 모델을 `resource/model/ocr/`에 다운로드하세요:

```bash
python tools/fetch_ocr.py
```

모델은 [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)의 PP-OCRv6 small(Apache-2.0)을 [MaaCommonAssets](https://github.com/MaaXYZ/MaaCommonAssets)(MIT)가 변환한 ONNX 버전입니다. 스크립트는 버전을 고정하고 파일을 검증하며, 양쪽의 라이선스도 함께 다운로드합니다. 일본어 곡명을 읽을 수 있고, 중국어·영어 버튼도 예전 중국어 모델보다 정확하게 읽습니다. 수동 연주(`play` 명령)만 할 때는 OCR 모델이 필요 없습니다.

게임 언어가 한국어(`loop.ocr_model: ko_kr`)일 때는 PP-OCRv3 한국어 인식 모델도 사용해야 합니다. 처음 사용할 때 `resource/model/ocr/ko_kr/`에 자동으로 다운로드되며, `python tools/fetch_ocr.py --model ko_kr`로 미리 받아 둘 수도 있습니다. 배포 패키지에는 이 모델이 들어 있지 않습니다.

## 설정

```bash
ournotes-auto init-config          # 모든 기본값이 들어 있는 config.yaml 생성
```

[config.example.yaml](../../config.example.yaml)(자주 쓰는 항목만 나열)을 `config.yaml`로 복사해 수정해도 됩니다. 적지 않은 항목은 기본값을 쓰며, 모든 파라미터와 설명은 `ournotes_auto/config.py`를 참고하세요.

최소한 두 항목은 확인하세요: `device.instance`(MuMu 멀티 인스턴스 관리자의 인스턴스 번호)와 `device.mumu_path`(MuMu 설치 경로). 일본 서버는 `device.package`도 `com.bushiroad.sirius`로 설정하세요(기본값은 국제 서버의 `com.bilibili.sirius.official`).

## 사용

```bash
# 전자동 연속 연주(프리 라이브 관련 화면이면 어디서든 시작하며, 밴드 확인 화면까지 스스로 이동)
ournotes-auto run                          # 설정대로
ournotes-auto run -n 3 --mode random -d expert   # 랜덤 선곡, EXPERT, 3판 플레이 후 정지
ournotes-auto run --watch-combo --record   # 디버그: 콤보가 끊긴 곳의 노트를 로그에 표시, 동기화 실패 시 스크린샷을 debug/sync에 저장
ournotes-auto run --until-lb-empty --lb-cost 3             # LB 소진: LB를 다 쓸 때까지 플레이
ournotes-auto run --until-lb-empty --wait-lb --lb-cost 3   # 방치 플레이: 다 쓰면 LB가 3개 회복될 때까지 기다렸다가 계속, 멈추지 않고 실행
ournotes-auto run --until-lb-empty --wait-lb --lb-cost 3 --claim-studio 4   # 방치 플레이, 시작할 때와 이후 4시간마다 스튜디오 연습 수령
ournotes-auto run --wait-lb --lb-cost 0 --claim-daily 22:30   # LB를 기다리지 않는 방치 플레이(LB가 있으면 판당 1개 소모, 다 쓰면 소모 0으로 계속), 매일 22:30에 데일리 수령
ournotes-auto run --until-lb-empty --lb-cost 3 --lb-refill 30   # LB 소진, LB 부족 시 아이템의 드링크로 보충, 최대 30개 보충(0은 다 쓸 때까지)
ournotes-auto run --challenge --mode rotate              # 챌린지 라이브(이벤트 기간): 판당 200 CP, 곡 순환, CP가 모자랄 때까지 플레이
ournotes-auto run --challenge --challenge-cost 1600 -n 3 # 챌린지 라이브, 판당 1600 CP(보상 ×8), 3판 플레이
ournotes-auto run --challenge --mode ap_first           # 챌린지 라이브: EXPERT에서 아직 AP를 못 한 곡부터, 모두 AP면 순환 플레이

# 게임을 실행하고 홈 화면으로 진입. 데일리 보상 수령(기본은 스토리 보기·이벤트 스토리를 뺀 7개 항목 모두, --jobs로 일부만 지정)
ournotes-auto start
ournotes-auto switch-account user_123   # 현재 계정에서 로그아웃하고, 로그인 기록에서 계정 이름에 user_123이 들어간 계정으로 로그인한 뒤 홈 화면으로 진입
ournotes-auto daily
ournotes-auto daily --jobs studio,missions,pass,limited,beginner,tgw,gifts   # 스튜디오 연습, 미션, 패스, 기간 한정 미션, 초보자 미션, T.G.W CARD, 선물함
ournotes-auto daily --jobs story   # 보지 않은 밴드 / 어나더 / 인연 스토리 스킵(명시해야 실행)
ournotes-auto daily --jobs event   # 기간 한정 이벤트에서 보지 않은 이벤트 스토리 / 어나더 스토리 스킵(명시해야 실행)

# 로컬 연주 기록 요약(총 판 수, AP한 채보, 아직 AP를 못 한 채보, 최근 몇 판)
ournotes-auto records [--recent 10]

# 현재 화면 인식(내비게이션 디버그)
ournotes-auto look [--save] [--ocr]

# 수동 모드: 밴드 확인 화면에 멈춘 상태에서 LIVE START를 누르고 지정한 곡을 연주
ournotes-auto play "迷星叫" -d expert --tap 1140,648 --tap 782,612

# 채보
ournotes-auto charts search [曲名]
ournotes-auto charts show 100026 -d expert
ournotes-auto charts prefetch              # 모든 채보를 미리 다운로드

# 스크린샷을 찍고 판정선/레인선을 겹쳐 그려 기하 파라미터 확인
ournotes-auto screenshot --overlay

# 속도를 바꾼 뒤 노트 이동 파라미터를 다시 측정(밴드 확인 화면에 멈춘 상태, --tap은 play와 같음)
ournotes-auto calibrate motion "迷星叫" --tap 1140,648 --tap 782,612
```

`python -m ournotes_auto ...`로 실행해도 됩니다. Windows 터미널에서 글자가 깨지면 환경 변수 `PYTHONIOENCODING=utf-8`를 설정하세요. Ctrl+C를 누르면 멈춥니다.

### 선곡 모드

`run`의 선곡 모드(`loop.song_mode`):

- `current`: 현재 선택된 곡을 계속 플레이합니다
- `random`: 매 판이 끝나면 「랜덤」을 누릅니다. 랜덤 선곡은 필터링된 목록에서만 뽑으므로, 처음 곡을 바꾸기 전에 곡 선택 화면의 「플레이 상황」 필터를 「지정 없음」으로 되돌립니다.
- `ap`: 전곡 AP 채우기. 분류를 「전체」로 바꾸고, `loop.ap_difficulties`의 순서대로 난이도마다 「ALL PERFECT 미달성」 필터를 건 뒤 랜덤으로 곡을 뽑아 플레이합니다.
  - 같은 곡을 `loop.ap_max_attempts`번 쳐도 AP가 안 되면 그 곡은 더 치지 않습니다.
  - 해금되지 않았거나 알아보지 못한 곡은 바로 다시 뽑습니다.
  - 정상 종료하면 필터와 분류를 원래대로 되돌리지만, 도중에 멈추면 되돌리지 않습니다.
  - 예: `ournotes-auto run --mode ap --ap-difficulties expert,hard --lb-cost 0`
- `ap_first`: 선택한 난이도에서 AP를 못 한 곡을 우선 플레이하고(곡 뽑기 규칙은 `ap`와 같음), 더 없으면 「지정 없음」으로 되돌려 랜덤 선곡합니다.
  - 여러 난이도를 채우려면 `loop.ap_first_difficulties`(예: `expert,hard,normal,easy`)를 지정하세요. 순서대로 다 채운 뒤 `-d`의 난이도로 랜덤 플레이합니다. UI에서 난이도를 「고난이도 우선」으로 고르면 이렇게 동작합니다.
  - 예: `ournotes-auto --set loop.ap_first_difficulties=expert,hard,normal,easy run --mode ap_first -d expert --until-lb-empty --lb-cost 3`
- `list`: 곡 목록(`--songs` / `loop.song_list`)대로 차례로 플레이하고, 한 바퀴 돌면 처음부터 다시 합니다.
  - 각 항목은 곡 ID 또는 곡명(어떤 언어든 가능, 퍼지 매칭)이며, `@난이도`를 붙일 수 있고 붙이지 않으면 `-d`의 난이도를 씁니다. 쉼표, 세미콜론, 줄바꿈으로 구분합니다. 이름이 같은 다른 버전은 ID로만 구분할 수 있습니다.
  - 분류를 「전체」로 바꾼 뒤 목록을 스크롤하면서 재킷을 인식해 곡을 찾으므로, 처음 한 곡을 찾는 데 20~30초 걸릴 수 있습니다. 해금되지 않았거나 찾을 수 없는 곡은 건너뜁니다.
  - 예: `ournotes-auto run --mode list --songs "100010, 碧天伴走@hard" -n 4`
- `rotate`: 챌린지 라이브(`--challenge` / `loop.challenge`) 전용입니다. 매 판이 끝나면 챌린지 라이브의 곡 선택 화면에서 다음 곡을 선택하고, 마지막 곡 다음에는 첫 곡으로 돌아갑니다. 챌린지 라이브에서는 `current`, `rotate`, `ap_first`만 쓸 수 있습니다.
- 챌린지 라이브의 `ap_first`: 챌린지 라이브의 곡 선택 화면에는 필터와 랜덤 선곡이 없으므로, 선택된 곡부터 아래로 한 곡씩 오른쪽 패널에 ALL PERFECT 표시가 있는지 보고 처음 나오는 AP를 못 한 곡을 플레이합니다(같은 곡을 `loop.ap_max_attempts`번 쳐도 AP가 안 되면 더 치지 않습니다). `loop.ap_first_difficulties`의 사용법은 위와 같으며, 다 채우면 `-d`의 난이도로 곡을 순환하며 플레이합니다.

챌린지 라이브(`--challenge`)는 판마다 `game.challenge_cost`(`--challenge-cost`, 200 / 400 / 800 / 1600, 기본 200, null은 게임 설정을 바꾸지 않음)만큼 챌린지 pt를 소모하고 LB는 소모하지 않으며, CP가 한 판에 모자랄 때까지 플레이합니다. 그래서 `--until-lb-empty`, `--wait-lb`, `--lb-refill`과 함께 쓸 수 없습니다.

곡 하나만 선택(곡 선택 화면에 멈춤): `ournotes-auto select 碧天伴走 [--category 全部]`.

### 종료 코드

0 정상 종료, 1 작업 실패(한 판도 플레이하지 못함, 내비게이션 오류, 서버 점검 등), 2 설정 또는 실행 환경 문제, 130 Ctrl+C를 누름.
