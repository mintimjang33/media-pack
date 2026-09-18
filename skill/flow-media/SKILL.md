---
name: flow-media
description: 구글 Flow(Nano Banana 2 이미지 + Omni 동영상)로 어떤 편에서든 쓸 이미지·클립을 브라우저 자동화로 만든다. 이미지 0크레딧·동영상 4s=7크레딧. 앵커 참조로 룩 통일, 제목 고정으로 원본 지정. 트리거 "구글 flow로 이미지/영상 만들어줘 / flow 미디어 / 이 편 소재를 flow로 뽑아줘 / 이어서". ⚠️로컬 Krea2·H3 를 쓰는 편은 각 편 스킬 그대로.
---

# flow-media — 구글 Flow 이미지·클립 생성기 (편 무관)

**무엇을 대체하는가**: Krea2(로컬 이미지) + H3/Wan(로컬 클립) 자리에 그대로 끼운다.
GPU·모델 다운로드가 필요 없고, **이미지는 0크레딧**이다.

| | 엔진 | 크레딧 | 산출 |
|---|---|---|---|
| 이미지 | Nano Banana 2 | **0** | 768×1376(9:16) / 1376×768(16:9) JPEG |
| 동영상 | Gemini Omni | 4s=**7** · 6s=10 · 8s=12 · 10s=15 | 720×1280 · 24fps · 4.01초 mp4 |

무료 티어 일일 50크레딧 = **4초 클립 7개**. 실측으로 95초 한 편이 49크레딧에 나왔다
(`examples/` · `template/`).

## 구성

| 파일 | 역할 |
|---|---|
| `scripts/src/video/flow_kit.py` | 공용 키트 — 좌표·클릭·칩·팝오버·프롬프트·피커·제출·완료판정 |
| `scripts/flow_media.py` | 드라이버 — `flow.json` 하나로 이미지·클립을 순서대로 만든다 |
| `scripts/flow_fetch_media.py` | 생성물 회수(인증 쿠키 필요 → 페이지 내 fetch→청크) |
| `scripts/config/flow_ui_map.json` | 실좌표 + 함정 주석. 성공한 클릭이 자동 적재된다 |

## 절차

### 0. 크롬 준비
디버그 크롬을 9222 포트로 띄운다 — `chrome.exe --remote-debugging-port=9222 --user-data-dir=<빈 폴더>`. Flow 로그인은 **그 프로필에서 한 번** 해두면 남는다(RUNBOOK.md §0).

### 1. `flow.json` 을 프로젝트 폴더에 쓴다

```json
{
 "ratio": "9:16",
 "style": "공통 룩 한 문장 — 모든 이미지 프롬프트 앞에 붙는다",
 "ref_hint": "Match the reference image exactly for camera style, colour grade and overall look.",
 "images": [
  {"id": "IMG01", "anchor": true, "prompt": "..."},
  {"id": "IMG02", "prompt": "..."}
 ],
 "clips": [
  {"id": "CLIP01", "image_id": "IMG02", "seconds": 4, "res": "720p", "prompt": "..."}
 ]
}
```

- `anchor` 1장을 먼저 만들고 **나머지는 그 장을 참조로 물린다** → 룩이 통일된다.
- 클립은 `image_id` 로 원본을 가리킨다. 드라이버가 그 이미지의 **Flow 자동 제목**을
  상태 파일에서 찾아 피커에서 제목으로 고른다.
- ⚠️**클립 길이는 한 실행에 한 종류만** — 설정 검증이 크레딧 기대값 기준이다.

### 2. 실행 (단계 분리 · 재개 지원)

```bash
# 이미지만 (0크레딧). 처음엔 FLOW_LIMIT=1 로 앵커 1장 확인 후 전량
FLOW_JOB=<프로젝트>/flow.json FLOW_STAGE=images FLOW_LIMIT=1 \
  BU_CDP_URL=http://127.0.0.1:9222 PYTHONUTF8=1 browser-harness < scripts/flow_media.py

# 클립 (크레딧 소모 — 승인 후)
FLOW_JOB=<프로젝트>/flow.json FLOW_STAGE=clips \
  BU_CDP_URL=http://127.0.0.1:9222 PYTHONUTF8=1 browser-harness < scripts/flow_media.py
```

