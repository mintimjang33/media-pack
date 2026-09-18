# 처음부터 공학 쇼츠 한 편 — 런북

> 이 문서만 따라가면 **소재 선정부터 완성 mp4 까지** 갑니다.
> 각 단계의 "왜 이렇게 하는가"는 `skill/engineering-shorts/SKILL.md` 와
> `playbook/engineering-shorts-production.md` 에 있습니다.

---

## 0. 준비물

| 필요 | 확인 |
|---|---|
| Python 3.10+ | `pip install httpx requests websockets` — 여기까지가 **본줄기**입니다.
  자막 타이밍을 로컬에서 뽑으려면 `pip install faster-whisper` 를 더 깔거나 `GROQ_API_KEY` 를 주세요(둘 다 없으면 4단계 끝에서 멈춥니다). 창 녹화(`record_flow.py`)를 쓸 때만 `pip install pyautogui pywinauto pywin32`(창 좌표를 읽는 `win32gui` 가 여기 들어 있습니다) |
| Node 20+ | `node --version` · 리모션(글자·수치·자막)이 여기서 돕니다 |
| ffmpeg | `ffmpeg -version` · 조립·음량 정규화 |
| 구글 계정 | 플로우 접속 + **영상 클립용 크레딧** (이미지는 0크레딧) |
| 디버깅 포트를 연 크롬 | 아래 명령 |
| CDP 를 다룰 도구 | 저는 browser-harness 를 씁니다 |

```bash
chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\bh-chrome
```

★**자동화 전용 크롬을 따로 띄우세요.** 평소 쓰는 크롬으로 돌리면 녹화본에 개인 화면이
섞입니다(실제로 겪었습니다 — README §"사람과 자동화가 같은 브라우저" 참고).

### 음성은 셋 중 하나

1. **직접 녹음** — 가장 확실합니다. `_tts/audio.mp3` 로 두면 그대로 씁니다.
2. **VoiceStudio(로컬)** — 아래 설치 절차. 공짜·무제한이고 인터넷도 안 탑니다.
3. **다른 TTS** — `voicestudio_tts.py` 는 OpenAI 호환 `/v1/audio/speech` 를 부릅니다.
   같은 규약이면 `VOICESTUDIO_URL` 만 바꿔 붙습니다.

---

## 0-1. VoiceStudio 설치 (로컬 음성)

> 영상으로 따라 하시려면: **https://youtu.be/OlE4mpwjiPU**

- 받는 곳: `github.com/debpalash/VoiceStudio` (구 OmniVoice-Studio · AGPL-3.0)
  ⚠️ **동명 레포가 셋입니다** — `msrbuilds/voice-studio`, `latentforge/VoiceStudio` 는 다른 물건입니다.

설치하면서 실제로 걸렸던 것들:

| 함정 | 실제 |
|---|---|
| **관리자 권한 필수** | MSI 가 `ALLUSERS=1` 이라 비관리자로 돌리면 **exit 1603 / Error 1925** 로 죽습니다 |
| 첫 실행이 멈춘 것처럼 보임 | 자동 초기화가 아니라 **설정 마법사가 떠서 대기**합니다. 10분을 기다린 적이 있습니다 |
| 전사·더빙을 쓰려면 | ASR 모델이 자동 설치되지 않습니다 — **2.9GB 추가 다운로드** |
| 설치 경로 | `C:\Program Files\VoiceStudio` (실행파일은 아직 `omnivoice-studio.exe`) |
| 백엔드 | `http://127.0.0.1:3900` · OpenAI 호환 · UI 는 한국어 자동 감지 |

⚠️ **seed 를 고정해도 결과가 매번 다릅니다**(길이가 10.0 / 9.26 / 8.93초로 갈렸습니다).
자동화에 붙일 때 "같은 입력 = 같은 파일"을 전제하지 마세요.

### 공개 음성 그대로 쓰기 (기본값 · 아무것도 안 해도 됩니다)

설치하면 딸려오는 보이스가 있어서 **프로필을 안 만들어도 소리가 납니다.**
기본값은 `alloy` 로 잡아뒀습니다. 목록은 앱을 켠 상태에서:

```bash
curl http://127.0.0.1:3900/v1/audio/voices
```

`type: openai_alias` 인 것들(alloy·echo·fable·nova·onyx·shimmer)이 공개 음성입니다.
바꾸려면 `VOICESTUDIO_VOICE=echo` 처럼 넘기면 됩니다.

### 내 목소리 프로필 만들기

```bash
python scripts/voicestudio_make_profile.py --name "my-clone" --ref my_voice_20s.wav
```

- 레퍼런스는 **5~20초로 짧게**. 60초짜리는 모델이 이어 읽습니다.
- ⛔**`--ref-text`(전사)를 넣지 마세요.** 넣으면 그 문장을 발화에 섞습니다 —
  실측으로 오디오 앞에 레퍼런스 대본 **142자**가 그대로 읽혔고, 단어 정렬 검사는 통과했습니다.
- 나온 `voice_id` 를 `VOICESTUDIO_VOICE` 로 넘겨 씁니다.
- ⛔남의 `voice_id` 는 내 PC 에 없습니다 — 목록에 없는 ID 를 주면 소리가 안 납니다.

---

## 1. 폴더 만들기

```
my-episode/                 ← 한 편 = 폴더 하나
  build_tts.py              template/ 에서 복사
  build_cuts.py             template/ 에서 복사
  finalize.py               template/ 에서 복사
  script.txt                당신이 씁니다
  flow.json                 당신이 씁니다
```

팩 위치를 알려줍니다(안 주면 스크립트 상위를 팩으로 봅니다).

