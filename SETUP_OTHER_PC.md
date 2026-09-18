# 다른 PC에서 이어서 쓰는 법 (경제학 파이프라인 14번 — 씬 이미지 자동 생성)

> 이 문서는 `status_dashboard.py`(웹 대시보드) + `status_extension`(크롬 사이드패널) +
> `flow_econ_driver.py`(Flow 자동화 드라이버) 3종을 **새 PC에서 처음부터 켜는 법**만 다룬다.
> 배경·설계 이유는 HongHub `workflow_content`(경제학 파이프라인, 14번 단계) DB 문서 —
> "14번 실제 사용법 — 웹 대시보드 + 크롬 확장" 문단 — 에 있다.

## 0. 준비물

| 필요 | 확인 |
|---|---|
| Python 3.10+ | `python --version`. 패키지: `pip install httpx websockets` |
| Google Chrome | 최신 버전 |
| 이 저장소 | `git clone` (아직 GitHub에 안 올라가 있다면 폴더 통째로 복사) |

## 1. Supabase 접속 정보 설정

이 폴더에 `.env.local` 파일을 만든다(`.env.local.example`을 복사해서 값만 채우면 됨):

```
NEXT_PUBLIC_SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...
```

값은 HongHub·U-Short·U-OneShot 등 형제 프로젝트의 `.env.local`에서 그대로 복사해오면 된다(전부 같은
Supabase 프로젝트를 공유한다). **이 파일은 절대 커밋/공유하지 말 것** — `.gitignore`에 이미 제외돼 있다.

`status_dashboard.py`와 `flow_econ_driver.py` 둘 다 이 폴더(`flow-media-pack/.env.local`)를 먼저
찾고, 없으면 원래 이 PC에만 있던 `C:\Users\user\Downloads\U-Short\.env.local`로 대체한다
(2026-09-10 코드 수정 — 예전엔 U-Short 경로가 하드코딩돼 있어서 다른 PC에선 무조건 실패했다).

## 2. Flow 자동화 전용 크롬 계정 준비

계정마다 별도의 크롬 프로필+원격디버깅 포트를 쓴다. 필요한 만큼만 만들면 된다(1개만 있어도 동작함,
여러 개면 병렬로 빨라질 뿐).

| 계정 | 포트 |
|---|---|
| 첫 번째 계정 | 9223 |
| 두 번째 계정(선택) | 9224 |
| 세 번째 계정(선택) | 9225 |
| 네 번째 계정(선택) | 9226 |

각 계정을 켜는 명령(포트·프로필 경로·계정 이름만 바꿔서 반복):

```bat
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9223 --user-data-dir="C:\flow-automation-chrome-계정이름" --no-first-run --no-default-browser-check --disable-backgrounding-occluded-windows --disable-renderer-backgrounding --disable-background-timer-throttling https://flow.google.com/
```

**처음 켰을 때 반드시 그 창에서 사람이 직접 구글 계정으로 로그인**해야 한다 — 로그인은 자동화가
대신 못 한다(자격증명 입력 금지 원칙). 한 번 로그인해두면 그 프로필 폴더에 세션이 남아 다음부턴
자동으로 로그인된 채로 뜬다.

## 3. 크롬 확장(사이드패널) 설치

1. 크롬 주소창에 `chrome://extensions` 입력
2. 우측 상단 "개발자 모드" 켜기
3. "압축해제된 확장 프로그램을 로드합니다" 클릭
4. 이 저장소의 `scripts/status_extension` 폴더 선택
5. 설치되면 크롬 툴바에서 사이드패널로 열 수 있다(확장 아이콘 → 사이드패널 열기, 또는 브라우저의
   사이드패널 버튼)

## 4. 대시보드 서버 켜기

```bat
python scripts\status_dashboard.py 8799
```

떠 있는 상태로 두면 된다(창을 닫지 않는다). 크롬 확장 패널을 열면 `http://127.0.0.1:8799/`를
자동으로 불러온다 — 이 서버가 안 켜져 있으면 패널이 빈 화면(깨진 이미지 아이콘)으로 보인다.

매번 이 명령을 치기 번거로우면, 이 PC용으로 `.bat` 파일을 하나 만들어 바탕화면에 바로가기를
둬도 된다(원본 PC의 `scripts/대시보드_켜기.bat` + `scripts/autostart.ps1`이 그 예시).

## 5. 실제로 씬 이미지 생성하기

1. `http://127.0.0.1:8799` (사이드패널 또는 그냥 브라우저 탭)을 연다.
2. **워크플로우** → **콘텐츠** 드롭다운에서 대상 선택. (HongHub의 14번 패널에서 먼저 씬 프롬프트가
   등록돼 있어야 목록에 뜬다.)
3. **화면비율** 선택.
4. **계정(포트)** 선택 — 2번에서 켜둔 것 중 하나.
5. **"▶ 이 콘텐츠로 시작"** — 아직 이미지 없는 씬부터 순서대로 생성 시작.
6. 계정을 여러 개 켜뒀다면 "여러 계정 동시 시작"(범위 직접 지정) 또는 "대기 씬 자동 분배 시작"
   (자동 균등 배분)으로 병렬 처리 가능.
7. 완료된 씬은 자동으로 Supabase Storage에 업로드되고 그 씬의 `scenePrompts`에 등록된다 —
   HongHub 14번 패널에 바로 반영되며, 사람이 손으로 등록할 필요가 없다.

## 6. 문제가 생기면

- **패널이 빈 화면 / "127.0.0.1에서 연결을 거부했습니다"** → 4번(대시보드 서버)이 꺼져있는 것.
  다시 켜고 패널의 🔄 새로고침 버튼을 누른다.
- **"이 콘텐츠엔 등록된 씬 프롬프트가 없습니다(14번 단계 먼저 필요)"** → HongHub 14번 패널에서
  먼저 씬을 등록해야 한다.
- **"포트 XXXX에 이미 실행 중인 작업이 있습니다"** → 그 계정으로 이미 다른 생성이 돌고 있다는 뜻.
  끝날 때까지 기다리거나 "■ 지금 멈추기"로 중단.
- **그 외 세부 함정들**(컴포저 비우기, 새 프로젝트 셀렉터, 이미지 회수 방식 등) → 이 파일보다
  `RUNBOOK.md`/`README.md`, 그리고 HongHub `workflow_content` DB 문서의 "14번 도구 확정" 문단을
  참고할 것 — 실제 시행착오 이력이 거기 다 적혀있다.