이미 만든 항목은 `_flow_state.json` 을 보고 건너뛴다. `FLOW_ONLY=IMG03` 으로 한 건만.

### 3. 회수

```bash
FLOW_OUT=<프로젝트>/output/clean FLOW_KIND=img \
  BU_CDP_URL=http://127.0.0.1:9222 PYTHONUTF8=1 browser-harness < scripts/flow_fetch_media.py
```

★**다운로드 목록은 최신 먼저**다. ID 매핑은 생성 역순이고, **콘택트 시트로 눈으로 교차확인**한다
(파일명·생성시각을 믿지 말 것 — 도구가 순서를 섞는다).

### 4. 승인 게이트

```
flow.json → 앵커 1장 검수 → 이미지 전량 검수(콘택트시트) → 클립 프롬프트·크레딧 확인
→ 클립 생성 → 클립 첫프레임 대조 → 후속 파이프라인(Remotion 등)
```

**크레딧을 쓰는 단계는 사람 승인 후에만.** 드라이버는 설정이 어긋나면 크레딧을 쓰기 전에 멈춘다.


## ★자동화 전용 브라우저를 따로 쓴다 (2026-09-04 실사고)

같은 크롬을 사람과 자동화가 나눠 쓰면 **자동화는 성공하는데 부수 피해가 조용히 난다.**

| 증상 | 원인 |
|---|---|
| 녹화본에 개인 화면이 찍힌다 | gdigrab 은 **화면 영역**을 찍는데 자동화는 **탭**을 조작한다 — 사용자가 딴 탭을 봐도 생성은 성공한다 |
| 사용자가 보던 탭이 Flow 로 이동한다 | `goto_url` 은 하니스의 **현재 탭**을 옮긴다. 그 탭은 사용자가 브라우징하면 흘러간다 |
| 피커 클릭이 빗나간다 | 창 크기가 달라지면 컨테이너 탐색이 페이지 루트까지 올라가 **뒤쪽 캔버스 카드**를 목록으로 착각한다 |

**해법**: 자동화 전용 크롬을 **다른 포트·다른 프로필**로 띄운다.

```bash
chrome --remote-debugging-port=9223 --user-data-dir=<전용 프로필>   --no-first-run --no-default-browser-check   --disable-backgrounding-occluded-windows --disable-renderer-backgrounding   --disable-background-timer-throttling https://labs.google/fx/ko/tools/flow
```

★**로그인 세션은 복사되지 않는다.** Chrome 127+ 의 앱 바운드 암호화가 쿠키를 프로필에 묶는다
(정보탈취 방지 기능 — 우회 대상이 아니다). **새 프로필에서 사람이 한 번 로그인**해야 한다.

★**`browser-harness --reload` 를 꼭 부른다.** 데몬이
① **파이썬 모듈을 캐시**해서 키트를 고쳐도 반영되지 않고,
② **이전 브라우저를 물고 있어** `BU_CDP_URL` 을 바꿔도 안 옮겨간다.

## ★녹화는 화면이 아니라 탭을 찍는다

`scripts/record_tab.py` — `Page.startScreencast` 로 **탭 자체**를 녹화한다. 화면에 뭐가 떠 있든
섞일 수 없다. gdigrab(`record_flow.py`)은 네이티브 앱(터미널·에디터) 촬영에만 쓴다.

```bash
.\.venv\Scripts\python scripts/record_tab.py --out <mp4> --cdp http://127.0.0.1:9223   --url-match labs.google --stdin scripts/flow_media.py -- browser-harness
```

- 백그라운드 탭은 Chrome 이 렌더를 얼려 **프레임이 0** 이다 → 대상 탭을 활성화하거나
  `--new-window <url>` 로 전용 창에 띄운다.
- CDP 프레임 핸들러는 **동기 함수**여야 한다. `async def` 로 두면 코루틴이 실행되지 않아
  프레임이 안 쌓인다(리더가 `h(params)` 로 그냥 부른다).

## ★프롬프트 규율

- **부정문을 쓰지 않는다.** `No text, no letters, no numbers` 를 넣으면 오히려
  `MEASURED SAG: 2.8m` 을 그렸다(실측). 없어야 할 것은 **아예 언급하지 않고**, 있어야 할 것만 적는다.
