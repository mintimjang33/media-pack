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
            return public_url
        script_draft = rows[0].get("script_draft") or {}
        units = script_draft.get("units") or []
        unit = next((u for u in units if u.get("id") == unit_id), None)
        if not unit:
            log(f"  ⚠ Storage 업로드는 됐지만 unit({unit_id})을 못 찾아 scenePrompts 등록 실패")
            return public_url

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
            return public_url

        unit["scenePrompts"] = "\n\n".join(new_blocks)
        patch = httpx.patch(
            f"{env['url']}/rest/v1/hub_sites", params={"id": f"eq.{site_id}"},
            headers={**headers, "Content-Type": "application/json"},
            content=json.dumps({"script_draft": script_draft}).encode("utf-8"), timeout=30,
        )
        patch.raise_for_status()
    log(f"  ✓ {scene_id} 장면이미지 홍허브에 자동 등록: {public_url}")
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
    shots_dir = here / "_shots"
    if not shots_dir.exists():
        return
    removed = 0
    for f in shots_dir.glob("*"):
        if f.is_file():
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
        # 2026-09-10 실사고 수정 — 완전히 새 계정의 첫 프로젝트(아직 아무 것도 생성 안 한
        # 빈 화면)는 설정(tune) 아이콘 자체가 툴바에 없다(실측: body 텍스트에 아예 없음).
        # 예전엔 여기서 그냥 죽어서 병렬 처리용 새 계정이 전부 못 쓰는 문제가 있었다 — 못
        # 찾으면 "기본값이 이미 맞다"고 보고 그냥 건너뛴다(기본 비율이 16:9라 우리 기본값과
        # 어차피 같다).
        pos = self.find_icon_button("tune")
        if not pos:
            log("  (설정 아이콘 없음 — 새 프로젝트 기본값 그대로 사용, 건너뜀)")
            return False
        self.click_xy(pos["x"], pos["y"])
        time.sleep(1.2)
        return True

    def close_settings(self):
        r = self.c.js(r"""(()=>{const h=[...document.querySelectorAll("*")]
          .find(e=>e.textContent.trim()==="에이전트 설정" && e.children.length===0);
          if(!h) return "NF";
          let n=h; for(let k=0;k<4;k++){ if(!n.parentElement) break; n=n.parentElement;
            const b=n.querySelector("button"); if(b){ const r=b.getBoundingClientRect();
              return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)});}}
          return "NF";})()""")
        if r != "NF":
            d = json.loads(r)
            self.click_xy(d["x"], d["y"])
            time.sleep(0.8)

    def set_ratio_chip(self, ratio: str, kind: str):
        """kind: 'image' → 이미지 생성 기본값 행 / 'video' → 동영상 생성 기본값 행.
        둘 다 화면에 같은 라벨(예: '16:9')이 두 번 나오므로 y좌표로 행을 가른다."""
        r = self.c.js(r"""(()=>{const suf=%s;
          const b=[...document.querySelectorAll("button,[role=button]")]
            .filter(e=>e.offsetParent && (e.innerText||"").replace(/\s+/g," ").trim().endsWith(suf));
          return JSON.stringify(b.map(e=>{const r=e.getBoundingClientRect();
            return {x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)};})
            .sort((a,b)=>a.y-b.y));})()""" % json.dumps(ratio))
        cands = json.loads(r) if r else []
        if not cands:
            log(f"  ✗ 비율 칩 없음: {ratio}")
            return False
        # 이미지 행이 위, 동영상 행이 아래 (실측 순서 고정)
        idx = 0 if kind == "image" else (1 if len(cands) > 1 else 0)
        c = cands[min(idx, len(cands) - 1)]
        self.click_xy(c["x"], c["y"])
        time.sleep(0.6)
        return True

    def ensure_settings(self, image_ratio="16:9", video_ratio="16:9"):
        """프로젝트당 1회 호출. 이미지·동영상 비율을 원하는 값으로 맞춘다(배치수·모델은 기본값 유지)."""
        if not self.open_settings():
            return
        self.set_ratio_chip(image_ratio, "image")
        self.set_ratio_chip(video_ratio, "video")
        self.close_settings()

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
        """'+' 로 애셋 피커를 열고 title 로 검색해 '프롬프트에 추가'."""
        pos = self.find_icon_button("add")
        if not pos:
            raise RuntimeError("★첨부(add) 아이콘을 못 찾았다")
        self.click_xy(pos["x"], pos["y"])
        time.sleep(1.5)
        # 검색창에 제목 입력
        # 2026-09-10 실사고 수정 — 프로젝트 미디어가 많아질수록(생성 반복) 피커가 열리는
        # 속도가 느려져 기존 재시도 예산(약 4초)으로도 못 잡는 경우가 실측됨(S14, 9224,
        # 두 번째 재시도까지 실패). 이 단계만 넉넉하게 늘림(최대 약 8초).
        r = self._poll_js(r"""(()=>{const i=[...document.querySelectorAll("input")]
          .find(e=>e.offsetParent && /검색/.test(e.placeholder||""));
          if(!i) return "NF"; const r=i.getBoundingClientRect();
          return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)});})()""",
                          tries=16, delay=0.5)
        if r == "NF":
            self.shot(f"attach_fail_{re.sub(r'[^A-Za-z0-9]+', '_', title)[:20]}")
            raise RuntimeError("★애셋 검색창을 못 찾았다")
        d = json.loads(r)
        search_box_y = d["y"]
        self.click_xy(d["x"], d["y"])
        time.sleep(0.3)
        self.type_text(title)
        time.sleep(1.2)
        # 2026-09-10 실사고 수정 — "검색하면 첫 결과가 자동 선택된다"(2026-09-06 실측)는
        # 항상 그런 게 아니었다 — S14 재시도 중 검색 결과가 선택 안 된 채로 뜬 경우가 실측됨
        # (미리보기·'프롬프트에 추가' 버튼 자체가 없어 다음 단계가 통째로 실패). 자동 선택을
        # 믿지 않고 검색 결과 행(제목 텍스트)을 직접 클릭해 확실히 선택시킨다.
        # 2026-09-10 실사고 수정(2) — 캐릭터를 실제로 씬에 몇 번 쓰고 나면, 그 캐릭터 이름으로
        # 시작하는 이미지 애셋들(예: "Gentleman Rouge snapping fingers")과 화면 다른 곳(상단
        # 캐릭터 스트립 등)에 같은 텍스트("Gentleman Rouge")가 동시에 존재해서, document 전체
        # 기준 첫 매치를 클릭하면 검색 팝업이 아닌 엉뚱한 요소가 클릭되는 사고가 실측됨(9223,
        # S06 — "프롬프트에 추가" 버튼이 끝내 안 뜸). 검색창 바로 아래(팝업 목록 안)에 있는
        # 후보만 인정하도록, 검색창 y좌표보다 아래에 있는 것 중 가장 위(=팝업의 첫 결과)를 쓴다.
        r = self._poll_js(r"""(()=>{const all=[...document.querySelectorAll("*")]
          .filter(e=>e.offsetParent && e.children.length===0 && (e.textContent||"").trim()===%s);
          const below = all.map(e=>({e, r:e.getBoundingClientRect()}))
            .filter(o=>o.r.top > %s).sort((a,b)=>a.r.top-b.r.top);
          if(!below.length) return "NF"; const r=below[0].r;
          return JSON.stringify({x:Math.round(r.left+10),y:Math.round(r.top+r.height/2)});})()""" % (json.dumps(title), search_box_y))
        if r == "NF":
            self.shot(f"attach_fail_row_{re.sub(r'[^A-Za-z0-9]+', '_', title)[:20]}")
            raise RuntimeError("★검색 결과 행을 못 찾았다")
        d = json.loads(r)
        self.click_xy(d["x"], d["y"])
        time.sleep(0.6)
        # '프롬프트에 추가' 버튼
        # 2026-09-10 실사고 수정(3) — 결과 행을 클릭하면 항상 미리보기+'프롬프트에 추가'
        # 버튼이 뜬다고 가정했는데, 실측해보니 행을 클릭하는 즉시 피커가 스스로 닫히며
        # 바로 첨부가 끝나는 경우도 있었다(9223 재검증 — 클릭 직후 스샷에 이미 첨부 칩이
        # 붙어 있었음). 그 경우 버튼을 아무리 기다려도 없는 게 정상이므로, 버튼이 안 보이면
        # 먼저 "검색창(피커) 자체가 닫혔는지"부터 확인해 이미 끝난 상태인지 구분한다.
        r = self._poll_js(r"""(()=>{const b=[...document.querySelectorAll("button")]
          .find(e=>e.offsetParent && (e.innerText||"").trim()==="프롬프트에 추가");
          if(!b) return "NF"; const r=b.getBoundingClientRect();
          return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)});})()""",
                          tries=6, delay=0.4)
        if r == "NF":
            picker_closed = self.c.js(r"""(()=>{const i=[...document.querySelectorAll("input")]
              .find(e=>e.offsetParent && /검색/.test(e.placeholder||""));
              return i ? "OPEN" : "CLOSED";})()""")
            if picker_closed == "CLOSED":
                log("  (행 클릭으로 피커가 바로 닫힘 — 이미 첨부된 것으로 간주)")
                return
            self.shot(f"attach_fail_addbtn_{re.sub(r'[^A-Za-z0-9]+', '_', title)[:20]}")
            raise RuntimeError("★'프롬프트에 추가' 버튼을 못 찾음")
        d = json.loads(r)
        self.click_xy(d["x"], d["y"])
        time.sleep(0.6)

    def submit(self):
        pos = self.find_icon_button("arrow_forward")
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

    def wait_done(self, before: int, timeout=480, tag="", on_tick=None):
        # 2026-09-09 수정 — 기본 240초가 실측 생성 시간(특히 부하가 있을 때)보다 짧아서
        # 실제로는 조금 뒤에 완성되는데도 타임아웃으로 오판하는 사례가 있었다(S05 실측:
        # 두 번의 240초 시도가 다 끝난 직후 화면엔 이미 완성돼 있었음). 480초로 늘림.
        # 2026-09-10 수정 — media_count()(이미지 렌더 크기 기준)만 보면 사이드패널 유무 등
        # 레이아웃 변화에 계속 흔들렸다. loading-percentage 엘리먼트가 다 사라졌는지를 우선
        # 신호로 삼고, media_count 증가를 보조 확인으로 같이 쓴다 — 최소 한 번은 로딩 표시가
        # 실제로 뜨는 걸 봐야(started=True) "처음부터 끝까지 로딩 표시가 아예 없었던" 상황과
        # "이미 다 끝나서 로딩 표시가 사라진" 상황을 구분할 수 있다.
        t0 = time.time()
        started = False
        while time.time() - t0 < timeout:
            lc = self.loading_count()
            if lc > 0:
                started = True
            elif started:
                return True
            n = self.media_count()
            if n > before and not started:
                # loading 표시를 한 번도 못 봤어도(폴링 간격 사이에 순식간에 끝난 경우)
                # 이미지 개수가 늘었으면 완료로 인정한다.
                return True
            if on_tick:
                on_tick(time.time() - t0)
            time.sleep(4)
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
        srcs = self.c.js(r"""(()=>{const imgs=[...document.querySelectorAll("img")]
          .filter(i=>i.naturalWidth>400 && i.src.includes("flow-content.google")
            && i.closest("flow-character-tile")==null);
          const big = imgs.filter(i=>i.getBoundingClientRect().width>150);
          return JSON.stringify(big.map(b=>b.src));})()""")
        srcs = json.loads(srcs) if srcs else []
        saved = []
        for i, src in enumerate(srcs[:n]):
            ext = ".png" if ".png" in src.split("?")[0].lower() else ".jpg"
            resp = httpx.get(src, timeout=30)
            resp.raise_for_status()
            path = out_dir / f"{tag}_{i}{ext}"
            path.write_bytes(resp.content)
            saved.append(str(path))
        return saved


