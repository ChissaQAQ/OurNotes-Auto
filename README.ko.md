<div align="center">

<img src="docs/ui/icon.png" alt="아이콘" width="128">

# OurNotes-Auto

[![최신 버전](https://img.shields.io/github/v/release/ChissaQAQ/OurNotes-Auto)](https://github.com/ChissaQAQ/OurNotes-Auto/releases/latest)
[![다운로드 수](https://img.shields.io/github/downloads/ChissaQAQ/OurNotes-Auto/total)](https://github.com/ChissaQAQ/OurNotes-Auto/releases)
[![스타](https://img.shields.io/github/stars/ChissaQAQ/OurNotes-Auto)](https://github.com/ChissaQAQ/OurNotes-Auto/stargazers)
[![라이선스](https://img.shields.io/github/license/ChissaQAQ/OurNotes-Auto)](LICENSE)

[简体中文](README.md) | [繁體中文](README.zh-TW.md) | [English](README.en.md) | [日本語](README.ja.md) | **한국어**

</div>

《BanG Dream! Our Notes》(국제 서버 `com.bilibili.sirius.official`(Google Play 버전 `com.bilibili.sirius`), 일본 서버 `com.bushiroad.sirius`) 자동 연주 도구입니다. MuMu 에뮬레이터 + Python + [MaaFramework](https://github.com/MaaXYZ/MaaFramework).

- 채보 사이트에서 채보를 다운로드하고, 판정 시각에 맞춰 터치를 보내 FULL COMBO / ALL PERFECT를 노립니다
- 첫 노트가 떨어지는 궤적을 추적하고, 판정선에 닿는 시각을 외삽해 곡 전체를 맞춥니다(오디오에 의존하지 않음)
- 결과 화면의 FAST/SLOW를 읽어 시간 오프셋을 자동으로 보정합니다
- 프리 라이브 화면 자동 내비게이션: 선곡, 라이브 시작, 결과 읽기, 한 판 더를 자동으로 진행해 무인으로 연속 연주할 수 있습니다. 그 밖에 방치 플레이, AP 채우기, 데일리 수령 등의 작업도 있습니다

이 소프트웨어는 무료 오픈 소스입니다. 사용하기 전에 [이용 약관](TERMS_OF_SERVICE.md)(중국어)을 읽어 주세요. 돈을 주고 샀다면 되팔기를 당한 것이니 환불을 요구하고 판매자를 신고할 수 있습니다.

## ⚠️ 위험 고지

**자동화 도구를 사용하는 것은 게임의 서비스 약관을 위반할 가능성이 높으며, 계정이 경고, 제한 또는 정지될 수 있습니다. 그 결과는 사용자 본인이 책임집니다.**

- 이 프로젝트는 학습과 기술 연구 목적으로만 제공됩니다. Bushiroad, Craft Egg, bilibili 및 게임의 어떤 운영사와도 관련이 없으며, 이들의 허가나 승인을 받지 않았습니다
- 이벤트 랭킹, 경쟁 등 다른 플레이어에게 영향을 주는 곳에 사용하지 말고, 주력 계정으로 위험을 감수하지도 마세요
- 이 도구는 스스로 유료 아이템(스타)을 소모하지 않지만, 화면 인식이 틀릴 수 있습니다. 팝업과 LB 설정에 주의하세요([게임 내 설정](docs/ko/usage.md#게임-내-설정) 참고). 「LB 부족 시 아이템으로 보충」을 켰을 때만 아이템의 부스트 드링크를 사용합니다
- 「있는 그대로」 제공되며, 어떠한 보증도 하지 않습니다

## 빠른 시작

1. [Releases](https://github.com/ChissaQAQ/OurNotes-Auto/releases/latest)에서 압축 파일 하나를 다운로드해 압축을 풉니다. Python은 설치할 필요가 없습니다. 두 UI는 기능이 같으니 아무거나 고르세요:

   | 압축 파일 | UI | 추가로 필요한 것 |
   |---|---|---|
   | `OurNotes-Auto-<버전>-win-x64-MFAA.zip` | [MFAAvalonia](https://github.com/SweetSmellFox/MFAAvalonia) | .NET 10 데스크톱 런타임(패키지의 `DependencySetup_依赖库安装_win.bat`으로 한 번에 설치 가능) |
   | `OurNotes-Auto-<버전>-win-x64-MXU.zip` | [MXU](https://github.com/MistEO/MXU) | WebView2(Windows 10/11에는 보통 기본 포함) |

   두 UI 모두 [VC++ 2015–2022 런타임](https://aka.ms/vs/17/release/vc_redist.x64.exe)이 필요합니다.
2. `MFAAvalonia.exe` 또는 `mxu.exe`를 실행하고, 처음 열 때 나오는 사용 안내를 먼저 끝까지 읽으세요.
3. 연결 설정에서 「MuMu 에뮬레이터」와 사용할 인스턴스를 고릅니다(n번째 인스턴스의 adb 주소는 `127.0.0.1:16384+32n`).
4. 작업을 체크하고 옵션을 설정한 뒤 시작합니다. 게임은 홈 화면이나 프리 라이브 곡 선택 화면에 있으면 되고, 꺼져 있어도 됩니다. 「기록 요약」을 뺀 나머지 작업은 먼저 게임을 실행하고(이미 켜져 있으면 그대로 사용), 타이틀 화면·로그인 보너스·공지를 지나 게임에 들어갑니다.

사용 전 확인 사항:

- MuMu 에뮬레이터 12, 해상도 **1280×720**, 프레임 레이트 60 이상
- 국제 서버, 게임 언어는 简体中文, 繁體中文, English 또는 한국어(한국어는 작업 설정에서 「게임 언어」를 한국어로 선택해야 함), 또는 일본 서버(「게임 언어」를 「日本語 (일본 서버)」로 선택, 클라이언트는 Google Play에서 설치해야 함)
- 게임 내 「노트 속도」 **5.00**(「랜덤 GREAT」를 켰다면 반드시 유지. 다른 속도에서는 누르는 타이밍이 전체적으로 어긋나 GREAT가 GOOD이 됨). MV / 배경은 끄거나 어둡고 움직이지 않는 배경으로 변경. 이펙트, 연출 레벨은 낮추기를 권장
- 인터넷 연결: 채보는 실행 중에 채보 사이트 `assets.bdon.moe`에서 다운로드

작업: 게임 시작, 계정 전환(국제 서버의 bilibili 로그인 기록에 있는 계정, 비밀번호 불필요), 반복 플레이, LB 소진, 방치 플레이(LB를 다 쓰면 회복을 기다렸다가 계속 플레이), AP 채우기, 챌린지 라이브(이벤트 기간), 데일리 수령, 기록 요약. 각 작업과 옵션의 자세한 내용은 [사용 설명서](docs/ko/usage.md)를, 명령줄 사용법은 [명령줄](docs/ko/cli.md)을 참고하세요.

## 실측 결과와 알려진 제한

실측(MuMu 6.6.4, Android 15 인스턴스 4코어 6GB 60프레임, 사람처럼 치기 끔). EXPERT는 판마다 잰 게임 프레임레이트별로 나눠 집계했습니다:

| 게임 프레임레이트 | 판 수 | ALL PERFECT | FULL COMBO |
|---|---|---|---|
| 50프레임 이상(원활) | 1075 | 1056(98.2%) | 1060(98.6%) |
| 40~49프레임 | 202 | 187(92.6%) | 190(94.1%) |
| 40프레임 미만 | 126 | 111(88.1%) | 114(90.5%) |

원활할 때 플레이한 EXPERT 채보 48개는 모두 ALL PERFECT를 달성한 적이 있고, AP를 못 한 판도 평균 1~2개 판정만 놓쳤으며 대부분 가끔 나오는 MISS 하나입니다. EASY, NORMAL, HARD는 따로 154판이며 ALL PERFECT 150판(97%), FULL COMBO 152판입니다.

알려진 제한:

- 에뮬레이터가 버벅이면(프레임 드롭, 터치 지연 흔들림) 복잡한 곡에서 PERFECT를 몇 개 놓치거나 콤보가 끊길 수 있습니다. 동기화와 터치 모두 에뮬레이터가 제때 프레임을 내고 제때 터치를 받는 데 의존하므로, 한 번 버벅이면 어긋나며, 프레임레이트가 낮으면 AP율이 눈에 띄게 떨어집니다(위 표 참고). CPU를 차지하는 다른 프로그램을 끄고, 에뮬레이터에 CPU와 메모리를 충분히 할당하고, 게임의 이펙트와 연출 레벨을 낮추면 훨씬 나아집니다
- 프리 라이브와 챌린지 라이브(이벤트 기간)만 지원합니다. 멀티 라이브 등 다른 모드는 지원하지 않습니다
- 곡명 OCR이 가끔 틀리며, 인식에 실패하면 그 판은 포기하고 다음 곡으로 넘어갑니다
- 첫 노트가 아주 이른 곡(첫 노트가 화면 전환이 끝나기 전에 이미 화면에 들어오는 곡)은 동기화에 실패할 수 있으며, 이때는 일시정지하고 그 판을 재시도합니다
- 지금까지는 주로 MuMu + 국제 서버, 이펙트와 연출 레벨을 낮춘 설정에서 검증했습니다. 일본 서버에서는 프리 라이브, AP 채우기, 데일리 수령만 검증했습니다

## 문제 보고

[Issues](https://github.com/ChissaQAQ/OurNotes-Auto/issues)에 올려 주세요. 다음을 첨부하면 좋습니다:

- 무슨 일이 있었는지, 어떻게 되기를 기대했는지, 그리고 대략적인 시각(로그를 시간으로 찾습니다)
- 로그 `data/ournotes.log`. 실행할 때마다 처음에 이 도구의 버전, OS, CPU를 기록하고, 연결할 때 에뮬레이터 버전, 인스턴스의 CPU / 메모리 / 프레임 레이트, 해상도, 터치 방식, 게임 버전을 기록하므로 **기기 정보를 따로 적을 필요가 없습니다**. GUI를 쓴다면 `data/agent.log`도 첨부하세요. 파일이 너무 크면 문제가 생긴 실행의 시작 부분인 「OurNotes-Auto 버전 번호（…）」 줄부터 문제가 생긴 뒤까지만 잘라 내면 됩니다
- 로그에 언급된 스크린샷(오류가 나면 로그에 「截图 debug/nav/….png」라고 기록됩니다)
- 곡 플레이 문제: 곡명, 난이도, 가능하면 결과 화면 스크린샷
- 로그와 스크린샷에는 플레이어 닉네임, 친구 초대 코드가 들어 있을 수 있으니 올리기 전에 가려도 됩니다

## 라이선스

Copyright (C) 2026 ChissaQAQ

이 프로젝트는 [AGPL-3.0](LICENSE)(버전 3만 해당)으로 공개되며, AGPL-3.0 제7조에 따라 두 가지 추가 조항이 있습니다: 배포할 때 작성자 표시와 「무료 오픈 소스」 고지를 유지할 것, 수정판에는 수정했음을 표시할 것. 자세한 내용은 [이용 약관](TERMS_OF_SERVICE.md)(중국어) 제2조를 참고하세요. 이 소프트웨어를 사용하려면 이용 약관의 커뮤니티 규범과 면책 조항도 지켜야 합니다.

배포 패키지의 서드파티 구성 요소는 각자의 라이선스에 따라 배포됩니다. [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)를 참고하세요.

소프트웨어 아이콘(`docs/ui/icon.png`, `docs/ui/logo.ico`)은 《BanG Dream! It's MyGO!!!!!》 애니메이션 공식 사이트에서 가져온 것으로, 저작권은 ©BanG Dream! Project에 있으며 AGPL-3.0 허가 범위에 **포함되지 않습니다**. 이용 약관 2.5를 참고하세요.

## 감사의 말

- [autodori](https://github.com/EvATive7/autodori): 설계 참고(GPLv3). 이 프로젝트는 그 코드를 복사하지 않았습니다
- [bdon.moe](https://bdon.moe/) 채보 사이트, [haneoka.org](https://haneoka.org/) 국제 서버 곡명 데이터
- [MaaFramework](https://github.com/MaaXYZ/MaaFramework), [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)과 [MaaCommonAssets](https://github.com/MaaXYZ/MaaCommonAssets)(OCR 모델)
- GUI: [MFAAvalonia](https://github.com/SweetSmellFox/MFAAvalonia), [MXU](https://github.com/MistEO/MXU)