```bash
set FLOW_PACK=C:\path\to\flow-media-pack
```

폴더 이름이 곧 슬러그입니다 — 리모션 자산은 `remotion/public/engshorts/my-episode/` 로 갑니다.

---

## 2. 소재 게이트 ★여기서 대부분이 결정됩니다

세 조건을 **전부** 넘어야 합니다(`playbook/engineering-shorts-production.md`).

1. 설명 없이 이름만 대도 아는 대상인가
2. 상식적 해법이 **실제로 실패**하는가 — 물리적 이유가 있는가
3. 진짜 해법이 **직관에 반하는가** — "~ 대신 ~한다" 한 줄이 나오는가

하나라도 안 되면 소재를 바꾸세요. 여기서 타협하면 뒤가 전부 낭비입니다.

---

## 3. 대본 — 650자 안팎, 8구간 비율

훅 9% · 문제 15 · 1차 해법 16 · 새 문제 15 · 반박 선점 6 · 발상 전환 10 ·
작동 원리 14 · 클로징 15. 수치는 **1차 출처에서만**, 없으면 정성적 표현으로.

`template/script.txt` 가 실제로 발행한 편의 대본입니다 — 길이와 호흡의 기준으로 보세요.

---

## 4. 나레이션 먼저 ★장면표보다 먼저입니다

```bash
python build_tts.py
```

산출 `_tts/audio.mp3`. **실제 초를 재고 나서** 컷을 짭니다.
추정 95초로 짜놨다가 음성이 125초로 나오면 장면표가 통째로 밀립니다.

합성 후 **독립 전사로 대조**하세요. 클론 음성은 레퍼런스 대본을 발화에 섞어 넣는데
정렬 검사로는 안 잡힙니다(README §TTS 검증).

---

## 5. 이미지 — 0크레딧

`flow.json` 을 씁니다(`examples/progressive-lens.flow.json` 이 실물 예시).

```jsonc
{
  "ratio": "9:16",
  "look": "모든 이미지에 공통으로 붙일 룩 한 문장",
  "images": [
    {"id": "anchor", "anchor": true, "prompt": "..."},
    {"id": "s02", "prompt": "..."}
  ],
  "clips": [{"id": "CLIP01", "from": "anchor", "prompt": "..."}]
}
```

```bash
set FLOW_JOB=flow.json
set FLOW_STAGE=images
set FLOW_LIMIT=1                     # ★처음엔 앵커 한 장만
browser-harness < scripts/flow_media.py
```

앵커 룩이 마음에 들면 `FLOW_LIMIT` 를 빼고 전부 뽑습니다. 0크레딧이니 몇 번이든 다시 뽑으세요.

⛔**부정문을 쓰지 마세요.** "글자 넣지 마"라고 쓰면 오히려 글자를 그립니다.
없어야 할 건 아예 언급하지 않습니다.

---

## 6. 클립 — 여기서만 크레딧이 나갑니다

```bash
set FLOW_STAGE=clips
browser-harness < scripts/flow_media.py
```

4초 = 7크레딧. 드라이버가 **제출 전에 설정(모드·비율·길이·장수)을 검증**하고
어긋나면 크레딧을 쓰기 전에 멈춥니다. 새 계정은 기본 장수가 2라 두 배가 나갑니다.

회수:

```bash
python scripts/flow_fetch_media.py       # 이미지
python scripts/flow_download_clips.py    # 클립(신 UI)
```

---

## 7. 컷 설계 → cuts.json

`build_cuts.py` 를 **당신 편에 맞게 고칩니다**. 이 파일이 저작물입니다 —
어느 컷에 어떤 그림을 쓰고, 어떤 수치·화살표·라벨을 얹을지.

```bash
python build_cuts.py
```

산출 `cuts.json` + 자산이 `remotion/public/engshorts/<슬러그>/` 로 복사됩니다.

규칙 몇 개:
- **자막은 언제나 한 줄** — 줄 수가 바뀌면 자막 덩어리가 출렁여 읽는 눈이 끊깁니다
- 클립은 원본 5초 중 **끝을 4.6초에 맞춰** 잘라 씁니다(조립이 덜 끝난 채 잘리는 것 방지)
- 그래픽은 컷당 2~3개, 글자는 라벨 1~3개 + 수치 0~1개

---

## 8. 렌더

```bash
cd remotion
npm install                      # 최초 1회
npx remotion render src/index.ts EngShorts out.mp4 --props=../../my-episode/cuts.json
```

한글 라벨·수치·화살표·자막이 전부 여기서 얹힙니다. 오탈자는 글자만 고쳐 다시 렌더하면
됩니다 — 영상을 다시 뽑을 필요가 없습니다.

---

## 9. 조립

```bash
python finalize.py
```

무음 영상 + 나레이션을 합치고 **−14 LUFS** 로 정규화합니다. 산출 `output/final.mp4`.

---

## 안 들어 있는 것 (솔직하게)

- **썸네일 생성** — 별도 도구입니다
- **카라오케(단어 싱크) 자막** — `word_align.py` 의 그 경로는 팩에 없는 모듈을 부릅니다.
  팩의 자막은 컷 단위 한 줄 자막입니다(리모션이 그립니다).
- **업로드 자동화**
- **제 목소리** — 클론 프로필은 각자 만들어야 합니다

## 이 자동화는 언젠가 깨집니다

화면을 보고 누르는 방식이라 구글이 버튼 하나만 옮겨도 어긋납니다. 실패하면 스크린샷이
자동 저장되니 그 자리만 고치세요. 어디가 왜 깨졌었는지는 README 의 함정 표에 있습니다.
