# flow-media — 구글 Flow로 영상 소재를 자동 생성하는 클로드 코드 스킬

이미지는 **0크레딧**, 4초 영상은 7크레딧. 무료 티어 하루 50크레딧으로
**95초 영상 한 편**이 실제로 나옵니다(실측 49크레딧).

이 팩은 브라우저 자동화로 Flow를 조작해
**① 룩이 통일된 이미지 여러 장 → ② 그 이미지로 만든 4초 클립들 → ③ 로컬 회수**까지 합니다.
산출물은 그냥 JPEG·MP4 파일이라, 뒤에 붙이는 편집 파이프라인은 쓰던 걸 그대로 쓰면 됩니다.

---

## 무엇이 들어 있나

```
skill/flow-media/SKILL.md            스킬 본문 — 절차·함정·프롬프트 규율
                                     → 쓰려면 프로젝트의 `.claude/skills/` 아래로 복사
scripts/flow_media.py                드라이버 — flow.json 하나로 이미지·클립 생성
scripts/flow_fetch_media.py          회수 — 인증 쿠키가 필요한 생성물을 로컬로
scripts/src/video/flow_kit.py        공용 키트 — 좌표·클릭·칩·프롬프트·피커·완료판정
scripts/config/flow_ui_map.json      실좌표 + 함정 주석(성공한 클릭이 자동 적재된다)
scripts/flow_download_clips.py       신 UI 영상 회수(다운로드 버튼 + CDP 저장경로)
scripts/record_tab.py                ★탭 전용 녹화 — 화면이 아니라 탭을 찍는다
scripts/record_flow.py               네이티브 창 녹화(터미널·에디터용, gdigrab)
scripts/voicestudio_make_profile.py  클론 보이스 프로필 재생성
examples/*.flow.json                 실제로 만든 두 편의 잡 파일
examples/WORKED-EXAMPLE.md           그 편의 전 과정과 실측 수치
```

---

## 설치

```bash
# 1) 스킬을 프로젝트(또는 ~/.claude)의 스킬 폴더로
mkdir -p .claude/skills && cp -r skill/flow-media .claude/skills/

# 2) 스크립트는 **레포 루트 기준 경로**를 그대로 쓴다(드라이버가 scripts/ 를 sys.path 에 넣는다)
cp -r scripts/* <내-프로젝트>/scripts/
```

★팩 안에 `.claude/skills/` 를 그대로 두지 않은 이유: 클로드 코드가 그걸 **중복 스킬로 자동 등록**해
라우팅이 헷갈립니다. 직접 복사하세요.

## 필요한 것