- 형태·질감을 같이 적는다. 재료 이름만 쓰면 다르게 알아듣는다.
- "walls close on both sides" 같은 표현은 **전경 벽**으로 그려져 화면을 막는다 —
  `receding into perspective, nothing in the foreground` 처럼 원근을 명시한다.
- 클립 프롬프트는 **첫 프레임 유지 + 느린 단일 카메라 이동** 한 줄이면 충분하다.
  `Live-action documentary footage, one continuous locked-off shot. The scene holds exactly
  as in the first frame. <카메라 이동 한 문장>`

## ★워터마크 (발행 전 확인)

Flow 산출물엔 ✦ 표식이 박힌다 — **이미지·영상 공통, 우하단 x 82~92% · y 88~95%**.
Google AI 표식이라 **지우지 않는다**(약관·표시의무). 가리려면 리프레임(`scale(1.14)` 상단 정렬)이
필요하고 그건 **사람 판단**이다. 발행 시 플랫폼의 합성 콘텐츠 고지도 체크한다.

## ★조용히 실패하는 지점 12개

전부 "성공했다고 보고하던" 실패다. 상세·근거는 `flow_kit.py` 머리말과
`scripts/config/flow_ui_map.json` 의 `notes`.

| # | 함정 |
|---|---|
| 1 | 합성 MouseEvent 무효 — **CDP Input** 만 통한다 |
| 2 | `offsetParent` 로 가시성 판정 → **position:fixed 요소가 사라진다**('새 프로젝트' 버튼) |
| 3 | 팝오버가 프롬프트창을 덮는다 → 입력이 조용히 증발(`isContentEditable=false`) |
| 4 | 입력창은 **Slate** — 자리표시자가 textContent 에 남아 길이 판정 금지 |
| 5 | 비우기 실패 시 **잔여 문구가 프롬프트를 지배**한다 → 빈 상태 확인 후 `startswith` 검증 |
| 6 | 참조는 프롬프트를 **먼저** 넣고 붙인다(비우기가 참조를 지운다) |
| 7 | 이미지 모드는 목록 클릭만으로 첨부 종료 — 확인 버튼 부재를 실패로 읽지 말 것 |
| 8 | 피커의 **큰 썸네일**을 누르면 편집화면 — 그 상태 제출은 새 생성이 아니라 편집 |
| 9 | 피커 위치 인덱스는 밀린다 → **제목으로 고정** |
| 10 | 완료 판정은 파일명이 아니라 `getMediaUrlRedirect` 고유 개수 |
| 11 | 비율 칩은 새 프로젝트에서 `crop_9_16` 이 없다 → **장수(x1~x4)** 로 칩을 찾는다 |
| 12 | Escape 로 피커가 닫히지 않는다 → 캔버스 **우측** 빈 곳 클릭(좌상단은 썸네일 자리) |

## 코드 규율 (이 계열에서 반복해 물린 것)

- **JS 를 담는 파이썬 리터럴은 전부 raw**(`r"""…"""`). 일반 문자열이면 `\r\n\t` 가 진짜
  제어문자가 되어 정규식 리터럴이 개행으로 쪼개진다.
- 같은 이유로 **bash heredoc 으로 JS 를 쓰지 않는다** — 파일로 쓴다.
- 문자열 조각을 이어 붙인 뒤 `%` 포맷하려면 **전체를 괄호로 묶는다**(`%` 가 `+` 보다 우선).

## 붙여 쓰는 곳

산출물이 그냥 이미지·클립 파일이라 **후속 파이프라인은 그대로 쓴다.**

| 편 | 붙는 자리 |
|---|---|
| engineering-shorts | CLEAN 이미지·조립 클립 → `cuts.json` → `EngShorts` |
| image-shorts(Mode-B) | 풀씬 배경 이미지 |
| paper-collage(Mode-D) · vox-explainer(Mode-E) | 씬 스틸·클립 후보 |
| scenic-longform(Mode-C) | 씬 이미지 |

⚠️ **한글 글자는 Flow 에 맡기지 않는다** — 화면의 한글·수치는 Remotion 합성이 원칙이다.
단 사진 안의 **실제 표지판**(도로명·건물명)은 Flow 가 제대로 렌더하며, 유지할지는 편별 판단.