def main():
    global STATUS_PATH
    cdp_url = os.environ.get("BU_CDP_URL", "http://127.0.0.1:9223")
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

    stage = os.environ.get("FLOW_STAGE", "images")
    only = os.environ.get("FLOW_ONLY", "")

    c = CDP(cdp_url)
    log(f"[harness] 연결됨 → {c.target.get('url')}")
    F = EconFlow(c, shots=here / "_shots")

    state["project_url"] = F.open_project(state.get("project_url"))
    save()
    log("프로젝트:", state["project_url"])

    if not state.get("settings_done"):
        F.ensure_settings(job.get("ratio", "16:9"), job.get("video_ratio", job.get("ratio", "16:9")))
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
        character_name = job.get("character_name")
        total = len(images)
        done_count = len(state["done"])
        write_status(
            total=total, done_count=done_count, phase="idle",
            workflow=job.get("workflow", ""),
            content_no=job.get("content_no"),
            content_title=job.get("content_title", ""),
            step_no=job.get("step_no"),
            step_name=job.get("step_name", ""),
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
                prompt = " ".join(x for x in (style, im["prompt"]) if x)
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
                F.composer_click()
                if ref_im and state["titles"].get(ref_im["id"]):
                    F.attach_reference(state["titles"][ref_im["id"]])
                    F.composer_click()
                # 2026-09-10 실사고 수정 — 처음엔 character_name이 있으면 씬 내용과 무관하게
                # 무조건 매 씬마다 첨부했는데, 실제로 캐릭터가 안 나오는 씬(S50: 변호사들이
                # 서류 보여주는 장면)에 억지로 캐릭터가 끼어들어가는 사고가 실측으로 확인됨.
                # VEO Automation(참고 확장 프로그램) 가이드에도 있는 원칙 그대로 — "프롬프트에
                # 캐릭터 이름이 실제로 언급된 씬에만" 첨부한다.
                if character_name and character_name in im["prompt"]:
                    F.attach_reference(character_name)
                    F.composer_click()
                F.type_text(prompt)
                before = F.media_count()
                F.submit()
                write_status(current=im["id"], phase="waiting", elapsed=0)
                for wait_round in range(3):
                    base_elapsed = wait_round * 480

                    def tick(t, _base=base_elapsed, _id=im["id"]):
                        write_status(current=_id, phase="waiting", elapsed=round(_base + t))

                    if F.wait_done(before, timeout=480, tag=im["id"], on_tick=tick):
                        saved = F.capture_newest_images(out_dir, im["id"], n=1)
                        break
                    log(f"  … {im['id']} 아직 생성 중 (누적 대기 {(wait_round + 1) * 480}초, 재제출하지 않고 계속 기다림)")
                if not saved:
                    log(f"  ✗ {im['id']} 최종 타임아웃 (총 {3 * 480}초 대기)")
                    write_status(current=im["id"], phase="failed")
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
                state["done"][im["id"]] = {"kind": "image", "title": title, "file": saved[0] if saved else None, "url": image_url}
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
                write_status(current=im["id"], phase="failed")
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
            write_status(phase="all_done")
            cleanup_debris(here)

    if stage in ("clips", "all"):
        log("★클립 단계는 이 드라이버에서 아직 실기 테스트 전입니다 — 크레딧이 나가니 직접 승인 후 진행하세요.")
        # TODO: 실측 후 구현 이식. 이미지 단계와 동일한 컴포저 흐름 + open_settings 에서
        # 배치수/모델을 동영상 쪽으로 맞추고, submit() 후 "생성 전 확인" 카드가 뜨면
        # 그 카드의 확인 버튼을 한 번 더 눌러야 할 가능성이 높다(2026-09-06 시점 미검증).


if __name__ == "__main__":
    main()