| 항목 | 내용 |
|---|---|
| **구글 계정** | [labs.google/fx/tools/flow](https://labs.google/fx/tools/flow) 접근 권한. 지역에 따라 미제공 |
| **크레딧** | 무료 티어 일일 50. 이미지는 0크레딧이라 무제한에 가깝다 |
| **Chrome** | 원격 디버깅 포트로 띄운 **전용 프로필** (아래 실행법) |
| **CDP 실행기** | `browser-harness` CLI, 또는 `js()`/`cdp()`를 주입해 파이썬을 exec하는 동등한 도구 |
| **Python** | 3.10+ · 본줄기 3개(`httpx` 음성 · `requests` 전사 · `websockets` 브라우저). 자막 타이밍은 `faster-whisper` 또는 `GROQ_API_KEY`, 창 녹화는 `pyautogui`·`pywinauto` 추가 |
| **디스크** | 이미지 ~150KB/장 · 4초 클립 ~2MB/개 |
| 선택 | Pillow (콘택트 시트로 눈 검수할 때) · ffmpeg (클립 검증) |

### 크롬 띄우기

```bash
chrome --remote-debugging-port=9222 \
  --user-data-dir=<전용 프로필 경로> \
  --no-first-run --no-default-browser-check \
  --disable-backgrounding-occluded-windows \
  --disable-renderer-backgrounding --disable-background-timer-throttling
```

그 창에서 Flow에 **한 번 로그인**해두면 프로필에 세션이 남습니다.
`--disable-backgrounding-*` 세 개가 없으면 창이 가려질 때 렌더러가 얼어 **합성 클릭이 조용히 드랍**됩니다.

---

## 쓰는 법

### 1. `flow.json`을 쓴다

```json
{
 "ratio": "9:16",
 "style": "모든 이미지 프롬프트 앞에 붙는 공통 룩 한 문장",
 "ref_hint": "Match the reference image exactly for camera style, colour grade and overall look.",
 "images": [
  {"id": "IMG01", "anchor": true, "prompt": "첫 장 — 이 편의 기준 룩"},
  {"id": "IMG02", "prompt": "두 번째 장 (앵커를 참조로 물린다)"}
 ],
 "clips": [
  {"id": "CLIP01", "image_id": "IMG02", "seconds": 4, "res": "720p",
   "prompt": "Live-action documentary footage, one continuous locked-off shot. The scene holds exactly as in the first frame. <카메라 이동 한 문장>"}
 ]
}
```

`anchor` 한 장을 먼저 만들고 나머지를 그 장에 물리는 게 **룩 통일의 전부**입니다.
클립은 `image_id`로 원본을 가리킵니다 — 드라이버가 그 이미지의 Flow 자동 제목을 기억해뒀다가
피커에서 제목으로 골라줍니다.

### 2. 이미지 (0크레딧)

```bash
# 먼저 앵커 1장만 만들어 룩을 확인한다
FLOW_JOB=<경로>/flow.json FLOW_STAGE=images FLOW_LIMIT=1 \
  BU_CDP_URL=http://127.0.0.1:9222 PYTHONUTF8=1 browser-harness < scripts/flow_media.py

# 룩이 맞으면 전량
FLOW_JOB=<경로>/flow.json FLOW_STAGE=images \
  BU_CDP_URL=http://127.0.0.1:9222 PYTHONUTF8=1 browser-harness < scripts/flow_media.py
```

### 3. 클립 (크레딧 소모 — 사람이 승인한 뒤에)

```bash
FLOW_JOB=<경로>/flow.json FLOW_STAGE=clips \
  BU_CDP_URL=http://127.0.0.1:9222 PYTHONUTF8=1 browser-harness < scripts/flow_media.py
```

### 4. 회수

```bash
FLOW_OUT=<경로>/output/images FLOW_KIND=img \
  BU_CDP_URL=http://127.0.0.1:9222 PYTHONUTF8=1 browser-harness < scripts/flow_fetch_media.py

FLOW_OUT=<경로>/output/clips FLOW_KIND=video \
  BU_CDP_URL=http://127.0.0.1:9222 PYTHONUTF8=1 browser-harness < scripts/flow_fetch_media.py
```

이미 만든 항목은 `_flow_state.json`을 보고 건너뜁니다. 중간에 끊겨도 다시 돌리면 이어서 합니다.

---

## ⚠️ 주의사항 — 이건 읽고 시작하세요

### 돈·계정

1. **크레딧을 쓰는 건 영상 단계뿐입니다.** 이미지는 0크레딧이니 마음껏 다시 뽑으세요.
   영상은 4s=7 · 6s=10 · 8s=12 · 10s=15. 하루 50이면 **4초 7개**가 상한입니다.
2. **본인 계정으로 본인 프로젝트만.** 남의 계정·공용 계정 자동화는 하지 마세요.
   동시 실행을 여러 개 띄우지 말고, 한 프로젝트씩 순차로 돌리세요.
3. 드라이버는 **설정이 어긋나면 크레딧을 쓰기 전에 멈춥니다**(칩·크레딧 기대값 검증).
   그래도 클립 단계는 사람이 한 번 보고 승인하는 걸 권합니다.

### 발행

4. **★Flow 산출물엔 ✦ 워터마크가 박힙니다** — 이미지·영상 공통, 우하단 x 82~92% · y 88~95%.
   Google의 AI 생성 표식이라 **지우지 마세요.** 약관 문제이기도 하고, 표시의무 쪽에서도 걸립니다.
   구도상 정말 걸리면 리프레임(위 정렬 `scale(1.14)`)으로 가릴 수는 있지만 그건 **사람이 판단할 일**입니다.
5. **플랫폼의 AI 생성 고지를 켜세요.** 유튜브는 업로드 시 "변경된 콘텐츠/합성 콘텐츠" 항목이 있습니다.

### 화면 글자

6. **한글을 Flow에 맡기지 마세요.** 화면의 한글 라벨·수치·자막은 편집 단계(예: Remotion)에서
   합성하는 게 원칙입니다. 생성모델은 오탈자를 내고, 고치려면 다시 뽑아야 합니다.
   단 사진 안의 **실제 표지판**(도로명·건물명)은 Flow가 제대로 렌더합니다 — 살릴지는 편별 판단.
7. **프롬프트에 부정문을 쓰지 마세요.** `No text, no letters, no numbers`를 넣었더니
   오히려 `MEASURED SAG: 2.8m`이라는 글자를 그렸습니다. **없어야 할 것은 언급하지 않고,
   있어야 할 것만 적습니다.**
8. `walls close on both sides` 같은 표현은 **전경 벽**으로 그려져 화면 40%를 막습니다.
   `receding into perspective, nothing in the foreground`처럼 원근을 명시하세요.

### 자동화

9. **크롬 창이 실제로 보입니다.** 무인 실행이 아니고, 그 창을 사람이 만지면 자동화가 어긋납니다.
   자동화 중에는 그 창을 건드리지 마세요.
10. **좌표는 보조 수단입니다.** 키트는 요소를 DOM(라벨·역할·아이콘)으로 찾고, 저장된 좌표는
    탐색이 실패했을 때의 폴백으로만 씁니다. 그래서 창 크기가 달라도 대체로 동작하지만,
    `flow_ui_map.json`의 좌표는 **1158×1304 뷰포트 기준**이라는 걸 알아두세요.
11. **Flow UI는 바뀝니다.** 라벨이 바뀌면 그 라벨을 쓰는 함수만 고치면 됩니다 —
    실패하면 `_shots/` 에 남는 스크린샷을 먼저 보세요. 단계마다 찍어둡니다.
12. 탭이 쌓이지 않게 **기존 Flow 탭을 재사용**합니다. 프로브를 여러 번 돌리면 탭이 쌓입니다.

### ★조용히 실패하는 지점 12개

전부 "성공했다고 보고하던" 실패입니다. 자동화를 직접 고칠 때 이 표부터 보세요.

| # | 함정 | 증상 |
|---|---|---|
| 1 | 합성 MouseEvent 무효 | 클릭이 아무 일도 안 함 — **CDP Input**만 통한다 |
| 2 | `offsetParent`로 가시성 판정 | **position:fixed 요소가 사라진다**('새 프로젝트' 버튼) |
| 3 | 팝오버가 프롬프트창을 덮는다 | 입력이 조용히 증발(`isContentEditable=false`) |
| 4 | 입력창이 **Slate** | 자리표시자가 textContent에 남아 길이 판정이 거짓 통과 |
| 5 | 비우기 실패 | **잔여 문구가 프롬프트를 지배** → 전혀 다른 그림이 나온다 |
| 6 | 참조를 먼저 붙였다 | 입력창 비우기가 **참조까지 지운다** → 프롬프트를 먼저 |
| 7 | 이미지 모드 첨부 | 목록 클릭만으로 끝 — 확인 버튼 부재를 실패로 읽으면 중단된다 |
| 8 | 피커의 큰 썸네일 클릭 | **편집화면**으로 나가고, 그 상태 제출은 새 생성이 아니라 편집 |
| 9 | 위치 인덱스로 원본 선택 | 선택 항목이 맨 앞으로 올라와 **인덱스가 밀린다** → 제목으로 고정 |
| 10 | 파일명으로 완료 판정 | 생성물엔 파일명이 없다 → `getMediaUrlRedirect` 고유 개수로 |
| 11 | `crop_9_16`으로 칩 탐지 | 새 프로젝트엔 비율이 없다 → **장수(x1~x4)**를 단 버튼이 설정 칩 |
| 12 | Escape로 피커 닫기 | 안 닫힌다 → 캔버스 **우측** 빈 곳 클릭(좌상단은 썸네일 자리) |

### 코드를 고칠 때

13. **JS를 담는 파이썬 리터럴은 전부 raw 문자열**(`r"""…"""`)로 쓰세요. 일반 문자열이면
    `\r` `\n` `\t`가 진짜 제어문자가 되어 JS 정규식 리터럴이 개행으로 쪼개집니다
    (`Invalid regular expression: missing /`).
14. 같은 이유로 **bash heredoc으로 JS를 쓰지 마세요** — 파일로 쓰세요.
15. 문자열 조각을 이어 붙인 뒤 `%` 포맷하려면 **전체를 괄호로 묶으세요**(`%`가 `+`보다 우선).

---


---

## ★Flow 도메인 이전 (2026-09) — 여기서 8군데가 조용히 깨졌다

`labs.google/fx/tools/flow` → **`flow.google.com`** 으로 옮겨가면서 자동화가 통째로 어긋났습니다.
**전부 "성공했다고 보고하던" 실패**라, 증상만 보면 원인을 못 찾습니다.

| # | 깨진 것 | 증상 | 대응 |
|---|---|---|---|
| 1 | 길이 라벨 `4s` → **`4초`** | 길이가 기본값(8초)인 채로 진행 | 두 라벨 다 시도 |
| 2 | 칩의 `data-state` 소멸 | 팝오버 열림 판정이 항상 거짓 | 장수 옵션(x3·x4) 가시성으로 판정 |
| 3 | 크레딧 문구가 조건부 | 검증이 통째로 무력화 | **칩 문자열**로 4항목(모드·비율·길이·장수) 검증 |
| 4 | **기본 장수 x2** | 한 번 제출에 **크레딧 2배**(14) | 장수를 명시 지정 |
| 5 | 피커 목록이 미리보기의 **형제** | 컨테이너 안에 항목 0개 | 문서 전역에서 "썸네일+짧은 제목+행 크기"로 탐색 |
| 6 | 목록 맨 아래 항목이 화면 밖 | 클릭이 피커 **바깥**을 눌러 그냥 닫힘 | `scrollIntoView` 후 **화면 안인지 확인하고** 클릭 |
| 7 | 미디어 URL 패턴 분화 | 생성이 끝났는데 **600초 대기 후 실패** | `getMediaUrlRedirect` + `/asb/` + `flow-content.google` |
| 8 | 그리드 가상화 | DOM 노드 수 ≠ 실제 미디어 수 | **완료 판정을 개수에서 제목 집합으로** |

★**완료 판정을 세지 마세요.** 이 계열에서 세 번 물렸습니다 — 파일명 → URL 패턴 → DOM 개수.
지금은 **카드 제목 집합의 증가**로 봅니다(생성물 제목은 프롬프트에서 자동 생성됩니다).

★**신 UI 는 그리드에 `<video>` 를 두지 않습니다.** 페이지 안 `fetch` 로 영상을 못 빼옵니다 —
상세의 **다운로드 버튼** + `Page.setDownloadBehavior` 로 받으세요(`flow_download_clips.py`).
그리고 편집 화면 URL(`/project/<id>/edit/<media>`)이 프로젝트 URL을 **접두어로 포함**하니,
"이미 도착했나" 검사는 **완전일치**로 하세요.

## ★사람과 자동화가 같은 브라우저를 쓰면 안 됩니다

가장 위험한 형태의 실패입니다 — **자동화는 성공하는데 부수 피해가 조용히 납니다.**

| 증상 | 원인 |
|---|---|
| 녹화본에 **개인 화면**이 찍힌다 | gdigrab 은 **화면 영역**을 찍는데 자동화는 **탭**을 조작한다 |
| 사용자가 보던 탭이 Flow 로 이동한다 | `goto_url` 은 하니스의 **현재 탭**을 옮긴다 |
| 피커 클릭이 빗나간다 | 창 크기가 달라지면 컨테이너 탐색이 어긋난다 |

실제로 25분 녹화본에 **개인 ChatGPT 대화 목록**이 들어갔습니다. 발행용 파일이었습니다.

**해법 둘을 같이 쓰세요.**

1. **자동화 전용 크롬**을 다른 포트·다른 프로필로 띄운다
   → ★로그인 세션은 복사되지 않습니다(Chrome 127+ 앱 바운드 암호화). 새 프로필에서 한 번 로그인하세요.
2. **녹화는 `record_tab.py`** — `Page.startScreencast` 로 **탭 자체**를 찍습니다.
   화면에 뭐가 떠 있든 물리적으로 섞이지 않습니다. gdigrab(`record_flow.py`)은 터미널·에디터 전용.

★`browser-harness --reload` 를 꼭 부르세요. 데몬이 ① **파이썬 모듈을 캐시**하고
② **이전 브라우저를 물고 있어** `BU_CDP_URL` 을 바꿔도 안 옮겨갑니다.

## ★TTS 검증은 `words.json` 이 아니라 독립 전사로

클론 TTS는 **레퍼런스 텍스트를 발화에 섞어 넣습니다.** 실제로 오디오 앞에 레퍼런스 대본 142자가
그대로 읽혔습니다. 그런데 **대본 단어는 전부 매칭되므로 정렬 검사는 "미정렬 0개"로 통과합니다.**

```
1차: 125초 · 일치율 87.0% · 누출 142자   ← ref_text 를 프로필에 넣었다
2차:  94초 · 일치율 96.9% · 누출 없음     ← 15초 레퍼런스 + 전사 없음
```

- 레퍼런스는 **5~20초**로 짧게. 60초짜리는 길어서 모델이 이어 읽습니다.
- **`ref_text` 를 넣지 마세요.** 넣으면 그걸 읽습니다.
- 합성 후 **독립 전사**로 대본과 대조하세요. 일치율 95% 미만이면 다시 뽑습니다.

## 처음부터 한 편 만들기

★**[RUNBOOK.md](RUNBOOK.md) 를 먼저 보세요.** 소재 게이트 → 대본 → 나레이션 →
이미지(0크레딧) → 클립(크레딧) → 컷 설계 → 리모션 렌더 → 조립까지 실제 명령으로 적혀 있습니다.

이 팩은 **소재만 만드는 게 아니라 한 편이 끝까지 나옵니다.**

| 층 | 담긴 것 |
|---|---|
| 소재 선정·대본 규격 | `skill/engineering-shorts/SKILL.md` · `playbook/` (소재 3조건 · 8구간 비율) |
| 음성 | `scripts/src/video/voicestudio_tts.py` · `template/build_tts.py` |
| 이미지·클립 | `scripts/flow_media.py` · `flow_kit.py` (이 팩의 본체) |
| 컷 설계 | `template/build_cuts.py` · `eng_subtitle.py` |
| 한글·수치·자막 | `remotion/` (자립 실행 가능한 최소 앱) |
| 조립·음량 | `template/finalize.py` (−14 LUFS) |

실물 예시 두 편의 `flow.json`·`cuts.json`·`script.txt` 가 `examples/`·`template/` 에 들어 있습니다.

## 안 들어 있는 것 (솔직하게)

- **편집·합성 파이프라인은 없습니다.** 이 팩은 소재(이미지·클립)까지만 만듭니다.
  워크드 예제에 쓰인 한글 자막·수치 오버레이·컷 편집은 Remotion 기반 별도 레인이고,
  그건 이 레포 내부 구조에 묶여 있어 그대로 떼어낼 수 없습니다.
  대신 어떤 층이 무엇을 담당했는지는 `examples/WORKED-EXAMPLE.md`에 적어뒀습니다.
- **나레이션(TTS)도 없습니다.** 예제는 별도 클론 음성을 썼습니다.
- Flow의 비공개 API를 쓰지 않습니다 — 사람이 누르는 것과 같은 경로(실제 클릭)만 씁니다.

## 라이선스·책임

- 이 팩의 코드는 자유롭게 쓰세요.
- **Flow의 이용약관·생성물 권리·지역 제한은 각자 확인하세요.** 이 팩은 그것을 대신 판단하지 않습니다.
- 워터마크 제거를 돕는 기능은 넣지 않았고, 넣을 계획도 없습니다.

### 2026-09-05 기준으로 확인한 것 (공식 문서)

- **하루 50크레딧은 구독과 무관합니다.** 결제 안 한 계정도 매일 50개를 받습니다.
  안 쓴 몫은 이월되지 않고, 갱신은 그날 첫 생성 시점 기준입니다.
- 이 팩이 쓰는 모델 = **Gemini Omni Flash 720p**. 공식 표의 `4s = 7크레딧` 과 실측이 일치합니다
  (이미지 12 + 클립 7 = 49크레딧). 이미지(Nano Banana 2)는 그 표에 없고 실측 0크레딧입니다.
- 🔴 **피크 시간(UTC 02~05시 = 한국 11~14시)에는 구독 없이 영상 생성이 안 될 수 있습니다.**
  원문: *"During peak hours, roughly 2 AM to 5 AM UTC, you may not be able to generate videos without a subscription."*
- **1080p 업스케일은 비구독 미지원**입니다. 다만 이 팩은 720p 클립을 받아 Remotion 이 1080 을 얹으므로
  영향이 없습니다. ([Flow 크레딧 관리](https://support.google.com/flow/answer/16526234))
- **생성물 소유권** — 구글 이용약관: *"Some of our services allow you to generate original content.
  Google won't claim ownership over that content."* 무료·유료를 나눠 상업 이용을 제한하는 조항은
  약관에서 찾지 못했습니다. ([Google 이용약관](https://policies.google.com/terms))
- ⚠️ **보이는 워터마크는 무료·Plus·Pro 티어에 붙습니다.** Ultra 만 (지역 규정상 필요할 때만) 예외입니다.
  모든 출력물에 보이지 않는 SynthID 도 들어갑니다.
- ⚠️ 위는 법률 자문이 아닙니다. **크게 쓰실 거면 최신 약관을 직접 확인하세요.** 정책은 자주 바뀝니다.
