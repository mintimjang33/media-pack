"""경제학 파이프라인 전용 Flow 드라이버 — 2026-09-06 신UI 대응.

flow-media-pack 은 저자가 만든 시점의 구 Flow UI(하단 "이미지/동영상" 모드 칩)를 기준으로
짜여 있는데, 2026-09-06 기준 Flow가 "에이전트 채팅" UI로 전면 개편되어 그 칩이 없어졌다
(공지 배너: "Faster loading, simpler login, and more! ... all URLs are now flow.google.com").
이 파일은 새 UI를 직접 실측해서 다시 짠 것 — cdp_harness.py 위에서 돈다.

새 UI 요약(실측, 2026-09-06):
    - 프로젝트 진입: 홈에서 "새 프로젝트" 버튼(텍스트 매칭)을 누르면 새 프로젝트로 들어간다
      (이 부분은 구 UI와 동일하게 동작한다 — 안 바뀜).
    - 화면 하단 컴포저: contenteditable 입력창 + 아이콘 4개(add · article_spark · tune · arrow_forward).
      * add(+)      → 좌측에서 기존 애셋을 검색해 "프롬프트에 추가"로 참조 첨부(앵커 일관성용)
      * tune(설정)  → 우측 "에이전트 설정" 패널. 이미지/동영상 각각 비율·배치수·모델을 여기서
                      **프로젝트당 한 번** 정해두면 이후 매 생성에 적용된다(구 UI처럼 매번
                      팝오버를 열 필요가 없다 — 오히려 더 단순해졌다).
      * arrow_forward → 제출. 이미지는 즉시 생성 시작(0크레딧이라 별도 확인이 없다).
        "생성 전 항상 확인" 설정이 켜져 있으면(기본값) **동영상처럼 크레딧이 드는 건** 제출 후
        확인 카드가 뜰 가능성이 높다(2026-09-06 시점엔 이미지만 실측, 클립은 미실측 — 아래 참고).
    - 완료 판정: 생성 중엔 타일에 "N%" 텍스트가 뜬다. 사라지고 naturalWidth>400 인 <img> 가
      늘어나면 완료. (구 UI의 getMediaUrlRedirect 패턴은 이제 flow-content.google 도메인으로
      바뀌었다 — img.src 로 직접 판별한다.)
    - 회수(가장 큰 변경점): 다운로드 버튼(⬇)을 눌러도 Browser.setDownloadBehavior 를
      브라우저 레벨 CDP로 잡아도 실측상 로컬에 파일이 안 떨어졌다(원인 미상 — 새 UI의 다운로드가
      Service Worker/Blob 경유일 가능성). in-page fetch() 도 flow-content.google 이 CORS를
      막아 실패한다(`Failed to fetch`). Page.getResourceContent 도 "리소스 없음"으로 실패.
      **그래서 이 드라이버는 회수를 `Page.captureScreenshot` 의 `clip` 파라미터로 이미지
      엘리먼트의 화면 좌표만 잘라내는 방식으로 한다** — CORS/다운로드 메커니즘을 아예 안 타서
      항상 먹힌다. 단점: 뷰포트 렌더 해상도 한계(대략 1000px 폭 표시 → clip.scale=2 정도가
      실용적 상한, 그 이상은 그냥 업스케일이라 의미 없다). Remotion 합성 파이프라인엔 이 정도
      해상도로 충분하다(최종 출력이 1080p 세로/가로인 롱폼 컷 배경 이미지 용도).

⚠️ 동영상 클립 생성은 이 파일에서 코드만 만들어뒀고 **아직 실기 테스트를 안 했다** —
   클립은 크레딧이 실제로 나가기 때문에(4초=7크레딧) 사람 승인 없이 먼저 써보지 않았다.
   처음 쓸 때는 반드시 FLOW_STAGE=clips 를 사람이 명시적으로 확인한 뒤 실행할 것.

사용 예 (flow-media-pack 폴더에서):
    BU_CDP_URL=http://127.0.0.1:9223 FLOW_JOB=<프로젝트>/flow.json \
      FLOW_STAGE=images PYTHONUTF8=1 python scripts/flow_econ_driver.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from cdp_harness import CDP  # noqa: E402


def log(*a):
    print(*a, flush=True)


# 2026-09-11 추가 — 사용자가 Flow 프로젝트 화면에서 직접 확인: 우리가 배치수를 1로 설정한
# 적이 없는데도, Flow 자체의 대화형 에이전트가 프롬프트를 받고 "I'm going to generate two
# images of..."처럼 스스로 여러 장을 만들겠다고 창작적으로 판단하는 경우가 있었다(사용자
# 지적: "왜 지멋대로 막 이렇게 여러개를 만드는거야?"). capture_newest_images(n=1)이 그중
# 최신 1장만 가져가서 씬-이미지 매칭 자체는 안 깨지지만, 생성 시간이 배로 걸리고 프로젝트
# 갤러리에 안 쓰는 이미지가 계속 쌓인다. 제출하는 프롬프트 맨 앞에 "정확히 1장만 만들어라"는
# 지시를 명시적으로 박아 넣어 이 창작적 이탈을 줄인다.
def image_count_instruction(n: int) -> str:
    """2026-09-11 추가 — 대시보드 패널에 체크박스(켜고 끄기)로 먼저 만들었다가, 사용자가
    "드롭으로 사진생성수량 선택하게 만들라고"라고 재지시해서 드롭다운(1~4장, 기본 1장)으로
    바꿨다. job.json의 image_count 값을 그대로 숫자로 박아 넣어 Flow 에이전트가 스스로
    "이미지 두 장 만들어볼게요" 식으로 창작적으로 이탈하지 않게 한다."""
    word = "ONE" if n == 1 else str(n)
    plural = "" if n == 1 else "s"
    return (
        f"Generate exactly {word} single image{plural} for this prompt. Do not create additional "
        f"variations, alternate angles, or extra candidate images beyond this exact count."
    )


# ── 2026-09-10 추가 — 생성 완료된 이미지를 Storage에 올리고, 그 씬의 scenePrompts
# (- 장면이미지: 줄)에 자동으로 등록한다. "수동은 문제 있을 때만, 평소엔 자동으로 등록돼야
# 다음 단계로 진행하지"라는 요청으로 추가 — HongHub 화면(14번 패널)에도 바로 반영된다.
_SUPA_ENV = None


def _supa_env():
    """2026-09-10 추가 — 다른 PC에서도 쓸 수 있도록, 이 팩 자체의 `.env.local`을 먼저 찾고
    (SETUP_OTHER_PC.md 참고), 없으면 이 PC에만 있던 예전 경로(U-Short 프로젝트)로 대체한다."""
    global _SUPA_ENV
    if _SUPA_ENV is None:
        candidates = [
            Path(__file__).resolve().parent.parent / ".env.local",
            Path(r"C:\Users\user\Downloads\U-Short\.env.local"),
        ]
        env_path = next((p for p in candidates if p.exists()), None)
        if env_path is None:
            raise FileNotFoundError(
                "Supabase .env.local을 찾을 수 없습니다 — flow-media-pack/.env.local을 만드세요"
                "(SETUP_OTHER_PC.md, .env.local.example 참고)."
            )
        env = {}
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
        _SUPA_ENV = {"url": env["NEXT_PUBLIC_SUPABASE_URL"], "key": env["SUPABASE_SERVICE_ROLE_KEY"]}
    return _SUPA_ENV


LOCK_DIR = Path(os.environ.get("TEMP", ".")) / "flow_econ_locks"


class _SiteLock:
    """2026-09-10 추가 — 여러 Flow 창(계정)을 동시에 돌리면 각자 register_scene_image()가
    같은 hub_sites 행의 script_draft를 '통째로 읽고 통째로 PATCH'하는 방식이라, 두 프로세스가
    비슷한 시점에 겹치면 나중에 쓴 쪽이 먼저 쓴 쪽의 등록 내용을 덮어써버리는 유실 위험이 있다
    (병렬 처리를 시작하며 예견됨). 파일 잠금으로 site_id 단위 DB 읽기~쓰기 구간을 직렬화한다
    — 실제 이미지 생성(수 분)은 그대로 병렬이고, DB 등록(수백ms)만 줄을 선다."""

    def __init__(self, site_id: str, timeout: float = 60.0):
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        self.path = LOCK_DIR / f"{site_id}.lock"
        self.timeout = timeout
        self.fd = None

    def __enter__(self):
        t0 = time.time()
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                if time.time() - t0 > self.timeout:
                    # 죽은 프로세스가 잠금을 놓지 않고 죽었을 가능성 — 강제로 넘어간다.
                    try:
                        self.path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                time.sleep(0.2)

    def __exit__(self, *a):
        if self.fd is not None:
            os.close(self.fd)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def register_scene_image(site_id: str, unit_id: str, scene_id: str, local_path: Path) -> str | None:
    env = _supa_env()
    headers = {"apikey": env["key"], "Authorization": f"Bearer {env['key']}"}
    ext = local_path.suffix.lstrip(".") or "jpg"
    ctype = "image/png" if ext == "png" else "image/jpeg"
    storage_path = f"scene-images/{unit_id}/{scene_id}.{ext}"
    up = httpx.post(
        f"{env['url']}/storage/v1/object/honghub-files/{storage_path}",
        headers={**headers, "Content-Type": ctype, "x-upsert": "true"},
        content=local_path.read_bytes(), timeout=60,
    )
    up.raise_for_status()
    public_url = f"{env['url']}/storage/v1/object/public/honghub-files/{storage_path}"

    # 2026-09-10 추가 — 여러 창(계정)이 동시에 같은 사이트에 등록할 수 있어서, 읽기~쓰기
    # 구간 전체를 파일 잠금으로 감싼다(아래 _SiteLock 참고) — 안 그러면 나중에 쓴 쪽이
    # 먼저 쓴 쪽의 등록을 덮어써서 유실된다.
    with _SiteLock(site_id):
        r = httpx.get(f"{env['url']}/rest/v1/hub_sites", params={"id": f"eq.{site_id}", "select": "script_draft"},
                      headers=headers, timeout=30)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            log(f"  ⚠ Storage 업로드는 됐지만 site({site_id})를 못 찾아 scenePrompts 등록 실패")
            return None
        script_draft = rows[0].get("script_draft") or {}
        units = script_draft.get("units") or []
        unit = next((u for u in units if u.get("id") == unit_id), None)
        if not unit:
            log(f"  ⚠ Storage 업로드는 됐지만 unit({unit_id})을 못 찾아 scenePrompts 등록 실패")
            return None

        text = unit.get("scenePrompts") or ""
        blocks = re.split(r"\n(?=### \S+)", text.strip()) if text.strip() else []
        changed = False
        new_blocks = []
        for b in blocks:
            if re.match(rf"### {re.escape(scene_id)}(\s|$)", b):
                lines = b.split("\n")
                lines = [ln for ln in lines if not ln.startswith("- 장면이미지:")]
                insert_at = 1
                for i, ln in enumerate(lines[1:], start=1):
                    if ln.startswith("- 시간:"):
                        insert_at = i + 1
                lines.insert(insert_at, f"- 장면이미지: {public_url}")
                new_blocks.append("\n".join(lines))
                changed = True
            else:
                new_blocks.append(b)
        if not changed:
            log(f"  ⚠ Storage 업로드는 됐지만 scenePrompts 안에서 {scene_id} 블록을 못 찾음")
            return None

        unit["scenePrompts"] = "\n\n".join(new_blocks)
        patch = httpx.patch(
            f"{env['url']}/rest/v1/hub_sites", params={"id": f"eq.{site_id}"},
            headers={**headers, "Content-Type": "application/json"},
            content=json.dumps({"script_draft": script_draft}).encode("utf-8"), timeout=30,
        )
        patch.raise_for_status()
    log(f"  ✓ {scene_id} 장면이미지 홍허브에 자동 등록: {public_url}")
    return public_url


def register_scene_video(site_id: str, unit_id: str, scene_id: str, local_path: Path) -> str | None:
    """register_scene_image()와 완전히 같은 흐름(Storage 업로드 → scenePrompts 등록)이지만
    "- 장면영상:" 줄(HongHub 2026-09-13 sceneVideo 필드)에 쓰고, HongHub utils.ts의 필드
    순서(시간→장면이미지→장면영상→이미지프롬프트...)에 맞춰 "- 장면이미지:"(없으면 "- 시간:")
    바로 뒤에 끼워넣는다."""
    env = _supa_env()
    headers = {"apikey": env["key"], "Authorization": f"Bearer {env['key']}"}
    ext = local_path.suffix.lstrip(".") or "mp4"
    ctype = "video/webm" if ext == "webm" else "video/mp4"
    storage_path = f"scene-videos/{unit_id}/{scene_id}.{ext}"
    up = httpx.post(
        f"{env['url']}/storage/v1/object/honghub-files/{storage_path}",
        headers={**headers, "Content-Type": ctype, "x-upsert": "true"},
        content=local_path.read_bytes(), timeout=120,
    )
    up.raise_for_status()
    public_url = f"{env['url']}/storage/v1/object/public/honghub-files/{storage_path}"

    with _SiteLock(site_id):
        r = httpx.get(f"{env['url']}/rest/v1/hub_sites", params={"id": f"eq.{site_id}", "select": "script_draft"},
                      headers=headers, timeout=30)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            log(f"  ⚠ Storage 업로드는 됐지만 site({site_id})를 못 찾아 scenePrompts 등록 실패")
            return None
        script_draft = rows[0].get("script_draft") or {}
        units = script_draft.get("units") or []
        unit = next((u for u in units if u.get("id") == unit_id), None)
        if not unit:
            log(f"  ⚠ Storage 업로드는 됐지만 unit({unit_id})을 못 찾아 scenePrompts 등록 실패")
            return None

        text = unit.get("scenePrompts") or ""
        blocks = re.split(r"\n(?=### \S+)", text.strip()) if text.strip() else []
        changed = False
        new_blocks = []
        for b in blocks:
            if re.match(rf"### {re.escape(scene_id)}(\s|$)", b):
                lines = b.split("\n")
                lines = [ln for ln in lines if not ln.startswith("- 장면영상:")]
                insert_at = 1
                for i, ln in enumerate(lines[1:], start=1):
                    if ln.startswith("- 시간:") or ln.startswith("- 장면이미지:"):
                        insert_at = i + 1
                lines.insert(insert_at, f"- 장면영상: {public_url}")
                new_blocks.append("\n".join(lines))
                changed = True
            else:
                new_blocks.append(b)
        if not changed:
            log(f"  ⚠ Storage 업로드는 됐지만 scenePrompts 안에서 {scene_id} 블록을 못 찾음")
            return None

        unit["scenePrompts"] = "\n\n".join(new_blocks)
        patch = httpx.patch(
            f"{env['url']}/rest/v1/hub_sites", params={"id": f"eq.{site_id}"},
            headers={**headers, "Content-Type": "application/json"},
            content=json.dumps({"script_draft": script_draft}).encode("utf-8"), timeout=30,
        )
        patch.raise_for_status()
    log(f"  ✓ {scene_id} 장면영상 홍허브에 자동 등록: {public_url}")
    return public_url


STATUS_PATH: Path | None = None


def write_status(**fields):
    """대시보드(scripts/status_dashboard.py)가 폴링하는 상태 파일을 갱신한다.
    2026-09-10 추가 — 사용자가 크롬 옆에서 진행 상황을 직접 볼 수 있게 해달라고 요청해서,
    로그 파일 대신 매 단계마다 이 JSON을 갱신하고 대시보드가 2초 간격으로 읽어간다."""
    if STATUS_PATH is None:
        return
    try:
        cur = json.loads(STATUS_PATH.read_text(encoding="utf-8")) if STATUS_PATH.exists() else {}
    except Exception:
        cur = {}
    cur.update(fields)
    cur["updated_at"] = time.time()
    STATUS_PATH.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")


def stop_requested(here: Path) -> bool:
    return (here / "stop.flag").exists()


CLEANUP_EVERY = 5  # 씬 몇 개마다 찌꺼기를 청소할지


def cleanup_debris(here: Path):
    """2026-09-10 추가 — "이미지 몇 개 하고 중간중간 찌꺼기 정리하고 다시 진행" 요청.
    돌아가는 동안 쌓이는 건 사실상 타임아웃 스크린샷(_shots/*_TIMEOUT.png)뿐이다 — 실제
    결과물(output/images/*)은 절대 안 지운다. 이미 지나간 씬의 타임아웃 스샷은 성공했든
    실패했든 더 이상 쓸모없으니 주기적으로 비운다."""
    # 2026-09-11 수정 — 디버그용 스샷(mention_fail_*, dbg_*)까지 매 씬마다 통째로 같이
    # 지워져서, 실패 원인을 조사하려 할 때마다 증거가 이미 없어져 있었다. 이 접두어들은
    # 사람이 직접 지울 때까지 남겨둔다.
    shots_dir = here / "_shots"
    if not shots_dir.exists():
        return
    keep_prefixes = ("mention_fail_", "dbg_")
    removed = 0
    for f in shots_dir.glob("*"):
        if f.is_file() and not f.name.startswith(keep_prefixes):
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    if removed:
        log(f"  🧹 찌꺼기 정리: 타임아웃 스크린샷 {removed}개 삭제")


class EconFlow:
    def __init__(self, cdp: CDP, shots: Path | None = None):
        self.c = cdp
        self.shots = shots
        if self.shots:
            self.shots.mkdir(parents=True, exist_ok=True)
        # 2026-09-11 추가 — 직전에 저장한 씬의 src와 비교해 "아직 안 바뀐 이미지를 또
        # 캡처하는" 레이스 컨디션(S03/S04, S09/S12, S10/S11, S13/S14 실사고, 완전히
        # 동일한 파일이 서로 다른 씬 번호로 저장됨)을 잡아내는 데 쓴다.
        self.last_srcs: list[str] = []
        # 2026-09-13 추가 — capture_newest_videos()용, last_srcs와 같은 역할(레이스
        # 컨디션 방지)이지만 이미지/영상을 분리 추적해야 한다 — 같은 프로세스가 이미지
        # 캡처 직후 영상 캡처를 하면 last_srcs(이미지 src)와 비교해봐야 의미가 없다.
        self.last_video_srcs: list[str] = []

    # ── 저수준 클릭 ──────────────────────────────────────────
    def click_xy(self, x, y):
        self.c.cdp("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y, buttons=0)
        time.sleep(0.15)
        self.c.cdp("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y,
                   button="left", buttons=1, clickCount=1)
        time.sleep(0.08)
        self.c.cdp("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y,
                   button="left", buttons=0, clickCount=1)

    def type_text(self, text: str):
        for ch in text:
            self.c.cdp("Input.dispatchKeyEvent", type="char", text=ch)

    def _composer_text_len(self) -> int:
        """컴포저(하단 40% 안의 contenteditable/textarea) 안 텍스트 길이. 못 찾으면 -1."""
        r = self.c.js(r"""(()=>{const H=innerHeight;
          const e=[...document.querySelectorAll("[contenteditable=true],textarea")]
            .filter(x=>x.offsetParent && x.getBoundingClientRect().top>H*0.6)[0];
          if(!e) return -1;
          return (e.innerText||e.value||"").length;})()""")
        try:
            return int(r)
        except (TypeError, ValueError):
            return -1

    def paste_text(self, text: str):
        """2026-09-12 추가 — 사용자 지적 + 홍허브 벤치마킹 항목(nam-ai-trend/7_threads_auto)
        확인: "타이핑은 클립보드 복사+Cmd/Ctrl+V로 붙여넣어 봇탐지 회피"가 실측된 방식이다.
        한 글자씩 dispatchKeyEvent(type="char")로 타이핑하는 지금 방식은 (1) 씬이 쌓일수록
        "거절"이 나기 시작했다는 사용자 관찰, (2) "@이름" 멘션이 어떨 때는 붙고 어떨 때는
        그냥 텍스트로 남는 불안정(S19/S34/S66 등)과 둘 다 관련 있을 수 있다.

        2026-09-12 (4차) 실사고 수정 — 최초 구현은 클립보드에 쓴 뒤 CDP
        Input.dispatchKeyEvent로 Ctrl+V "키 이벤트"만 흉내 냈는데, 이 컴포저(ProseMirror
        리치텍스트 에디터)에서는 이 가짜 키 이벤트가 실제 붙여넣기로 처리되지 않는다는 게
        실측으로 확인됨(9223에 직접 CDP로 붙어 before/after 컴포저 길이를 재보니 그대로 1 →
        1, 전혀 안 늘어남 — 사용자도 "나는 붙여넣기 되던데?"라며 본인이 직접 누르는 진짜
        Ctrl+V는 되는데 자동화만 안 된다고 확인해줌). 반면 CDP `Input.insertText`는 같은
        컴포저에서 한 번에 텍스트 전체를 정확히 삽입하는 게 실측으로 확인됨(1 → 22자, 정확히
        일치). 그래서 클립보드+가짜 키 이벤트 조합을 걷어내고 Input.insertText로 완전히
        교체했다 — 이것도 한 글자씩 dispatchKeyEvent(type="char")로 타이핑하는 것과 달리
        텍스트 전체가 한 번의 삽입 이벤트로 들어가므로, 애초 목적(봇탐지 회피용 "한 번에
        붙여넣기" 패턴)은 그대로 유지된다.
        붙여넣기(삽입) 전후로 컴포저 텍스트 길이가 실제로 늘어났는지 확인한 뒤, 확인되면
        사람처럼 짧게 쉬었다가 리턴한다 — 사용자 지적("프롬프트 다 적히고 클릭하는거 맞아?
        너무 순식간이라 봇같지 않아?")대로, 삽입 직후 0초 만에 바로 제출 버튼을 누르는 것
        자체가 부자연스러운 패턴이라 봇 탐지에도 안 좋을 수 있다. 확인이 실패해도(길이 감지
        자체가 셀렉터 문제로 못 미더울 수 있으므로) 타이핑으로 되돌아가지 않는다 — 이미
        Input.insertText는 성공했다고 신뢰하고 경고만 남긴다."""
        before_len = self._composer_text_len()
        self.c.cdp("Input.insertText", text=text)
        grew = False
        for _ in range(8):
            time.sleep(0.25)
            if self._composer_text_len() > before_len:
                grew = True
                break
        if not grew:
            log(f"  ⚠ 붙여넣기(insertText) 반영 확인 실패(컴포저 길이 감지 안 됨, before={before_len}) — "
                "타이핑으로 전환하지 않고 이미 보낸 insertText를 그대로 신뢰함")
        # 사람이 붙여넣은 뒤 훑어보는 정도의 짧은 정지 — 확인 성공/실패 여부와 무관하게 항상 쉰다.
        time.sleep(0.8)

    def shot(self, tag):
        if self.shots:
            try:
                self.c.capture_screenshot(str(self.shots / f"{tag}.png"), max_dim=1400)
            except Exception as e:
                log("  (스샷 실패)", e)

    # ── 오버레이 정리 (업데이트 공지·프로모 카드·쿠키배너) ──────
    def dismiss_overlays(self):
        for label in ("시작하기", "확인", "나중에", "닫기"):
            r = self.c.js(r"""(()=>{const b=[...document.querySelectorAll("button")]
              .find(e=>(e.innerText||"").trim()===%s && e.offsetParent);
              if(!b) return "NF"; const r=b.getBoundingClientRect();
              return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)});})()"""
                        % json.dumps(label))
            if r and r != "NF":
                d = json.loads(r)
                self.click_xy(d["x"], d["y"])
                time.sleep(1)

    # ── 프로젝트 ─────────────────────────────────────────────
    def open_project(self, url: str | None = None):
        if url:
            self.c.goto_url(url)
            time.sleep(8)
            self.dismiss_overlays()
            return url
        self.c.goto_url("https://flow.google.com/")
        time.sleep(8)
        self.dismiss_overlays()
        # 2026-09-09 수정 — 예전엔 button/[role=button] 태그만 찾았는데, 실측(홈 화면 스크린샷)
        # 결과 "새 프로젝트" 타일이 그 태그가 아니었다(UI가 카드형 그리드로 바뀜). 태그 무관하게
        # 텍스트가 "새 프로젝트"인 요소 중, 그 텍스트를 직접 담고 있는 가장 안쪽(leaf-most) 것을
        # 찾아 클릭한다 — 부모 컨테이너까지 같이 걸리면 좌표가 엉뚱한 곳(그리드 전체)이 된다.
        r = self.c.js(r"""(()=>{const all=[...document.querySelectorAll("*")]
          .filter(e=>e.offsetParent && (e.innerText||"").trim()==="새 프로젝트");
          if(!all.length) return "NF";
          const leaf=all.reduce((a,b)=>(a.contains(b)?b:a));
          const r=leaf.getBoundingClientRect();
          return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)});})()""")
        if r == "NF":
            raise RuntimeError("★'새 프로젝트' 버튼을 못 찾았다 — UI가 또 바뀌었을 수 있다")
        d = json.loads(r)
        self.click_xy(d["x"], d["y"])
        time.sleep(10)
        self.dismiss_overlays()
        info = self.c.page_info()
        if "/project/" not in info.get("url", ""):
            raise RuntimeError(f"프로젝트 진입 실패: {info}")
        return info["url"]

    # 2026-09-11 추가 — 사용자 지적: "캐릭터도 프로젝트별로 생성이 되네" / "프로잭트를
    # 찾아서 선택을 하게끔 수정해야겠어". 그동안 project_url을 코드에 하드코딩된 기본값이나
    # 로컬 상태 파일(_flow_state.json)에 의존했는데, 실측 결과 그 값이 실제 캐릭터가 등록된
    # 프로젝트와 다른 경우(오래된 테스트 프로젝트 등)가 실제로 발생해서, 대시보드가 "대기중"
    # 으로 보여줘도 드라이버는 엉뚱한(캐릭터 없는) 프로젝트에서 돌고 있었다. 홈 화면의 프로젝트
    # 카드 목록을 직접 읽어와서 사람이 고를 수 있게 한다 — 하드코딩 대신 실제 목록에서 선택.
    def list_projects(self):
        """flow.google.com 홈 화면의 프로젝트 카드 목록을 [{id,url,name}] 로 반환한다."""
        self.c.goto_url("https://flow.google.com/")
        time.sleep(6)
        self.dismiss_overlays()
        r = self._poll_js(r"""(()=>{
          const links = [...document.querySelectorAll('a[href*="/project/"]')];
          const seen = new Set(); const out = [];
          for (const a of links) {
            const m = (a.getAttribute('href')||'').match(/\/project\/([0-9a-fA-F-]{36})/);
            if (!m) continue;
            const id = m[1];
            if (seen.has(id)) continue;
            seen.add(id);
            let name = (a.innerText || a.textContent || '').trim().split('\n')[0];
            if (!name) name = id;
            out.push({id, name});
          }
          return JSON.stringify(out);
        })()""", tries=6, delay=0.5)
        if r == "NF" or not r:
            raise RuntimeError("★홈 화면에서 프로젝트 카드를 하나도 못 찾았다 — UI 구조가 다를 수 있다")
        return json.loads(r)

    # ── 에이전트 설정 (프로젝트당 1회) ───────────────────────
    def _poll_js(self, expr: str, tries: int = 12, delay: float = 0.5):
        """2026-09-10 실사고 수정 — UI를 딱 한 번만 조회하고 못 찾으면 바로 죽던 여러 지점
        (composer_click, attach_reference, submit 등)이 직전 씬 완료 직후·캐릭터 첨부 직후처럼
        화면이 막 다시 그려지는 순간에 실제로 죽는 사고가 반복 확인됨(S14 9224, S16 9225).
        한 번 안 보인다고 바로 포기하지 않고 짧게 재조회해서 일시적 렌더 지연을 흡수하는
        공용 헬퍼 — 기본 예산을 6회(3초)에서 12회(6초)로 늘렸다."""
        r = "NF"
        for _ in range(tries):
            r = self.c.js(expr)
            if r != "NF":
                return r
            time.sleep(delay)
        return r

    def find_icon_button(self, icon_text: str):
        """mat-icon 리가처 텍스트(add·tune·arrow_forward 등)로 버튼을 찾는다.
        하단 컴포저 영역(화면 하단 20%)에 한정 — 같은 아이콘이 다른 곳에도 있을 수 있다."""
        r = self._poll_js(r"""(()=>{const H=innerHeight;
          const b=[...document.querySelectorAll("button,[role=button]")].filter(e=>{
            if(!e.offsetParent) return false;
            const r=e.getBoundingClientRect();
            if(r.top < H*0.75) return false;
            const ic=e.querySelector(".mat-icon,mat-icon");
            return ic && (ic.textContent||"").trim()===%s;
          });
          if(!b.length) return "NF";
          const r=b[0].getBoundingClientRect();
          return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)});})()"""
                    % json.dumps(icon_text))
        return None if r == "NF" else json.loads(r)

    def open_settings(self) -> bool:
        # 2026-09-11 실사고 수정(2차, 결정적) — 사용자 스크린샷으로 실제 위치 확인: 설정은
        # 상단 툴바 톱니바퀴가 아니라, 컴포저(화면 하단) 오른쪽의 모델/비율 요약 칩이었다
        # (예: "Nano Banana 2 ▭ x1" — 모델명+비율아이콘+배치수가 한 칩에 요약돼 있고, 이걸
        # 클릭하면 팝오버로 "이미지/동영상" 탭 + 16:9/4:3/1:1/3:4/9:16 비율 버튼 + 배치수
        # x1~x4가 뜬다). 이전 두 번의 수정(하단 25% 제한 → 화면 전체 tune/settings 아이콘
        # 검색)은 전부 틀린 위치를 찾고 있었다. 이 칩은 "x숫자" 패턴을 포함하는 짧은 버튼
        # 텍스트로 식별한다.
        r = self._poll_js(r"""(()=>{const H=innerHeight;
          const b=[...document.querySelectorAll("button,[role=button]")].filter(e=>{
            if(!e.offsetParent) return false;
            const r=e.getBoundingClientRect();
            if(r.top < H*0.6) return false;
            const t=(e.innerText||"").replace(/\s+/g," ").trim();
            return /x\d/i.test(t) && t.length < 40;
          });
          if(!b.length) return "NF";
          const r=b[0].getBoundingClientRect();
          return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)});})()""",
                          tries=8, delay=0.4)
        if r == "NF":
            log("  (모델/비율 칩 없음 — 기본값 그대로 사용, 건너뜀)")
            return False
        pos = json.loads(r)
        self.click_xy(pos["x"], pos["y"])
        time.sleep(1.0)
        return True

    def close_settings(self):
        # 팝오버는 별도 닫기 버튼 없이 바깥을 클릭하면 닫힌다 — 컴포저 입력창을 클릭해서
        # 닫는 동시에 다음 프롬프트 입력 준비까지 겸한다.
        self.composer_click()

    def switch_settings_tab(self, kind: str) -> None:
        """팝오버 안 '이미지'/'동영상' 탭 전환. 기본이 '이미지' 탭이라 kind='image'면 생략 가능."""
        label = "이미지" if kind == "image" else "동영상"
        r = self.c.js(r"""(()=>{const target=%s;
          const b=[...document.querySelectorAll("button,[role=button]")]
            .find(e=>e.offsetParent && (e.innerText||"").trim()===target);
          if(!b) return "NF"; const r=b.getBoundingClientRect();
          return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)});})()""" % json.dumps(label))
        if r and r != "NF":
            d = json.loads(r)
            self.click_xy(d["x"], d["y"])
            time.sleep(0.5)

    def set_ratio_chip(self, ratio: str, kind: str):
        """팝오버 안에서 '이미지'/'동영상' 탭을 고른 뒤, 그 탭의 비율 버튼(예: '16:9')을 클릭."""
        self.switch_settings_tab(kind)
        r = self.c.js(r"""(()=>{const target=%s;
          const b=[...document.querySelectorAll("button,[role=button]")]
            .filter(e=>e.offsetParent && (e.innerText||"").replace(/\s+/g," ").trim()===target);
          if(!b.length) return "NF"; const r=b[0].getBoundingClientRect();
          return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)});})()""" % json.dumps(ratio))
        if r == "NF" or not r:
            log(f"  ✗ 비율 버튼 없음: {ratio} ({kind})")
            return False
        d = json.loads(r)
        self.click_xy(d["x"], d["y"])
        time.sleep(0.5)
        return True

    def set_batch_count(self, n: int, kind: str):
        """팝오버 안에서 '이미지'/'동영상' 탭을 고른 뒤, 그 탭의 배치수 버튼(예: 'x1')을 클릭.
        2026-09-12 추가 — 사용자 지적: "사진생성1장 설정도 체크 안해". job.json의 image_count는
        지금까지 image_count_instruction()으로 프롬프트 텍스트에 "정확히 N장만 만들어라"는
        지시만 넣었을 뿐, Flow 자체의 배치수 UI(설정 칩 "Nano Banana 2 ▭ x1"을 열면 나오는
        x1~x4 버튼, open_settings() docstring에 실측 기록됨)는 한 번도 클릭한 적이 없었다.
        비율(set_ratio_chip)과 똑같은 방식으로 실제 UI 버튼도 맞춰서, 텍스트 지시에만
        의존하지 않게 한다."""
        self.switch_settings_tab(kind)
        target = f"x{n}"
        r = self.c.js(r"""(()=>{const target=%s;
          const b=[...document.querySelectorAll("button,[role=button]")]
            .filter(e=>e.offsetParent && (e.innerText||"").replace(/\s+/g," ").trim()===target);
          if(!b.length) return "NF"; const r=b[0].getBoundingClientRect();
          return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)});})()""" % json.dumps(target))
        if r == "NF" or not r:
            log(f"  ✗ 배치수 버튼 없음: {target} ({kind})")
            return False
        d = json.loads(r)
        self.click_xy(d["x"], d["y"])
        time.sleep(0.5)
        return True

    def ensure_settings(self, image_ratio="16:9", video_ratio="16:9", agent_off=True, image_count=1) -> bool:
        """프로젝트당 1회 호출. 컴포저의 모델/비율 칩을 열어 이미지·동영상 비율·배치수를 맞춘다.
        2026-09-13 (6차) 수정 — 실사고: 설정 칩을 못 찾아 여기서 그냥 조용히 return해도,
        호출부(main())가 성공 여부를 확인 안 하고 무조건 state["settings_done"]=true를
        저장해버려서 — 한 번 실패하면 그 프로젝트/포트/모드 조합에서는 영원히 다시 시도조차
        안 하고 "이미 설정 끝남"으로 착각한 채 계속 진행했다(에이전트 모드도 꺼진 적이 없어
        needsVideoClip 씬이 채팅 AI에게 넘어가 이미지로 만들어진 사고로 발견됨: "니가 설정을
        열어서 동영상으로 설정을 안하고 그냥 진행을 한거자나"). 이제 성공 여부를 bool로
        돌려주고, main()이 실패 시 settings_done을 저장하지 않아 다음 실행에서 다시 시도한다.
        2026-09-13 (7차) 수정 — 실기 확인(9226, CDP 직접 조작): Flow가 프로젝트를 열면 기본으로
        하단이 "에이전트" 대화형 채팅 화면(오른쪽 전체가 채팅 스레드)으로 뜨고, 우리가 찾는
        모델/비율 요약 칩(open_settings가 찾는 "…x1" 텍스트)은 그 채팅 화면이 아니라 별도의
        "클래식 컴포저" 화면에만 있다 — 하단의 `agent-mode-chip` 버튼(aria-pressed="true"=
        채팅 모드, "false"=클래식 컴포저 모드)을 먼저 꺼야 그 컴포저가 드러난다. 그래서
        ensure_agent_off()를 open_settings()보다 반드시 먼저 호출해야 한다(순서가 바뀌어 있던
        게 이번 사고의 진짜 원인 — open_settings()가 채팅 화면에선 칩을 아예 찾을 수 없어 항상
        실패하고, agent_off는 그 뒤에 있어서 실행될 기회조차 없었다)."""
        if agent_off:
            self.ensure_agent_off()
        if not self.open_settings():
            return False
        self.set_ratio_chip(image_ratio, "image")
        self.set_batch_count(image_count, "image")
        self.set_ratio_chip(video_ratio, "video")
        self.close_settings()
        return True

    def ensure_agent_off(self):
        """2026-09-12 추가 — Flow가 2026년 5월 새로 붙인 "에이전트" 대화형 모드(구글 공식 문서:
        support.google.com/flow/answer/17093911) 때문에, 캐릭터를 멘션한 뒤 제출하면 곧장
        생성되지 않고 "이 캐릭터로 뭘 하고 싶으세요?" 같은 확인 메뉴가 뜨는 게 실측 확인됐다
        (S70 자동화 실행이 이 지점에서 멈춰있었음 — gen_started:false로 계속 대기).
        사용자가 직접 화면에서 "에이전트" 칩을 꺼서 문제가 해결됨을 확인했다 — 이 칩이 켜져
        있으면 자동으로 꺼서, 매 프로젝트 시작 시 이 메뉴가 아예 안 뜨게 한다.
        Flow가 이 칩의 켜짐 상태를 정확히 어떤 속성/클래스로 표시하는지 실측된 바 없어서,
        aria-pressed(또는 유사 속성)를 우선 신뢰하되 못 찾으면 함부로 클릭하지 않고 디버그
        스크린샷만 남긴다 — 꺼진 걸 실수로 다시 켜버리는 것보다는 아무 것도 안 하는 쪽이
        안전하다."""
        r = self.c.js(r"""(()=>{const H=innerHeight;
          const b=[...document.querySelectorAll("button,[role=button]")].filter(e=>{
            if(!e.offsetParent) return false;
            const r=e.getBoundingClientRect();
            if(r.top < H*0.6) return false;
            const t=(e.innerText||"").replace(/\s+/g," ").trim();
            return t === "에이전트";
          });
          if(!b.length) return "NF";
          const el=b[0]; const r=el.getBoundingClientRect();
          const pressed = el.getAttribute("aria-pressed") || el.getAttribute("aria-selected") || "";
          return JSON.stringify({x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2),
            pressed, cls: el.className || ""});})()""")
        if r == "NF":
            log("  (에이전트 칩 안 보임 — 건너뜀)")
            return
        d = json.loads(r)
        self.shot("dbg_agent_chip")
        if d["pressed"] == "true":
            log(f"  에이전트 켜짐 감지(pressed={d['pressed']!r}) — 꺼는 중")
            self.click_xy(d["x"], d["y"])
            time.sleep(0.5)
        elif d["pressed"] == "false":
            log("  에이전트 이미 꺼짐 확인")
        else:
            log(f"  ⚠ 에이전트 칩 상태 불명(pressed 속성 없음, class={d['cls']!r}) — "
                "잘못 눌러서 켜버릴 위험이 있어 건드리지 않음. dbg_agent_chip 스샷 확인 필요.")

    # ── 동영상 "프레임" 모드 설정 (2026-09-13 추가, 실기 검증 전) ─────────────
    # 사용자 지시: "동영상,프레임,16:9(롱폼),옴니1.1 프레쉬,720,1장". 사용자가 직접 Flow
    # 화면에서 실측 확인(스크린샷)한 절차를 그대로 코드로 옮긴다 — 동영상 탭 안에 "프레임"/
    # "소재" 서브탭이 있고, 프레임을 고르면 컴포저 위에 "시작"/"종료" 슬롯이 뜬다. 그 슬롯을
    # 누르면 "프레임 이미지 선택" 모달이 열리는데, 여기서 검색되는 이미지는 Flow가 AI로 붙인
    # 요약 제목(예: "Shadow creeping over Coca-Cola c...")이라 우리 씬 ID/프롬프트로는 절대
    # 못 찾는다(직접 확인) — 대신 Flow의 "업로드" 자산은 우리가 올린 파일명 그대로 검색되므로,
    # 씬 이미지를 먼저 로컬에서 Flow로 업로드한 뒤 그 파일명으로 검색해서 정확히 찾는다.
    def set_video_frame_mode(self):
        """동영상 탭 안 '프레임' 서브탭을 클릭한다(기본이 '프레임'일 수도 있어 안 보이면 그냥 넘어감)."""
        r = self.c.js(r"""(()=>{
          const b=[...document.querySelectorAll('button,[role=button]')]
            .filter(e=>e.offsetParent && (e.innerText||'').trim()==='프레임');
          if(!b.length) return 'NF'; const r=b[0].getBoundingClientRect();
          return JSON.stringify({x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)});})()""")
        if r and r != 'NF':
            d = json.loads(r)
            self.click_xy(d['x'], d['y'])
            time.sleep(0.5)
        else:
            log("  (프레임 서브탭 버튼 안 보임 — 이미 프레임 모드이거나 UI가 다를 수 있음)")

    def set_video_resolution(self, resolution: str):
        """'360p'/'720p' 버튼 클릭. switch_settings_tab('video')+set_video_frame_mode() 이후 호출 전제."""
        r = self.c.js(r"""(()=>{const target=%s;
          const b=[...document.querySelectorAll('button,[role=button]')]
            .filter(e=>e.offsetParent && (e.innerText||'').replace(/\s+/g,' ').trim().startsWith(target));
          if(!b.length) return 'NF'; const r=b[0].getBoundingClientRect();
          return JSON.stringify({x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)});})()""" % json.dumps(resolution))
        if r == 'NF' or not r:
            log(f"  ✗ 해상도 버튼 없음: {resolution}")
            return False
        d = json.loads(r)
        self.click_xy(d['x'], d['y'])
        time.sleep(0.5)
        return True

    def set_video_duration(self, seconds_label: str):
        """'4초'/'6초'/'8초'/'10초' 버튼 클릭."""
        r = self.c.js(r"""(()=>{const target=%s;
          const b=[...document.querySelectorAll('button,[role=button]')]
            .filter(e=>e.offsetParent && (e.innerText||'').trim()===target);
          if(!b.length) return 'NF'; const r=b[0].getBoundingClientRect();
          return JSON.stringify({x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)});})()""" % json.dumps(seconds_label))
        if r == 'NF' or not r:
            log(f"  ✗ 길이 버튼 없음: {seconds_label}")
            return False
        d = json.loads(r)
        self.click_xy(d['x'], d['y'])
        time.sleep(0.5)
        return True

    def set_video_model(self, model_name: str):
        """동영상 탭의 모델 드롭다운(예: 'Omni 1.1 Flash ▾')을 열어 정확한 이름의 항목을 고른다."""
        r = self.c.js(r"""(()=>{
          const b=[...document.querySelectorAll('button,[role=button]')].find(e=>{
            if(!e.offsetParent) return false;
            const ic = e.querySelector('.mat-icon,mat-icon');
            return ic && (ic.textContent||'').trim()==='arrow_drop_down';
          });
          if(!b) return 'NF'; const r=b.getBoundingClientRect();
          return JSON.stringify({x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)});})()""")
        if r == 'NF' or not r:
            log("  ✗ 동영상 모델 드롭다운을 못 찾음")
            return False
        d = json.loads(r)
        self.click_xy(d['x'], d['y'])
        time.sleep(0.6)
        r2 = self._poll_js(r"""(()=>{
          const overlay = document.querySelector('.cdk-overlay-container');
          const root = overlay || document.body;
          const items = [...root.querySelectorAll('*')]
            .filter(e=>e.offsetParent && e.children.length===0 && (e.textContent||'').trim().includes(%s));
          if(!items.length) return 'NF'; const r=items[0].getBoundingClientRect();
          return JSON.stringify({x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)});})()""" % json.dumps(model_name))
        if r2 == 'NF' or not r2:
            log(f"  ✗ 동영상 모델 목록에서 못 찾음: {model_name}")
            return False
        d2 = json.loads(r2)
        self.click_xy(d2['x'], d2['y'])
        time.sleep(0.5)
        return True

    def ensure_video_frame_settings(self, ratio="16:9", model="Omni 1.1 Flash", resolution="720p",
                                     duration="8초", count=1, agent_off=True) -> bool:
        """프로젝트당 1회(또는 클립 단계 시작 시 1회) 호출 — 동영상 탭을 '프레임' 모드+
        16:9+모델+해상도+길이+배치수로 맞춘다. 사용자 지시값 그대로 기본값을 둠:
        "동영상,프레임,16:9(롱폼),옴니1.1 프레쉬,720,1장".
        2026-09-13 (6차) 수정 — 실사고: 설정 칩을 못 찾으면 경고 로그만 남기고 그냥
        넘어가서, 컴포저가 이전 세션의 "이미지" 모드에 그대로 남은 채 프롬프트가
        제출됐다 — needsVideoClip 씬인데 실제로는 이미지가 생성됨(사용자 지적: "왜
        영상이 아니라 이미지를 만들었을까", "실행이 안되었는데 왜 진행이 된거야?").
        이제 실패하면 False를 돌려주고, 호출부(clips 스테이지)가 이 경우 전체 실행을
        그 자리에서 중단한다 — 잘못된 모드로 나머지 씬까지 계속 생성하는 걸 막는다.
        2026-09-13 (7차) 수정 — 실기 확인(9226): open_settings()가 찾는 모델/비율 요약 칩은
        Flow의 기본 "에이전트" 채팅 화면이 아니라 별도의 "클래식 컴포저" 화면에만 있다 —
        ensure_agent_off()로 먼저 그 채팅 화면을 꺼야 컴포저가 드러나고 칩이 보인다
        (ensure_settings()와 동일한 순서 수정, 상세 사유는 그쪽 docstring 참고)."""
        if agent_off:
            self.ensure_agent_off()
        if not self.open_settings():
            log("  ✗ 설정 칩을 못 열어 동영상 설정을 건너뜀 — 기본값(대개 이미지 모드)으로 진행될 수 있음")
            return False
        self.switch_settings_tab("video")
        self.set_video_frame_mode()
        self.set_ratio_chip(ratio, "video")
        self.set_video_model(model)
        self.set_video_resolution(resolution)
        self.set_video_duration(duration)
        self.set_batch_count(count, "video")
        self.close_settings()
        return True

    def upload_and_attach_frame(self, local_path: Path, search_term: str, slot: str = "종료") -> bool:
        """씬 이미지를 Flow '업로드' 자산으로 올린 뒤, '프레임' 모드의 '시작' 또는 '종료' 슬롯에서
        그 파일명으로 검색해 선택·첨부한다. 호출 전제: ensure_video_frame_settings()로 이미
        '프레임' 모드가 켜져 있어(컴포저 위에 '시작'/'종료' 슬롯이 보임).

        2026-09-13 (3차) 수정 — 사용자 지적: "사건직전이고 하이라이트가 아니고" / "그걸 무조건
        정해놓는게 어디있어". 기존에 이미 만들어둔 씬 이미지는 대부분 사건의 결과/클라이맥스
        순간을 그린 것이라(13번 마스터 프롬프트가 needsVideoClip 씬도 예전엔 이 기준으로만
        만들게 했었다), 그걸 "시작" 프레임으로 쓰면 애니메이션할 동작이 안 남는다. 대신 그
        결과 이미지를 "종료" 프레임으로 쓰고 "시작"은 비워서, videoPrompt가 그 결과 상태로
        끝나는 동작을 텍스트로 설명하게 한다 — 재생성 없이 기존 이미지를 그대로 활용하는
        기본값. 마스터 프롬프트 수정 이후 "사건 직전" 상태로 새로 만든 이미지가 있다면
        slot="시작"으로 호출해서 쓸 수 있다.
        ⚠️ 실기 검증 전 — 사용자가 수동으로 업로드→프레임 첨부까지는 직접 확인했지만(스크린샷),
        이 자동화 경로(Page.setInterceptFileChooserDialog) 자체는 이 프로젝트에서 파일 업로드
        자동화가 처음이라 별도 검증이 필요하다."""
        # 1) '시작' 또는 '종료' 슬롯 클릭 → '프레임 이미지 선택' 모달 열기
        r = self._poll_js(r"""(()=>{const target=%s;
          const all=[...document.querySelectorAll('*')]
            .filter(e=>e.offsetParent && e.children.length<=1 && (e.textContent||'').trim()===target);
          if(!all.length) return 'NF';
          const el=all[0].closest('button,[role=button]')||all[0];
          const r=el.getBoundingClientRect();
          return JSON.stringify({x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)});})()""" % json.dumps(slot))
        if r == 'NF' or not r:
            self.shot(f"dbg_no_{slot}_slot")
            raise RuntimeError(f"★'{slot}' 프레임 슬롯을 못 찾았다 — '프레임' 모드가 아닐 수 있다")
        d = json.loads(r)
        self.click_xy(d['x'], d['y'])
        time.sleep(1.0)

        # 2) 모달의 '업로드' 진입점을 찾아 클릭 — Page.fileChooserOpened를 기다렸다가
        #    실제 OS 다이얼로그 대신 그 input에 로컬 파일을 직접 꽂는다.
        self.c.enable_file_chooser_interception()
        r2 = self._poll_js(r"""(()=>{
          const overlay = document.querySelector('.cdk-overlay-container');
          const root = overlay || document.body;
          const b=[...root.querySelectorAll('*')]
            .filter(e=>e.offsetParent && ((e.textContent||'').trim()==='업로드' || e.getAttribute('aria-label')==='업로드'));
          if(!b.length) return 'NF'; const el=b[0].closest('button,[role=button]')||b[0];
          const r=el.getBoundingClientRect();
          return JSON.stringify({x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)});})()""")
        if r2 == 'NF' or not r2:
            self.shot("dbg_no_upload_entry")
            raise RuntimeError("★'프레임 이미지 선택' 모달 안에서 '업로드' 진입점을 못 찾았다 — UI가 다를 수 있다(dbg_no_upload_entry 스샷 확인)")
        d2 = json.loads(r2)
        self.click_xy(d2['x'], d2['y'])
        params = self.c.wait_for_file_chooser(timeout=10)
        backend_node_id = params.get("backendNodeId")
        if not backend_node_id:
            raise RuntimeError(f"★fileChooserOpened 이벤트에 backendNodeId가 없다: {params}")
        self.c.set_file_input_files(backend_node_id, [str(local_path)])
        time.sleep(2.0)  # 업로드 진행/썸네일 반영 대기

        # 3) 검색창에 파일명(확장자 제외)을 입력해 방금 올린 파일을 찾는다.
        r3 = self._poll_js(r"""(()=>{
          const overlay = document.querySelector('.cdk-overlay-container');
          const root = overlay || document.body;
          const b=[...root.querySelectorAll('input')].find(e=>e.offsetParent);
          if(!b) return 'NF'; const r=b.getBoundingClientRect();
          return JSON.stringify({x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)});})()""",
                            tries=10, delay=0.5)
        if r3 == 'NF' or not r3:
            self.shot("dbg_no_search_box")
            raise RuntimeError("★프레임 이미지 선택 모달의 검색창을 못 찾았다")
        d3 = json.loads(r3)
        self.click_xy(d3['x'], d3['y'])
        time.sleep(0.2)
        self.type_text(search_term)
        time.sleep(1.0)

        # 4) 검색 결과 첫 항목 클릭 → '프롬프트에 추가'
        self.shot(f"dbg_frame_search_{search_term}")
        r4 = self._poll_js(r"""(()=>{
          const overlay = document.querySelector('.cdk-overlay-container');
          const root = overlay || document.body;
          const items = [...root.querySelectorAll('[role=listitem], li, button')]
            .filter(e=>e.offsetParent && (e.textContent||'').trim().length>0 && e.querySelector && e.querySelector('img'));
          if(!items.length) return 'NF'; const r=items[0].getBoundingClientRect();
          return JSON.stringify({x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)});})()""",
                            tries=10, delay=0.5)
        if r4 == 'NF' or not r4:
            self.shot("dbg_no_search_result")
            raise RuntimeError(f"★'{search_term}' 검색 결과에서 항목을 못 찾았다(업로드가 아직 안 끝났을 수 있음)")
        d4 = json.loads(r4)
        self.click_xy(d4['x'], d4['y'])
        time.sleep(0.5)
        r5 = self.c.js(r"""(()=>{
          const b=[...document.querySelectorAll('button,[role=button]')]
            .find(e=>e.offsetParent && (e.textContent||'').trim()==='프롬프트에 추가');
          if(!b) return 'NF'; const r=b.getBoundingClientRect();
          return JSON.stringify({x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)});})()""")
        if r5 == 'NF' or not r5:
            self.shot("dbg_no_add_to_prompt")
            raise RuntimeError("★'프롬프트에 추가' 버튼을 못 찾았다")
        d5 = json.loads(r5)
        self.click_xy(d5['x'], d5['y'])
        time.sleep(0.8)
        return True

    # ── 컴포저 ───────────────────────────────────────────────
    def composer_click(self):
        # 2026-09-10 실사고 수정 — 직전 씬 완료 직후(그리드가 막 다시 그려지는 순간) 이
        # 검색이 한 번에 실패해서 드라이버 전체가 죽는 사고가 실측됨(S14, 9224). 한 번
        # 못 찾았다고 바로 죽지 않고, 짧게 재시도해서 일시적 렌더 지연을 흡수한다.
        r = "NF"
        for _ in range(6):
            r = self.c.js(r"""(()=>{const H=innerHeight;
              const e=[...document.querySelectorAll("[contenteditable=true],textarea")]
                .filter(x=>x.offsetParent && x.getBoundingClientRect().top>H*0.6)[0];
              if(!e) return "NF"; const r=e.getBoundingClientRect();
              return JSON.stringify({x:Math.round(r.left+30),y:Math.round(r.top+r.height/2)});})()""")
            if r != "NF":
                break
            time.sleep(0.5)
        if r == "NF":
            raise RuntimeError("★컴포저 입력창을 못 찾았다")
        d = json.loads(r)
        self.click_xy(d["x"], d["y"])
        time.sleep(0.3)
        # 2026-09-09 추가 — 클릭 직후 항상 JS로 직접 비운다. 안 비우면 이전 프롬프트
        # 잔여물에 새 프롬프트가 이어붙어 뒤섞인 채 제출된다(README 실사고 기록,
        # "1880s Apothecary Cinema" 세션). 키보드 Ctrl+A 대신 JS로 지우는 이유는,
        # 포커스가 엉뚱한 곳(이미지 그리드)에 가 있으면 Ctrl+A가 화면의 이미지 전체를
        # 선택해버리고 그다음 Delete가 그것들을 통째로 지워버리는 사고가 나기 때문이다
        # (2026-09-07 실제 발생, "실행취소"로 복구) — 이 방식은 그 위험 자체가 없다.
        self.c.js(r"""(()=>{const H=innerHeight;
          const e=[...document.querySelectorAll("[contenteditable=true],textarea")]
            .filter(x=>x.offsetParent && x.getBoundingClientRect().top>H*0.6)[0];
          if(!e) return "NF";
          e.focus();
          if(e.tagName==="TEXTAREA"){
            const setter=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,"value").set;
            setter.call(e, "");
          } else {
            e.textContent = "";
          }
          e.dispatchEvent(new InputEvent("input", {bubbles:true}));
          return "OK";})()""")
        time.sleep(0.2)

    def attach_reference(self, title: str):
        """2026-09-11 전면 재설계 — 사용자 지시("웹 다시 찾아봐 플로우 사용법")로 공식 문서를
        재확인한 결과(support.google.com/flow/answer/16729550, googleautomator.com/user-guide):
        등록된 이름 있는 캐릭터를 참조로 쓰는 공식 방법은 "+"로 피커를 여는 게 아니라, 프롬프트
        입력창에 "@" + 캐릭터 이름을 직접 타이핑해서 뜨는 자동완성 목록에서 고르는 것이다
        ("+" 버튼은 파일 업로드/프로젝트 애셋 첨부용). 지금까지 이 메서드가 "+"를 눌러 피커를
        열고 클릭으로 조작하던 접근 전체가 애초에 공식 경로가 아니었던 것으로 보이고, 이게
        반복된 불안정(어떤 씬은 정상 캐릭터, 어떤 씬은 완전히 다른 캐릭터)의 근본 원인일
        가능성이 높다. 호출 전제: composer가 이미 포커스돼 있고(컴포저를 지우지 않은 채) 커서
        위치에 "@이름"을 타이핑한다 — 이 메서드는 컴포저를 새로 클릭/초기화하지 않는다.
        2026-09-11 실사고 수정 — 사용자 지적: "스탁맨인데 틱맨만 찾고있어". "@"+이름을 한
        번에 타이핑하면, 이름의 첫 글자 직후에 피커 오버레이가 뜨면서 포커스가 검색창으로
        넘어가고, 그 타이밍에 나머지 글자만 검색창에 들어가 첫 글자가 빠진다(실측 스샷으로
        확인 — "@젠"은 컴포저에 남고 "틀맨루즈"만 검색창에 들어감).
        2026-09-12 단순화 — 사용자가 직접 Flow에서 확인: "검색창에 이름 안 쳐도 되" — 캐릭터가
        2개뿐이라 "@"만 쳐도 오버레이에 둘 다 뜨고, 검색창에 따로 이름을 입력할 필요가 없다.
        그래서 검색창을 찾아 비우고 이름을 다시 타이핑/붙여넣던 단계를 통째로 없앴다.
        2026-09-12 (2차) 단순화 — 사용자 지적: "@를 입력하면 스샷처럼 자동으로 나와, 그런데
        넌 계속 입력을 했던거야" — 스샷으로 확인해보니 "@" 딱 한 글자만 쳐도 오버레이가 바로
        뜬다(이름의 첫 글자까지 칠 필요조차 없었다). "@"+첫 글자를 타이핑하던 것도 과했던
        것 — 이제 "@" 하나만 실제 타이핑해서 오버레이를 띄운다.
        2026-09-12 (3차, 핵심) — 디버그 스샷(dbg_mention_before/after)으로 실측 확인: "@"만
        치면 기본으로 "전체"(모든 미디어) 탭이 열리는데, 거기서 이름을 클릭하면 미리보기로
        선택만 되고 컴포저엔 "@"만 남은 채 첨부는 안 된다 — "프롬프트에 추가"를 한 번 더
        눌러야 끝난다. 반면 사용자가 직접 확인: "캐릭터에선 그냥 이름만 선택하면 되" — "캐릭터"
        탭으로 미리 좁혀두면 이름 클릭 한 번으로 바로 첨부까지 끝난다(버튼 불필요). 그래서
        "캐릭터" 탭을 먼저 클릭해 좁히는 단계를 다시 넣는다 — 예전에 검색창 정리 단계를
        없애면서 실수로 같이 지워버렸던 부분(사용자 지적: "왜 넌 자꾸 전체로 가???")."""
        # 2026-09-12 실사고 수정 — 캐릭터 이름이 전부 한글(스틱맨/젠틀맨루즈)이라, ASCII만
        # 남기던 이전 방식([^A-Za-z0-9])은 둘 다 밑줄 하나("_")로 뭉개져서 디버그 스샷 파일명이
        # "dbg_step1__.png"로 완전히 겹쳤다 — 어느 캐릭터를 찾으려던 시도였는지 스샷만으로는
        # 구분이 안 됐다(사용자 지적: "지금 캐릭터를 루즈만 찾고 있어 확인해봐"에 대응하려다
        # 발견). 윈도우 파일명에 실제로 못 쓰는 문자만 걸러내고 한글은 그대로 남긴다.
        tag = re.sub(r'[<>:"/\\|?*\s]+', '_', title)[:20]
        # 2026-09-12 (4차) 실사고 수정 — 사용자 지적: "스틱맨 장면에서 루즈를 선택하자나".
        # 라이브 CDP로 컴포저 DOM을 직접 읽어 확인한 결과, class="mention-chip-invalid"인
        # 깨진 멘션 칩("젠틀맨루즈")이 스틱맨 씬 컴포저에 그대로 남아있었다. 세션이 길어지고
        # 프로젝트에 이미지가 쌓여 Flow가 무거워지면, "@"를 쳐도 정상 피커 대신 예전에 쓰던
        # 캐시된 멘션이 그대로 꽂히고 정상 오버레이가 아예 안 뜨는 현상으로 보인다(사용자
        # 관찰과 일치: "처음에는 스틱맨 잘 찾았는데" 세션 후반부터 이 오류 발생). 다음 씬으로
        # 오염되지 않게, 매 시도 전에 남은 깨진 칩부터 먼저 지운다.
        self.c.js(r"""(()=>{const H=innerHeight;
          const e=[...document.querySelectorAll("[contenteditable=true],textarea")]
            .filter(x=>x.offsetParent && x.getBoundingClientRect().top>H*0.6)[0];
          if(!e) return "NF";
          [...e.querySelectorAll('.mention-chip-invalid')].forEach(b=>b.remove());
          return "OK";})()""")
        self.type_text("@")
        time.sleep(1.0)
        # 오버레이(피커) 자체가 실제로 떴는지부터 확인한다 — 안 뜬 채로 "캐릭터" 탭이나
        # 이름을 찾으러 가면, 방금 꽂힌 캐시된 엉뚱한 멘션이나 화면의 다른 요소를 잘못
        # 집을 위험이 있다. 없으면 여기서 바로 명확한 원인으로 실패 처리한다.
        overlay_r = self._poll_js(r"""(()=>{
          const overlay = document.querySelector('.cdk-overlay-container, [role="dialog"], [role="listbox"]');
          return overlay ? "OK" : "NF";})()""", tries=8, delay=0.3)
        if overlay_r != "OK":
            self.shot(f"mention_no_overlay_{tag}")
            raise RuntimeError(f"★'@' 입력해도 캐릭터 피커가 안 열렸다(오버레이 없음, 세션 부하로 캐시된 멘션이 꽂혔을 수 있음)")
        # 2026-09-12 (5차, 근본 원인) — 실사고로 확인: "캐릭터"는 눌러서 목록을 좁히는 탭이
        # 아니라, "전체" 목록에서 각 항목(이미지/캐릭터) 이름 아래 붙는 작은 종류 라벨일
        # 뿐이었다(스크린샷으로 직접 확인 — "전체 ▾" 드롭다운 하나만 있고 탭 버튼 자체가
        # 없음). 그래서 지금까지 "캐릭터" 텍스트를 찾아 클릭하던 이 단계는, 실제로는 목록의
        # 첫 번째 캐릭터 행(이 프로젝트에선 "젠틀맨루즈")에 붙은 라벨을 클릭해서 그 항목을
        # 그대로 선택/첨부해버리고 있었다 — "스틱맨을 찾으려다 젠틀맨루즈가 붙는" 사고의
        # 진짜 원인이었다(라이브 CDP로 클릭 전/후 스크린샷을 직접 비교해 확정). "전체" 목록에
        # 이름이 그대로 노출돼 있어 좁히는 탭 자체가 필요 없었으므로, 이 잘못된 클릭 단계를
        # 통째로 없애고 이름을 바로 찾는다.
        self.shot(f"dbg_step1_{tag}")
        r = self._poll_js(r"""(()=>{
          const overlay = document.querySelector('.cdk-overlay-container, [role="dialog"], [role="listbox"]');
          const root = overlay || document;
          const all=[...root.querySelectorAll("*")]
          .filter(e=>e.offsetParent && e.children.length===0 && (e.textContent||"").trim()===%s);
          if(!all.length) return "NF"; const r=all[0].getBoundingClientRect();
          return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)});})()""" % json.dumps(title),
                          tries=10, delay=0.4)
        if r == "NF":
            self.shot(f"mention_fail_{tag}")
            raise RuntimeError(f"★'@{title}' 자동완성 목록에서 못 찾았다")
        d = json.loads(r)
        self.shot(f"dbg_mention_before_{tag}")
        self.click_xy(d["x"], d["y"])
        time.sleep(0.5)
        self.shot(f"dbg_mention_after_{tag}")
        # paste_text()의 "하고 나서 반드시 확인" 원칙과 동일하게, 클릭한 뒤 실제로
        # 그 이름의 유효한(invalid 아닌) 칩이 컴포저에 붙었는지 검증한다 — 위에서 확인한
        # "엉뚱한 캐릭터가 붙는" 사고를 여기서 최종적으로 잡아낸다. 다르거나 깨졌으면
        # 그 칩을 바로 지워서 다음 씬으로 오염되지 않게 하고 이 씬만 실패 처리한다.
        verify = self._poll_js(r"""(()=>{const H=innerHeight;
          const e=[...document.querySelectorAll("[contenteditable=true],textarea")]
            .filter(x=>x.offsetParent && x.getBoundingClientRect().top>H*0.6)[0];
          if(!e) return "NF";
          const chips=[...e.querySelectorAll('.mention-chip')];
          const last=chips[chips.length-1];
          if(!last) return "NOCHIP";
          return JSON.stringify({txt:(last.textContent||"").trim(), invalid:last.classList.contains('mention-chip-invalid')});
          })()""", tries=4, delay=0.3)
        try:
            vd = json.loads(verify)
            ok = isinstance(vd, dict) and not vd.get("invalid") and vd.get("txt") == title
        except (TypeError, ValueError):
            vd = verify
            ok = False
        if not ok:
            self.shot(f"mention_bad_attach_{tag}")
            self.c.js(r"""(()=>{const H=innerHeight;
              const e=[...document.querySelectorAll("[contenteditable=true],textarea")]
                .filter(x=>x.offsetParent && x.getBoundingClientRect().top>H*0.6)[0];
              if(e) e.textContent = "";
              return "OK";})()""")
            raise RuntimeError(f"★'@{title}' 첨부 확인 실패(다른 캐릭터가 붙었거나 깨짐): {vd}")
        # 멘션 첨부 직후 커서가 그 칩 바로 뒤에 있다 — 이어서 타이핑할 실제 프롬프트와
        # 붙지 않게 공백 하나 넣는다.
        self.type_text(" ")

    def submit(self, timeout=300):
        # 2026-09-11 실사고 수정 — 사용자 지적: "왜 퍼센트가 진행중인데 지 마음데로
        # 타임아웃을 내고 멈춰버리는거야?". find_icon_button()의 기본 재시도 예산은 6초뿐이라,
        # 직전 씬(또는 참조 캐릭터 첨부)의 생성이 화면에서 아직 안 끝나 arrow_forward 버튼이
        # 일시적으로 안 보이는 것뿐인데도 6초 만에 바로 "못 찾았다"며 그 씬을 실패 처리하는
        # 사고가 반복됐다(S09/S12/S16/S19/S23 전부 이 패턴 — 로딩 %가 여전히 올라가는 중에
        # 다음 씬 제출이 실패로 기록됨). 버튼이 나타날 때까지 최대 5분(300초)까지 계속
        # 재시도한다 — 정말 못 찾는 경우(UI 변경 등)만 최종적으로 실패 처리된다.
        t0 = time.time()
        pos = None
        while time.time() - t0 < timeout:
            pos = self.find_icon_button("arrow_forward")
            if pos:
                break
            time.sleep(1.5)
        if not pos:
            self.shot("submit_fail")
            raise RuntimeError("★제출(arrow_forward) 아이콘을 못 찾았다 — 생성 중일 수도 있다")
        self.click_xy(pos["x"], pos["y"])
        time.sleep(1.5)

    def media_count(self):
        """그리드 안의 결과물 이미지만 센다 — 컴포저에 붙인 참조 썸네일(화면 하단)은 제외한다.
        ★참조 썸네일도 naturalWidth 는 원본 그대로라, 걸러내지 않으면 첨부 직후 before 값이
          부풀어 wait_done 이 실제로는 끝났는데도 타임아웃으로 오판한다(실측, 2026-09-06).
        2026-09-09 수정 — `top<innerHeight*0.75` 조건은 상단 캐릭터 썸네일 스트립(rectTop
        약 25px)까지 전부 통과시켜버려서 개수가 뻥튀기되고, 새 이미지 1장이 추가돼도 그
        증가분이 희석돼 완료 판정이 늦거나 틀리는 원인이었다 — `capture_newest_images()`와
        같은 기준(rect.width>400 + flow-content.google 도메인만, 실제로 화면에 크게 렌더된
        생성 이미지만)으로 통일했다.
        2026-09-10 실사고 수정(캐릭터 첨부 기능 도입 후) — 캐릭터를 프롬프트에 첨부하면
        <flow-character-tile> 안에도 naturalWidth>400·width>150·flow-content.google
        조건을 전부 만족하는 <img>(참조 캐릭터의 포트레이트)가 생겨서, 실제로는 캐릭터를
        새로 하나 더 첨부/변경한 것뿐인데 media_count가 늘어난 것처럼 잘못 셌다. 결과물
        타일은 항상 <flow-image-tile> 안에만 있으므로, 그 조상을 가진 것만 센다."""
        # 2026-09-10 실사고 수정 — 사이드패널(대시보드)을 옆에 띄워두면 Flow 그리드 폭이
        # 줄어들어 실제 생성된 이미지가 353px로 렌더되는데, 기준이 >400이라 전부 못 잡고
        # "완료 감지 실패로 영원히 대기"하는 사고가 있었다(사용자 지적: "생성 다 되도 왜
        # 진행을 안 해"). 캐릭터 썸네일 스트립(~25~40px)과는 여전히 확실히 구분되는 150으로
        # 낮췄다 — capture_newest_images()와 반드시 같은 기준을 유지할 것.
        return int(self.c.js(
            "(()=>[...document.querySelectorAll('img')]"
            ".filter(i=>i.naturalWidth>400 && i.getBoundingClientRect().width>150"
            " && i.src.includes('flow-content.google') && i.closest('flow-character-tile')==null).length)()"
        ) or 0)

    def loading_count(self):
        """2026-09-10 추가 — Flow가 타일 위에 직접 그리는 '.loading-percentage'(예: '20%')
        엘리먼트 개수. 사용자가 실측으로 확인해준 훨씬 확실한 진행 신호 — 이게 0이 되면
        더 이상 생성 중인 게 없다는 뜻이라, 화면 렌더 크기에 좌우되는 media_count()의 그리드
        레이아웃 의존성(사이드패널 유무로 353px/400px 등 계속 흔들리던 문제)이 없다."""
        return int(self.c.js(
            "document.querySelectorAll('.loading-percentage').length"
        ) or 0)

    def loading_percent_text(self):
        """2026-09-11 추가 — 사용자 지적: "시작을 그냥 플로우에서 보여주는 %를 그대로
        보여줘". 예전엔 대시보드가 "경과 X초"라는 자체 타이머만 보여줘서, 실제로 Flow가
        진행 중인지(예: 37%) 멈춰있는지(예: 계속 0%) 구분이 안 됐다 — Flow 화면의
        '.loading-percentage' 엘리먼트가 실제로 표시하는 텍스트(예: "42%")를 그대로 읽어
        상태에 실어 보낸다. 여러 장을 동시에 만드는 중이면 엘리먼트가 여러 개일 수 있어
        가장 낮은(=가장 최근 시작한) 값을 대표로 쓴다."""
        raw = self.c.js(
            "JSON.stringify([...document.querySelectorAll('.loading-percentage')]"
            ".map(e=>e.textContent.trim()))"
        )
        try:
            texts = json.loads(raw) if raw else []
        except Exception:
            texts = []
        return texts[0] if texts else None

    def wait_done(self, before: int, timeout=480, tag="", on_tick=None, stop_check=None):
        # 2026-09-09 수정 — 기본 240초가 실측 생성 시간(특히 부하가 있을 때)보다 짧아서
        # 실제로는 조금 뒤에 완성되는데도 타임아웃으로 오판하는 사례가 있었다(S05 실측:
        # 두 번의 240초 시도가 다 끝난 직후 화면엔 이미 완성돼 있었음). 480초로 늘림.
        # 2026-09-11 재작성 — 사용자 지적: "무작위로 프롬프트를 밀어넣을게 아니라 0%로
        # 시작되었는지 판단하고 100%로 완료되었는지 판단하고 이미지 등록 확인하고 그다음
        # 진행해야지, 안그러면 나중에 시간이 더 걸린다". 예전엔 loading 표시를 한 번도
        # 못 봐도 media_count()만 늘면 완료로 인정하는 예외가 있었는데, 그 틈에 "사실
        # 아직 이전 씬 그림이거나 진행 중"인 걸 완료로 오판해서 S15/S17/S18이 전부 한 칸씩
        # 밀린 씬으로 저장되는 사고로 이어졌다(실측 확인). 반드시 loading_count()>0(=0%
        # 시작 확인)을 한 번 이상 실제로 관측해야만, 그게 다시 0으로 돌아온 시점(=100%
        # 완료)을 성공으로 인정한다 — media_count 단독 증가는 더 이상 완료 신호로 안 쓴다.
        # 2026-09-11 (2차) 수정 — 사용자 지적: "로그는 또 로그를 확인을 해야하잖아 — 왜
        # 일을 두 번 하니", "그냥 플로우에서 보여주는 %를 그대로 보여줘". 로그 파일에
        # 경고를 남기는 대신, Flow 화면이 실제로 표시하는 퍼센트 텍스트(loading_percent_text)를
        # 매 폴링마다 그대로 on_tick에 실어 보낸다 — 호출부가 이미 보고 있는 대시보드 화면에
        # 바로 반영하므로 별도로 로그를 열어 확인할 필요가 없다.
        t0 = time.time()
        started = False
        # 제출 직후 로딩 표시가 아주 짧게 떴다 사라질 수 있어서, 시작 확인을 놓치지 않도록
        # 처음 8초는 1초 간격으로 더 촘촘히 본다.
        fast_poll_until = t0 + 8
        # 2026-09-12 추가 — 사용자 지적: "멈추기를 누르거나 새로고침을 하면 대기가 멈추거나
        # 사라져야 하는데 계속진행됨". stop.flag는 그동안 씬과 씬 사이(이 while 루프 바깥)
        # 에서만 확인했는데, 이 루프 자체가 최대 480초(3회 반복이면 최대 1440초)까지 돈다 —
        # 그 안에서는 "멈추기"를 눌러도 이 루프가 끝날 때까지 전혀 반응하지 않았다. 매 폴링
        # 간격마다 stop_check()도 같이 확인해서 즉시(최대 1~4초 내) 빠져나오게 한다.
        while time.time() - t0 < timeout:
            if stop_check and stop_check():
                log(f"  ★stop.flag 감지 — {tag} 대기 중단")
                return None
            lc = self.loading_count()
            pct = self.loading_percent_text() if lc > 0 else None
            elapsed = time.time() - t0
            if lc > 0:
                started = True
            elif started:
                return True
            if on_tick:
                on_tick(elapsed, started, pct)
            time.sleep(1 if time.time() < fast_poll_until else 4)
        self.shot((tag or "wait") + "_TIMEOUT")
        return False

    # ── 회수 (스크린샷 클립 방식 — README 참고) ────────────────
    def capture_newest_images(self, out_dir: Path, tag: str, n=1, scale=2):
        """가장 최근 생성된 이미지 n장의 실제 원본 파일을 그대로 다운로드해 저장한다.
        2026-09-09 재작성 — 원래는 화면 좌표를 스크린샷으로 잘라내는 방식(Page.captureScreenshot
        + clip)이었는데, naturalWidth>400인 <img>가 실제로는 37개나 걸리는 화면(상단 캐릭터
        썸네일 스트립 25px, 우측 히스토리 패널 카드 40~248px, 진짜 메인 이미지 888px 등 전부
        원본 파일 해상도는 400 초과라서)에서 DOM 순서상 맨 앞(=썸네일)을 잘라버려 계속 깨진
        파일이 저장되는 사고가 반복됐다. 화면 렌더 크기(rect.width>400)로 실제 메인 이미지만
        골라내도록 1차 수정했지만, 사용자 지적대로 애초에 "화면을 자르는" 접근 자체가 근본적으로
        불안정하다 — 뷰포트 렌더 해상도에 갇히고, 좌표/레이아웃이 바뀌면 또 깨진다. 12번(캐릭터
        시스템) 단계에서 이미 검증된 방법대로, <img>의 실제 src(flow-content.google 도메인,
        서명된 signed URL)를 찾아 그 원본 파일을 그대로 httpx로 다운로드하는 방식으로 교체했다
        — 이 URL의 CORS 제약은 브라우저 안에서만 적용되므로 서버사이드 httpx로는 그냥 받아지고,
        원본 해상도 그대로 받기 때문에 화면 렌더 크기와 무관하게 항상 정확하다."""
        # 2026-09-09 추가 발견 — flow.google.com/asb/... 도메인 이미지(Flow UI 자체의 프로필/
        # 아바타 등 크롬 요소로 추정)가 우연히 rect.width>400을 만족해 최우선으로 뽑히는 사고가
        # 있었다. 이 도메인은 구글 로그인 세션 쿠키가 있어야만 받아져서 서버사이드 httpx로는
        # 302(로그인 페이지 리다이렉트)만 돌아온다 — 실제 생성 이미지 CDN 도메인
        # (flow-content.google)만 후보로 남기도록 필터를 추가했다.
        out_dir.mkdir(parents=True, exist_ok=True)
        # 2026-09-10 실사고 수정 — width>150으로 낮추고 나니(사이드패널 때문에 그리드가
        # 좁아져도 감지되게) 화면에 동시에 여러 장(S01~S04 등)이 같은 크기로 걸려서, "면적이
        # 가장 큰 것" 기준으로는 그중 아무거나 뽑힐 수 있었다. read_newest_title()이 이미
        # 검증한 대로 Flow 그리드는 DOM 순서 첫 번째가 항상 가장 최근 생성물이다 — 면적 정렬을
        # 버리고 DOM 순서 그대로(첫 n개)를 쓴다.
        # 2026-09-10 실사고 수정(2) — 캐릭터 첨부 기능 도입 후, 실제 결과물이 아니라 첨부된
        # 캐릭터의 포트레이트(<flow-character-tile> 안의 썸네일)가 저장되는 사고가 실측됨
        # (S14, 9224 — 회수된 파일이 장면이 아니라 그냥 캐릭터 얼굴이었음). 결과물 타일은
        # 항상 <flow-image-tile> 안에만 있으므로 캐릭터 타일 조상을 가진 건 후보에서 뺀다.
        def read_srcs():
            raw = self.c.js(r"""(()=>{const imgs=[...document.querySelectorAll("img")]
              .filter(i=>i.naturalWidth>400 && i.src.includes("flow-content.google")
                && i.closest("flow-character-tile")==null);
              const big = imgs.filter(i=>i.getBoundingClientRect().width>150);
              return JSON.stringify(big.map(b=>b.src));})()""")
            return json.loads(raw) if raw else []

        srcs = read_srcs()
        # 2026-09-11 실사고 수정 — wait_done()이 "로딩 표시 사라짐"을 완료 신호로 보고
        # 통과시킨 직후, 실제로는 새 썸네일의 src가 아직 DOM에 반영되기 전이라 그리드 맨
        # 앞이 여전히 직전 씬의 이미지인 순간이 있다(레이스 컨디션). 그 상태로 그냥
        # 캡처하면 서로 다른 씬 번호로 완전히 동일한 파일이 저장된다(S03/S04, S09/S12,
        # S10/S11, S13/S14 전부 이 패턴으로 실측 확인됨 — md5 완전 일치). 직전에 저장한
        # src와 겹치면 새 썸네일이 실제로 뜰 때까지 최대 20초 더 폴링한다.
        if self.last_srcs and srcs[:n] == self.last_srcs:
            for _ in range(10):
                time.sleep(2)
                srcs = read_srcs()
                if srcs[:n] != self.last_srcs:
                    break
            else:
                log(f"  ⚠ {tag} 20초를 더 기다려도 그리드 맨 앞 이미지가 직전 씬과 동일 — "
                    f"그대로 저장하지만 중복일 수 있으니 나중에 확인 필요")
        # 2026-09-11 추가 — 사용자 지적대로, "직전과 다르다"만으로는 부족했다(S15가
        # S14의 진짜 결과물을, S17이 S15의 진짜 결과물을 가져간 연쇄 밀림 사고 — 매번
        # src가 '직전과는' 달랐지만 실제로는 한 칸씩 밀린 오래된 결과물이었다). DOM이
        # 완전히 안정될 때까지(연속 두 번 같은 값) 기다려서, 그리드가 막 갱신되는
        # "과도기" 순간을 잡아채는 걸 막는다.
        stable = read_srcs()[:n]
        for _ in range(8):
            time.sleep(1.5)
            again = read_srcs()[:n]
            if again == stable:
                srcs = stable
                break
            stable = again
        else:
            log(f"  ⚠ {tag} 그리드가 12초 동안 계속 바뀌어서 안정화를 못 기다림 — 마지막 값으로 진행")
            srcs = stable
        saved = []
        for i, src in enumerate(srcs[:n]):
            ext = ".png" if ".png" in src.split("?")[0].lower() else ".jpg"
            resp = httpx.get(src, timeout=30)
            resp.raise_for_status()
            path = out_dir / f"{tag}_{i}{ext}"
            path.write_bytes(resp.content)
            saved.append(str(path))
        self.last_srcs = srcs[:n]
        return saved

    # ── 2026-09-13 추가 — 영상 클립 회수 ────────────────────────
    # 사용자 요청: "탭으로 이미지/영상 구분해주고 ... 자동 생성 버튼까지 전부 연결".
    # capture_newest_images()와 같은 패턴(그리드 DOM 순서 첫 n개, 안정화 대기 후 원본
    # 다운로드)을 <video> 태그에 그대로 적용한다.
    # ⚠️⚠️ 2026-09-13 — 이 메서드는 아직 실기 테스트 전이다. capture_newest_images()는
    # 사용자가 실제로 여러 번 돌려보고 검증된 코드지만, 이 영상용 버전은 Flow가 영상
    # 결과물을 그리드에 <video src="..."> 로 바로 렌더하는지(포스터 썸네일만 <img>로
    # 보이고 실제 파일 URL은 다른 방식으로만 노출될 수도 있음) 확인된 바 없다. 처음 쓸 때
    # 이 메서드가 빈 배열을 돌려주거나 타임아웃이 나면, 여기 querySelector 로직부터 실제
    # Flow 화면 DOM을 직접 열어보고(F12 개발자 도구) 다시 맞출 것 — 잘못돼도 최악의 경우는
    # "못 찾아서 등록 실패"이지 엉뚱한 파일을 잘못 등록하는 것은 아니다(도메인 필터로
    # flow-content.google 소스만 받으므로).
    def capture_newest_videos(self, out_dir: Path, tag: str, n=1):
        out_dir.mkdir(parents=True, exist_ok=True)

        def read_video_srcs():
            raw = self.c.js(r"""(()=>{const vids=[...document.querySelectorAll("video")]
              .filter(v=>(v.currentSrc||v.src||"").includes("flow-content.google")
                && v.closest("flow-character-tile")==null);
              return JSON.stringify(vids.map(v=>v.currentSrc||v.src));})()""")
            return json.loads(raw) if raw else []

        srcs = read_video_srcs()
        if self.last_video_srcs and srcs[:n] == self.last_video_srcs:
            for _ in range(10):
                time.sleep(2)
                srcs = read_video_srcs()
                if srcs[:n] != self.last_video_srcs:
                    break
            else:
                log(f"  ⚠ {tag} 20초를 더 기다려도 그리드 맨 앞 영상이 직전 씬과 동일 — "
                    f"그대로 저장하지만 중복일 수 있으니 나중에 확인 필요")
        stable = read_video_srcs()[:n]
        for _ in range(8):
            time.sleep(1.5)
            again = read_video_srcs()[:n]
            if again == stable:
                srcs = stable
                break
            stable = again
        else:
            log(f"  ⚠ {tag} 그리드가 12초 동안 계속 바뀌어서 안정화를 못 기다림 — 마지막 값으로 진행")
            srcs = stable
        if not srcs:
            raise RuntimeError(
                "★그리드에서 <video src>를 하나도 못 찾았다 — Flow가 영상을 <video> 태그로 "
                "바로 렌더하지 않을 수 있다(실기 미검증 항목). 대시보드의 '🔗 링크로 등록'으로 "
                "수동 등록해주세요."
            )
        saved = []
        for i, src in enumerate(srcs[:n]):
            ext = ".webm" if ".webm" in src.split("?")[0].lower() else ".mp4"
            resp = httpx.get(src, timeout=60)
            resp.raise_for_status()
            path = out_dir / f"{tag}_{i}{ext}"
            path.write_bytes(resp.content)
            saved.append(str(path))
        self.last_video_srcs = srcs[:n]
        return saved

    # 2026-09-13 추가 — driver.py 상단 docstring(17~18줄)에 이미 적혀 있던 예측: "동영상처럼
    # 크레딧이 드는 건 제출 후 확인 카드가 뜰 가능성이 높다(2026-09-06 시점엔 이미지만 실측,
    # 클립은 미실측)". ⚠️ 이 메서드는 그 예측에 대한 최선의 추정 구현이고 실기 검증 전이다 —
    # 정확한 버튼 문구/위치를 알 수 없어서, 제출 직후 화면 하단(컴포저 근처)에 새로 나타난
    # 버튼 중 흔한 확인 문구("생성"/"계속"/"확인"/"Generate"/"Continue"/"Confirm")를 찾아
    # 있으면 누르고, 없으면(카드가 애초에 안 뜨는 설정이었을 수 있음) 아무것도 안 하고 넘어간다
    # — 잘못 눌러도 이미 우리가 의도한 이 씬의 제출을 그대로 진행시키는 버튼만 찾으므로, 엉뚱한
    # 다른 동작을 실행할 위험은 낮다고 판단했다.
    def confirm_generation_if_prompted(self, wait=4.0):
        deadline = time.time() + wait
        labels = ["생성", "계속", "확인", "Generate", "Continue", "Confirm"]
        while time.time() < deadline:
            r = self.c.js(r"""(()=>{const H=innerHeight;const labels=%s;
              const b=[...document.querySelectorAll("button,[role=button]")].filter(e=>{
                if(!e.offsetParent) return false;
                const r=e.getBoundingClientRect();
                if(r.top < H*0.5) return false;
                const t=(e.innerText||"").replace(/\s+/g," ").trim();
                return labels.includes(t);
              });
              if(!b.length) return "NF";
              const r=b[0].getBoundingClientRect();
              return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)});})()""" % json.dumps(labels))
            if r != "NF" and r:
                d = json.loads(r)
                log("  (생성 전 확인 카드로 추정되는 버튼 발견 — 클릭)")
                self.click_xy(d["x"], d["y"])
                time.sleep(1.0)
                return True
            time.sleep(0.5)
        return False


def main():
    global STATUS_PATH
    cdp_url = os.environ.get("BU_CDP_URL", "http://127.0.0.1:9223")
    stage = os.environ.get("FLOW_STAGE", "images")

    # 2026-09-11 추가 — job.json 없이도 그냥 "지금 이 계정에 어떤 프로젝트가 있는지"만
    # 가볍게 조회하는 모드. 대시보드의 "프로젝트 불러오기" 버튼이 이 모드로 짧게 한 번
    # 실행하고 stdout의 JSON 한 줄만 읽어간다(상태 파일·job 없이 즉시 종료).
    if stage == "list_projects":
        c = CDP(cdp_url)
        F = EconFlow(c, shots=Path("_shots_tmp"))
        projects = F.list_projects()
        print("LIST_PROJECTS_RESULT:" + json.dumps({"ok": True, "projects": projects}, ensure_ascii=False))
        return

    job_path = Path(os.environ["FLOW_JOB"]).resolve()
    job = json.loads(job_path.read_text(encoding="utf-8"))
    here = job_path.parent
    STATUS_PATH = here / "status.json"
    (here / "stop.flag").unlink(missing_ok=True)
    state_path = here / "_flow_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    state.setdefault("project_url", None)
    state.setdefault("titles", {})
    state.setdefault("done", {})

    def save():
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

    only = os.environ.get("FLOW_ONLY", "")

    c = CDP(cdp_url)
    log(f"[harness] 연결됨 → {c.target.get('url')}")
    F = EconFlow(c, shots=here / "_shots")

    # 2026-09-11 추가 — job.json에 project_url이 명시돼 있으면(대시보드에서 사람이 목록에서
    # 직접 고른 경우) 로컬 상태 파일에 캐싱된 값보다 항상 우선한다 — 캐릭터가 실제로 등록된
    # 프로젝트와 어긋나는 사고(9b8ec20f... 오래된 프로젝트를 계속 쓰던 실사고)를 막기 위함.
    chosen_project = job.get("project_url") or state.get("project_url")
    state["project_url"] = F.open_project(chosen_project)
    save()
    log("프로젝트:", state["project_url"])

    if not state.get("settings_done"):
        settings_ok = F.ensure_settings(job.get("ratio", "16:9"), job.get("video_ratio", job.get("ratio", "16:9")),
                                         job.get("agent_off", True), int(job.get("image_count", 1)))
        if not settings_ok:
            msg = "설정 칩을 못 찾아 비율·에이전트 설정을 하나도 못 함 — 화면이 예상한 컴포저 상태가 아닐 수 있음(예: 채팅형 에이전트 세션). 다음 실행에서 다시 시도할 수 있게 settings_done을 저장하지 않고 지금 실행은 중단합니다."
            log(f"  ✗ {msg}")
            write_status(phase="failed", error=msg, failures=[{"id": "*", "error": msg}])
            cleanup_debris(here)
            return
        state["settings_done"] = True
        save()

    out_dir = here / "output" / "images"

    if stage in ("images", "all"):
        style = job.get("style", "")
        images = job.get("images") or []
        # ★여러 앵커 지원 — 시대·톤이 크게 갈리는 편(예: 1880년대 빈티지 vs 현재 그래픽)은
        #   앵커 하나로 룩을 억지로 통일하면 안 어울린다. 각 이미지는 "ref"로 자신이 참조할
        #   앵커의 id를 명시한다. ref가 없고 자기 자신도 앵커가 아니면 첫 앵커를 기본값으로 쓴다.
        by_id = {i["id"]: i for i in images}
        # 2026-09-09 수정 — 예전엔 anchor가 하나도 없으면 첫 이미지를 기본 앵커로 삼아
        # 매 씬마다 그 이미지를 참조로 자동 첨부했다. 지금 우리 프롬프트(경제학 파이프라인
        # 14번)는 캐릭터 외형을 매 씬 텍스트 안에 통째로 다시 적어두는 방식이라 참조 첨부가
        # 필요 없고, 오히려 첫 씬(콜드오픈 등 전혀 다른 장면)을 억지로 참조하면 구도가
        # 이상해진다 — "ref"를 명시한 이미지만 참조를 쓰도록 폴백을 없앴다.
        default_anchor = next((i for i in images if i.get("anchor")), None)
        # 2026-09-10 추가 — "일관성은?" 요청: 새 프로젝트(빈 프로젝트)에서 첫 1~3장이 스타일이
        # 튀는 문제(S05/S06/S07/S50 실사고)의 해결책으로, Flow의 공식 "캐릭터"(Ingredient)
        # 기능을 씀 — 프로젝트 안에 미리 만들어둔 캐릭터를 애셋 검색(attach_reference, 캐릭터도
        # 같은 피커에서 검색됨을 실측 확인)으로 매 씬마다 첨부한다.
        # 2026-09-11 다중 캐릭터로 확장 — 사회자(젠틀맨 루즈)와 배우(스틱맨 베이스)처럼
        # 캐릭터가 여러 개면 각자 다른 매칭 키워드로 구분해서 첨부해야 한다. 각 항목은
        # {"name": <Flow에 등록된 캐릭터 이름>, "match": [<이 중 하나라도 프롬프트에 있으면 첨부>]}.
        characters = job.get("characters") or []
        image_count = int(job.get("image_count", 1))
        one_image_instruction = image_count_instruction(image_count)
        total = len(images)
        done_count = len(state["done"])
        # 2026-09-11 추가 — 사용자 지적: "그런 오류를 표시를 해줘야 다시 시작을 하던 할꺼 아니야".
        # 예전엔 phase="failed"만 기록해서 다음 씬이 시작되는 순간 곧바로 덮어써졌고(화면엔
        # "실행 중인 계정 없음"으로만 보임), 실패 사유(에러 메시지)도 아예 기록되지 않아서
        # run.log 파일을 직접 열어보지 않는 한 뭐가 왜 실패했는지 알 길이 없었다. 이번 실행에서
        # 쌓인 실패를 전부 이 리스트에 누적해서 write_status(failures=...)로 계속 실어 보낸다 —
        # 대시보드(status_dashboard.py)가 이걸 읽어 배너로 보여주고 재시작 버튼을 제공한다.
        failures: list[dict] = []
        write_status(
            total=total, done_count=done_count, phase="idle",
            workflow=job.get("workflow", ""),
            content_no=job.get("content_no"),
            content_title=job.get("content_title", ""),
            step_no=job.get("step_no"),
            step_name=job.get("step_name", ""),
            failures=failures,
        )
        for im in images:
            if stop_requested(here):
                log("★stop.flag 발견 — 사용자 요청으로 중단합니다.")
                write_status(phase="stopped")
                cleanup_debris(here)
                break
            active_ids = job.get("active_ids")
            if active_ids is not None and im["id"] not in active_ids:
                continue
            if im["id"] in state["done"] or (only and im["id"] != only):
                continue
            try:
                ref_id = im.get("ref") if not im.get("anchor") else None
                ref_im = by_id.get(ref_id) if ref_id else (None if im.get("anchor") else default_anchor)
                label = "앵커" if im.get("anchor") else (f"참조={ref_im['id']}" if ref_im else "참조없음")
                log(f"[{im['id']}] {label}")
                prompt = " ".join(x for x in (one_image_instruction, style, im["prompt"]) if x)
                write_status(current=im["id"], phase="submitting", elapsed=0, done_count=done_count, total=total,
                             current_prompt=prompt)
                # 2026-09-09 추가 — 첫 생성(새 프로젝트 직후 등)이 유독 느려 240초 타임아웃에
                # 걸리는 사례를 실측으로 확인(S01). 실패해도 스킵하지 않고 한 번 더 시도한다 —
                # 이미 제출된 프롬프트가 뒤늦게 완료돼도 media_count 기준으로 다시 잡아낸다.
                saved = None
                # 2026-09-10 수정 — 예전엔 타임아웃마다 같은 프롬프트를 새로 제출해서
                # 아직 끝나지 않은 생성 위에 또 제출이 겹치는 "계속 생성 중" 현상이 있었다.
                # 이제는 딱 한 번만 제출하고, 끝날 때까지 재제출 없이 계속 기다린다
                # (wait_done을 구간별로 반복 호출해서 총 대기시간만 늘리는 방식).
                # 2026-09-11 수정 — attach_reference()가 이제 "@이름"을 컴포저에 직접 타이핑해서
                # 멘션을 붙이는 방식이라(위 메서드 docstring 참고), 첨부 뒤에 composer_click()을
                # 또 부르면 방금 붙인 멘션이 지워진다. 컴포저는 이 블록 시작에 한 번만 클릭해서
                # 비우고, 그 뒤로는 계속 같은 컴포저에 이어서 타이핑만 한다.
                F.composer_click()
                if ref_im and state["titles"].get(ref_im["id"]):
                    F.attach_reference(state["titles"][ref_im["id"]])
                # 2026-09-10 실사고 수정 — 캐릭터가 있으면 씬 내용과 무관하게 무조건 매 씬마다
                # 첨부했는데, 실제로 캐릭터가 안 나오는 씬(S50: 변호사들이 서류 보여주는 장면)에
                # 억지로 캐릭터가 끼어들어가는 사고가 실측으로 확인됨. VEO Automation(참고 확장
                # 프로그램) 가이드에도 있는 원칙 그대로 — "프롬프트에 캐릭터가 실제로 언급된
                # 씬에만" 첨부한다. 2026-09-11 — 캐릭터 여러 개 지원: 각자 매칭 키워드가
                # 프롬프트에 있는지 따로 확인해서, 해당하는 캐릭터를 전부(0~여러 개) 첨부한다.
                for char in characters:
                    name = char.get("name")
                    if name and any(kw in im["prompt"] for kw in char.get("match") or []):
                        F.attach_reference(name)
                # 2026-09-12 수정 — 사용자 지적("66번부터 거절") + 홍허브 벤치마킹(nam-ai-trend/
                # 7_threads_auto: "타이핑은 클립보드 복사+Ctrl+V로 붙여넣어 봇탐지 회피") 확인
                # 결과, 씬 프롬프트(가장 긴 텍스트, 매 씬 반복)는 한 글자씩 타이핑 대신
                # 붙여넣기로 바꾼다.
                F.paste_text(prompt)
                before = F.media_count()
                F.submit()
                write_status(current=im["id"], phase="waiting", elapsed=0, gen_percent=None)
                for wait_round in range(3):
                    base_elapsed = wait_round * 480

                    # 2026-09-11 (2차) 수정 — 사용자 요청: "그냥 플로우에서 보여주는 %를
                    # 그대로 보여줘". Flow 화면의 실제 퍼센트 텍스트(gen_percent)를 그대로
                    # 상태에 실어서, 대시보드가 자체 타이머 대신 이 값을 바로 보여줄 수 있게
                    # 한다 — started=False(아직 로딩 표시 자체가 안 뜬 상태)면 gen_percent도
                    # None이라 "시작 대기" 상태임이 화면에서 바로 구분된다.
                    def tick(t, started, pct, _base=base_elapsed, _id=im["id"]):
                        write_status(current=_id, phase="waiting", elapsed=round(_base + t),
                                     gen_percent=pct, gen_started=started)

                    wr = F.wait_done(before, timeout=480, tag=im["id"], on_tick=tick,
                                      stop_check=lambda: stop_requested(here))
                    if wr is None:
                        # stop.flag 감지로 중단된 경우 — 이 씬은 실패로 기록하지 않고
                        # (다음 배치에서 다시 시도 가능하도록) 바로 바깥 루프까지 빠져나간다.
                        write_status(phase="stopped")
                        cleanup_debris(here)
                        raise KeyboardInterrupt("stop.flag")
                    if wr:
                        # 2026-09-11 수정 — 생성 장수 드롭다운(image_count)만큼 로컬엔 다 저장한다
                        # (n=1 고정이면 2장 이상 선택했을 때 나머지가 그냥 버려짐). 다만 홍허브
                        # scenePrompts 등록(- 장면이미지: 줄)은 필드가 URL 하나뿐이라 여전히
                        # saved[0](가장 최근 1장)만 쓴다 — 여러 장 등록은 별도 기능이 필요하다.
                        saved = F.capture_newest_images(out_dir, im["id"], n=image_count)
                        break
                    log(f"  … {im['id']} 아직 생성 중 (누적 대기 {(wait_round + 1) * 480}초, 재제출하지 않고 계속 기다림)")
                if not saved:
                    timeout_msg = f"최종 타임아웃 (총 {3 * 480}초 대기)"
                    log(f"  ✗ {im['id']} {timeout_msg}")
                    failures.append({"id": im["id"], "error": timeout_msg})
                    write_status(current=im["id"], phase="failed", error=timeout_msg, failures=list(failures))
                    continue
                # 2026-09-11 추가 — 사용자 지적: "이미지 등록 확인하고 그다음 진행해야지".
                # capture_newest_images()의 DOM 안정화 대기로도 못 거른 경우를 대비한
                # 마지막 안전망 — 방금 받은 파일이 바로 직전에 성공한 씬과 바이트 단위로
                # 완전히 같으면(예: S03/S04, S13/S14 실사고 패턴) 절대 다음 씬으로 넘어가지
                # 않고 이 씬을 실패로 기록한다 — 등록 안 됨 = pending 상태 그대로 남아
                # 나중에 다시 시도할 수 있다.
                new_hash = hashlib.md5(Path(saved[0]).read_bytes()).hexdigest()
                if new_hash == state.get("_last_saved_hash"):
                    dup_msg = "직전 씬과 완전히 동일한 이미지(중복 캡처 의심) — 등록하지 않고 건너뜀"
                    log(f"  ✗ {im['id']} {dup_msg}")
                    failures.append({"id": im["id"], "error": dup_msg})
                    write_status(current=im["id"], phase="failed", error=dup_msg, failures=list(failures))
                    continue
                # 2026-09-10 실사고 수정 — read_newest_title()이 제목을 읽으려고 상세보기 페이지로
                # 들어갔다가 그리드로 못 돌아오는 사고가 있었다(뒤로가기 클릭 좌표가 상황에 따라
                # 안 맞음). 지금 우리 job(ref/anchor 없음)은 이 제목을 attach_reference에서 전혀
                # 쓰지 않으므로(참조 이미지 첨부 자체를 안 함), 굳이 상세보기에 들어갈 이유가 없다
                # — 호출 자체를 없애 그리드 화면을 벗어나지 않게 한다.
                title = im["id"]
                state["titles"][im["id"]] = title
                image_url = None
                if job.get("site_id") and job.get("unit_id") and saved:
                    try:
                        image_url = register_scene_image(job["site_id"], job["unit_id"], im["id"], Path(saved[0]))
                    except Exception as e:
                        log(f"  ⚠ {im['id']} 홍허브 자동 등록 실패(이미지는 로컬에 저장됨): {e}")
                # 2026-09-11 추가 — 사용자 지적: "이미지 등록 확인하고 그다음 진행해야지".
                # register_scene_image()가 예외 없이 URL을 돌려줘도, 업로드는 됐지만
                # scenePrompts 블록을 못 찾아 등록이 반쪽으로 끝난 경우가 있다(register_scene_image
                # 안의 "⚠ ... 등록 실패" 로그 참고). 실제로 그 URL이 받아지는지 직접 확인해서,
                # 안 되면 "완료"로 기록하지 않고 이 씬만 실패 처리해 다음 배치에서 재시도되게 한다.
                # 2026-09-12 (S74 실사고) 수정 — register_scene_image()가 site/unit/블록을 못 찾은
                # 반쪽 실패 상황에서도 이전엔 public_url을 그대로 돌려줬다(호출부가 "업로드는
                #됐으니 일단 성공"으로 오해) — 업로드는 됐는데 scenePrompts엔 등록이 안 된 채로
                # image_url이 truthy라 곧장 여기 통과했고, 결국 done으로 기록돼버려서 다음부터
                # "이미 끝난 씬"으로 계속 건너뛰는데 DB엔 영영 등록이 안 되는 상태(S74)가 됐다.
                # register_scene_image()를 그런 경우 None을 돌려주도록 고쳤으니, 여기서도
                # image_url이 애초에 없으면(등록 자체가 반쪽/전부 실패) 곧장 실패 처리한다.
                if not image_url:
                    verify_msg = "scenePrompts 등록 실패(업로드는 됐을 수 있으나 DB에 URL이 기록되지 않음)"
                    log(f"  ✗ {im['id']} {verify_msg}")
                    failures.append({"id": im["id"], "error": verify_msg})
                    write_status(current=im["id"], phase="failed", error=verify_msg, failures=list(failures))
                    continue
                try:
                    chk = httpx.get(image_url, timeout=15)
                    chk.raise_for_status()
                    if not chk.content:
                        raise RuntimeError("응답 본문이 비어있음")
                except Exception as e:
                    verify_msg = f"이미지 등록 확인 실패(업로드된 URL을 못 받아옴): {e}"
                    log(f"  ✗ {im['id']} {verify_msg}")
                    failures.append({"id": im["id"], "error": verify_msg})
                    write_status(current=im["id"], phase="failed", error=verify_msg, failures=list(failures))
                    continue
                # 2026-09-12 추가 — 사용자 요청: "저장되면 삭제해 오류내지말고". 여기까지 왔다는
                # 건 Storage 업로드 + DB 등록(scenePrompts) + 실제 URL 응답 확인까지 전부 끝났다는
                # 뜻이라(위에서 하나라도 실패하면 continue로 빠짐), 로컬 사본은 더 이상 필요
                # 없다 — 지워도 안전하다(_flow_state.json의 done 판정은 파일 존재 여부가 아니라
                # 이 딕셔너리 기록만 보고 하므로 지워도 "이미 완료" 판정엔 영향 없다).
                if saved:
                    try:
                        Path(saved[0]).unlink(missing_ok=True)
                    except Exception:
                        pass
                state["done"][im["id"]] = {"kind": "image", "title": title, "file": saved[0] if saved else None, "url": image_url}
                state["_last_saved_hash"] = new_hash
                save()
                done_count += 1
                write_status(current=im["id"], phase="done", done_count=done_count, total=total,
                             last_image=saved[0] if saved else None, last_image_url=image_url)
                log(f"  완료 · 제목={title[:30]!r} · 저장={saved}")
                if done_count % CLEANUP_EVERY == 0:
                    cleanup_debris(here)
            except Exception as e:
                # 2026-09-10 실사고 수정 — 3계정을 동시에 돌리면 UI 자동화 지점(컴포저·검색·
                # 제출 버튼 찾기 등) 중 하나가 가끔(부하 때문으로 추정) 못 찾고 실패하는데,
                # 예전엔 이게 프로세스 전체를 죽여서 그 계정에 남은 수십 개 씬이 전부 멈췄다
                # (9224 S18, 9225 S19 실사고). 한 씬 실패는 그 씬만 건너뛰고(등록 안 됨 =
                # 나중에 다시 배정 가능한 pending 상태 그대로) 다음 씬으로 계속 진행한다.
                log(f"  ✗ {im['id']} 자동화 오류로 이 씬만 건너뜀(다음 배치에서 재시도 가능): {e}")
                failures.append({"id": im["id"], "error": str(e)})
                write_status(current=im["id"], phase="failed", error=str(e), failures=list(failures))
                cleanup_debris(here)
                # 실패 시점에 열려있던 팝업(애셋 피커 등)이 다음 씬 시도를 계속 방해하지
                # 않도록 Esc로 정리한다 — 실패 없이 진행 중일 땐 어차피 영향 없음.
                try:
                    F.c.cdp("Input.dispatchKeyEvent", type="keyDown", key="Escape", code="Escape")
                    F.c.cdp("Input.dispatchKeyEvent", type="keyUp", key="Escape", code="Escape")
                except Exception:
                    pass
                continue
        else:
            write_status(phase="all_done", failures=list(failures))
            cleanup_debris(here)

    if stage in ("clips", "all"):
        # 2026-09-13 구현 — 사용자 요청(status_dashboard.py 영상 탭 "▶ 생성" 버튼을
        # 실제 자동 생성까지 연결해달라는 요청, "자동 생성 버튼까지 전부 연결"을 선택).
        # 2026-09-13 (2차) — "프레임"(시작/종료 슬롯에 기존 씬 이미지 첨부) 방식을 실제로
        # 구현했으나, 이 자동화(CDP 파일 업로드 인터셉션+검색+첨부, upload_and_attach_frame)가
        # 검증 전 상태에서 실기 멈춤을 일으켰고("생성클릭해도 반응이 없는데" 확인 요청),
        # 애초에 기존 씬 이미지 자체가 대부분 "결과/클라이맥스" 순간이라 애니메이션할 동작이
        # 안 남는다는 콘텐츠 품질 문제("시작 이미지가 족같아")도 있었다.
        # 2026-09-13 (3차) — 사용자 최종 결정: "아까 1안,2안,지금은 3안(텍스트 투 영상)으로
        # 생성이 되어야 해". 이제 각 씬의 videoPrompt 자체가 이미지 없이도 자기완결적으로
        # 동작하도록 다시 쓰였으므로(13번 마스터 프롬프트 참고), 프레임 첨부를 완전히
        # 제거하고 순수 텍스트만 제출한다 — upload_and_attach_frame()/frame_slot/image_url은
        # 이 단계에서 더 이상 쓰지 않는다(코드는 남아있지만 미사용).
        # ⚠️ 아래 루프는 이미지 단계(위, 사용자가 수백 개 씬으로 실제 검증한 코드)를 최대한
        # 그대로 미러링했지만, 다음 항목들은 여전히 실기 테스트 전이다 — 처음 쓸 때는 반드시
        # 사람이 화면 옆에서 직접 지켜보면서 씬 1개만으로 먼저 확인할 것(크레딧이 실제로
        # 나간다, 8초=12크레딧 기본):
        #   1) ensure_video_frame_settings() — 동영상/프레임/16:9/모델/해상도/길이/배치수
        #      버튼들의 정확한 텍스트·위치가 사용자 실측 스크린샷과 일치하는지.
        #   2) confirm_generation_if_prompted() — 제출 후 뜰 수 있다고 예측된 "생성 전
        #      확인" 카드의 정확한 버튼 문구/위치.
        #   3) capture_newest_videos() — 그리드에 실제 결과물이 <video src="...">로
        #      바로 렌더되는지(포스터 썸네일만 있고 실제 파일은 다른 방식으로 노출될
        #      가능성 있음). 못 찾으면 예외를 던지고 그 씬은 실패로 기록되니(크레딧은
        #      이미 소모됨) 대시보드의 "🔗 링크로 등록"으로 수동 등록해줄 것.
        log("★클립 단계 시작(텍스트 투 영상, 프레임 미사용) — 이 경로는 실기 검증 전입니다. 반드시 소량으로 직접 확인하며 진행하세요.")
        style = job.get("style", "")
        images = job.get("images") or []
        characters = job.get("characters") or []
        image_count = int(job.get("image_count", 1))
        one_image_instruction = image_count_instruction(image_count)
        total = len(images)
        done_count = len(state["done"])
        failures: list[dict] = []
        write_status(
            total=total, done_count=done_count, phase="idle",
            workflow=job.get("workflow", ""),
            content_no=job.get("content_no"),
            content_title=job.get("content_title", ""),
            step_no=job.get("step_no"),
            step_name=job.get("step_name", ""),
            failures=failures,
        )
        out_dir = here / "output" / "videos"
        # 2026-09-13 (6차) 수정 — 실사고: needsVideoClip 씬을 제출했는데 실제로는 이미지가
        # 생성됨(사용자 지적: "왜 영상이 아니라 이미지를 만들었을까"). 원인은 두 가지가 겹쳐
        # 있었다 — (1) ensure_agent_off()가 이미지 단계(ensure_settings 안)에만 연결돼 있고
        # 영상(clips) 단계에는 애초에 빠져 있어서, Flow의 대화형 "에이전트" 모드가 켜진 채로
        # 프롬프트가 그 채팅창에 그대로 들어가 AI가 스스로 이미지를 만들어버렸다. (2) 설정
        # 칩을 못 찾아 동영상 모드 전환 자체가 실패해도 그냥 경고만 남기고 계속 진행했다
        # (사용자 지적: "실행이 안되었는데 왜 진행이 된거야?"). 이제 동영상 설정 전환이 실제로
        # 성공했는지 확인해서 실패하면 그 자리에서 전체 클립 단계를 중단한다 — 잘못된 모드로
        # 나머지 씬까지 계속 만들어 크레딧을 낭비하는 사고를 막는다.
        # 2026-09-13 (7차) 수정 — 실기 확인(9226, CDP 직접 조작으로 검증): agent_off는 이제
        # ensure_video_frame_settings() 내부에서 open_settings()보다 먼저 실행된다(순서가
        # 핵심 — 채팅형 "에이전트" 화면에선 설정 칩 자체가 안 보여서, 그 화면을 먼저 꺼야
        # 클래식 컴포저가 드러나 칩을 찾을 수 있다). 여기서 따로 또 부르지 않는다.
        settings_ok = F.ensure_video_frame_settings(
            ratio=job.get("video_ratio", "16:9"),
            model=job.get("video_model", "Omni 1.1 Flash"),
            resolution=job.get("video_resolution", "720p"),
            duration=job.get("video_duration", "8초"),
            count=int(job.get("video_count", 1)),
            agent_off=job.get("agent_off", True),
        )
        if not settings_ok:
            msg = "동영상 설정 칩을 못 찾아 영상 모드로 전환하지 못함 — 화면이 예상한 컴포저(하단 이미지/영상 탭) 상태가 아닐 수 있음. 전체 클립 단계를 중단합니다(잘못된 모드로 계속 생성되는 것을 막기 위함)."
            log(f"  ✗ {msg}")
            write_status(phase="failed", error=msg, failures=[{"id": "*", "error": msg}])
            cleanup_debris(here)
            return
        for im in images:
            if stop_requested(here):
                log("★stop.flag 발견 — 사용자 요청으로 중단합니다.")
                write_status(phase="stopped")
                cleanup_debris(here)
                break
            active_ids = job.get("active_ids")
            if active_ids is not None and im["id"] not in active_ids:
                continue
            if im["id"] in state["done"] or (only and im["id"] != only):
                continue
            if not (im.get("prompt") or "").strip():
                # needs_video로 표시됐지만 영상 프롬프트(- 영상: 줄)가 비어있는 경우 —
                # 조용히 건너뛰지 않고 실패로 남겨서 사람이 알아채게 한다.
                msg = "영상 프롬프트가 비어있음(- 영상: 줄 없음)"
                log(f"  ✗ {im['id']} {msg}")
                failures.append({"id": im["id"], "error": msg})
                write_status(current=im["id"], phase="failed", error=msg, failures=list(failures))
                continue
            try:
                # 2026-09-13 (4차) 수정 — 사용자 지시: "아까 1안,2안,지금은 3안(텍스트 투
                # 영상)으로 생성이 되어야 해". 2차 구현(씬 이미지를 "프레임" 슬롯에 자동 첨부)은
                # 검증 전 자동화(파일 업로드+검색+첨부)라 실기에서 멈춰버렸고("생성클릭해도
                # 반응이 없는데"), 애초에 그 이미지-재활용 방식 자체도 콘텐츠 품질 문제
                # ("시작 이미지가 족같아")로 폐기됐다. 지금은 각 씬의 videoPrompt 자체가
                # 이미지 없이도 자기완결적으로 동작하도록 다시 쓰였으므로(13번 마스터 프롬프트
                # 참고), 프레임 첨부를 아예 하지 않고 순수 텍스트만 제출한다 — image_url/
                # frame_slot은 더 이상 쓰지 않는다(과거 job에 남아있어도 무시).
                prompt = " ".join(x for x in (one_image_instruction, style, im["prompt"]) if x)
                write_status(current=im["id"], phase="submitting", elapsed=0, done_count=done_count, total=total,
                             current_prompt=prompt)
                saved = None
                F.composer_click()
                for char in characters:
                    name = char.get("name")
                    if name and any(kw in im["prompt"] for kw in char.get("match") or []):
                        F.attach_reference(name)
                F.paste_text(prompt)
                before = F.media_count()
                F.submit()
                # 위 경고 2) — 크레딧이 드는 생성이라 "생성 전 항상 확인" 카드가 뜰 수
                # 있다는 게 이 파일 docstring의 예측이다. 있으면 눌러서 진행하고, 없으면
                # (이미 즉시 생성됐거나 그 설정이 꺼져 있으면) 그냥 넘어간다.
                F.confirm_generation_if_prompted()
                write_status(current=im["id"], phase="waiting", elapsed=0, gen_percent=None)
                for wait_round in range(3):
                    base_elapsed = wait_round * 480

                    def tick(t, started, pct, _base=base_elapsed, _id=im["id"]):
                        write_status(current=_id, phase="waiting", elapsed=round(_base + t),
                                     gen_percent=pct, gen_started=started)

                    wr = F.wait_done(before, timeout=480, tag=im["id"], on_tick=tick,
                                      stop_check=lambda: stop_requested(here))
                    if wr is None:
                        write_status(phase="stopped")
                        cleanup_debris(here)
                        raise KeyboardInterrupt("stop.flag")
                    if wr:
                        saved = F.capture_newest_videos(out_dir, im["id"], n=1)
                        break
                    log(f"  … {im['id']} 아직 생성 중 (누적 대기 {(wait_round + 1) * 480}초, 재제출하지 않고 계속 기다림)")
                if not saved:
                    timeout_msg = f"최종 타임아웃 (총 {3 * 480}초 대기)"
                    log(f"  ✗ {im['id']} {timeout_msg}")
                    failures.append({"id": im["id"], "error": timeout_msg})
                    write_status(current=im["id"], phase="failed", error=timeout_msg, failures=list(failures))
                    continue
                title = im["id"]
                state["titles"][im["id"]] = title
                video_url = None
                if job.get("site_id") and job.get("unit_id") and saved:
                    try:
                        video_url = register_scene_video(job["site_id"], job["unit_id"], im["id"], Path(saved[0]))
                    except Exception as e:
                        log(f"  ⚠ {im['id']} 홍허브 자동 등록 실패(영상은 로컬에 저장됨): {e}")
                if not video_url:
                    verify_msg = "scenePrompts 등록 실패(업로드는 됐을 수 있으나 DB에 URL이 기록되지 않음)"
                    log(f"  ✗ {im['id']} {verify_msg}")
                    failures.append({"id": im["id"], "error": verify_msg})
                    write_status(current=im["id"], phase="failed", error=verify_msg, failures=list(failures))
                    continue
                try:
                    chk = httpx.get(video_url, timeout=30)
                    chk.raise_for_status()
                    if not chk.content:
                        raise RuntimeError("응답 본문이 비어있음")
                except Exception as e:
                    verify_msg = f"영상 등록 확인 실패(업로드된 URL을 못 받아옴): {e}"
                    log(f"  ✗ {im['id']} {verify_msg}")
                    failures.append({"id": im["id"], "error": verify_msg})
                    write_status(current=im["id"], phase="failed", error=verify_msg, failures=list(failures))
                    continue
                if saved:
                    try:
                        Path(saved[0]).unlink(missing_ok=True)
                    except Exception:
                        pass
                state["done"][im["id"]] = {"kind": "video", "title": title, "file": saved[0] if saved else None, "url": video_url}
                save()
                done_count += 1
                write_status(current=im["id"], phase="done", done_count=done_count, total=total,
                             last_image=saved[0] if saved else None, last_image_url=video_url)
                log(f"  완료(영상) · {im['id']} · 저장={saved}")
                if done_count % CLEANUP_EVERY == 0:
                    cleanup_debris(here)
            except Exception as e:
                log(f"  ✗ {im['id']} 자동화 오류로 이 씬만 건너뜀(다음 배치에서 재시도 가능): {e}")
                failures.append({"id": im["id"], "error": str(e)})
                write_status(current=im["id"], phase="failed", error=str(e), failures=list(failures))
                cleanup_debris(here)
                try:
                    F.c.cdp("Input.dispatchKeyEvent", type="keyDown", key="Escape", code="Escape")
                    F.c.cdp("Input.dispatchKeyEvent", type="keyUp", key="Escape", code="Escape")
                except Exception:
                    pass
                continue
        else:
            write_status(phase="all_done", failures=list(failures))
            cleanup_debris(here)


if __name__ == "__main__":
    main()
