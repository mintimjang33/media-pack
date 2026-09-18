"""flow_econ_driver.py용 실시간 컨트롤 대시보드.
2026-09-10 — 워크플로우(hub_sites) 드롭다운 → 콘텐츠(script_draft.units) 드롭다운 →
씬 스크롤 리스트(각 씬 상태: 대기/진행/완료/실패) → 시작/멈춤 버튼까지 전부 여기서 조작한다.
Flow 옆 사이드패널(scripts/status_extension)이 이 서버(127.0.0.1:8799)를 iframe으로 띄운다.

사용법:
    python scripts/status_dashboard.py [port]
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from websockets.sync.client import connect as ws_connect

HERE = Path(__file__).resolve().parent
RUN_DIR = HERE.parent / "examples-economics-newstyle" / "dashboard_runs"
RUN_DIR.mkdir(parents=True, exist_ok=True)

# ── Supabase (읽기 전용: 워크플로우/콘텐츠 목록, scenePrompts 읽기만) ──────────
# 2026-09-10 — 다른 PC에서도 쓸 수 있도록, 이 팩 자체의 `.env.local`을 먼저 찾고
# (SETUP_OTHER_PC.md 참고), 없으면 이 PC에만 있던 예전 경로(U-Short 프로젝트)로 대체한다.
_env_candidates = [
    Path(__file__).resolve().parent.parent / ".env.local",
    Path(r"C:\Users\user\Downloads\U-Short\.env.local"),
]
_env_path = next((p for p in _env_candidates if p.exists()), None)
if _env_path is None:
    raise FileNotFoundError(
        "Supabase .env.local을 찾을 수 없습니다 — flow-media-pack/.env.local을 만드세요"
        "(SETUP_OTHER_PC.md, .env.local.example 참고)."
    )
_env = {}
for _line in _env_path.read_text(encoding="utf-8").splitlines():
    if "=" in _line and not _line.strip().startswith("#"):
        k, _, v = _line.partition("=")
        _env[k.strip()] = v.strip()
SUPA_URL = _env["NEXT_PUBLIC_SUPABASE_URL"]
SUPA_KEY = _env["SUPABASE_SERVICE_ROLE_KEY"]


def supa_headers():
    return {"apikey": SUPA_KEY, "Authorization": f"Bearer {SUPA_KEY}"}


# 2026-09-12 추가 — 사용자 지적: "계정 포트를 사용자가 바꾸지 않는 한 고정되어야 한다" /
# "크롬창이 4개가 동시에 다 저장이 되는거지? 각각" — 아니다. localStorage는 브라우저
# 프로필(크롬창)마다 완전히 분리되어 있어서, 계정 4개(mintimjang33/minsiljang0/minssajang/
# minsiljjang)가 각자 다른 --user-data-dir로 뜨는 이 구성에서는 창마다 "마지막으로 고른 계정"이
# 따로 기억되고, 심지어 사이드패널 iframe과 일반 탭도 파티셔닝 때문에 저장소가 갈릴 수 있다 —
# 그래서 창을 바꿔서 열면(또는 사이드패널/탭을 오가면) 다른 계정으로 "돌아간 것처럼" 보였다.
# 이 서버(dashboard) 하나를 모든 창이 공유하므로, 선택된 계정을 서버 쪽 파일에 저장해서
# 어느 창에서 열든 항상 같은 값을 보게 한다 — localStorage는 이제 폴백일 뿐이다.
DASHBOARD_STATE_PATH = Path(__file__).resolve().parent / "dashboard_state.json"
_state_lock = threading.Lock()


def load_dashboard_state() -> dict:
    with _state_lock:
        try:
            return json.loads(DASHBOARD_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}


def save_dashboard_state(patch: dict):
    with _state_lock:
        try:
            cur = json.loads(DASHBOARD_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            cur = {}
        cur.update(patch)
        DASHBOARD_STATE_PATH.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")


# 2026-09-12 추가 — 사용자 요청: "메인컨트롤 페이지에서 각 서버띄우고 페이지 열고 각각 할 수
# 있게". 바탕화면 개별 아이콘(크롬_{계정}_켜기.bat)이 쓰던 것과 정확히 같은 파라미터를 여기서도
# 재사용해서, 메인 컨트롤 페이지의 "▶ 크롬 켜기" 버튼이 그 계정의 자동화 크롬을 직접 띄울 수
# 있게 한다 — 값은 실제 바탕화면 .bat 파일들에서 그대로 옮겨왔다(계정별 --user-data-dir).
CHROME_ACCOUNTS = {
    9223: {"name": "mintimjang33", "profile": r"C:\Users\user\flow-automation-chrome"},
    9224: {"name": "minsiljang0", "profile": r"C:\Users\user\flow-automation-chrome-minsiljang0"},
    9225: {"name": "minssajang", "profile": r"C:\Users\user\flow-automation-chrome-minssajang"},
    9226: {"name": "minsiljjang", "profile": r"C:\Users\user\flow-automation-chrome-minsiljang"},
}
CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def is_port_open(port: int, timeout: float = 0.8) -> bool:
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def has_open_tab(port: int) -> bool:
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/json", timeout=3)
        return any(t.get("type") == "page" for t in r.json())
    except Exception:
        return False


def open_tab_for_port(port: int, url: str = "https://flow.google.com/"):
    httpx.put(f"http://127.0.0.1:{port}/json/new", params={"": url}, timeout=5)


# 2026-09-12 추가, 같은 날 2차 수정 — 사용자 지적: "링크를 넣어도 자동으로 저장이 되게끔
# 해둬야지" / 1차 수정(FLOW_SHARE_RE로 공유 페이지만 골라 계정 크롬으로 열어서 실제 이미지를
# 찾는 방식)을 넣은 뒤에도 "왜 이렇게 링크로 등록을 하면 항상 깨져서 보여?"가 재발함. 원인이
# 두 가지였다:
#   1) URL 형태 판별을 정규식(`flow.google.com/shared/`) 하나에만 의존했다 — Flow가 링크
#      형식을 바꾸거나(예: www. 접두사, 쿼리스트링 등) 사람이 공유 페이지가 아닌 다른 주소를
#      붙여넣으면 정규식이 안 맞아서 그 페이지 주소를 실제 이미지인 것처럼 그냥 그대로
#      등록해버렸다.
#   2) 설령 정규식이 맞아 진짜 CDN 이미지 주소(`flow-content.google`)를 찾아내도, 그 다음
#      다운로드를 이 파이썬 프로세스가 `httpx.get()`으로 맨몸(쿠키/리퍼러 없이) 요청했다 —
#      그 CDN이 접근 권한(세션 쿠키 등)을 요구하면 200과 함께 빈 이미지/에러 페이지를 받아도
#      코드가 이를 구분하지 못하고 그대로 Storage에 "이미지"로 업로드해버려 깨진 채로 등록됐다.
# 두 문제를 함께 고친다: 먼저 URL을 직접 받아봐서 진짜 이미지(Content-Type: image/*)인지
# 확인하고(`_fetch_direct_image`), 아니면 정규식 판별 없이 무조건 계정 크롬으로 그 주소를 열어
# 화면에 뜨는 진짜 이미지를 찾은 뒤(`resolve_flow_share_image`), 다운로드도 이 서버가 아니라
# 방금 그 이미지를 실제로 띄운 그 브라우저 탭 안에서(`fetch` → `blob` → dataURL) 받아온다 —
# 쿠키/리퍼러/세션이 전부 자동으로 함께 전달되므로 권한 문제로 깨질 수가 없다. 최종적으로는
# 어떤 경로든 항상 우리 Supabase Storage에 다시 업로드해서 등록한다 — Google CDN 주소를 그대로
# 등록하면 서명 토큰이 나중에 만료돼 "등록 당시엔 멀쩡했다가 나중에 깨지는" 문제도 원천 차단된다.
def _fetch_direct_image(url: str) -> tuple[bytes, str] | None:
    """URL을 직접 받아봐서 진짜 이미지(Content-Type: image/*)면 (바이트, mime)을 돌려주고,
    아니면(HTML 공유 페이지, 에러 응답 등) None을 돌려준다 — 이때 호출자는 resolve_flow_share_image로
    폴백해야 한다."""
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
    except Exception:
        return None
    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
    if r.status_code == 200 and ctype.startswith("image/"):
        return r.content, ctype
    return None


# 2026-09-12 (5차) 수정 — 사용자 지적: "새창은 왜 뜨는거냐고~" / "스샷같은것도 왜 뜨고~" /
# "생성버튼으로 생성이 되었을땐 아무 반응없이 알아서 가져와서 저장이 되는데". 지금까지
# resolve_flow_share_image()는 사람이 실제로 보고 있을 수도 있는 4개 계정 중 하나의 진짜 크롬
# 창을 빌려(CDP로 새 탭을 열어) 공유 페이지를 렌더링했다 — 그게 바로 "생성"과 달리 화면에
# 갑자기 새 창/탭이 튀어나오는 원인이었다("생성"은 이미 열려서 보이고 있는 프로젝트 탭 위에서
# 조용히 진행되니 새로 뜨는 게 없다). 이제 그 4개 계정 창을 전혀 건드리지 않고, 이 용도로만
# 쓰는 눈에 안 보이는 헤드리스 크롬 인스턴스를 임시 프로필로 새로 띄워서 처리한 뒤 바로
# 종료한다 — 공유 링크는 로그인 없이도 보이는 공개 페이지라(실측 확인됨) 헤드리스로도 문제없이
# 이미지를 찾을 수 있다.
HEADLESS_RESOLVE_PORT = 9299


def _launch_headless_chrome_for_resolve():
    profile_dir = Path(tempfile.mkdtemp(prefix="flow_share_resolve_"))
    proc = subprocess.Popen(
        [
            CHROME_EXE,
            "--headless=new",
            f"--remote-debugging-port={HEADLESS_RESOLVE_PORT}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
        ],
        close_fds=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 10
    while time.time() < deadline:
        if is_port_open(HEADLESS_RESOLVE_PORT, timeout=0.5):
            return proc, profile_dir
        time.sleep(0.3)
    try:
        proc.terminate()
    except Exception:
        pass
    shutil.rmtree(profile_dir, ignore_errors=True)
    raise RuntimeError("이미지 회수용 헤드리스 크롬을 띄우지 못했습니다.")


# 2026-09-12 (6차) 수정 — 실제로 재현해서 원인을 찾음: 헤드리스 크롬으로 이 URL을 열면
# document.body가 완전히 비어있다(title도 빈 문자열, innerHTML.length==0) — flow.google.com이
# User-Agent 문자열의 "HeadlessChrome"을 보고 아예 페이지를 안 내려주는 것으로 확인됨(직접
# navigator.userAgent를 일반 데스크톱 Chrome UA로 CDP `Network.setUserAgentOverride`로 바꿔서
# 다시 열어보니 이미지 2개가 정상적으로 바로 나타남). navigator.webdriver는 이미 false라 그쪽은
# 문제가 아니었다 — 순전히 UA 문자열의 "Headless" 표시 때문이었다.
DESKTOP_CHROME_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"


def resolve_flow_share_image(share_url: str) -> tuple[bytes, str]:
    port = HEADLESS_RESOLVE_PORT
    proc, profile_dir = _launch_headless_chrome_for_resolve()
    try:
        # about:blank로 먼저 연 뒤, UA를 일반 Chrome처럼 바꾸고 나서 실제 주소로 이동한다 —
        # 처음부터 공유 URL로 탭을 열면(?url=) 이동이 이미 "HeadlessChrome" UA로 일어나버려서
        # 늦다.
        r = httpx.put(f"http://127.0.0.1:{port}/json/new", params={"": "about:blank"}, timeout=10)
        r.raise_for_status()
        tab = r.json()
        tab_id = tab["id"]
        ws_url = tab["webSocketDebuggerUrl"]
        try:
            ws = ws_connect(ws_url, max_size=None, open_timeout=10)
            try:
                def cdp_call(method, **params):
                    mid = int(time.time() * 1000) % 1000000
                    ws.send(json.dumps({"id": mid, "method": method, "params": params}))
                    while True:
                        msg = json.loads(ws.recv(timeout=15))
                        if msg.get("id") == mid:
                            if "error" in msg:
                                raise RuntimeError(f"CDP {method} 실패: {msg['error']}")
                            return msg.get("result", {})

                cdp_call("Network.enable")
                cdp_call("Network.setUserAgentOverride", userAgent=DESKTOP_CHROME_UA)
                cdp_call("Page.navigate", url=share_url)
                cdp_call("Runtime.enable")
                # 페이지가 이미지 목록을 불러와 렌더링할 시간을 준다 — 헤드리스라도 콜드 스타트라
                # 처음 로딩에 몇 초 이상 걸릴 수 있다 — 넉넉히 기다리고, 중간에 evaluate 자체가
                # 실패해도(아직 about:blank 등) 계속 재시도한다.
                # 2026-09-12 (4차) 수정 — 사용자 지시: "생성버튼으로 되듯이 링크를 주면 저장을
                # 시키라고" — 이 폴백이 정상 생성 경로만큼 그냥 확실히 되길 원함. 30초/엄격한
                # 정사각형 제외 필터 조합이 실측(직접 이 URL을 열어보니 진짜 이미지가 3초 안에
                # 떴음)보다 훨씬 여유로워 보이긴 하지만, 45초로 늘리고, 그래도 못 찾았을 때
                # 완전히 실패시키는 대신 "정사각형이라 제외됐던" flow-content.google 이미지가
                # 하나라도 있었으면(예: 실제로 1:1 비율로 생성된 씬일 수 있음) 그중 가장 큰
                # 것을 대신 쓴다 — 아예 등록을 포기하는 것보다 낫다.
                deadline = time.time() + 45
                urls: list[str] = []
                any_fc_candidates: list[dict] = []
                while time.time() < deadline:
                    try:
                        res = cdp_call(
                            "Runtime.evaluate",
                            expression=(
                                "Array.from(document.querySelectorAll('img'))"
                                ".filter(i => i.naturalWidth > 0)"
                                ".map(i => ({src: i.src, w: i.naturalWidth, h: i.naturalHeight}))"
                            ),
                            returnByValue=True,
                        )
                        imgs = (res.get("result") or {}).get("value") or []
                    except Exception:
                        imgs = []
                    fc_imgs = [i for i in imgs if "flow-content.google" in i.get("src", "")]
                    if fc_imgs:
                        any_fc_candidates = fc_imgs
                    candidates = [i for i in fc_imgs if i.get("w") != i.get("h")]
                    if candidates:
                        candidates.sort(key=lambda i: i["w"] * i["h"], reverse=True)
                        urls = [c["src"] for c in candidates]
                        break
                    time.sleep(0.75)
                if not urls and any_fc_candidates:
                    any_fc_candidates.sort(key=lambda i: i["w"] * i["h"], reverse=True)
                    urls = [c["src"] for c in any_fc_candidates]
                if not urls:
                    raise RuntimeError("공유 페이지에서 실제 이미지를 못 찾았습니다(로딩이 오래 걸렸을 수 있음) — 잠시 후 다시 시도해주세요.")
            finally:
                ws.close()
        finally:
            try:
                httpx.get(f"http://127.0.0.1:{port}/json/close/{tab_id}", timeout=5)
            except Exception:
                pass

        # 2026-09-12 (3차 수정, 되돌림) — 브라우저 안 fetch()+blob() 방식은 flow_econ_driver.py가
        # 이미 실측으로 "in-page fetch()도 flow-content.google이 CORS를 막아 실패한다"고
        # 문서화해둔 함정이었다. capture_newest_images()와 동일하게, CORS는 브라우저 안에서만
        # 적용되므로 서버사이드 httpx.get()으로는 문제없이 받아진다.
        img = httpx.get(urls[0], timeout=30)
        img.raise_for_status()
        ctype = img.headers.get("content-type", "").split(";")[0].strip().lower()
        if not ctype.startswith("image/"):
            ctype = "image/png" if ".png" in urls[0].split("?")[0].lower() else "image/jpeg"
        return img.content, ctype
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        shutil.rmtree(profile_dir, ignore_errors=True)


def start_chrome_for_port(port: int):
    acc = CHROME_ACCOUNTS.get(port)
    if not acc:
        return {"error": f"알 수 없는 포트: {port}"}
    if is_port_open(port):
        # 2026-09-12 실사고 수정 — 사용자 지적: "2개는 크롬켜기를 눌렀는데 왜 안떠???".
        # 원격 디버깅 포트는 열려있는데(프로세스는 살아있음) 실제 창/탭이 하나도 없는 경우가
        # 있었다(9223/9225에서 실측). 이 상태에선 "이미 켜져있음"으로 그냥 넘겨버리면 사용자
        # 눈엔 아무것도 안 뜨는 것처럼 보인다 — 탭이 하나도 없으면 새 탭을 직접 열어준다.
        if not has_open_tab(port):
            try:
                open_tab_for_port(port)
            except Exception as e:
                return {"error": f"탭이 없어서 새로 열려 했지만 실패: {e}"}
            return {"ok": True, "already_running": True, "opened_tab": True}
        return {"ok": True, "already_running": True}
    try:
        subprocess.Popen(
            [
                CHROME_EXE,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={acc['profile']}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-background-timer-throttling",
                "https://flow.google.com/",
            ],
            close_fds=True,
        )
    except Exception as e:
        return {"error": f"크롬 실행 실패: {e}"}
    return {"ok": True, "already_running": False}


# 2026-09-12 추가 — 사용자 지적: "켜기는 있는데 끄기는 없어?" — "크롬 켜기"의 짝. 그 포트로
# 원격 디버깅을 열어둔 chrome.exe만 정확히 골라서 죽인다(다른 일반 크롬 창까지 건드리지 않도록
# 커맨드라인에 그 포트 번호가 있는지로 매칭).
def stop_chrome_for_port(port: int):
    if port not in CHROME_ACCOUNTS:
        return {"error": f"알 수 없는 포트: {port}"}
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        f"Where-Object {{ $_.CommandLine -match '--remote-debugging-port={port}\\b' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], timeout=15, capture_output=True)
    except Exception as e:
        return {"error": f"크롬 종료 실패: {e}"}
    return {"ok": True}


# 2026-09-12 추가 — 사용자 지적: "각 계정 서버 켜는건 어디있어" / "여기에 만들어 두라고 하자나".
# 계정별 "크롬 켜기"의 짝으로, 이 대시보드 서버(8799) 자체도 메인 컨트롤 화면에서 재시작할 수
# 있게 한다 — 지금까지 세션 내내 이 서버를 고칠 때마다 사람이 직접 터미널에서 프로세스를 죽이고
# 다시 띄워야 했던 걸, 화면의 버튼 하나로 대신한다. 자기 자신을 죽이면서 동시에 응답을 보낼 수는
# 없으므로, 별도의 짧은 PowerShell 헬퍼를 분리 실행해 "잠깐 기다렸다가 지금 이 프로세스를 죽이고
# 새로 띄우는" 일을 대신 시킨다.
def restart_server():
    this_pid = os.getpid()
    repo = str(HERE.parent)
    py = sys.executable
    script_path = str(Path(__file__).resolve())
    ps = (
        f"Start-Sleep -Milliseconds 800; "
        f"Stop-Process -Id {this_pid} -Force -ErrorAction SilentlyContinue; "
        f"Start-Sleep -Milliseconds 500; "
        f"$env:PYTHONIOENCODING='utf-8'; "
        f"Start-Process -WindowStyle Hidden -FilePath '{py}' "
        f"-ArgumentList '{script_path}','8799' -WorkingDirectory '{repo}'"
    )
    subprocess.Popen(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
        close_fds=True,
    )
    return {"ok": True, "restarting": True}


def parse_scene_prompts(text: str):
    """SceneBlock 직렬화 포맷("### S01 제목\\n- 시간:...\\n- 이미지프롬프트:...")을 파싱.
    2026-09-10 — 원문 문서 순서가 뒤섞여 있어도(수동 편집 등) 항상 S01→S91 번호 순으로
    나오도록 씬 번호 기준으로 다시 정렬한다(사용자 지적: "프롬프트가 순서대로 나와야지").
    2026-09-13 추가 — 사용자 요청: "탭으로 이미지/영상 구분해주고 영상쪽에 영상프롬프트
    보여주고". 기존엔 이미지 프롬프트/이미지 URL만 뽑았는데, "- 영상:" 줄과 "- 장면영상:" 줄
    (HongHub 2026-09-13 sceneVideo 필드, 실제 등록된 영상 클립 URL)도 같이 뽑아
    video_prompt/needs_video/video_url로 돌려준다 — 이미지 탭과 완전히 같은 구조를 영상 탭에도
    그대로 쓸 수 있게 하기 위함.
    2026-09-13 (2차) 수정 — HongHub utils.ts의 parseSceneBlocks()가 "영상: [영상클립 필요] 텍스트"
    한 줄 마커 방식을 "- 무빙:"(카메라 지시, moving_prompt)/"- 영상클립필요:"(진짜 boolean)/
    "- 영상:"(순수 Flow 생성 프롬프트, video_prompt) 세 줄로 분리했다(사용자 지시: "영상 / 무빙
    이렇게 나눠서 정확하게 분리를 하면 어떨까?" → 완전 분리 확정). 이 대시보드가 실제로 필요한 건
    Flow에 넣을 video_prompt뿐이지만, TS 쪽과 동일한 하위호환 로직을 그대로 옮겨야 "- 영상클립필요:"
    줄이 없는 옛 데이터(마커 방식)도 안 깨지고 재해석된다."""
    # 2026-09-10 수정 — \d\d(정확히 2자리)로 고정돼 있어서 100번대 이상 씬(예: S100)이
    # 앞 두 자리만 잘려 "S10"으로 잘못 파싱되고 겹치는 사고가 있었다(실제 108씬 코카콜라
    # 유닛에서 S100~S108이 전부 S10 하나로 뭉개짐). \d{2,4}로 자릿수 제한을 풀었다.
    # 2026-09-13 수정 — 그래도 "S01B"처럼 숫자 뒤에 글자가 붙는 id는 여전히 놓쳤다: \b는
    # 숫자→글자 경계에서 성립하지 않아 "### S01B"가 새 블록 시작으로 안 잡히고 앞 블록(S01)에
    # 그대로 붙어버렸다 — 그 결과 S01B/S02B가 각각 S01/S02에 흡수되어 대시보드에 6개가 아니라
    # 4개로만 보이는 버그가 있었다(사용자 스크린샷으로 발견). set_scene_image_url() 등 다른
    # 함수들이 이미 쓰고 있는 "### \S+" 패턴(HongHub utils.ts의 parseSceneBlocks와도 동일한
    # 방식)으로 통일해서, id 형식과 무관하게 항상 정확히 나뉘게 한다.
    blocks = re.split(r"\n(?=### \S+)", text.strip())
    scenes = []
    for b in blocks:
        m_id = re.search(r"### (\S+)\s*(.*)", b)
        m_prompt = re.search(r"- 이미지프롬프트:\s*(.+)", b)
        # 2026-09-10 추가 — 서버를 재시작하면 메모리 속 실행 상태(_current)가 날아가서
        # 방금 완료된 씬도 화면엔 "pending"으로 보이는 문제가 있었다(사용자가 스샷으로 지적:
        # 실제론 완료·홍허브 등록까지 됐는데 목록만 pending). 어느 세션에서 확인하든 항상
        # 맞게 나오도록, DB에 이미 박힌 "- 장면이미지:" 줄 자체를 완료 여부의 근거로 삼는다.
        m_image = re.search(r"- 장면이미지:\s*(\S+)", b)
        m_moving = re.search(r"- 무빙:\s*(.+)", b)
        m_needs_raw = re.search(r"- 영상클립필요:\s*(\S+)", b)
        m_video_line = re.search(r"- 영상:\s*(.+)", b)
        m_video_url = re.search(r"- 장면영상:\s*(\S+)", b)
        if m_id and m_prompt:
            moving_prompt = m_moving.group(1).strip() if m_moving else ""
            video_line_raw = m_video_line.group(1).strip() if m_video_line else ""
            if m_needs_raw is not None:
                # 새 포맷 — "- 영상클립필요:" 줄이 있으면 그 값을 그대로 boolean으로 쓰고,
                # "- 영상:" 줄은 마커 없는 순수 Flow 프롬프트다.
                needs_video = m_needs_raw.group(1).strip() == "true"
                video_prompt = video_line_raw
            else:
                # 하위호환 — 옛 포맷은 "- 영상클립필요:" 줄이 없다. "- 영상:" 한 줄이 마커를
                # 겸했으므로 마커 유무로 재해석한다: 마커 있으면 needs_video=true+본문만 남기고,
                # 마커 없으면 그 값은 애초에 카메라 무빙 지시였을 뿐이니 moving_prompt로 옮긴다.
                legacy_has_marker = "[영상클립 필요]" in video_line_raw
                needs_video = legacy_has_marker
                if legacy_has_marker:
                    video_prompt = video_line_raw.replace("[영상클립 필요]", "").strip()
                else:
                    if video_line_raw and not moving_prompt:
                        moving_prompt = video_line_raw
                    video_prompt = ""
            m_num = re.search(r"\d+", m_id.group(1))
            scenes.append({"id": m_id.group(1), "num": int(m_num.group(0)) if m_num else 0,
                           "title": m_id.group(2).strip(), "prompt": m_prompt.group(1).strip(),
                           "image_url": m_image.group(1).strip() if m_image else None,
                           "moving_prompt": moving_prompt,
                           "video_prompt": video_prompt, "needs_video": needs_video,
                           "video_url": m_video_url.group(1).strip() if m_video_url else None})
    scenes.sort(key=lambda s: s["num"])
    return scenes


# ── 2026-09-11 추가 — 파일 업로드로 씬 등록 ──────────────────────────────────
# 사용자 요청: "여기에 파일업로드를 하나 추가하자~ 니가 만들어준 텍스트로 하는 방법" — 지금까지는
# Claude가 만든 JSON 씬 파일을 HongHub 앱(13번 패널)에 따로 가서 붙여넣어야 여기(이미지 생성
# 대시보드)에서 씬 목록이 보였다. 그 왕복을 없애고, 이 대시보드에서 바로 파일을 올리면 곧장
# scenePrompts에 등록되게 한다 — HongHub 앱의 registerParsed()/findSceneIssues()와 정확히
# 같은 포맷·같은 검증 규칙을 파이썬으로 그대로 옮겨서, 어느 경로로 등록하든 결과가 동일하다.
MAX_SCENE_SEC = 15
GAP_TOLERANCE_SEC = 1


def format_sec(sec: float) -> str:
    total = max(0, round(sec or 0))
    m, s = divmod(int(total), 60)
    return f"{m}:{s:02d}"


def find_scene_issues(scenes: list[dict]) -> list[str]:
    """HongHub 앱 step16-17-ImageVideoPanel.tsx의 findSceneIssues()와 동일한 규칙 —
    15초 초과 장면, 장면 간 시간 공백/겹침을 계산해서 문제 목록을 돌려준다."""
    issues = []
    prev_end = None
    for i, s in enumerate(scenes):
        start = s.get("startSec") or 0
        end = s.get("endSec") or 0
        dur = end - start
        label = s.get("id") or f"#{i + 1}"
        if dur > MAX_SCENE_SEC:
            issues.append(f"{label} ({format_sec(start)}~{format_sec(end)}): {dur:.1f}초 — 최대 {MAX_SCENE_SEC}초 초과")
        if prev_end is not None:
            gap = start - prev_end
            if abs(gap) > GAP_TOLERANCE_SEC:
                kind = "공백" if gap > 0 else "겹침"
                issues.append(f"{label} 앞 구간: {format_sec(prev_end)} → {format_sec(start)} 사이에 {kind} {abs(gap):.1f}초")
        prev_end = end
    return issues


def serialize_scenes(scenes: list[dict]) -> str:
    """HongHub 앱 utils.ts의 serializeSceneBlocks()와 동일한 SceneBlock 텍스트 포맷으로
    직렬화한다("### S01 제목\\n- 시간:...\\n- 이미지프롬프트:...\\n- 무빙:...\\n- 영상클립필요:...
    \\n- 영상:...") — 이 포맷이어야 parse_scene_prompts()와 HongHub 앱 양쪽에서 똑같이 읽힌다.
    2026-09-13 (2차) 수정 — HongHub utils.ts가 moving/needsVideoClip/video 세 필드로 분리되면서,
    이 함수가 받는 원본 JSON(register_scenes 파일 업로드 경로)의 필드 이름도
    step16-17-ImageVideoPanel.tsx의 RawScene과 맞춰 movingPrompt/needsVideoClip/videoPrompt로
    바꿨다(예전엔 transitionPrompt 한 필드가 무빙 지시와 영상 생성 프롬프트 두 역할을 겸했다)."""
    blocks = []
    for s in scenes:
        sid = s.get("id", "")
        title = s.get("screenDescription", "")
        lines = [f"### {sid}{' ' + title if title else ''}"]
        time_str = f"{format_sec(s.get('startSec') or 0)}-{format_sec(s.get('endSec') or 0)}"
        lines.append(f"- 시간: {time_str}")
        if s.get("sceneImage"):
            lines.append(f"- 장면이미지: {s['sceneImage']}")
        # 2026-09-13 추가 — register_scenes()의 carry-over 로직(아래)이 기존 등록된
        # 영상 클립 URL도 함께 넘겨줄 수 있도록, sceneImage와 나란히 sceneVideo도 지원한다.
        if s.get("sceneVideo"):
            lines.append(f"- 장면영상: {s['sceneVideo']}")
        image_prompt = s.get("imagePrompt", "")
        if image_prompt:
            lines.append(f"- 이미지프롬프트: {image_prompt}")
        if s.get("movingPrompt"):
            lines.append(f"- 무빙: {s['movingPrompt']}")
        needs_video_clip = bool(s.get("needsVideoClip"))
        # 항상 이 줄을 쓴다(false여도) — parse_scene_prompts()가 이 줄의 존재 여부로
        # 새 포맷/옛 포맷(마커 방식)을 구분하기 때문.
        lines.append(f"- 영상클립필요: {'true' if needs_video_clip else 'false'}")
        if needs_video_clip and s.get("videoPrompt"):
            lines.append(f"- 영상: {s['videoPrompt']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def register_scenes(site_id: str, unit_id: str, scenes: list[dict]):
    if not scenes:
        return {"error": "등록할 씬이 없습니다(파일에서 JSON 배열을 못 찾음)."}
    issues = find_scene_issues(scenes)
    if issues:
        return {"error": "등록 중단 — 문제 발견:\n" + "\n".join(issues)}
    r = httpx.get(f"{SUPA_URL}/rest/v1/hub_sites", params={"id": f"eq.{site_id}", "select": "script_draft"},
                  headers=supa_headers(), timeout=30)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return {"error": "사이트를 찾을 수 없습니다."}
    script_draft = rows[0].get("script_draft") or {}
    units = script_draft.get("units") or []
    unit = next((u for u in units if u.get("id") == unit_id), None)
    if not unit:
        return {"error": "콘텐츠(유닛)를 찾을 수 없습니다."}
    # 2026-09-11 실사고 방지 — 업로드한 JSON에는 애초에 "이미 생성된 이미지 URL" 필드가 없다
    # (Claude가 만드는 씬 파일은 시간·프롬프트만 담고, 이미지는 이 대시보드가 나중에 채운다).
    # 그래서 그냥 덮어쓰면 이미 생성·등록된 이미지가 전부 사라져서 "지우고 다시 만드는" 낭비가
    # 생긴다(사용자 지적: "이미지를 싹 지우고 올리고 하는게 문제아닐까?"). 기존 scenePrompts에서
    # 씬 id별 이미지 URL을 먼저 뽑아서, 업로드된 씬 중 같은 id가 있으면 그 URL을 그대로 이어붙인다.
    existing_scenes = parse_scene_prompts(unit.get("scenePrompts") or "")
    existing_images = {s["id"]: s["image_url"] for s in existing_scenes if s["image_url"]}
    # 2026-09-13 추가 — 씬 파일을 다시 올려서 등록을 갈아엎을 때, 이미지와 같은 이유로
    # 이미 등록된 영상 클립 URL도 같이 잃어버리면 안 된다(영상은 크레딧을 써서 만든
    # 것이라 이미지보다 유실 비용이 더 크다) — 같은 id면 그대로 이어붙인다.
    existing_videos = {s["id"]: s["video_url"] for s in existing_scenes if s["video_url"]}
    carried_over = 0
    video_carried_over = 0
    for s in scenes:
        url = existing_images.get(s.get("id"))
        if url:
            s["sceneImage"] = url
            carried_over += 1
        vurl = existing_videos.get(s.get("id"))
        if vurl:
            s["sceneVideo"] = vurl
            video_carried_over += 1
    unit["scenePrompts"] = serialize_scenes(scenes)
    patch = httpx.patch(f"{SUPA_URL}/rest/v1/hub_sites", params={"id": f"eq.{site_id}"},
                        headers={**supa_headers(), "Content-Type": "application/json"},
                        content=json.dumps({"script_draft": script_draft}).encode("utf-8"), timeout=30)
    patch.raise_for_status()
    return {"ok": True, "scene_count": len(scenes), "images_carried_over": carried_over,
            "videos_carried_over": video_carried_over}


# ── 2026-09-11 추가 — 이미지 링크 수동 등록 ──────────────────────────────────
# 사용자 지적: "1번이 아까 생성이 되었는데 기록이 안 되었나봐 → 이럴 때 수동으로 링크를
# 입력할 수 있었으면 하는데". flow_econ_driver.py가 완료 감지에 실패(최종 타임아웃 등)하면
# Flow 프로젝트 안엔 이미지가 실제로 만들어져 있어도 로컬 저장·Storage 업로드·scenePrompts
# 등록이 전부 스킵된다. 그 경우 사람이 Flow 화면에서 직접 이미지 URL을 복사해와 붙여넣으면
# 곧바로 그 씬의 "- 장면이미지:" 줄에 등록되게 한다(flow_econ_driver.py의
# register_scene_image()와 동일한 블록 편집 규칙 — 기존 파일 업로드는 안 하고 URL만 그대로 씀).
def set_scene_image_url(site_id: str, unit_id: str, scene_id: str, image_url: str):
    image_url = (image_url or "").strip()
    if not image_url:
        return {"error": "이미지 URL을 입력하세요."}
    # 2026-09-12 실사고 수정, 같은 날 2차 수정 — 위 _fetch_direct_image/resolve_flow_share_image
    # 설명 참고. URL 형태를 정규식으로 미리 판별하지 않는다 — 먼저 직접 받아봐서 진짜 이미지가
    # 맞으면 그걸 쓰고, 아니면(공유 페이지든 그 사이 Flow가 형식을 바꾼 무엇이든) 무조건 계정
    # 크롬으로 그 주소를 열어 실제 이미지를 찾아 안전하게 받아온다. 둘 중 어느 경로든 결과는
    # 항상 우리 Storage에 다시 올려서 그 주소로 등록한다 — Google CDN 주소를 그대로 쓰면 서명
    # 토큰이 나중에 만료돼 등록 당시엔 멀쩡하다가 나중에 깨지는 문제도 막는다.
    direct = _fetch_direct_image(image_url)
    if direct is not None:
        img_bytes, mime = direct
    else:
        try:
            img_bytes, mime = resolve_flow_share_image(image_url)
        except Exception as e:
            return {"error": f"이미지를 가져오지 못했습니다: {e}"}
    ext = (mime.split("/")[-1].split("+")[0] or "jpg") if mime else "jpg"
    if ext not in ("jpg", "jpeg", "png", "webp", "gif"):
        ext = "jpg"
    storage_path = f"scene-images/{unit_id}/{scene_id}.{ext}"
    up = httpx.post(
        f"{SUPA_URL}/storage/v1/object/honghub-files/{storage_path}",
        headers={**supa_headers(), "Content-Type": mime or "image/jpeg", "x-upsert": "true"},
        content=img_bytes, timeout=60,
    )
    up.raise_for_status()
    image_url = f"{SUPA_URL}/storage/v1/object/public/honghub-files/{storage_path}"
    r = httpx.get(f"{SUPA_URL}/rest/v1/hub_sites", params={"id": f"eq.{site_id}", "select": "script_draft"},
                  headers=supa_headers(), timeout=30)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return {"error": "사이트를 찾을 수 없습니다."}
    script_draft = rows[0].get("script_draft") or {}
    units = script_draft.get("units") or []
    unit = next((u for u in units if u.get("id") == unit_id), None)
    if not unit:
        return {"error": "콘텐츠(유닛)를 찾을 수 없습니다."}
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
            lines.insert(insert_at, f"- 장면이미지: {image_url}")
            new_blocks.append("\n".join(lines))
            changed = True
        else:
            new_blocks.append(b)
    if not changed:
        return {"error": f"{scene_id} 씬을 찾을 수 없습니다."}
    unit["scenePrompts"] = "\n\n".join(new_blocks)
    patch = httpx.patch(f"{SUPA_URL}/rest/v1/hub_sites", params={"id": f"eq.{site_id}"},
                        headers={**supa_headers(), "Content-Type": "application/json"},
                        content=json.dumps({"script_draft": script_draft}).encode("utf-8"), timeout=30)
    patch.raise_for_status()
    return {"ok": True, "scene_id": scene_id, "image_url": image_url}


# ── 2026-09-11 추가 — 이미지 삭제(프롬프트는 유지) ──────────────────────────────
# 사용자 지적: "이미지랑 프롬프트가 안 맞는거 같아" — capture_newest_images()의 레이스
# 컨디션으로 서로 다른 씬 번호에 완전히 동일한 파일이 저장되는 사고가 실측 확인됨
# (S03/S04, S09/S12, S10/S11, S13/S14). 잘못 등록된 씬을 발견하면 프롬프트는 그대로
# 두고 이미지만 지워서 재생성할 수 있어야 한다 — set_scene_image_url()과 반대로,
# "- 장면이미지:" 줄을 그 씬 블록에서 제거만 한다(대체하지 않음).
def delete_scene_image(site_id: str, unit_id: str, scene_id: str):
    r = httpx.get(f"{SUPA_URL}/rest/v1/hub_sites", params={"id": f"eq.{site_id}", "select": "script_draft"},
                  headers=supa_headers(), timeout=30)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return {"error": "사이트를 찾을 수 없습니다."}
    script_draft = rows[0].get("script_draft") or {}
    units = script_draft.get("units") or []
    unit = next((u for u in units if u.get("id") == unit_id), None)
    if not unit:
        return {"error": "콘텐츠(유닛)를 찾을 수 없습니다."}
    text = unit.get("scenePrompts") or ""
    blocks = re.split(r"\n(?=### \S+)", text.strip()) if text.strip() else []
    changed = False
    new_blocks = []
    for b in blocks:
        if re.match(rf"### {re.escape(scene_id)}(\s|$)", b):
            lines = b.split("\n")
            filtered = [ln for ln in lines if not ln.startswith("- 장면이미지:")]
            if len(filtered) != len(lines):
                changed = True
            new_blocks.append("\n".join(filtered))
        else:
            new_blocks.append(b)
    if changed:
        unit["scenePrompts"] = "\n\n".join(new_blocks)
        patch = httpx.patch(f"{SUPA_URL}/rest/v1/hub_sites", params={"id": f"eq.{site_id}"},
                            headers={**supa_headers(), "Content-Type": "application/json"},
                            content=json.dumps({"script_draft": script_draft}).encode("utf-8"), timeout=30)
        patch.raise_for_status()

    # 2026-09-12 (7차) 수정 — 실사고: DB에는 등록이 안 됐지만(업로드는 성공했는데 DB 등록
    # 단계만 누락된 경우) 로컬 done 기록·파일·Storage 원본은 그대로 남아있는 상태에서 삭제를
    # 누르면, 예전엔 "DB에 지울 게 없다"고 바로 실패 처리하고 로컬 정리를 아예 안 했다 — 그래서
    # 이어서 "생성"을 눌러도 드라이버가 여전히 로컬 done 기록만 보고 건너뛰어 계속 생성이 안
    # 되는 사고가 났다(사용자: "삭제가 되었으니 펜딩상태고... 새로 생성이 안되는 상태"). DB에
    # 지울 게 없어도 로컬 done 기록·파일·Storage 원본은 항상 정리한다 — 이 셋 중 아무것도
    # 못 찾았을 때만 진짜 "지울 게 없다"로 실패 처리한다.
    # 이 씬을 생성했던 계정(포트)이 여러 개일 수 있으므로(9223~9226), 같은 unit_id를
    # 쓰는 run_dir을 전부 훑어서 로컬 done 기록·저장 파일까지 같이 지운다 — 안 그러면
    # "▶ 생성"을 눌러도 flow_econ_driver.py가 이미 done인 씬으로 보고 건너뛴다.
    removed_files = []
    for run_dir in RUN_DIR.glob(f"{unit_id}__*"):
        # 2026-09-13 추가 — run_dir이 이제 "{unit_id}__{port}__{image|video}"로 모드가
        # 분리돼 있다(위 start_run 참고). 이미지 삭제가 실수로 영상 쪽 done 기록까지
        # 건드리면 안 되므로 영상 전용 폴더는 건너뛴다(기존에 모드 접미사가 없던 옛
        # 폴더는 계속 이미지로 취급해 하위호환을 유지한다).
        if run_dir.name.endswith("__video"):
            continue
        state_path = run_dir / "_flow_state.json"
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if scene_id in (state.get("done") or {}):
                    del state["done"][scene_id]
                    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
            except Exception:
                pass
        img_dir = run_dir / "output" / "images"
        if img_dir.exists():
            for f in img_dir.glob(f"{scene_id}_*.*"):
                try:
                    f.unlink()
                    removed_files.append(str(f))
                except OSError:
                    pass

    # Supabase Storage에 올라간 원본도 지운다(있으면) — 실패해도 위의 핵심 작업(scenePrompts
    # 수정, 로컬 상태 정리)은 이미 끝났으니 무시하고 넘어간다.
    storage_removed = False
    try:
        for ext in ("jpg", "jpeg", "png"):
            resp = httpx.delete(
                f"{SUPA_URL}/storage/v1/object/honghub-files/scene-images/{unit_id}/{scene_id}.{ext}",
                headers=supa_headers(), timeout=15,
            )
            if resp.status_code == 200:
                storage_removed = True
    except Exception:
        pass

    if not changed and not removed_files and not storage_removed:
        return {"error": f"{scene_id} 씬엔 등록된 이미지가 DB/로컬/Storage 어디에도 없습니다."}
    return {"ok": True, "scene_id": scene_id, "removed_files": removed_files}


# ── 2026-09-13 추가 — 영상 링크 수동 등록/삭제 (이미지 쪽과 동일한 방식) ──────────────
# 사용자 요청: "탭으로 이미지/영상 구분해주고 ... 이미지쪽과 동일하게 만들어주면돼".
# set_scene_image_url()/delete_scene_image()와 완전히 같은 구조를 영상에 그대로
# 미러링한다 — 다른 점은 Content-Type이 video/*인지 확인하고, 공유 페이지에서 실제
# 미디어를 찾을 때 <img> 대신 <video>를 찾는다는 것뿐이다.
def _fetch_direct_video(url: str) -> tuple[bytes, str] | None:
    """_fetch_direct_image()와 동일 — URL을 직접 받아봐서 진짜 영상 파일(Content-Type:
    video/*)이면 (바이트, mime)을 돌려주고, 아니면 None을 돌려준다(호출자가
    resolve_flow_share_video로 폴백)."""
    try:
        r = httpx.get(url, timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
    except Exception:
        return None
    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
    if r.status_code == 200 and ctype.startswith("video/"):
        return r.content, ctype
    return None


def resolve_flow_share_video(share_url: str) -> tuple[bytes, str]:
    """resolve_flow_share_image()와 완전히 같은 방식(눈에 안 보이는 헤드리스 크롬으로
    공유 페이지를 열어 실제 미디어를 찾는다)이지만 <img> 대신 <video>(및 그 안의
    <source>) 태그를 찾는다.
    ⚠️ 2026-09-13 — resolve_flow_share_image()는 실제 사용해보고 검증된 함수이지만,
    이 영상용 버전은 Flow의 영상 결과 공유 페이지가 실제로 <video src="...">를 바로
    렌더하는지(포스터 썸네일만 <img>로 보이고 실제 파일은 별도 조작이 있어야 나오는
    구조일 수도 있음) 실기 테스트 전이다 — 처음 써보고 "공유 페이지에서 실제 영상을
    못 찾았습니다" 에러가 나면 이 함수의 querySelector 부분부터 다시 점검할 것."""
    port = HEADLESS_RESOLVE_PORT
    proc, profile_dir = _launch_headless_chrome_for_resolve()
    try:
        r = httpx.put(f"http://127.0.0.1:{port}/json/new", params={"": "about:blank"}, timeout=10)
        r.raise_for_status()
        tab = r.json()
        tab_id = tab["id"]
        ws_url = tab["webSocketDebuggerUrl"]
        try:
            ws = ws_connect(ws_url, max_size=None, open_timeout=10)
            try:
                def cdp_call(method, **params):
                    mid = int(time.time() * 1000) % 1000000
                    ws.send(json.dumps({"id": mid, "method": method, "params": params}))
                    while True:
                        msg = json.loads(ws.recv(timeout=15))
                        if msg.get("id") == mid:
                            if "error" in msg:
                                raise RuntimeError(f"CDP {method} 실패: {msg['error']}")
                            return msg.get("result", {})

                cdp_call("Network.enable")
                cdp_call("Network.setUserAgentOverride", userAgent=DESKTOP_CHROME_UA)
                cdp_call("Page.navigate", url=share_url)
                cdp_call("Runtime.enable")
                deadline = time.time() + 45
                urls: list[str] = []
                while time.time() < deadline:
                    try:
                        res = cdp_call(
                            "Runtime.evaluate",
                            expression=(
                                "Array.from(document.querySelectorAll('video'))"
                                ".flatMap(v => [v.currentSrc || v.src, "
                                "...Array.from(v.querySelectorAll('source')).map(s=>s.src)])"
                                ".filter(Boolean)"
                            ),
                            returnByValue=True,
                        )
                        vids = (res.get("result") or {}).get("value") or []
                    except Exception:
                        vids = []
                    fc_vids = [v for v in vids if "flow-content.google" in v]
                    if fc_vids:
                        urls = fc_vids
                        break
                    time.sleep(0.75)
                if not urls:
                    raise RuntimeError(
                        "공유 페이지에서 실제 영상을 못 찾았습니다(로딩이 오래 걸렸을 수 있음, "
                        "또는 Flow가 영상을 <video> 태그로 바로 렌더하지 않을 수 있음) — "
                        "잠시 후 다시 시도하거나 영상 파일을 직접 다운로드해 그 파일 URL로 등록해주세요."
                    )
            finally:
                ws.close()
        finally:
            try:
                httpx.get(f"http://127.0.0.1:{port}/json/close/{tab_id}", timeout=5)
            except Exception:
                pass

        vid = httpx.get(urls[0], timeout=60)
        vid.raise_for_status()
        ctype = vid.headers.get("content-type", "").split(";")[0].strip().lower()
        if not ctype.startswith("video/"):
            ctype = "video/mp4"
        return vid.content, ctype
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        shutil.rmtree(profile_dir, ignore_errors=True)


def set_scene_video_url(site_id: str, unit_id: str, scene_id: str, video_url: str):
    """set_scene_image_url()과 동일한 흐름 — 링크를 직접 받아보고 진짜 영상이면 그걸
    쓰고, 아니면 헤드리스 크롬으로 공유 페이지를 열어 실제 영상을 찾는다. 최종적으로는
    항상 우리 Supabase Storage에 다시 올려서 등록한다(서명 URL 만료로 나중에 깨지는
    문제 방지)."""
    video_url = (video_url or "").strip()
    if not video_url:
        return {"error": "영상 URL을 입력하세요."}
    direct = _fetch_direct_video(video_url)
    if direct is not None:
        vid_bytes, mime = direct
    else:
        try:
            vid_bytes, mime = resolve_flow_share_video(video_url)
        except Exception as e:
            return {"error": f"영상을 가져오지 못했습니다: {e}"}
    ext = (mime.split("/")[-1].split("+")[0] or "mp4") if mime else "mp4"
    if ext not in ("mp4", "webm", "mov", "m4v"):
        ext = "mp4"
    storage_path = f"scene-videos/{unit_id}/{scene_id}.{ext}"
    up = httpx.post(
        f"{SUPA_URL}/storage/v1/object/honghub-files/{storage_path}",
        headers={**supa_headers(), "Content-Type": mime or "video/mp4", "x-upsert": "true"},
        content=vid_bytes, timeout=120,
    )
    up.raise_for_status()
    video_url = f"{SUPA_URL}/storage/v1/object/public/honghub-files/{storage_path}"
    r = httpx.get(f"{SUPA_URL}/rest/v1/hub_sites", params={"id": f"eq.{site_id}", "select": "script_draft"},
                  headers=supa_headers(), timeout=30)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return {"error": "사이트를 찾을 수 없습니다."}
    script_draft = rows[0].get("script_draft") or {}
    units = script_draft.get("units") or []
    unit = next((u for u in units if u.get("id") == unit_id), None)
    if not unit:
        return {"error": "콘텐츠(유닛)를 찾을 수 없습니다."}
    text = unit.get("scenePrompts") or ""
    blocks = re.split(r"\n(?=### \S+)", text.strip()) if text.strip() else []
    changed = False
    new_blocks = []
    for b in blocks:
        if re.match(rf"### {re.escape(scene_id)}(\s|$)", b):
            lines = b.split("\n")
            lines = [ln for ln in lines if not ln.startswith("- 장면영상:")]
            # HongHub utils.ts의 필드 순서(시간→장면이미지→장면영상→이미지프롬프트...)와
            # 맞추기 위해 "- 장면이미지:"(없으면 "- 시간:") 줄 바로 뒤에 끼워넣는다.
            insert_at = 1
            for i, ln in enumerate(lines[1:], start=1):
                if ln.startswith("- 시간:") or ln.startswith("- 장면이미지:"):
                    insert_at = i + 1
            lines.insert(insert_at, f"- 장면영상: {video_url}")
            new_blocks.append("\n".join(lines))
            changed = True
        else:
            new_blocks.append(b)
    if not changed:
        return {"error": f"{scene_id} 씬을 찾을 수 없습니다."}
    unit["scenePrompts"] = "\n\n".join(new_blocks)
    patch = httpx.patch(f"{SUPA_URL}/rest/v1/hub_sites", params={"id": f"eq.{site_id}"},
                        headers={**supa_headers(), "Content-Type": "application/json"},
                        content=json.dumps({"script_draft": script_draft}).encode("utf-8"), timeout=30)
    patch.raise_for_status()
    return {"ok": True, "scene_id": scene_id, "video_url": video_url}


def delete_scene_video(site_id: str, unit_id: str, scene_id: str):
    """delete_scene_image()와 동일한 흐름 — 프롬프트는 그대로 두고 "- 장면영상:" 줄만
    제거한 뒤, 이 씬을 영상 생성으로 시도했던 모든 계정(포트)의 로컬 done 기록·저장
    파일도 같이 정리한다(run_dir이 아래 start_run()에서 __{port}__video로 이미지와
    분리되므로 그 접미사가 붙은 폴더만 훑는다)."""
    r = httpx.get(f"{SUPA_URL}/rest/v1/hub_sites", params={"id": f"eq.{site_id}", "select": "script_draft"},
                  headers=supa_headers(), timeout=30)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return {"error": "사이트를 찾을 수 없습니다."}
    script_draft = rows[0].get("script_draft") or {}
    units = script_draft.get("units") or []
    unit = next((u for u in units if u.get("id") == unit_id), None)
    if not unit:
        return {"error": "콘텐츠(유닛)를 찾을 수 없습니다."}
    text = unit.get("scenePrompts") or ""
    blocks = re.split(r"\n(?=### \S+)", text.strip()) if text.strip() else []
    changed = False
    new_blocks = []
    for b in blocks:
        if re.match(rf"### {re.escape(scene_id)}(\s|$)", b):
            lines = b.split("\n")
            filtered = [ln for ln in lines if not ln.startswith("- 장면영상:")]
            if len(filtered) != len(lines):
                changed = True
            new_blocks.append("\n".join(filtered))
        else:
            new_blocks.append(b)
    if changed:
        unit["scenePrompts"] = "\n\n".join(new_blocks)
        patch = httpx.patch(f"{SUPA_URL}/rest/v1/hub_sites", params={"id": f"eq.{site_id}"},
                            headers={**supa_headers(), "Content-Type": "application/json"},
                            content=json.dumps({"script_draft": script_draft}).encode("utf-8"), timeout=30)
        patch.raise_for_status()

    removed_files = []
    for run_dir in RUN_DIR.glob(f"{unit_id}__*__video"):
        state_path = run_dir / "_flow_state.json"
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if scene_id in (state.get("done") or {}):
                    del state["done"][scene_id]
                    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
            except Exception:
                pass
        vid_dir = run_dir / "output" / "videos"
        if vid_dir.exists():
            for f in vid_dir.glob(f"{scene_id}_*.*"):
                try:
                    f.unlink()
                    removed_files.append(str(f))
                except OSError:
                    pass

    storage_removed = False
    try:
        for ext in ("mp4", "webm", "mov", "m4v"):
            resp = httpx.delete(
                f"{SUPA_URL}/storage/v1/object/honghub-files/scene-videos/{unit_id}/{scene_id}.{ext}",
                headers=supa_headers(), timeout=15,
            )
            if resp.status_code == 200:
                storage_removed = True
    except Exception:
        pass

    if not changed and not removed_files and not storage_removed:
        return {"error": f"{scene_id} 씬엔 등록된 영상이 DB/로컬/Storage 어디에도 없습니다."}
    return {"ok": True, "scene_id": scene_id, "removed_files": removed_files}


# ── 실행 중인 러너 상태 — 2026-09-10 수정: 계정(포트)별로 여러 개 동시 실행 지원 ─────
# 예전엔 프로세스 하나만 허용하는 단일 슬롯(_current)이었는데, 계정 3개(9223/9224/9225)로
# 병렬 처리하려면 포트별로 각자 추적해야 한다. 포트를 키로 하는 dict로 바꿨다.
_lock = threading.Lock()
_runs: dict[int, dict] = {}  # port -> {"proc":..., "run_dir":...}


def start_run(site_id: str, unit_id: str, scene_ids: list[str] | None = None,
              ratio: str = "16:9", new_project: bool = False, port: int = 9223,
              characters: list[dict] | None = None, image_count: int = 1,
              project_url: str | None = None, agent_off: bool = True,
              mode: str = "image", video_ratio: str = "16:9",
              video_model: str = "Omni 1.1 Flash", video_resolution: str = "720p",
              video_duration: str = "8초", video_count: int = 1):
    """2026-09-13 수정 — 사용자 요청: "탭으로 이미지/영상 구분해주고 ... 이미지쪽과
    동일하게 만들어주면돼". mode='video'면 이미지 프롬프트 대신 영상 프롬프트(- 영상:
    줄)를 job에 담아 보내고, needs_video로 표시된 씬만 대상으로 삼는다 — 모든 씬이
    영상이 필요한 건 아니므로(사용자 표현: "영상 필요한 것들만 몇 개만 등록할 거야").
    포트별 실행 슬롯(_runs)과 로컬 상태 폴더(run_dir)도 이미지/영상을 별도 키로 분리한다
    — 같은 씬 id("S01")가 이미지 쪽에서 done으로 기록됐다고 영상 쪽까지 건너뛰면 안 되기
    때문이다."""
    with _lock:
        # 2026-09-13 — _runs는 여전히 포트 하나당 슬롯 하나다(모드별로 나누지 않음): 같은
        # Chrome 창(계정)이 동시에 이미지·영상 두 작업을 같이 할 수는 없으니, "포트 하나당
        # 동시 작업 하나"라는 기존 제약이 모드가 늘어나도 그대로 맞다. 모드별로 분리가
        # 필요한 건 로컬 상태(run_dir/_flow_state.json)뿐 — 그건 아래에서 처리한다.
        existing = _runs.get(port)
        if existing and existing["proc"] and existing["proc"].poll() is None:
            return {"error": f"포트 {port}에 이미 실행 중인 작업이 있습니다 — 먼저 멈춰주세요."}

        r = httpx.get(
            f"{SUPA_URL}/rest/v1/hub_sites",
            params={"id": f"eq.{site_id}", "select": "script_draft"},
            headers=supa_headers(), timeout=30,
        )
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return {"error": "사이트를 찾을 수 없습니다."}
        units = (rows[0].get("script_draft") or {}).get("units") or []
        unit = next((u for u in units if u.get("id") == unit_id), None)
        if not unit:
            return {"error": "콘텐츠(유닛)를 찾을 수 없습니다."}
        scene_text = unit.get("scenePrompts") or ""
        all_scenes = parse_scene_prompts(scene_text)
        if not all_scenes:
            return {"error": "이 콘텐츠엔 등록된 씬 프롬프트가 없습니다(13번 단계 먼저 필요)."}
        # 영상 모드는 needs_video(= "[영상클립 필요]" 마커)로 표시된 씬만 대상으로 한다 —
        # 이미지 모드는 지금까지와 동일하게 전체 씬을 대상으로 한다.
        scenes = all_scenes if mode == "image" else [s for s in all_scenes if s.get("needs_video")]
        if mode == "video" and not scenes:
            return {"error": "이 콘텐츠엔 영상이 필요한 장면([영상클립 필요])이 없습니다."}
        # 2026-09-10 수정 — "이 씬만 생성"을 눌렀을 때 job["images"]를 그 씬 하나로 줄여버려서
        # 화면 목록(전체 108개)까지 그거 하나로 보이는 사고가 있었다("다른 건 다 사라졌다"는
        # 지적). images는 항상 전체 씬을 담고, 실제로 이번에 처리할 대상만 active_ids로 따로
        # 표시한다 — 목록 표시는 항상 전체 유지, 생성만 선택된 것으로 한정.
        active_ids = set(scene_ids) if scene_ids else {s["id"] for s in scenes}
        if not active_ids:
            return {"error": "선택된 씬이 없습니다."}

        # 2026-09-11 추가했다가 즉시 롤백 — 사용자 지적: "4번 같은 경우 3번이미지에서
        # 변경을 해야하는거 아닌가"에 대한 대응으로, 같은 "Setting: ..." 문장을 쓰는 씬은
        # 이전에 완료된 씬을 attach_reference로 자동 참조 첨부하는 기능을 넣었었다. 그런데
        # attach_reference(title)이 검색하는 title은 Flow에 실제로 등록된 애셋 이름이 아니라
        # 그냥 내부 씬 id 문자열("S26")이라(2026-09-10에 read_newest_title()을 없애면서
        # 실제 제목을 더 이상 안 읽어옴), Flow 애셋 피커에서 그 문자열을 찾지 못해 매번
        # "★검색 결과 행을 못 찾았다"로 씬 자체가 실패하는 사고가 실측됨(S25). 실제 Flow
        # 애셋 제목을 안전하게 다시 읽어오는 방법을 마련하기 전까지는 ref를 아예 안 붙인다
        # — 배경 일관성은 지금처럼 프롬프트의 "Setting:" 문장을 그대로 복사해 쓰는 텍스트
        # 방식에만 의존한다(완벽하진 않지만 최소한 생성 자체는 안 막힌다).
        # 2026-09-13 — 영상 모드는 image 필드에 이미지 프롬프트 대신 영상 프롬프트를
        # 담는다. job.json 스키마(images: [{id, prompt}])는 그대로 재사용하고, 드라이버가
        # job["mode"]를 보고 이미지로 저장할지 영상으로 저장할지만 다르게 처리한다.
        # 2026-09-13 (2차) 추가 — 사용자 질문("01번의 경우 1번 이미지가 있어야 하는거
        # 아니야?")으로 드러난 실사고 수정: 영상 모드 job에 그 씬의 기존 이미지 URL
        # (image_url)이 전혀 안 담겨 있어서, 드라이버가 씬 이미지와 무관하게 순수 텍스트
        # 만으로 영상을 생성하고 있었다. 이제 image_url도 같이 넘겨서, 드라이버가 그
        # 이미지를 Flow "프레임" 모드의 시작 프레임으로 첨부할 수 있게 한다.
        images_payload = [
            {
                "id": s["id"],
                "prompt": (s["prompt"] if mode == "image" else s["video_prompt"]),
                **({"image_url": s.get("image_url")} if mode == "video" else {}),
            }
            for s in scenes
        ]

        # 2026-09-10 추가 — 포트별로 로컬 상태 파일(_flow_state.json/status.json/job.json)을
        # 따로 둔다 — 같은 파일을 여러 프로세스가 동시에 쓰면 로컬에서도 경쟁이 생긴다.
        # 2026-09-13 추가 — 여기에 mode 접미사를 더 붙인다: 이미지 done 기록과 영상 done
        # 기록이 같은 씬 id(예: "S01")를 공유하면, 이미지를 이미 만든 씬을 영상 모드에서
        # "이미 done"으로 착각해 건너뛰는 사고가 난다 — 완전히 분리된 폴더를 쓴다.
        run_dir = RUN_DIR / f"{unit_id}__{port}__{mode}"
        run_dir.mkdir(parents=True, exist_ok=True)
        job = {
            "style": "",
            "ratio": ratio,
            "images": images_payload,
            "active_ids": sorted(active_ids),
            "workflow": site_name_cache.get(site_id, ""),
            "content_no": None,
            "content_title": unit.get("title", ""),
            "step_no": 13,
            "step_name": "씬(스토리보드) 분할 · 이미지 프롬프트 작성 · 생성" if mode == "image" else "씬별 영상 클립 생성",
            # 2026-09-13 추가 — flow_econ_driver.py가 FLOW_STAGE 환경변수뿐 아니라 job 안에서도
            # 모드를 알 수 있게(로그·저장 경로 라벨링 등에 사용).
            "mode": mode,
            # 2026-09-13 (5차) 추가 — 사용자 지적: "이 체크부분도 탭선택(이미지,영상)에 따라
            # 동적으로 변해야겠다" — 대시보드 화면엔 이미지용 설정(화면비율/생성장수)만 있고
            # 영상용 설정(비율/모델/해상도/길이/배치수)을 고를 UI 자체가 없어서, ensure_video_
            # frame_settings()가 항상 하드코딩된 기본값만 쓰고 있었다. 이제 영상 탭 UI에서 고른
            # 값을 그대로 job에 실어 flow_econ_driver.py가 읽게 한다(mode='image'일 땐 안 쓰임).
            "video_ratio": video_ratio,
            "video_model": video_model,
            "video_resolution": video_resolution,
            "video_duration": video_duration,
            "video_count": int(video_count),
            # 2026-09-10 추가 — 생성 완료 시 flow_econ_driver.py가 Storage 업로드 +
            # scenePrompts의 sceneImage 자동 등록까지 하려면 이 두 id가 필요하다.
            "site_id": site_id,
            "unit_id": unit_id,
            # 2026-09-10 추가, 2026-09-11 다중 캐릭터로 확장 — "일관성" 대응: 프로젝트 안에
            # 미리 만들어둔 Flow 캐릭터 이름들을 넣으면, 드라이버가 씬 프롬프트에 매칭 키워드가
            # 있는 캐릭터만 골라 자동으로 첨부한다(예: 사회자 "Gentleman Rouge"/"Rouge" 키워드,
            # 배우 "representing" 키워드 — 계정 프로젝트에 실제로 만들어둔 이름과 정확히 일치해야 함).
            "characters": [
                {"name": str(c.get("name", "")).strip(), "match": [str(k).strip() for k in (c.get("match") or []) if str(k).strip()]}
                for c in (characters or [])
                if str(c.get("name", "")).strip() and c.get("match")
            ],
            # 2026-09-11 추가 — 사용자 지시: 패널에서 켜고 끌 수 있는 설정으로 노출("1장으로
            # 지정할 수 있게 설정을 부분을 만들어줘"). True면 드라이버가 제출 프롬프트 맨 앞에
            # "이미지 1장만 만들어라" 지시를 붙인다(기본 켜짐 — Flow 에이전트가 스스로 여러 장을
            # 만드는 창작적 이탈을 줄이기 위함).
            "image_count": int(image_count),
            # 2026-09-11 추가 — 사람이 "프로젝트 불러오기"로 직접 고른 프로젝트 URL. 있으면
            # 드라이버가 로컬 캐시(_flow_state.json의 project_url)보다 이걸 항상 우선한다.
            "project_url": (project_url or "").strip() or None,
            # 2026-09-12 추가 — Flow의 새 "에이전트" 대화형 모드가 켜져 있으면 캐릭터 멘션 뒤
            # 제출해도 곧장 생성 안 되고 확인 메뉴가 뜨는 게 실측됨(S70). 기본 켜짐 — 프로젝트
            # 시작 시 드라이버가 에이전트 칩을 확인해서 켜져 있으면 자동으로 끈다.
            "agent_off": bool(agent_off),
        }
        job_path = run_dir / "job.json"
        job_path.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
        (run_dir / "stop.flag").unlink(missing_ok=True)
        state_path = run_dir / "_flow_state.json"
        if new_project:
            # "새 프로젝트로 시작" 체크됨 — 기존 done/titles 기록(이미 생성된 씬)은 그대로 두고
            # project_url만 비워서 open_project(None)이 새 Flow 프로젝트를 만들게 한다.
            prev = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
            prev["project_url"] = None
            prev.setdefault("titles", {})
            prev.setdefault("done", {})
            prev["settings_done"] = False  # 새 프로젝트는 비율 설정도 새로 해야 한다
            state_path.write_text(json.dumps(prev, ensure_ascii=False, indent=1), encoding="utf-8")
        elif not state_path.exists():
            # 2026-09-10 실사고 수정 — 여기가 항상 빈 문자열("")이었는데 open_project("")는
            # falsy라 새 프로젝트를 만들어버린다(오늘 두 번째로 이 버그가 재발함, 엉뚱한
            # 프로젝트 779605ab...가 또 생김). 실제 진짜 프로젝트 URL을 직접 넣는다 — 단,
            # 이건 mintimjang33(9223) 계정 전용 프로젝트라 다른 포트(계정)엔 안 맞는다.
            # 다른 포트는 project_url을 비워서 그 계정에서 처음 한 번 새 프로젝트가 만들어지고,
            # 이후 이 상태 파일에 저장돼 같은 포트에서는 계속 재사용된다.
            default_project = (
                "https://flow.google.com/project/6f8960c1-1b98-4e73-b5c0-0e63010bf835"
                if port == 9223 else None
            )
            state_path.write_text(json.dumps({
                "project_url": os.environ.get("FLOW_PROJECT_URL", default_project),
                "titles": {}, "done": {},
            }, ensure_ascii=False, indent=1), encoding="utf-8")
        status_path = run_dir / "status.json"
        status_path.write_text(json.dumps({
            "phase": "idle", "total": len(scenes), "done_count": 0,
            "workflow": job["workflow"], "content_title": job["content_title"],
            "step_no": job["step_no"], "step_name": job["step_name"],
        }, ensure_ascii=False, indent=1), encoding="utf-8")

        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["BU_CDP_URL"] = f"http://127.0.0.1:{port}"
        env["FLOW_JOB"] = str(job_path)
        env["FLOW_STAGE"] = "images" if mode == "image" else "clips"
        log_path = run_dir / "run.log"
        proc = subprocess.Popen(
            [sys.executable, str(HERE / "flow_econ_driver.py")],
            cwd=str(HERE.parent), env=env,
            stdout=open(log_path, "w", encoding="utf-8"), stderr=subprocess.STDOUT,
        )
        _runs[port] = {"proc": proc, "run_dir": run_dir}
        # 2026-09-11 추가 — 드라이버 프로세스는 대시보드가 재시작돼도 안 죽고 계속 돈다(별도
        # 프로세스라서). 근데 worker_status()가 메모리 속 _runs만 보던 시절엔, 대시보드를
        # 재시작하는 순간(오늘 로딩 버그 수정 중 여러 번 재시작함) 실제로는 S25까지 계속
        # 진행 중인데도 화면엔 "실행 중인 계정 없음"으로 나오는 사고가 있었다(사용자 지적:
        # "상단에 현재 작업중인게 몇번인지도 안나오고~"). PID를 파일로 남겨서, 재시작 후에도
        # worker_status()가 실제 살아있는 프로세스를 다시 찾아낼 수 있게 한다.
        (run_dir / "driver.pid").write_text(str(proc.pid), encoding="utf-8")
        # 2026-09-10 추가 — 드라이버가 시작하자마자 죽으면(예: Flow 크롬에 탭이 없어서 CDP
        # 연결 실패) 사용자 화면엔 "생성 눌러도 아무 반응 없음"으로만 보였다. 잠깐 기다렸다가
        # 바로 죽었으면 run.log 마지막 줄을 에러로 돌려줘서 최소한 원인은 바로 보이게 한다.
        time.sleep(1.5)
        if proc.poll() is not None:
            tail = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            return {"error": f"포트 {port}: 생성이 시작되자마자 멈췄습니다: " + (tail[-1] if tail else "(로그 없음)")}
        return {"ok": True, "run_dir": str(run_dir), "scene_count": len(scenes), "port": port}


# 2026-09-11 추가 — 사용자 지적: "프로잭트를 찾아서 선택을 하게끔 수정해야겠어". 코드에
# 박힌 기본 프로젝트 URL이나 로컬 상태 파일 캐시가 실제 캐릭터가 등록된 프로젝트와 어긋나는
# 사고(9b8ec20f... 오래된 프로젝트를 계속 쓰던 실사고)가 있었다 — 매번 홈 화면에서 실제
# 프로젝트 목록을 읽어와 사람이 직접 고르게 한다. job.json 없이 짧게 한 번만 실행되는
# 모드(FLOW_STAGE=list_projects)를 서브프로세스로 돌려 결과를 동기적으로 기다린다.
def list_flow_projects(port: int) -> dict:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["BU_CDP_URL"] = f"http://127.0.0.1:{port}"
    env["FLOW_STAGE"] = "list_projects"
    try:
        result = subprocess.run(
            [sys.executable, str(HERE / "flow_econ_driver.py")],
            cwd=str(HERE.parent), env=env,
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"포트 {port}: 프로젝트 목록 조회가 60초를 넘겨 타임아웃됐습니다."}
    for line in (result.stdout or "").splitlines():
        if line.startswith("LIST_PROJECTS_RESULT:"):
            try:
                data = json.loads(line[len("LIST_PROJECTS_RESULT:"):])
                return {"ok": True, "projects": data.get("projects", [])}
            except json.JSONDecodeError:
                pass
    tail = (result.stdout or "").strip().splitlines()
    err_tail = (result.stderr or "").strip().splitlines()
    detail = (err_tail[-1] if err_tail else (tail[-1] if tail else "(출력 없음)"))
    return {"error": f"포트 {port}: 프로젝트 목록을 못 읽었습니다 — {detail}"}


def stop_run(port: int):
    with _lock:
        run = _runs.get(port)
        if run and run.get("run_dir"):
            (run["run_dir"] / "stop.flag").write_text("stop", encoding="utf-8")
            return {"ok": True}
        return {"error": f"포트 {port}엔 실행 중인 작업이 없습니다."}


def _pid_alive(pid: int) -> bool:
    """2026-09-11 추가 — driver.pid로 남긴 PID가 아직 살아있는 프로세스인지 확인한다
    (Windows, 외부 의존성 없이 ctypes로). 확인 자체가 실패하면(권한 등) 죽었다고 섣불리
    판단해서 화면에서 사라지는 것보다는, 살아있다고 보수적으로 간주하는 쪽이 안전하다."""
    if pid <= 0:
        return False
    try:
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    except Exception:
        return True


def worker_status():
    """2026-09-10 재설계 — 계정(포트) 여러 개를 동시에 돌리게 되면서, 씬 목록(done/pending)은
    더 이상 어느 한 프로세스의 로컬 상태만 보고 판단할 수 없다(다른 포트가 다른 씬을 끝낼
    수도 있으므로). 씬 목록은 /api/scenes(DB 직접 조회)가 계속 맡고, 여기서는 포트별
    "지금 뭘 하고 있는지"만 돌려준다 — 프론트가 이 둘을 합쳐서 보여준다.
    2026-09-11 실사고 수정 — 드라이버 프로세스는 대시보드가 재시작돼도 계속 살아있는데(별도
    프로세스), 여기가 메모리 속 _runs만 보고 있어서 대시보드를 재시작하면(오늘 로딩 버그
    수정 중 여러 번 재시작함) 실제로는 계속 진행 중인데도 "실행 중인 계정 없음"으로 보이는
    사고가 있었다(사용자 지적: "상단에 현재 작업중인게 몇번인지도 안나오고~", "현재
    작업중인 번호가 펼쳐져서 프롬프트 확인하게 해달랬는데 그렇게 동작을 안 하네"). _runs에
    없는 run_dir도 RUN_DIR를 스캔해서 driver.pid가 실제로 살아있으면 워커 목록에 포함시킨다."""
    with _lock:
        tracked = dict(_runs)
    workers = []
    seen_dirs = set()
    for port, run in tracked.items():
        run_dir = run["run_dir"]
        seen_dirs.add(run_dir)
        status = json.loads((run_dir / "status.json").read_text(encoding="utf-8")) if (run_dir / "status.json").exists() else {}
        status["port"] = port
        workers.append(status)
    if RUN_DIR.exists():
        for d in RUN_DIR.iterdir():
            if not d.is_dir() or d in seen_dirs:
                continue
            # 2026-09-13 수정 — run_dir 이름이 "{unit_id}__{port}__{mode}"로 바뀌면서
            # (이미지/영상 로컬 상태 분리) 끝이 더 이상 숫자가 아니라 "image"/"video"로
            # 끝난다 — 포트 번호는 그 앞 그룹에서 뽑는다.
            m = re.search(r"__(\d+)__(?:image|video)$", d.name)
            if not m:
                continue
            pid_path = d / "driver.pid"
            status_path = d / "status.json"
            if not pid_path.exists() or not status_path.exists():
                continue
            try:
                pid = int(pid_path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                continue
            if not _pid_alive(pid):
                continue
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["port"] = int(m.group(1))
            workers.append(status)
    return {"workers": workers}


site_name_cache: dict[str, str] = {}

HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>씬 이미지 생성 진행상황</title>
<style>
  body{font-family:system-ui,sans-serif;background:#111;color:#eee;margin:0;padding:14px;font-size:13px}
  h1{font-size:15px;color:#9ad;margin:0 0 10px}
  select,button{background:#222;color:#eee;border:1px solid #444;border-radius:6px;padding:6px 8px;font-size:12px}
  select{width:100%;margin-bottom:8px}
  .row{display:flex;gap:8px;align-items:center;margin:6px 0}
  label{color:#999;min-width:70px}
  .badge{padding:2px 7px;border-radius:6px;font-size:11px;font-weight:700}
  .waiting,.submitting,.running{background:#553}
  .done{background:#265}
  .failed{background:#622}
  .idle,.pending{background:#333}
  .bar{background:#222;border-radius:6px;overflow:hidden;height:16px;flex:1}
  .fill{background:#4a8;height:100%;transition:width .3s}
  img{max-width:100%;border-radius:8px;border:1px solid #333;display:block;margin-top:6px}
  .gallery-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:8px;padding:8px}
  .gallery-item{cursor:pointer}
  .gallery-item img{width:100%;height:120px;object-fit:cover;margin-top:0}
  .gallery-item .gid{font-size:10px;color:#9ad;text-align:center;margin-top:2px}
  #startBtn{background:#275;font-weight:700}
  #stopBtn{background:#a33;font-weight:700}
  #scenes{max-height:340px;overflow-y:auto;border:1px solid #2a2a2a;border-radius:6px;margin-top:8px}
  .scene-row{display:flex;justify-content:space-between;gap:6px;padding:5px 8px;border-bottom:1px solid #222;font-size:11px}
  .scene-row:last-child{border-bottom:none}
  .scene-detail{background:#1a1a1a;padding:6px 10px;border-bottom:1px solid #222}
  .scene-id{color:#9ad;font-weight:700;min-width:32px}
  .scene-title{flex:1;color:#bbb;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
</style></head>
<body>
  <h1>🎬 씬 이미지 생성 — 실시간 진행</h1>
  <!-- 2026-09-12 추가 — 사용자 요청: "제일상단에 통일적용 체크박스 추가 => 여기에서 선택한걸로
       동일하게 적용되게(워크플로우,콘텐츠,비율,장수,설정)". 통합 패널(?panel=multistart)
       전용이라 기본은 숨겨두고(다른 모드에선 의미 없음), 그 모드에서만 보여준다 — 체크하면
       이 화면(워크플로우/콘텐츠/화면비율/생성 장수/에이전트 꺼짐/캐릭터)을 그대로 부모(메인
       컨트롤) 페이지의 계정별 임베드(?view=922X) 4개에 실시간으로 밀어넣는다
       (applyUnifyAll() 참고). -->
  <div id="unifyAllRow" class="row" style="display:none"><label><input type="checkbox" id="unifyAllChk" onchange="saveUiState(); applyUnifyAll()"> 여기서 고른 대로 4개 계정 전부 동일하게 적용(워크플로우·콘텐츠·비율·장수·설정)</label></div>
  <div class="row"><label>씬 파일</label>
    <input type="file" id="sceneFileInput" accept=".txt,.json" style="flex:1;background:#222;color:#eee;border:1px solid #444;border-radius:6px;padding:4px;font-size:11px">
    <button onclick="uploadSceneFile()" style="background:#275;font-weight:700">📤 파일로 씬 등록</button></div>
  <div id="sceneFileResult" style="font-size:11px;color:#c66;white-space:pre-line;margin:-4px 0 6px"></div>
  <div class="row"><label>워크플로우</label>
    <select id="siteSel" onchange="onSiteChange().then(applyUnifyAll)"><option value="">불러오는 중...</option></select></div>
  <div class="row"><label>콘텐츠</label>
    <select id="unitSel" onchange="onUnitChange().then(applyUnifyAll)"><option value="">워크플로우를 먼저 선택하세요</option></select></div>
  <!-- 2026-09-13 (5차) 수정 — 사용자 지적: "이 체크부분도 탭선택(이미지,영상)에 따라
       동적으로 변해야겠다" — 화면비율/생성장수는 이미지 생성 전용 설정이라 이름을
       imageSettingsBlock으로 감싸고, 영상 전용 설정(videoSettingsBlock)을 새로 만들어
       setMediaMode()가 탭에 맞는 쪽만 보이게 전환한다(기본은 이미지 탭이 선택돼 있으므로
       이미지 설정만 보임). -->
  <div id="imageSettingsBlock">
    <div class="row"><label>화면비율</label>
      <select id="ratioSel" onchange="saveUiState(); applyUnifyAll()">
        <option value="16:9">16:9 (가로)</option>
        <option value="9:16">9:16 (세로)</option>
        <option value="1:1">1:1 (정사각)</option>
      </select></div>
    <div class="row"><label>생성 장수</label>
      <select id="imageCountSel" onchange="saveUiState(); applyUnifyAll()">
        <option value="1" selected>1장</option>
        <option value="2">2장</option>
        <option value="3">3장</option>
        <option value="4">4장</option>
      </select></div>
    <div class="row" style="margin-top:-4px"><span style="color:#777;font-size:10px">기본 1장 — 이 숫자를 프롬프트에 명시해서 Flow 에이전트가 제멋대로 몇 장씩 만드는 걸 막는다. 저장·홍허브 등록은 항상 가장 최근 1장만 됨(2장 이상 선택해도 나머지는 로컬에만 남고 등록 안 됨).</span></div>
  </div>
  <!-- 영상 탭 전용 설정 — 사용자 지시값 그대로 기본값을 둠: "동영상,프레임,16:9(롱폼),
       옴니1.1 프레쉬,720,1장". 크레딧 안내도 사용자가 알려준 값 그대로("4초=>7크레딧,
       8초=>12크레딧, 10초=>15크레딧") — 길이를 바꿀 때마다 크레딧 소모량을 바로 알 수 있게. -->
  <div id="videoSettingsBlock" style="display:none">
    <div class="row"><label>화면비율</label>
      <select id="videoRatioSel" onchange="saveUiState()">
        <option value="16:9" selected>16:9 (롱폼)</option>
        <option value="9:16">9:16 (숏폼)</option>
      </select></div>
    <div class="row"><label>모델</label>
      <select id="videoModelSel" onchange="saveUiState()">
        <option value="Omni 1.1 Flash" selected>Omni 1.1 Flash</option>
        <option value="Veo 3.1 – Lite">Veo 3.1 – Lite</option>
        <option value="Veo 3.1 – Fast">Veo 3.1 – Fast</option>
        <option value="Veo 3.1 – Quality">Veo 3.1 – Quality</option>
      </select></div>
    <div class="row"><label>해상도</label>
      <select id="videoResolutionSel" onchange="saveUiState()">
        <option value="360p">360p</option>
        <option value="720p" selected>720p</option>
      </select></div>
    <div class="row"><label>길이</label>
      <select id="videoDurationSel" onchange="saveUiState()">
        <option value="4초">4초 (7크레딧)</option>
        <option value="6초">6초</option>
        <option value="8초" selected>8초 (12크레딧)</option>
        <option value="10초">10초 (15크레딧)</option>
      </select></div>
    <div class="row"><label>배치수</label>
      <select id="videoCountSel" onchange="saveUiState()">
        <option value="1" selected>x1</option>
        <option value="2">x2</option>
        <option value="3">x3</option>
        <option value="4">x4</option>
      </select></div>
    <div class="row" style="margin-top:-4px"><span style="color:#777;font-size:10px">크레딧은 길이 기준(사용자 실측): 4초=7, 8초=12, 10초=15. 배치수를 늘리면 그만큼 곱해서 소모됨.</span></div>
  </div>
  <!-- 2026-09-12 추가 — 사용자 요청: "화면비율, 생성장수, 에이전트 선택체크를 한곳에 모아두고".
       Flow의 새 "에이전트" 대화형 모드(support.google.com/flow/answer/17093911)가 켜져 있으면
       캐릭터를 멘션한 뒤 제출해도 곧장 생성되지 않고 확인 메뉴가 뜨는 게 실측 확인됨(S70 자동화가
       이 지점에서 멈춰있었음) — 기본으로 꺼서 시작하되, 체크 해제하면 건드리지 않는다. -->
  <div class="row"><label><input type="checkbox" id="agentOffChk" checked onchange="saveUiState(); applyUnifyAll()"> 에이전트 꺼짐 확인(프로젝트 시작 시 자동으로 끔)</label></div>
  <div style="margin:6px 0"><label style="color:#999;display:block;margin-bottom:4px">캐릭터(선택, 여러 개 가능)</label>
    <textarea id="charactersInput" rows="2" placeholder="한 줄에 하나씩: Flow등록이름=매칭키워드1,키워드2&#10;예) 젠틀맨루즈=Gentleman Rouge,Rouge&#10;예) 스틱맨=representing" onchange="saveUiState(); renderScenes(previewScenes); applyUnifyAll()" style="width:100%;box-sizing:border-box;background:#222;color:#eee;border:1px solid #444;border-radius:6px;padding:6px 8px;font-size:12px;font-family:inherit;resize:vertical">젠틀맨루즈=Gentleman Rouge,Rouge
스틱맨=stickman actor</textarea>
    <span style="color:#777;font-size:10px;display:block;margin-top:2px">씬 프롬프트에 매칭키워드 중 하나라도 있으면 그 이름의 Flow 캐릭터를 자동 첨부합니다(계정 프로젝트에 미리 등록된 이름과 정확히 일치해야 함).</span></div>
  <!-- 2026-09-12 추가 — 사용자 지적: "이부분 삭제 해야 할꺼 같아"(계정(포트)·새 프로젝트로
       시작·Flow 프로젝트 선택을 가리킴). 이 셋은 원래 "이 계정 하나"를 위한 설정이라 ?panel=
       multistart(위쪽 통합 패널, 4개 계정에 동시에 쏘는 용도)에서 보면 마치 그 중 한 계정
       (예: minsiljjang 9226)에만 적용되는 것처럼 보여 오해를 준다 — 실제로 startAllAccounts/
       autoDispatch는 이 값들을 아예 읽지 않는다(포트별로 각 계정 화면에 저장된 마지막 프로젝트를
       그대로 씀). id로 감싸서 통합 패널에서만 숨긴다(계정별 개별 화면 — ?view=922X —  에서는
       그대로 필요하므로 유지). -->
  <div id="accountProjectBlock">
    <div class="row"><label>계정(포트)</label>
      <select id="portSel" autocomplete="off" onchange="saveUiState(); savePortToServer(this.value); applyProjectListForPort(parseInt(this.value,10))">
        <option value="9223">mintimjang33 (9223)</option>
        <option value="9224">minsiljang0 (9224)</option>
        <option value="9225">minssajang (9225)</option>
        <option value="9226">minsiljjang (9226)</option>
      </select></div>
    <!-- 2026-09-12 추가 — 사용자 요청: "동일한 숫자가 2곳에 표시되어야해". 드롭다운 하나만 보고
         믿기 어렵다는 지적으로, 서버(dashboard_state.json)에 실제 저장된 값을 별도 텍스트로
         한 번 더 보여준다 — 두 표시가 항상 같은 값이어야 정상이다. -->
    <div class="row"><label></label><span id="savedPortLabel" style="color:#6a6;font-size:11px;font-weight:700"></span></div>
    <div class="row"><label><input type="checkbox" id="newProjectChk" onchange="saveUiState()"> 새 프로젝트로 시작</label></div>
    <div style="margin:6px 0">
      <label style="color:#999;display:block;margin-bottom:4px">Flow 프로젝트(선택)</label>
      <div style="display:flex;gap:6px">
        <select id="projectSel" onchange="onProjectSelChange()" style="flex:1">
          <option value="">(고르면 캐릭터가 등록된 정확한 프로젝트로 고정됨)</option>
        </select>
        <button onclick="loadFlowProjects()" style="background:#358;white-space:nowrap">🔍 목록 불러오기</button>
      </div>
      <span id="projectLoadResult" style="color:#c66;font-size:10px;display:block;margin-top:2px"></span>
      <span style="color:#777;font-size:10px;display:block;margin-top:2px">비워두면 이 계정에서 마지막에 쓰던 프로젝트를 그대로 씀 — 캐릭터가 다른 프로젝트에 있어서 실패하면 여기서 정확한 프로젝트를 골라주세요.</span>
    </div>
  </div>
  <hr style="border-color:#333">
  <!-- 2026-09-12 추가 — 사용자 요청: "이거 접고 펴게 해줘 평소에 접어두고". 여러 계정 동시
       시작 섹션은 자주 안 쓰는 고급 기능이라, <details>로 접어서 평소엔 한 줄(제목)만
       보이게 하고 필요할 때만 펼친다(open 속성 없음 = 기본 접힘). -->
  <details id="multiStartDetails">
    <summary class="row" style="cursor:pointer;display:list-item"><b>⚡ 여러 계정 동시 시작</b></summary>
    <div class="row"><label style="min-width:110px">9223 범위</label>
      <input type="text" id="range9223" placeholder="예: 5~9" onchange="saveUiState()" style="flex:1;background:#222;color:#eee;border:1px solid #444;border-radius:6px;padding:6px 8px;font-size:12px"></div>
    <div class="row"><label style="min-width:110px">9224 범위</label>
      <input type="text" id="range9224" placeholder="예: 10~14" onchange="saveUiState()" style="flex:1;background:#222;color:#eee;border:1px solid #444;border-radius:6px;padding:6px 8px;font-size:12px"></div>
    <div class="row"><label style="min-width:110px">9225 범위</label>
      <input type="text" id="range9225" placeholder="예: 15~19" onchange="saveUiState()" style="flex:1;background:#222;color:#eee;border:1px solid #444;border-radius:6px;padding:6px 8px;font-size:12px"></div>
    <div class="row"><label style="min-width:110px">9226 범위</label>
      <input type="text" id="range9226" placeholder="예: 20~24" onchange="saveUiState()" style="flex:1;background:#222;color:#eee;border:1px solid #444;border-radius:6px;padding:6px 8px;font-size:12px"></div>
    <div class="row"><button id="startAllBtn" onclick="startAllAccounts()" style="background:#275;font-weight:700;width:100%">▶▶▶ 여러 계정 동시 시작</button></div>
    <div id="startAllResult" style="font-size:11px;color:#c66"></div>
    <div class="row"><button id="autoDispatchBtn" onclick="autoDispatch()" style="background:#25a;font-weight:700;width:100%">🔀 대기 씬 자동 분배 시작 (9223~9226 균등 배분)</button></div>
    <div id="autoDispatchResult" style="font-size:11px;color:#c66"></div>
  </details>
  <hr style="border-color:#333">
  <!-- 2026-09-13 추가 — 사용자 요청: "탭으로 이미지/영상 구분해주고 이미지쪽에 이미지
       프롬프트 보여주듯이 영상쪽에 영상프롬프트 보여주고 이미지쪽과 동일하게 만들어주면돼".
       이 밑의 번호범위/시작/체크박스/목록이 전부 이 토글에 따라 이미지 또는 영상 대상으로
       동작한다(setMediaMode 참고) — 영상 쪽 "▶ 생성"도 이미지와 동일하게 실제 Flow 자동
       생성(flow_econ_driver.py clips 스테이지)까지 연결돼 있다. -->
  <div class="row">
    <button id="modeImageBtn" onclick="setMediaMode('image')" style="background:#358;flex:1;font-weight:700">🖼 이미지</button>
    <button id="modeVideoBtn" onclick="setMediaMode('video')" style="background:#222;flex:1;font-weight:700">🎬 영상</button>
  </div>
  <div class="row"><label>번호 범위</label>
    <input type="text" id="rangeInput" placeholder="예: 5~10 (비우면 체크된 씬 전부)" oninput="updateSelectedCount()" onchange="saveUiState()" style="width:100%;background:#222;color:#eee;border:1px solid #444;border-radius:6px;padding:6px 8px;font-size:12px"></div>
  <div class="row">
    <button id="startBtn" onclick="startRun()">▶ 선택한 번호부터 생성시작</button>
    <button id="stopBtn" onclick="stopRun()">⏹ 지금 멈추기</button>
  </div>
  <div class="row"><span id="stopResult" style="font-size:11px;color:#999"></span></div>
  <div class="row">
    <button onclick="selectAll(true)">☑ 전체 선택</button>
    <button onclick="selectAll(false)">☐ 전체 해제</button>
    <button id="refreshBtn" onclick="refreshNow()">🔄 새로고침</button>
  </div>
  <div class="row"><span>선택됨:</span><b id="selectedCount">0개</b> <span style="margin-left:10px">남은:</span><b id="remainingCount">0개</b> <span id="lastSync" style="color:#666;font-size:10px;margin-left:auto"></span></div>
  <!-- 2026-09-12 추가 — 사용자 요청: 기본은 지금 고른 계정 것만 보여주되(위 필터 참고),
       "차라리 그러면 메인 컨트롤페이지를 띄울 수 있게 해줘" — 4계정을 한눈에 보고 싶을 때는
       이 체크박스로 전체를 다시 볼 수 있게 한다. -->
  <div class="row"><label style="color:#999;font-size:11px"><input type="checkbox" id="showAllWorkersChk" onchange="saveUiState(); fetchAndRender()"> 🖥 전체 계정 보기(메인 컨트롤)</label></div>
  <div id="workers" style="width:100%"></div>
  <div id="failBanner" style="display:none;background:#3a1414;border:1px solid #a33;border-radius:6px;padding:8px;margin:6px 0;font-size:11px"></div>
  <div class="row" id="sceneFilterRow" style="gap:4px;flex-wrap:wrap"></div>
  <div class="row"><button id="galleryToggleBtn" onclick="toggleGalleryMode()">🖼 갤러리로 보기 (일관성 확인)</button></div>
  <div id="scenes"></div>
<script>
async function loadSites(){
  const r = await fetch('/api/sites');
  const sites = await r.json();
  const sel = document.getElementById('siteSel');
  sel.innerHTML = '<option value="">-- 선택 --</option>' + sites.map(s=>`<option value="${s.id}">${s.name} (${s.unit_count}개 콘텐츠)</option>`).join('');
}
let previewScenes = [];
// 2026-09-10 추가 — "선택한 상태를 기억하고 있을수 있어?"라는 요청으로, 워크플로우/콘텐츠/
// 비율/새프로젝트여부/진행개수를 localStorage에 저장해뒀다가 패널을 새로고침해도 복원한다.
// 2026-09-11 추가 — 캐릭터 2개(사회자+배우) 이상을 지원하려고 "이름=키워드1,키워드2" 한 줄씩
// 받는 텍스트칸으로 바꿨다. 이름은 Flow에 등록된 캐릭터 이름과 정확히 일치해야 하고, 키워드
// 중 하나라도 씬 프롬프트에 있으면 그 캐릭터를 자동 첨부한다.
function parseCharactersInput(raw){
  return (raw || '').split('\\n').map(line => {
    const i = line.indexOf('=');
    if(i < 0) return null;
    const name = line.slice(0, i).trim();
    const match = line.slice(i + 1).split(',').map(k => k.trim()).filter(Boolean);
    if(!name || !match.length) return null;
    return {name, match};
  }).filter(Boolean);
}
// 2026-09-12 추가 — 계정(포트) 선택은 서버(dashboard_state.json)에도 같이 저장한다.
// localStorage는 크롬창(프로필)마다 따로 노는데, 이 서버는 4개 창이 전부 공유하는 유일한
// 지점이라 여기 저장해야 "어느 창에서 열어도 항상 같은 계정"이 보장된다.
function showSavedPort(port){
  const el = document.getElementById('savedPortLabel');
  if(el) el.textContent = port ? `✓ 서버에 저장된 계정: ${port}` : '';
}
function savePortToServer(port){
  fetch('/api/ui_state', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({port: String(port)})})
    .then(() => showSavedPort(String(port)))
    .catch(()=>{});
}
function saveUiState(){
  localStorage.setItem('flowDashUi', JSON.stringify({
    siteId: document.getElementById('siteSel').value,
    unitId: document.getElementById('unitSel').value,
    ratio: document.getElementById('ratioSel').value,
    newProject: document.getElementById('newProjectChk').checked,
    range: document.getElementById('rangeInput').value,
    port: document.getElementById('portSel').value,
    range9223: document.getElementById('range9223').value,
    range9224: document.getElementById('range9224').value,
    range9225: document.getElementById('range9225').value,
    range9226: document.getElementById('range9226').value,
    charactersText: document.getElementById('charactersInput').value,
    imageCount: parseInt(document.getElementById('imageCountSel').value, 10),
    projectUrl: document.getElementById('projectSel').value,
    agentOff: document.getElementById('agentOffChk').checked,
    unifyAll: document.getElementById('unifyAllChk') ? document.getElementById('unifyAllChk').checked : false,
    // 2026-09-13 (5차) 추가 — 영상 탭 설정도 새로고침/재시작 후에 유지되게 저장한다.
    videoRatio: document.getElementById('videoRatioSel').value,
    videoModel: document.getElementById('videoModelSel').value,
    videoResolution: document.getElementById('videoResolutionSel').value,
    videoDuration: document.getElementById('videoDurationSel').value,
    videoCount: parseInt(document.getElementById('videoCountSel').value, 10),
  }));
}
// 2026-09-12 추가 — 사용자 요청: "제일상단에 통일적용 체크박스 추가 => 여기에서 선택한걸로
// 동일하게 적용되게(워크플로우,콘텐츠,비율,장수,설정)". 통합 패널(?panel=multistart)의
// "여기서 고른 대로 4개 계정 전부 동일하게 적용" 체크박스가 켜져 있을 때, 지금 이 화면의
// 워크플로우/콘텐츠/화면비율/생성 장수/에이전트 꺼짐/캐릭터를 그대로 부모(메인 컨트롤)
// 페이지에 나란히 떠 있는 계정별 임베드(?view=922X) 4개에 밀어넣는다 — 같은 출처
// (127.0.0.1:8799)라 iframe끼리도 서로 DOM·함수에 접근 가능하다. 워크플로우/콘텐츠는 단순
// 값 복사가 아니라 그 계정 화면 자신의 onSiteChange()/onUnitChange()를 그대로 호출해서
// (콘텐츠 목록 재조회 등 필요한 비동기 절차를 그대로 밟도록) 맞춘다. 부모가 없으면(독립 실행)
// 조용히 아무 일도 안 한다.
async function applyUnifyAll(){
  const chk = document.getElementById('unifyAllChk');
  if(!chk || !chk.checked) return;
  if(window.parent === window) return;
  const siteId = document.getElementById('siteSel').value;
  const unitId = document.getElementById('unitSel').value;
  const ratio = document.getElementById('ratioSel').value;
  const imageCount = document.getElementById('imageCountSel').value;
  const agentOff = document.getElementById('agentOffChk').checked;
  const charactersText = document.getElementById('charactersInput').value;
  let frames;
  try { frames = Array.from(window.parent.document.querySelectorAll('iframe[src*="?view="]')); }
  catch(e) { return; }
  for(const f of frames){
    try {
      const w = f.contentWindow, d = f.contentDocument;
      if(!w || !d) continue;
      const s = d.getElementById('siteSel');
      if(s && s.value !== siteId && typeof w.onSiteChange === 'function'){
        s.value = siteId;
        await w.onSiteChange(unitId);
      } else {
        const u = d.getElementById('unitSel');
        if(u && u.value !== unitId && typeof w.onUnitChange === 'function'){
          u.value = unitId;
          await w.onUnitChange();
        }
      }
      const r = d.getElementById('ratioSel'); if(r) r.value = ratio;
      const c = d.getElementById('imageCountSel'); if(c) c.value = imageCount;
      const a = d.getElementById('agentOffChk'); if(a) a.checked = agentOff;
      const ch = d.getElementById('charactersInput'); if(ch) ch.value = charactersText;
      if(typeof w.saveUiState === 'function') w.saveUiState();
    } catch(e) {}
  }
}
// 2026-09-11 추가 — "프로잭트를 찾아서 선택을 하게끔 수정해야겠어": 홈 화면의 실제 프로젝트
// 카드 목록을 읽어와 드롭다운으로 보여준다. 하드코딩된 기본 프로젝트나 로컬 캐시에 의존하지
// 않고, 지금 이 계정에 실제로 존재하는 프로젝트 중에서 고르게 한다.
//
// 2026-09-12 추가 — 사용자 지적: "이게 계정마다 다른거자나? 처음 실행했을때 불러온값을
// 저장하고 있으면 되자나~ 선택한걸로~~", "프로젝트를 삭제하거나 했을때만 사용자가 다시
// 불러오기 하면 되는거고". 매번 새로고침할 때마다 서버에 다시 물어보지 않고, 계정(포트)별로
// 마지막에 불러온 프로젝트 목록 + 그때 고른 값을 localStorage에 저장해뒀다가 그대로 복원한다.
// 실제로 프로젝트를 새로 만들거나 지운 경우에만 사람이 "🔍 목록 불러오기"를 다시 눌러
// 캐시를 갱신하면 된다.
function loadProjectCache(){
  try { return JSON.parse(localStorage.getItem('flowProjectCache') || '{}'); } catch(e) { return {}; }
}
function saveProjectCache(cache){
  localStorage.setItem('flowProjectCache', JSON.stringify(cache));
}
function applyProjectListForPort(port){
  const cache = loadProjectCache();
  const entry = cache[String(port)];
  const sel = document.getElementById('projectSel');
  const resultSpan = document.getElementById('projectLoadResult');
  if(!entry || !entry.projects){
    sel.innerHTML = '<option value="">(고르면 캐릭터가 등록된 정확한 프로젝트로 고정됨)</option>';
    resultSpan.style.color = '#777';
    resultSpan.textContent = '이 계정은 아직 저장된 목록이 없습니다 — 🔍 목록 불러오기를 눌러주세요.';
    return;
  }
  sel.innerHTML = '<option value="">(고르면 캐릭터가 등록된 정확한 프로젝트로 고정됨)</option>'
    + entry.projects.map(p => `<option value="https://flow.google.com/project/${p.id}">${p.name} (${p.id.slice(0,8)}…)</option>`).join('');
  if(entry.selected && [...sel.options].some(o=>o.value===entry.selected)) sel.value = entry.selected;
  resultSpan.style.color = '#6a6';
  resultSpan.textContent = `✅ ${entry.projects.length}개 프로젝트(저장된 목록) — 프로젝트를 새로 만들거나 지웠으면 🔍로 갱신하세요.`;
}
function onProjectSelChange(){
  saveUiState();
  const port = parseInt(document.getElementById('portSel').value, 10);
  const cache = loadProjectCache();
  if(cache[String(port)]){
    cache[String(port)].selected = document.getElementById('projectSel').value;
    saveProjectCache(cache);
  }
}
async function loadFlowProjects(){
  const resultSpan = document.getElementById('projectLoadResult');
  const sel = document.getElementById('projectSel');
  const port = parseInt(document.getElementById('portSel').value, 10);
  resultSpan.style.color = '#999'; resultSpan.textContent = '불러오는 중... (홈 화면 여는 중, 몇 초 걸릴 수 있음)';
  try{
    const r = await fetch('/api/list_projects', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({port})});
    const d = await r.json();
    if(d.error){ resultSpan.style.color = '#c66'; resultSpan.textContent = '⚠ ' + d.error; return; }
    const prev = sel.value;
    sel.innerHTML = '<option value="">(고르면 캐릭터가 등록된 정확한 프로젝트로 고정됨)</option>'
      + d.projects.map(p => `<option value="https://flow.google.com/project/${p.id}">${p.name} (${p.id.slice(0,8)}…)</option>`).join('');
    if(prev && [...sel.options].some(o=>o.value===prev)) sel.value = prev;
    resultSpan.style.color = '#6a6';
    resultSpan.textContent = `✅ ${d.projects.length}개 프로젝트 찾음 — 목록에서 골라주세요.`;
    const cache = loadProjectCache();
    cache[String(port)] = { projects: d.projects, selected: sel.value || '' };
    saveProjectCache(cache);
    saveUiState();
  }catch(e){ resultSpan.style.color = '#c66'; resultSpan.textContent = '⚠ 요청 실패: ' + e; }
}
// "9223에서 5개, 9224에서 5개, 9225에서 5개 각각 뽑아서 여기서 한번에 명령" 요청 —
// 계정별 범위 3칸을 한 번에 읽어서 /api/start를 포트마다 따로(순차) 호출한다.
function parseRangeStr(raw, scenes){
  raw = (raw||'').trim();
  if(!raw) return null;
  const m = raw.match(/^(\d+)\s*[~\-]\s*(\d+)$/);
  if(!m) return null;
  const lo = parseInt(m[1], 10), hi = parseInt(m[2], 10);
  return (scenes||[]).filter(s => s.num >= lo && s.num <= hi).map(s => s.id);
}
async function startAllAccounts(){
  const resultDiv = document.getElementById('startAllResult');
  resultDiv.textContent = '';
  const siteId = document.getElementById('siteSel').value;
  const unitId = document.getElementById('unitSel').value;
  if(!siteId || !unitId){ alert('워크플로우와 콘텐츠를 먼저 선택하세요.'); return; }
  const ratio = document.getElementById('ratioSel').value;
  const newProject = document.getElementById('newProjectChk').checked;
  const characters = parseCharactersInput(document.getElementById('charactersInput').value);
  const imageCount = parseInt(document.getElementById('imageCountSel').value, 10);
  const projectUrl = document.getElementById('projectSel').value || null;
  const agentOff = document.getElementById('agentOffChk').checked;
  const ranges = {
    9223: document.getElementById('range9223').value,
    9224: document.getElementById('range9224').value,
    9225: document.getElementById('range9225').value,
    9226: document.getElementById('range9226').value,
  };
  const btn = document.getElementById('startAllBtn');
  btn.disabled = true; btn.textContent = '전송 중...';
  const lines = [];
  for(const portStr of Object.keys(ranges)){
    const port = parseInt(portStr, 10);
    const ids = parseRangeStr(ranges[portStr], previewScenes);
    if(ids === null) continue;
    if(!ids.length){ lines.push(`포트 ${port}: 해당 범위 씬 없음`); continue; }
    try{
      const r = await fetch('/api/start', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({site_id: siteId, unit_id: unitId, scene_ids: ids, ratio, new_project: newProject, port, characters, image_count: imageCount, project_url: projectUrl, agent_off: agentOff})});
      const d = await r.json();
      lines.push(d.error ? `포트 ${port}: 실패 — ${d.error}` : `포트 ${port}: ${d.scene_count !== undefined ? ids.length : ''}개 시작됨`);
    }catch(e){ lines.push(`포트 ${port}: 요청 실패 — ${e}`); }
  }
  btn.disabled = false; btn.textContent = '▶▶▶ 여러 계정 동시 시작';
  resultDiv.style.color = lines.some(l=>l.includes('실패')) ? '#c66' : '#6a6';
  resultDiv.textContent = lines.join(' / ') || '범위를 하나 이상 입력하세요.';
}
// 2026-09-10 추가 — "창이 4개 떠 있으면 순서대로 돌아가면서 작업이 되어야 하는거 아니야?"
// 지적 — 위 startAllAccounts()는 사람이 계정별 범위를 직접 나눠 적어야 하는 1회성 기능이라,
// 대기(pending) 씬을 자동으로 계정 수만큼 균등하게 돌아가며(round-robin) 나눠서 한 번에
// 다 시작시키는 버튼을 별도로 추가한다. 각 계정은 배정받은 목록을 이후 순서대로 처리한다
// (계정 내부 순서는 driver가 이미 처리 — 여기선 "누구에게 어떤 씬을 줄지"만 나눈다).
async function autoDispatch(){
  const resultDiv = document.getElementById('autoDispatchResult');
  resultDiv.textContent = '';
  const siteId = document.getElementById('siteSel').value;
  const unitId = document.getElementById('unitSel').value;
  if(!siteId || !unitId){ alert('워크플로우와 콘텐츠를 먼저 선택하세요.'); return; }
  const pending = (previewScenes||[]).filter(s => s.status !== 'done').slice().sort((a,b)=>a.num-b.num);
  if(!pending.length){ resultDiv.style.color = '#6a6'; resultDiv.textContent = '대기 중인 씬이 없습니다 — 전부 완료 상태.'; return; }
  const ratio = document.getElementById('ratioSel').value;
  const newProject = document.getElementById('newProjectChk').checked;
  const characters = parseCharactersInput(document.getElementById('charactersInput').value);
  const imageCount = parseInt(document.getElementById('imageCountSel').value, 10);
  const projectUrl = document.getElementById('projectSel').value || null;
  const agentOff = document.getElementById('agentOffChk').checked;
  const ports = [9223, 9224, 9225, 9226];
  const groups = {}; ports.forEach(p => groups[p] = []);
  pending.forEach((s, i) => groups[ports[i % ports.length]].push(s.id));
  const btn = document.getElementById('autoDispatchBtn');
  btn.disabled = true; btn.textContent = '전송 중...';
  const lines = [];
  for(const port of ports){
    const ids = groups[port];
    if(!ids.length) continue;
    try{
      const r = await fetch('/api/start', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({site_id: siteId, unit_id: unitId, scene_ids: ids, ratio, new_project: newProject, port, characters, image_count: imageCount, project_url: port === parseInt(document.getElementById('portSel').value,10) ? projectUrl : null, agent_off: agentOff})});
      const d = await r.json();
      lines.push(d.error ? `포트 ${port}: 실패 — ${d.error}` : `포트 ${port}: ${ids.length}개 배정(${ids[0]}~${ids[ids.length-1]})`);
    }catch(e){ lines.push(`포트 ${port}: 요청 실패 — ${e}`); }
  }
  btn.disabled = false; btn.textContent = '🔀 대기 씬 자동 분배 시작 (9223~9226 균등 배분)';
  resultDiv.style.color = lines.some(l=>l.includes('실패')) ? '#c66' : '#6a6';
  resultDiv.textContent = lines.join(' / ');
}
// 2026-09-10 추가 — "진행개수보다 번호로 5~10 이렇게 지정하는 게 더 명확하겠다"는 요청.
// "5~10"/"5-10" 모두 허용, num(스토리보드 씬 번호) 기준으로 previewScenes에서 해당 범위의
// id만 순서대로 뽑는다. 비어있으면 null(범위 미사용, 기존 체크박스/진행개수 로직 그대로).
function rangeIds(){
  const raw = (document.getElementById('rangeInput').value || '').trim();
  if(!raw) return null;
  const m = raw.match(/^(\d+)\s*[~\-]\s*(\d+)$/);
  if(!m) return null;
  const lo = parseInt(m[1], 10), hi = parseInt(m[2], 10);
  return (previewScenes||[]).filter(s => s.num >= lo && s.num <= hi).map(s => s.id);
}
function loadUiState(){
  try { return JSON.parse(localStorage.getItem('flowDashUi') || '{}'); } catch(e) { return {}; }
}
async function onSiteChange(keepUnit){
  const siteId = document.getElementById('siteSel').value;
  const unitSel = document.getElementById('unitSel');
  saveUiState();
  if(!siteId){ unitSel.innerHTML = '<option value="">워크플로우를 먼저 선택하세요</option>'; return; }
  unitSel.innerHTML = '<option value="">불러오는 중...</option>';
  const r = await fetch('/api/units?site_id=' + encodeURIComponent(siteId));
  const units = await r.json();
  unitSel.innerHTML = units.map(u=>`<option value="${u.id}">${u.no?u.no+'번 · ':''}${u.title}${u.scene_count!=null?` (${u.scene_count}씬)`:''}</option>`).join('') || '<option value="">콘텐츠 없음</option>';
  if(keepUnit && [...unitSel.options].some(o=>o.value===keepUnit)) unitSel.value = keepUnit;
  await onUnitChange();
}
async function onUnitChange(){
  const siteId = document.getElementById('siteSel').value;
  const unitId = document.getElementById('unitSel').value;
  saveUiState();
  if(!siteId || !unitId){ previewScenes = []; return; }
  const r = await fetch('/api/scenes?site_id=' + encodeURIComponent(siteId) + '&unit_id=' + encodeURIComponent(unitId));
  previewScenes = await r.json();
  // 2026-09-10 — 이미 완료(done)된 씬은 기본적으로 체크 해제해서, 전체 시작을 눌러도
  // 다시 생성되며 섞이지 않게 한다("완성된 건 체크 해제해야 자동 진행에서 안 빠지지 않겠냐"는 지적).
  // 2026-09-13 수정 — 지금 고른 탭(이미지/영상) 기준으로 판정한다.
  checkedIds = new Set(activeScenes().filter(sc => !isDoneInMode(sc)).map(sc => sc.id));
  updateSelectedCount();
  renderScenes(previewScenes);
}
// 2026-09-11 추가 — 사용자 요청: "여기에 파일업로드를 하나 추가하자~ 니가 만들어준 텍스트로
// 하는 방법". Claude가 만든 씬 JSON 파일을 HongHub 앱(13번 패널)에 따로 안 가고 이 대시보드에서
// 바로 올려서 등록할 수 있게 한다. HongHub 앱의 extractSceneArrays()와 같은 방식으로, 파일
// 안에서 최상위 `[...]` JSON 배열 블록을 전부 찾아(문자열 안의 대괄호는 무시) 이어붙인다 —
// 여러 구간(청크)으로 나눠 만든 파일을 통째로 붙여넣어도 자동으로 합쳐진다.
function extractSceneArrays(raw){
  const results = [];
  let i = 0;
  while (i < raw.length) {
    if (raw[i] === '[') {
      const start = i;
      let depth = 0, inString = false, escape = false, j = i;
      for (; j < raw.length; j++) {
        const ch = raw[j];
        if (inString) {
          if (escape) escape = false;
          else if (ch === '\\\\') escape = true;
          else if (ch === '"') inString = false;
        } else {
          if (ch === '"') inString = true;
          else if (ch === '[') depth++;
          else if (ch === ']') { depth--; if (depth === 0) { j++; break; } }
        }
      }
      const candidate = raw.slice(start, j);
      try { const parsed = JSON.parse(candidate); if (Array.isArray(parsed)) results.push(...parsed); }
      catch(e) { /* JSON 배열이 아니면 건너뜀 */ }
      i = Math.max(j, start + 1);
    } else { i++; }
  }
  return results;
}
async function uploadSceneFile(){
  const resultDiv = document.getElementById('sceneFileResult');
  const siteId = document.getElementById('siteSel').value;
  const unitId = document.getElementById('unitSel').value;
  if(!siteId || !unitId){ alert('워크플로우와 콘텐츠를 먼저 선택하세요.'); return; }
  const fileInput = document.getElementById('sceneFileInput');
  const file = fileInput.files[0];
  if(!file){ alert('파일을 먼저 선택하세요.'); return; }
  resultDiv.style.color = '#999'; resultDiv.textContent = '읽는 중...';
  const text = await file.text();
  const scenes = extractSceneArrays(text);
  if(!scenes.length){ resultDiv.style.color = '#c66'; resultDiv.textContent = '❌ 파일에서 JSON 배열을 찾지 못했습니다.'; return; }
  resultDiv.textContent = `${scenes.length}개 파싱됨 — 등록 중...`;
  try{
    const r = await fetch('/api/register_scenes', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({site_id: siteId, unit_id: unitId, scenes})});
    const d = await r.json();
    if(d.error){ resultDiv.style.color = '#c66'; resultDiv.textContent = '⚠ ' + d.error; return; }
    resultDiv.style.color = '#6a6';
    resultDiv.textContent = `✅ ${d.scene_count}개 씬 등록 완료` + (d.images_carried_over ? ` (기존 생성 이미지 ${d.images_carried_over}개 유지됨)` : '');
    fileInput.value = '';
    await onUnitChange();
  }catch(e){ resultDiv.style.color = '#c66'; resultDiv.textContent = '⚠ 요청 실패: ' + e; }
}
let checkedIds = null; // null = 전체 선택(기본), Set이면 그 안의 id만 선택됨
let promptById = {};
// 2026-09-10 수정 — 계정(포트) 여러 개가 동시에 서로 다른 씬을 처리할 수 있게 되면서,
// "지금 하나만 펼침"이 아니라 "지금 실행 중인 애들은 전부(여러 개) 펼침"으로 바꿨다.
let expandedIds = new Set();
let imageUrlById = {};
// 2026-09-13 추가 — 사용자 요청: "탭으로 이미지/영상 구분해주고 이미지쪽에 이미지
// 프롬프트 보여주듯이 영상쪽에 영상프롬프트 보여주고 이미지쪽과 동일하게 만들어주면돼".
// previewScenes 하나(서버가 이미지·영상 필드를 한 번에 내려줌)를 그대로 두고, 화면
// 표시만 mediaMode로 바꾼다 — 캐시(promptById/imageUrlById)는 이미지 전용으로 그대로
// 두고, 영상은 별도 캐시(videoPromptById/videoUrlById)를 써서 탭을 오가도 서로
// 덮어쓰지 않게 한다.
let mediaMode = 'image'; // 'image' | 'video'
let videoPromptById = {};
let videoUrlById = {};
// 이 씬이 현재 모드 기준으로 이미 완료됐는지(이미지 모드=image_url 있음, 영상
// 모드=video_url 있음) — 체크박스 기본 선택, 남은 개수 집계, 탭별 (해당/남은) 표시가
// 전부 이 하나의 판정 기준을 공유해야 서로 어긋나지 않는다.
function isDoneInMode(sc){
  return mediaMode === 'video' ? sc.video_status === 'done' : sc.status === 'done';
}
// 영상 모드에선 needsVideoClip으로 표시된 씬만 대상으로 삼는다 — 모든 씬이 영상이
// 필요한 건 아니기 때문("영상 필요한 것들만 몇 개만 등록할 거야").
function activeScenes(){
  return mediaMode === 'video' ? (previewScenes||[]).filter(sc => sc.needs_video) : (previewScenes||[]);
}
function setMediaMode(m){
  if(mediaMode === m) return;
  mediaMode = m;
  document.getElementById('modeImageBtn').style.background = m === 'image' ? '#358' : '#222';
  document.getElementById('modeVideoBtn').style.background = m === 'video' ? '#358' : '#222';
  // 2026-09-13 (5차) 추가 — 사용자 지적: "이 체크부분도 탭선택(이미지,영상)에 따라
  // 동적으로 변해야겠다" — 탭을 바꾸면 그 모드 전용 설정판만 보이게 전환한다.
  document.getElementById('imageSettingsBlock').style.display = m === 'image' ? '' : 'none';
  document.getElementById('videoSettingsBlock').style.display = m === 'video' ? '' : 'none';
  // 탭을 바꾸면 그 모드 기준으로 "아직 안 끝난 것"만 기본 체크되게 다시 계산한다
  // (onUnitChange가 처음 목록을 불러올 때와 동일한 규칙).
  checkedIds = new Set(activeScenes().filter(sc => !isDoneInMode(sc)).map(sc => sc.id));
  sceneFilter = 'all';
  renderScenes(previewScenes);
  updateSelectedCount();
}
// 2026-09-11 추가 — 사용자 요청: "81개씬중 탭으로 분류되? 캐릭터 있는장면, 없는장면 이런거".
// 별도 필드 없이 프롬프트 텍스트에 "Gentleman Rouge"/"stickman actor" 문자열이 있는지로
// 분류한다 — 13번 마스터 프롬프트 원칙(캐릭터 롤플레이 명확화)이 항상 이 두 문구를 정확히
// 그대로 쓰도록 강제하고 있어서, 텍스트 매칭만으로 충분히 정확하다. 필터는 화면 표시에만
// 영향을 주고, 체크박스 선택(checkedIds)·번호범위 등 실제 생성 대상 목록은 그대로 전체 유지.
let sceneFilter = 'all';
// 2026-09-11 추가(3) — 사용자 지적: "어긋 안나게 체크해서 수정해줘" — 필터가 "Gentleman
// Rouge"/"stickman actor"를 하드코딩해서 판별하면, 화면 위쪽 "캐릭터(선택)" 칸의 매칭
// 키워드를 나중에 바꿨을 때 실제 생성기(flow_econ_driver.py의 characters 매칭, 886~890줄)
// 동작과 필터 표시가 어긋난다. 그래서 하드코딩을 없애고, 이 필터도 매번 그 칸을 직접
// 파싱해서(parseCharactersInput) 같은 매칭 키워드로 판정한다 — 두 곳이 항상 같은 소스를
// 본다. 캐릭터 목록이 몇 개든(2개 고정 아님) 자동으로 탭이 생기고, 2개 이상 매칭되면
// "같이출연" 탭에 잡힌다.
function classifyScene(prompt, characters){
  if(!prompt) return [];
  return (characters||[])
    .filter(c => (c.match||[]).some(kw => kw && prompt.includes(kw)))
    .map(c => c.name);
}
// 2026-09-11 추가(4) — 사용자 지적: "탭별로 해도 되???" — 탭은 지금까지 화면 표시만
// 필터링하고 실제 체크박스 선택(=생성 대상)은 안 건드려서, 탭을 눌러도 "시작"을 누르면
// 여전히 81개 전부가 돌아갔다. 탭을 누르면 그 탭에 해당하는(완료 안 된) 씬만 자동으로
// 체크되게 해서, 탭 → 시작만으로 그 분류만 생성되게 한다.
function setSceneFilter(f){
  sceneFilter = f;
  const characters = parseCharactersInput(document.getElementById('charactersInput').value);
  checkedIds = new Set(activeScenes()
    .filter(sc => !isDoneInMode(sc) && (f === 'all' || sceneMatchesFilter(sc.prompt, characters)))
    .map(sc => sc.id));
  renderScenes(previewScenes);
  updateSelectedCount();
}
function renderFilterTabs(scenes, characters){
  const matches = (scenes||[]).map(sc => classifyScene(sc.prompt, characters));
  const tabs = [{key:'all', label:'전체'}];
  characters.forEach(c => tabs.push({key: 'char:' + c.name, label: c.name}));
  if(characters.length >= 2) tabs.push({key:'multi', label:'🎭 같이출연'});
  tabs.push({key:'none', label:'🎨 캐릭터없음'});
  // 캐릭터 칸을 수정해서 지금 선택된 탭이 더 이상 존재하지 않으면(예: 그 이름을 지움) 전체로 되돌린다.
  if(!tabs.some(t => t.key === sceneFilter)) sceneFilter = 'all';
  const matchesKey = (m, key) => {
    if(key === 'all') return true;
    if(key === 'none') return m.length === 0;
    if(key === 'multi') return m.length >= 2;
    return m.includes(key.slice(5));
  };
  const countFor = (key) => matches.filter(m => matchesKey(m, key)).length;
  // 2026-09-12 추가 — 사용자 요청: "(해당숫자 / 남은숫자)". 탭별로 전체 개수뿐 아니라 그중
  // 아직 이미지가 없는(pending) 개수도 같이 보여줘서, 이 캐릭터가 몇 개 남았는지 탭만 보고
  // 바로 알 수 있게 한다.
  const remainingFor = (key) => matches.filter((m, i) => matchesKey(m, key) && !isDoneInMode(scenes[i] || {})).length;
  document.getElementById('sceneFilterRow').innerHTML = tabs.map(t =>
    `<button class="filter-btn" onclick="setSceneFilter('${t.key}')" style="font-size:11px;padding:4px 7px;background:${sceneFilter===t.key?'#358':'#222'}">${t.label} (${countFor(t.key)}/${remainingFor(t.key)})</button>`
  ).join('');
}
function sceneMatchesFilter(prompt, characters){
  const matched = classifyScene(prompt, characters);
  if(sceneFilter === 'all') return true;
  if(sceneFilter === 'none') return matched.length === 0;
  if(sceneFilter === 'multi') return matched.length >= 2;
  return matched.includes(sceneFilter.slice(5));
}
// 2026-09-12 추가 — 사용자 요청: "13단계에서도 탭별로(전체/루즈 등) 분류해서 일관성 유지가
// 잘 되었는지 수월하게 확인해볼 수 있게 해줘". 기존 필터 탭은 목록 행만 걸러줄 뿐, 이미지를
// 보려면 한 줄씩 펼쳐야(toggleExpand) 했다. 탭을 고른 상태에서 그 캐릭터의 완료된 씬 이미지를
// 썸네일 격자로 한 번에 쭉 늘어놓으면, 클릭 없이 스크롤만으로 디자인이 어긋난 컷을 바로 찾을
// 수 있다 — 목록 보기와 토글로 전환.
let galleryMode = false;
function toggleGalleryMode(){
  galleryMode = !galleryMode;
  const btn = document.getElementById('galleryToggleBtn');
  btn.style.background = galleryMode ? '#358' : '#222';
  btn.textContent = galleryMode ? '📋 목록으로 보기' : '🖼 갤러리로 보기 (일관성 확인)';
  renderScenes(previewScenes);
}
function renderGallery(visible){
  const list = document.getElementById('scenes');
  const urlById = mediaMode === 'video' ? videoUrlById : imageUrlById;
  const textById = mediaMode === 'video' ? videoPromptById : promptById;
  const withMedia = visible.filter(sc => urlById[sc.id]);
  if(!withMedia.length){
    list.innerHTML = `<div style="padding:12px;color:#777;font-size:12px">이 분류엔 아직 생성된 ${mediaMode === 'video' ? '영상' : '이미지'}이 없습니다.</div>`;
    return;
  }
  list.innerHTML = '<div class="gallery-grid">' + withMedia.map(sc => {
    const media = mediaMode === 'video'
      ? `<video src="${urlById[sc.id]}" muted></video>`
      : `<img src="${urlById[sc.id]}" loading="lazy">`;
    return `<div class="gallery-item" onclick="window.open('${urlById[sc.id]}','_blank')" title="${(textById[sc.id]||'').replace(/"/g,'&quot;')}">`
      + media + `<div class="gid">${sc.id}</div></div>`;
  }).join('') + '</div>';
}
function renderScenes(scenes){
  const list = document.getElementById('scenes');
  // 2026-09-13 추가 — 영상 탭은 needs_video 씬만 대상으로 하고(위 activeScenes와 동일
  // 기준), 캐시도 지금 모드에 맞는 쪽(이미지/영상)에만 채운다 — 탭을 오가도 서로
  // 덮어쓰지 않게.
  const modeScenes = mediaMode === 'video' ? (scenes||[]).filter(sc => sc.needs_video) : (scenes||[]);
  modeScenes.forEach(sc => {
    if(mediaMode === 'video'){
      if(sc.video_prompt) videoPromptById[sc.id] = sc.video_prompt;
      if(sc.video_url) videoUrlById[sc.id] = sc.video_url;
    } else {
      if(sc.prompt) promptById[sc.id] = sc.prompt;
      if(sc.image_url) imageUrlById[sc.id] = sc.image_url;
    }
  });
  const characters = parseCharactersInput(document.getElementById('charactersInput').value);
  // 캐릭터 탭 분류는 항상 이미지 프롬프트(sc.prompt) 텍스트 기준이다 — 캐릭터 묘사가
  // 원래 이미지 프롬프트 쪽에 있으므로, 영상 탭에서도 "어떤 캐릭터가 나오는 씬인지"는
  // 같은 기준으로 분류해야 두 탭의 캐릭터 탭 구성이 어긋나지 않는다.
  renderFilterTabs(modeScenes, characters);
  const visible = modeScenes.filter(sc => sceneMatchesFilter(sc.prompt, characters));
  if(mediaMode === 'video' && modeScenes.length === 0){
    list.innerHTML = '<div style="padding:12px;color:#777;font-size:12px">이 콘텐츠엔 영상이 필요한 장면([영상클립 필요])이 없습니다.</div>';
    return;
  }
  if(galleryMode){ renderGallery(visible); return; }
  list.innerHTML = visible.map(sc => {
    const checked = checkedIds === null || checkedIds.has(sc.id);
    const isOpen = expandedIds.has(sc.id);
    const full = (mediaMode === 'video' ? videoPromptById[sc.id] : promptById[sc.id]) || '';
    const mediaUrl = mediaMode === 'video' ? videoUrlById[sc.id] : imageUrlById[sc.id];
    const statusLabel = mediaMode === 'video' ? sc.video_status : sc.status;
    const mediaTag = mediaUrl
      ? (mediaMode === 'video'
          ? `<video src="${mediaUrl}" controls style="max-width:100%;border-radius:8px;border:1px solid #333;display:block;margin-bottom:4px"></video>`
          : `<img src="${mediaUrl}" style="max-width:100%;border-radius:8px;border:1px solid #333;display:block;margin-bottom:4px">`)
      : '';
    const deleteFn = mediaMode === 'video' ? 'deleteSceneVideo' : 'deleteSceneImage';
    const doneBlock = mediaUrl
      ? `<div style="margin:6px 0">${mediaTag}`
        + `<div style="display:flex;gap:6px;align-items:center">`
        + `<button onclick="navigator.clipboard.writeText('${mediaUrl}')">🔗 링크 복사</button>`
        + `<button onclick="${deleteFn}('${sc.id}')" style="background:#733">🗑 ${mediaMode === 'video' ? '영상' : '이미지'} 삭제</button>`
        + `<span style="color:#6a6;font-size:11px">✓ 홍허브 저장완료</span></div>`
        + `<input type="text" readonly value="${mediaUrl}" onclick="this.select()" style="width:100%;margin-top:4px;background:#111;color:#9ad;border:1px solid #333;border-radius:4px;padding:4px 6px;font-size:10px">`
        + `</div>`
      : '';
    const manualPlaceholder = mediaMode === 'video'
      ? 'Flow에서 완성됐는데 등록이 안 됐으면 영상 URL을 여기 붙여넣기'
      : 'Flow에서 완성됐는데 등록이 안 됐으면 이미지 URL을 여기 붙여넣기';
    const manualSetFn = mediaMode === 'video' ? 'setSceneVideoUrl' : 'setSceneImageUrl';
    return `<div class="scene-row" onclick="toggleExpand('${sc.id}')" style="cursor:pointer">`
      + `<input type="checkbox" class="scene-chk" data-id="${sc.id}" ${checked?'checked':''} onclick="event.stopPropagation()" onchange="onCheckChange()">`
      + `<span class="scene-id">${sc.id}</span><span class="scene-title">${sc.title}</span><span class="badge ${statusLabel}">${statusLabel}</span></div>`
      + (isOpen ? `<div class="scene-detail" onclick="event.stopPropagation()">`
          + `<div style="white-space:pre-wrap;color:#ccc;font-size:11px;margin:4px 0">${full.replace(/</g,'&lt;')}</div>`
          + doneBlock
          + `<button onclick="copyPrompt('${sc.id}', this)">📋 복사</button>`
          + `<button onclick="startOne('${sc.id}')" style="margin-left:6px;background:#275">▶ 생성</button>`
          + `<div style="display:flex;gap:4px;margin-top:6px">`
          + `<input type="text" id="manualUrl_${sc.id}" placeholder="${manualPlaceholder}" style="flex:1;background:#111;color:#9ad;border:1px solid #333;border-radius:4px;padding:4px 6px;font-size:10px">`
          + `<button onclick="${manualSetFn}('${sc.id}')" style="background:#358">🔗 링크로 등록</button></div></div>` : '');
  }).join('');
}
function toggleExpand(id){
  if(expandedIds.has(id)) expandedIds.delete(id); else expandedIds.add(id);
  renderScenes(previewScenes.length ? previewScenes : []);
}
// 2026-09-12 추가 — 사용자 지적: "복사도 안됨". 크롬 사이드패널 iframe 안에서는
// navigator.clipboard.writeText()가 권한 정책(Permissions Policy)에 막혀 조용히
// 실패할 수 있다(panel.html의 iframe에 allow="clipboard-write"를 추가했지만, 확장
// 프로그램은 파일을 고쳐도 재로드 전까진 반영 안 됨). 그 API가 막혀 있어도 동작하도록
// 구식 document.execCommand('copy') 방식을 대체 경로로 같이 둔다.
function legacyCopy(text){
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.left = '-9999px';
  document.body.appendChild(ta);
  ta.focus(); ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch(e) {}
  document.body.removeChild(ta);
  return ok;
}
// 2026-09-12 추가 — 사용자 지적: "복사는 되는데 복사완료 같은 표시가 안되서 복사가
// 안되는지 알았나봐". 실제로는 항상 복사가 되고 있었는데 성공 피드백이 전혀 없어서
// 안 되는 줄 알았던 것 — 버튼 자체에 잠깐 "✅ 복사됨" 표시를 띄운다.
function flashCopied(btn){
  if(!btn) return;
  const orig = btn.textContent;
  btn.textContent = '✅ 복사됨';
  btn.disabled = true;
  setTimeout(()=>{ btn.textContent = orig; btn.disabled = false; }, 1200);
}
function copyPrompt(id, btn){
  const text = (mediaMode === 'video' ? videoPromptById[id] : promptById[id]) || '';
  const done = () => flashCopied(btn);
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(() => { legacyCopy(text); done(); });
  } else {
    legacyCopy(text);
    done();
  }
}
function onCheckChange(){
  const boxes = document.querySelectorAll('.scene-chk');
  checkedIds = new Set([...boxes].filter(b=>b.checked).map(b=>b.dataset.id));
  updateSelectedCount();
}
function selectAll(on){
  checkedIds = on ? null : new Set();
  renderScenes((previewScenes||[]).length ? previewScenes : []);
  updateSelectedCount();
}
// 2026-09-10 추가 — "생성 다 되면 신호 안 와?" 요청으로, 배치 전체가 끝나면(phase가
// all_done/stopped로 바뀌는 순간) 알림+소리+배너로 알려준다. 매 tick마다 반복 알림이 뜨지
// 않게 phase가 실제로 "막 끝난" 전이일 때만 한 번 울린다.
let lastPhaseByPort = {};
try { Notification.requestPermission(); } catch(e) {}
function beep(){
  try{
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const o = ctx.createOscillator(); const g = ctx.createGain();
    o.connect(g); g.connect(ctx.destination);
    o.frequency.value = 880; g.gain.value = 0.2;
    o.start(); o.stop(ctx.currentTime + 0.3);
  }catch(e){}
}
// 2026-09-12 실사고 수정 — 사용자 지적: "9226에서 아무것도 생성이 안되었는데 이렇게 표시가 되".
// 드라이버(flow_econ_driver.py)는 배치의 모든 씬이 실패해도(예: 크롬 응답 없음 등으로 전부
// 건너뜀) 루프가 끝나면 그냥 phase="all_done"을 쓴다 — "끝났다"이지 "성공했다"가 아닌데,
// 여기서는 phase만 보고 무조건 "✅ 배치 완료!"라고 축하하는 알림을 띄웠다. 실제로 등록된 게
// 하나도 없어도(failures만 쌓였어도) 성공 알림이 뜨는 오탐이었다 — failures 유무로 문구를
// 나눈다.
function notifyIfFinished(port, phase, failures, updatedAt){
  const finished = phase === 'all_done' || phase === 'stopped';
  const last = lastPhaseByPort[port];
  if(finished && last !== phase && last !== undefined){
    // 2026-09-12 (8차) 수정 — 사용자 지적: "이것도 왜 이렇게 많이 뜨는거야???". 이 대시보드를
    // 여러 창/탭(사이드패널 + 메인 컨트롤의 계정별 임베드 등)으로 동시에 열어두면 각 탭이
    // 독립적으로 "끝났다"를 감지해서 각자 알림을 띄우는 바람에 같은 완료 하나가 여러 번
    // 겹쳐 보였다. localStorage(같은 출처의 모든 탭이 공유)에 "이 포트에서 마지막으로 알림을
    // 보낸 updated_at"을 기록해서, 이미 알림을 보낸 완료 건이면 다른 탭이어도 다시 안 띄운다.
    // Notification에 tag도 같이 줘서, 그래도 겹치면 새로 쌓이는 대신 이전 것을 교체한다.
    const dedupKey = 'flowGenNotified_' + port;
    let alreadyNotified = false;
    try { alreadyNotified = localStorage.getItem(dedupKey) === String(updatedAt); } catch(e) {}
    if(!alreadyNotified){
      const failCount = (failures || []).length;
      const ok = failCount === 0;
      beep();
      // 2026-09-12 (9차) 수정 — 사용자 요청: "저 메세지 안나오게 해줘" (OS/브라우저 알림 토스트
      // 팝업 자체를 원치 않음). new Notification()을 완전히 제거하고, 아래 페이지 내 배너와
      // beep()만 남긴다 — 화면 밖으로 튀어나오는 팝업 없이 이 대시보드를 보고 있을 때만 표시.
      const banner = document.createElement('div');
      banner.textContent = ok ? `✅ 포트 ${port} 배치 완료!` : `⚠️ 포트 ${port} 배치 종료 — ${failCount}개 씬 실패(등록 안 됨)`;
      banner.style.cssText = `background:${ok ? '#175' : '#733'};color:#fff;padding:8px;border-radius:6px;text-align:center;font-weight:700;margin-bottom:8px`;
      document.body.insertBefore(banner, document.body.firstChild);
      setTimeout(()=>banner.remove(), 6000);
      try { localStorage.setItem(dedupKey, String(updatedAt)); } catch(e) {}
    }
  }
  lastPhaseByPort[port] = phase;
}
function updateSelectedCount(){
  // 2026-09-10 수정 — 체크된 개수만 보여줘서 "진행 개수"에 5를 넣었는데 105개로 나온다는
  // 지적이 있었다. "선택됨"은 실제로 이번에 진행될 개수를 보여줘야 헷갈리지 않는다.
  // 번호 범위(예: 5~10)가 채워져 있으면 그게 최우선이다.
  const r = rangeIds();
  if(r !== null){
    document.getElementById('selectedCount').textContent = r.length + '개';
    return;
  }
  const n = checkedIds === null ? activeScenes().length : checkedIds.size;
  document.getElementById('selectedCount').textContent = n + '개';
  // 2026-09-12 추가 — 사용자 요청: "선택된것 숫자, 남은것 숫자, 현재 작업중인 번호"를 한눈에.
  // "남은"은 선택 여부와 무관하게 전체 씬 중 아직 이미지가 없는(pending) 개수 — 작업중인
  // 번호는 이미 아래 워커 박스(예: "9223 S81 99%")에 표시되고 있어 여기선 안 겹치게 둔다.
  // 2026-09-13 수정 — 영상 탭에서는 needs_video 씬 + video_status 기준으로 센다.
  const remaining = activeScenes().filter(sc => !isDoneInMode(sc)).length;
  document.getElementById('remainingCount').textContent = remaining + '개';
}
function selectedIds(){
  if(checkedIds === null) return activeScenes().map(s=>s.id);
  return [...checkedIds];
}
function runOptions(){
  return {
    ratio: document.getElementById('ratioSel').value,
    new_project: document.getElementById('newProjectChk').checked,
    port: parseInt(document.getElementById('portSel').value, 10),
    characters: parseCharactersInput(document.getElementById('charactersInput').value),
    image_count: parseInt(document.getElementById('imageCountSel').value, 10),
    project_url: document.getElementById('projectSel').value || null,
    agent_off: document.getElementById('agentOffChk').checked,
    // 2026-09-13 추가 — 사용자 요청: 영상 탭의 "생성" 버튼도 이미지와 동일하게 실제
    // Flow 자동화(flow_econ_driver.py의 clips 스테이지)로 연결한다.
    mode: mediaMode,
    // 2026-09-13 (5차) 추가 — videoSettingsBlock에서 고른 값을 그대로 실어 보낸다
    // (mode==='image'일 땐 서버가 무시하지만, 값 자체는 항상 같이 보내도 무해하다).
    video_ratio: document.getElementById('videoRatioSel').value,
    video_model: document.getElementById('videoModelSel').value,
    video_resolution: document.getElementById('videoResolutionSel').value,
    video_duration: document.getElementById('videoDurationSel').value,
    video_count: parseInt(document.getElementById('videoCountSel').value, 10),
  };
}
async function startRun(){
  const siteId = document.getElementById('siteSel').value;
  const unitId = document.getElementById('unitSel').value;
  if(!siteId || !unitId){ alert('워크플로우와 콘텐츠를 먼저 선택하세요.'); return; }
  // 2026-09-10 추가 — "번호 범위"(예: 5~10)가 채워져 있으면 최우선으로 그 범위만 진행한다.
  // 없으면 체크된 씬 전부.
  let ids = rangeIds();
  if(ids === null){ ids = selectedIds(); }
  const r = await fetch('/api/start', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({site_id: siteId, unit_id: unitId, scene_ids: ids, ...runOptions()})});
  const d = await r.json();
  if(d.error) alert(d.error);
}
// 2026-09-11 추가 — 사용자 지적: "그런 오류를 표시를 해줘야 다시 시작을 하던 할꺈 아니야" /
// "패널에 띄어주고 다시 어떻게 하라고 해줘야 하자나 그래야 확인을 누르고 다시 진행을 하지".
// 실패는 phase="failed"로만 기록되고 다음 씬이 시작되면 바로 덮어써져서, 배치가 다 끝나도
// 뭐가 왜 실패했는지 화면에서 전혀 안 보였다(run.log 파일을 직접 열어야만 알 수 있었음).
// 드라이버(flow_econ_driver.py)가 이제 실패를 failures 배열에 누적해서 상태 파일에 실어
// 보내주므로, 여기서는 그걸 배너로 보여주고 "재시작" 버튼 하나로 실패한 씬만 다시 큐에
// 넣을 수 있게 한다 — 사람이 원인 읽고 → 확인 누르고 → 그대로 이어서 진행하는 흐름.
async function retryFailed(port, ids){
  const siteId = document.getElementById('siteSel').value;
  const unitId = document.getElementById('unitSel').value;
  if(!siteId || !unitId){ alert('워크플로우와 콘텐츠를 먼저 선택하세요.'); return; }
  // 2026-09-12 수정 — 사용자 지적: "계정 포트를 사용자가 바꾸지 않는 한 고정되어야 한다".
  // 예전엔 실패 배너의 "재시작"을 누르면 그 실패가 난 포트로 드롭다운을 몰래 바꿔버려서,
  // 사용자가 고른 계정이 자기도 모르게 다른 계정으로 바뀌어 있었다(요청 자체는 아래처럼
  // port를 명시로 넘기므로 드롭다운을 바꾸지 않아도 정확한 계정으로 재시작된다).
  const r = await fetch('/api/start', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({site_id: siteId, unit_id: unitId, scene_ids: ids, ...runOptions(), port})});
  const d = await r.json();
  if(d.error) alert(d.error);
  document.getElementById('failBanner').style.display = 'none';
}
// 2026-09-11 추가 — 사용자 지적: "이 씬만 재시작이 아니라 확인을 클릭해서 내가 팝업을
// 닫게 해주면 되는거야~ X박스를 해주거나". 재시작 없이 그냥 "읽었다"고 닫기만 하는 버튼 —
// 같은 실패 목록(포트+씬 id 조합)이 유지되는 동안은 계속 숨겨두고, 새 실패가 생기면
// (목록/사유가 달라지면) 다시 뜬다(위 fetchAndRender의 withFailures 필터 참고).
// 2026-09-11 (2차) 수정 — 사용자 지적: "X누르고 새로고침을 하면 또 나타나는데?". 그냥
// JS 변수(let)에만 담아뒀더니 페이지를 새로고침하는 순간 초기화돼서 dismiss가 없었던
// 것처럼 다시 떴다 — localStorage에 저장해서 새로고침·패널 껐다 켬을 버텨내게 한다.
let dismissedFailureSig = {};
try { dismissedFailureSig = JSON.parse(localStorage.getItem('dismissedFailureSig') || '{}'); } catch(e) {}
function dismissFailure(port, sig){
  dismissedFailureSig[port] = sig;
  try { localStorage.setItem('dismissedFailureSig', JSON.stringify(dismissedFailureSig)); } catch(e) {}
  document.getElementById('failBanner').style.display = 'none';
}
async function startOne(sceneId){
  const siteId = document.getElementById('siteSel').value;
  const unitId = document.getElementById('unitSel').value;
  if(!siteId || !unitId){ alert('워크플로우와 콘텐츠를 먼저 선택하세요.'); return; }
  const r = await fetch('/api/start', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({site_id: siteId, unit_id: unitId, scene_ids: [sceneId], ...runOptions()})});
  const d = await r.json();
  if(d.error) alert(d.error);
}
// 2026-09-11 추가 — 사용자 지적: "1번이 아까 생성이 되었는데 기록이 안 되었나봐 → 이럴 때
// 수동으로 링크를 입력할 수 있었으면 하는데". 자동 등록(register_scene_image)이 타임아웃 등으로
// 실패해도 Flow 프로젝트 안엔 이미지가 실제로 만들어져 있는 경우가 있다 — 그 URL을 직접
// 붙여넣으면 다른 씬과 동일하게 scenePrompts에 등록되고 화면에도 바로 반영된다.
async function setSceneImageUrl(sceneId){
  const siteId = document.getElementById('siteSel').value;
  const unitId = document.getElementById('unitSel').value;
  if(!siteId || !unitId){ alert('워크플로우와 콘텐츠를 먼저 선택하세요.'); return; }
  const input = document.getElementById('manualUrl_' + sceneId);
  const url = (input.value || '').trim();
  if(!url){ alert('이미지 URL을 입력하세요.'); return; }
  const r = await fetch('/api/set_scene_image', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({site_id: siteId, unit_id: unitId, scene_id: sceneId, image_url: url})});
  const d = await r.json();
  if(d.error){ alert(d.error); return; }
  // 붙여넣은 원본 url이 아니라 서버가 실제로 재업로드해서 등록한 image_url을 써야 한다 —
  // 원본(공유 페이지 등)을 그대로 쓰면 새로고침 전까지 화면에 깨진 이미지가 보인다.
  imageUrlById[sceneId] = d.image_url || url;
  await onUnitChange();
}
// 2026-09-11 추가 — 사용자 지적: "이미지랑 프롬프트가 안 맞는거 같아" (S03/S04, S09/S12,
// S10/S11, S13/S14 실사고 — 완전히 동일한 파일이 서로 다른 씬 번호로 등록됨). 잘못 등록된
// 씬을 발견했을 때 프롬프트는 그대로 두고 이미지만 지워서 재생성할 수 있어야 한다는 요청으로
// 추가. 홍허브 scenePrompts의 "- 장면이미지:" 줄과, 이 씬을 생성했던 모든 계정(포트)의 로컬
// 상태(_flow_state.json done 기록)·저장된 파일까지 한 번에 지운다.
async function deleteSceneImage(sceneId){
  const siteId = document.getElementById('siteSel').value;
  const unitId = document.getElementById('unitSel').value;
  if(!siteId || !unitId){ alert('워크플로우와 콘텐츠를 먼저 선택하세요.'); return; }
  if(!confirm(sceneId + ' 이미지를 삭제할까요? (프롬프트는 그대로 남고, 이미지만 지워져서 다시 생성할 수 있게 됩니다)')) return;
  const r = await fetch('/api/delete_scene_image', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({site_id: siteId, unit_id: unitId, scene_id: sceneId})});
  const d = await r.json();
  if(d.error){ alert(d.error); return; }
  delete imageUrlById[sceneId];
  await onUnitChange();
}
// 2026-09-13 추가 — 사용자 요청: "탭으로 이미지/영상 구분해주고 ... 이미지쪽과 동일하게
// 만들어주면돼". setSceneImageUrl/deleteSceneImage와 완전히 같은 구조를 영상에 그대로
// 미러링한다.
async function setSceneVideoUrl(sceneId){
  const siteId = document.getElementById('siteSel').value;
  const unitId = document.getElementById('unitSel').value;
  if(!siteId || !unitId){ alert('워크플로우와 콘텐츠를 먼저 선택하세요.'); return; }
  const input = document.getElementById('manualUrl_' + sceneId);
  const url = (input.value || '').trim();
  if(!url){ alert('영상 URL을 입력하세요.'); return; }
  const r = await fetch('/api/set_scene_video', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({site_id: siteId, unit_id: unitId, scene_id: sceneId, video_url: url})});
  const d = await r.json();
  if(d.error){ alert(d.error); return; }
  videoUrlById[sceneId] = d.video_url || url;
  await onUnitChange();
}
async function deleteSceneVideo(sceneId){
  const siteId = document.getElementById('siteSel').value;
  const unitId = document.getElementById('unitSel').value;
  if(!siteId || !unitId){ alert('워크플로우와 콘텐츠를 먼저 선택하세요.'); return; }
  if(!confirm(sceneId + ' 영상을 삭제할까요? (프롬프트는 그대로 남고, 영상만 지워져서 다시 생성할 수 있게 됩니다)')) return;
  const r = await fetch('/api/delete_scene_video', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({site_id: siteId, unit_id: unitId, scene_id: sceneId})});
  const d = await r.json();
  if(d.error){ alert(d.error); return; }
  delete videoUrlById[sceneId];
  await onUnitChange();
}
async function stopRun(){
  const port = parseInt(document.getElementById('portSel').value, 10);
  const resultSpan = document.getElementById('stopResult');
  const r = await fetch('/api/stop', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({port})});
  const d = await r.json();
  // 2026-09-12 추가 — 사용자 요청: 멈추기를 눌러도 즉시 멈추는 게 아니라 진행 중인 씬을
  // 마저 끝낸 뒤에 멈추는 구조라, 눌렀을 때 그 사실을 바로 알려준다("눌렀는데 왜 안 멈춰?"
  // 오해 방지).
  if(d.error){ resultSpan.style.color = '#6a6'; resultSpan.textContent = '✅ 작업 중인 게 없어서 바로 멈췄습니다.'; }
  else { resultSpan.style.color = '#fb5'; resultSpan.textContent = '⏳ 작업 중인 씬이 있으면 완료 후 멈춥니다.'; }
}
// 2026-09-10 추가 — "계정마다 달라~ 2초마다 가져온다면서?" 지적: 씬 목록 fetch가 실패해도
// catch(e){}로 조용히 삼켜서 화면이 낡은 채로 멈춰 있어도 알 방법이 없었다. 실패하면 lastSync에
// 바로 보이게 표시하고, "새로고침" 버튼으로 2초를 안 기다리고 즉시 다시 가져올 수 있게 한다.
// 2026-09-11 추가 — 사용자 지적: "13단계에서 이미지 업로드 다운받고 하는게 용량을 많이
// 잡아먹는게 아닌가". 확인해보니 실제 원인은 이미지가 아니라, 이 함수가 2초마다 씬 하나 상태만
// 바뀌어도 `script_draft` 전체(경제학 사이트 기준 약 82KB)를 슈퍼베이스에서 통째로 다시 읽어오고
// 있었다 — 패널을 몇 시간 켜두면 그것만으로 수백 MB~GB가 쌓이는 구조. `/api/status`(로컬 파일
// 읽기라 거의 공짜)는 그대로 2초마다 돌리되, 비싼 `/api/scenes`(DB 조회)는 평소엔 10초에 한 번만
// 돌리고, 방금 씬 하나가 완료돼서 목록을 당장 갱신해야 할 때만 예외적으로 바로 돌린다.
let scenesTickCounter = 0;
const SCENES_POLL_EVERY_N_TICKS = 5; // 2초 * 5 = 10초
async function fetchAndRender(){
  try{
    const r = await fetch('/api/status?_=' + Date.now());
    const s = await r.json();
    const workers = s.workers || [];
    // 2026-09-10 재설계 — 포트(계정) 여러 개가 동시에 돌 수 있어서, 한 줄이 아니라
    // 활성 워커별로 한 줄씩("9223: S05 waiting 20초") 보여준다.
    const wdiv = document.getElementById('workers');
    // 2026-09-12 추가 — 사용자 지적: "왜 9226에 9223의 작업내용이 보이냐고, 각자 자기꺼만
    // 보여야지". 예전엔 4계정 워커를 전부 한꺼번에 보여줘서, 지금 고른 계정과 무관한 다른
    // 계정의 상태(예: 멈춰있는 9223)까지 같이 떠서 "지금 이 계정이 이걸 하고 있나?" 하고
    // 헷갈리게 만들었다 — 현재 드롭다운에서 고른 계정 하나만 보여준다.
    const selectedPort = parseInt(document.getElementById('portSel').value, 10);
    const myWorkers = workers.filter(w => w.port === selectedPort);
    // 2026-09-11 (2차) 수정 — 사용자 요청: "시작을 그냥 플로우에서 보여주는 %를 그대로
    // 보여줘". 자체 경과시간 타이머(elapsed) 대신 Flow 화면이 실제로 표시하는 퍼센트
    // (gen_percent)를 그대로 보여준다 — 로딩 표시가 아직 안 떴으면(gen_started===false)
    // "시작 대기"로 표시해 한눈에 구분되게 한다(로그를 따로 열어볼 필요 없음).
    wdiv.innerHTML = myWorkers.map(w => {
      const running = w.phase === 'submitting' || w.phase === 'waiting';
      let badge = '';
      if (running) {
        if (w.gen_percent) badge = `<span class="badge waiting" style="font-size:10px">${w.gen_percent}</span>`;
        else if (w.gen_started === false) badge = `<span class="badge waiting" style="font-size:10px;background:#733">시작 대기 ${w.elapsed||0}초</span>`;
        else badge = `<span class="badge waiting" style="font-size:10px">제출 중</span>`;
      }
      return `<div class="row"><span style="color:#888;font-size:11px">${w.port}</span>`
        + `<b>${running ? w.current : '-'}</b>`
        + badge
        + `</div>`;
    }).join('') || '<div class="row" style="color:#666;font-size:11px">실행 중인 계정 없음</div>';
    workers.forEach(w => notifyIfFinished(w.port, w.phase, w.failures, w.updated_at));
    // 2026-09-11 추가 — 실패 배너: 어느 포트가 뭘 왜 실패했는지 사유까지 그대로 보여주고,
    // 바로 그 자리에서 재시작 버튼을 눌러 이어갈 수 있게 한다(위 retryFailed 참고).
    // 2026-09-11 추가(2) — 사용자 지적: "이 씬만 재시작이 아니라 확인을 클릭해서 내가
    // 팝업을 닫게 해주면 되는거야~" / "X박스를 해주거나". 재시작 없이 그냥 읽었다는
    // 확인만 하고 닫을 수 있는 ✕ 버튼을 추가한다 — 같은 실패 목록(포트+씬 id 조합)이면
    // 계속 숨겨두고, 새로운 실패가 생기면(목록이 달라지면) 다시 뜬다.
    const failBanner = document.getElementById('failBanner');
    const withFailures = workers.filter(w => {
      if(!(w.failures || []).length) return false;
      const sig = w.failures.map(f => f.id).slice().sort().join(',');
      return dismissedFailureSig[w.port] !== sig;
    });
    if(withFailures.length){
      failBanner.style.display = 'block';
      failBanner.innerHTML = withFailures.map(w => {
        const ids = (w.failures || []).map(f => f.id);
        const sig = ids.slice().sort().join(',');
        const lines = (w.failures || []).map(f => `${f.id}: ${f.error}`).join('<br>');
        return `<div style="margin-bottom:6px;position:relative;padding-right:20px">`
          + `<button onclick="dismissFailure(${w.port}, '${sig}')" title="닫기" style="position:absolute;right:0;top:0;background:transparent;border:none;color:#daa;font-size:14px;font-weight:700;cursor:pointer;padding:2px 6px">✕</button>`
          + `<b style="color:#f88">⚠ ${w.port} 계정 — ${ids.length}개 씬 실패</b><br>`
          + `<div style="color:#daa;margin:4px 0">${lines}</div>`
          + `<button onclick='retryFailed(${w.port}, ${JSON.stringify(ids)})' style="background:#733;font-weight:700">🔁 이 ${ids.length}개 씬만 재시작</button></div>`;
      }).join('');
    } else {
      failBanner.style.display = 'none';
    }
    // 2026-09-10 추가 — "지금 진행 중인 것만 프롬프트를 펼치고, 완료되면 접고, 다음
    // 진행 중인 걸로 넘어가면서 순차적으로 펼쳐지고 접히게" — 여러 워커가 각자 다른 씬을
    // 처리 중이면 그 씬들을 전부(동시에) 펼친다.
    const runningIds = new Set(workers.filter(w => w.phase === 'submitting' || w.phase === 'waiting')
      .map(w => w.current).filter(Boolean));
    if(runningIds.size){ expandedIds = runningIds; }
    // 2026-09-10 — 씬 목록(done/pending)은 한 워커의 로컬 상태가 아니라 항상 DB에서 새로
    // 읽는다 — 어느 포트가 어느 씬을 끝냈든 정확하게 반영되게(다중 계정 집계 문제 회피).
    const siteId = document.getElementById('siteSel').value;
    const unitId = document.getElementById('unitSel').value;
    const justFinishedAScene = workers.some(w => w.phase === 'done');
    scenesTickCounter++;
    const shouldPollScenes = justFinishedAScene || (scenesTickCounter % SCENES_POLL_EVERY_N_TICKS === 0);
    if(siteId && unitId && shouldPollScenes){
      const sr = await fetch('/api/scenes?site_id=' + encodeURIComponent(siteId) + '&unit_id=' + encodeURIComponent(unitId));
      const fresh = await sr.json();
      fresh.forEach(sc => { if(runningIds.has(sc.id)) sc.status = 'running'; });
      previewScenes = fresh;
      // 완료된 씬은 계속 체크에서 빼준다("완료된 거 체크 해제가 안 되고 있어").
      if(checkedIds !== null){
        fresh.filter(sc => sc.status === 'done').forEach(sc => checkedIds.delete(sc.id));
      }
      updateSelectedCount();
      renderScenes(previewScenes);
    } else if(siteId && unitId && runningIds.size){
      // DB는 다시 안 읽어도, 지금 진행 중인 씬 표시(running 배지)만 캐시된 목록 위에 갱신한다.
      previewScenes.forEach(sc => { if(runningIds.has(sc.id) && sc.status !== 'done') sc.status = 'running'; });
      renderScenes(previewScenes);
    }
    document.getElementById('lastSync').textContent = '동기화 ' + new Date().toLocaleTimeString('ko-KR');
    document.getElementById('lastSync').style.color = '#666';
  }catch(e){
    document.getElementById('lastSync').textContent = '⚠ 동기화 실패: ' + e;
    document.getElementById('lastSync').style.color = '#c66';
  }
}
async function refreshNow(){
  const btn = document.getElementById('refreshBtn');
  btn.disabled = true; btn.textContent = '🔄 갱신 중...';
  // 2026-09-11 추가 — 사용자 실사고: 서버가 재시작되는 타이밍에 페이지가 열리면 loadSites()가
  // 한 번 실패한 채로 "불러오는 중..."에 영원히 멈춰있었다 — 이 함수가 그동안 워크플로우 목록은
  // 다시 안 불러오고 상태/씬 목록만 갱신해서, "새로고침"을 눌러도 안 고쳐졌다("아직
  // 못불러오고 있는데~"). 워크플로우 드롭다운이 비어있으면(첫 로딩 실패 신호) 목록부터 다시
  // 불러오고, 있으면 그대로 둔다(현재 선택값 유지).
  const siteSel = document.getElementById('siteSel');
  if (!siteSel.options.length || siteSel.options[0].value === '' && siteSel.options.length === 1 && siteSel.options[0].textContent.includes('불러오는')) {
    const keepSite = siteSel.value, keepUnit = document.getElementById('unitSel').value;
    await loadSites();
    if (keepSite) { siteSel.value = keepSite; await onSiteChange(keepUnit); }
  }
  await fetchAndRender();
  btn.disabled = false; btn.textContent = '🔄 새로고침';
}
async function tick(){
  await fetchAndRender();
  setTimeout(tick, 2000);
}
async function restoreAndBoot(){
  await loadSites();
  const saved = loadUiState();
  if(saved.siteId){
    document.getElementById('siteSel').value = saved.siteId;
    await onSiteChange(saved.unitId);
  }
  if(saved.ratio) document.getElementById('ratioSel').value = saved.ratio;
  if(saved.newProject) document.getElementById('newProjectChk').checked = true;
  if(saved.agentOff === false) document.getElementById('agentOffChk').checked = false;
  if(saved.unifyAll && document.getElementById('unifyAllChk')) document.getElementById('unifyAllChk').checked = true;
  if(saved.range) document.getElementById('rangeInput').value = saved.range;
  // 2026-09-12 수정 — 계정(포트)은 이 서버가 이미 HTML에 정확한 <option selected>를 구워서
  // 내려주므로 여기서 다시 손대지 않는다(브라우저별 localStorage로 덮어쓰면 크롬창마다 다른
  // 계정으로 보이는 문제가 재발한다) — 서버 상태만 최종 확인차 한 번 더 반영한다.
  // 2026-09-12 추가 — ?view=922X로 열린 임베드(메인 컨트롤 페이지의 계정별 iframe)는 전역
  // 선택값을 절대 따라가면 안 된다 — 그러면 4개 iframe이 서로 자기 계정으로 계속 덮어써서
  // 전부 마지막 것 하나로 수렴해버린다. view 쿼리스트링이 있으면 서버가 이미 그 값으로
  // selected를 구워 보냈으니 여기서는 손대지 않고 그대로 둔다.
  const isViewEmbed = new URLSearchParams(location.search).has('view');
  if(!isViewEmbed){
    try {
      const serverState = await (await fetch('/api/ui_state')).json();
      if(serverState.port) document.getElementById('portSel').value = serverState.port;
      showSavedPort(serverState.port || document.getElementById('portSel').value);
    } catch(e) {}
  } else {
    // 읽기 전용 임베드 — 이 계정 선택이 실수로라도 전역 상태를 바꾸지 못하게 막는다.
    document.getElementById('portSel').disabled = true;
  }
  if(saved.range9223) document.getElementById('range9223').value = saved.range9223;
  if(saved.range9224) document.getElementById('range9224').value = saved.range9224;
  if(saved.range9225) document.getElementById('range9225').value = saved.range9225;
  if(saved.range9226) document.getElementById('range9226').value = saved.range9226;
  if(saved.charactersText) document.getElementById('charactersInput').value = saved.charactersText;
  // 2026-09-12 수정 — 프로젝트 목록·선택값은 이제 계정(포트)별로 캐시돼 있으므로, 현재
  // 포트 기준으로 복원한다(applyProjectListForPort) — 서버에 다시 안 물어봄.
  applyProjectListForPort(parseInt(document.getElementById('portSel').value, 10));
  // 2026-09-11 추가 — 기본값 켜짐(1장만 생성)이므로, 저장된 값이 명시적으로 false일 때만 끈다.
  document.getElementById('imageCountSel').value = saved.imageCount || 1;
  // 2026-09-13 (5차) 추가 — 영상 탭 설정 복원(저장된 값 없으면 위 <select> 기본값 그대로 둠).
  if(saved.videoRatio) document.getElementById('videoRatioSel').value = saved.videoRatio;
  if(saved.videoModel) document.getElementById('videoModelSel').value = saved.videoModel;
  if(saved.videoResolution) document.getElementById('videoResolutionSel').value = saved.videoResolution;
  if(saved.videoDuration) document.getElementById('videoDurationSel').value = saved.videoDuration;
  if(saved.videoCount) document.getElementById('videoCountSel').value = saved.videoCount;
  updateSelectedCount();
  // 2026-09-12 추가 — 바탕화면 "메인 컨트롤 열기" 아이콘이 ?all=1로 열면 전체 계정 보기를
  // 자동으로 켠 상태로 시작한다.
  if (new URLSearchParams(location.search).get('all') === '1') {
    document.getElementById('showAllWorkersChk').checked = true;
  }
  // 2026-09-12 추가 — 사용자 요청: "메인 컨트롤화면에서 여러계정 동시시작 기능은 각 계정에서는
  // 빼고 상단에 통합 1번만 있기로 했는데". "⚡ 여러 계정 동시 시작"은 애초에 포트 4개 전부에
  // /api/start를 순서대로 쏘는 전역 기능이라, 계정별 임베드(?view=922X)마다 하나씩 4번 반복
  // 보일 이유가 없었다(메인 컨트롤 화면에 4개 나란히 뜸). 코드/설정 값을 그대로 재사용해서
  // 새로 만들지 않고 이 셀렉터를 두 가지 모드로 나눈다:
  //   - ?view=922X(계정별 임베드): 이 섹션만 숨긴다 — 나머지(개별 "생성" 등)는 그대로.
  //   - ?panel=multistart(메인 컨트롤 상단에 새로 추가한 통합 1개용): 이 섹션과 그 위의 설정
  //     패널(워크플로우/콘텐츠 선택·비율·캐릭터 등 — 여기 값들을 그대로 읽어서 /api/start를
  //     호출하므로 필요)만 남기고, 그 아래(번호 범위/개별 생성/씬 목록 등)는 전부 숨긴다.
  const multiStartEl = document.getElementById('multiStartDetails');
  // 2026-09-13 추가 — 사용자 지적: "각 계정창에 [여러 계정 동시 시작] 부분은 삭제되어야 해,
  // 메인에서 통제하니까". 크롬 확장 사이드패널(status_extension/panel.html)은 ?view=922X 같은
  // 쿼리스트링 없이 그냥 http://127.0.0.1:8799/ 를 iframe으로 통째로 띄우기만 한다 — 그래서
  // isViewEmbed 조건에 안 걸려 계정별 사이드패널 4개 전부에 이 전역 기능이 그대로 노출됐다.
  // "쿼리스트링 유무"가 아니라 "iframe 안에서 열렸는지"(window.self !== window.top)로 판별
  // 기준을 넓힌다 — 단 ?panel=multistart(의도적으로 iframe 안에서 이 기능만 보여주는 통합
  // 패널)는 이 규칙에서 제외해야 하므로 먼저 배제한다.
  const isMultistartPanel = new URLSearchParams(location.search).get('panel') === 'multistart';
  const isEmbeddedElsewhere = !isMultistartPanel && window.self !== window.top;
  if(isViewEmbed || isEmbeddedElsewhere){
    if(multiStartEl) multiStartEl.style.display = 'none';
  } else if(isMultistartPanel){
    // 사용자 지적: "이부분 삭제 해야 할꺼 같아" — 계정(포트)/새 프로젝트로 시작/Flow 프로젝트
    // 선택은 "이 계정 하나" 설정이라 4계정 동시 시작 패널에는 안 어울린다(startAllAccounts/
    // autoDispatch도 이 값들을 안 읽음) — 통합 패널에서만 숨긴다.
    const accountProjectBlock = document.getElementById('accountProjectBlock');
    if(accountProjectBlock) accountProjectBlock.style.display = 'none';
    // 사용자 요청: "제일상단에 통일적용 체크박스 추가". 통합 패널에서만 보여준다.
    const unifyAllRow = document.getElementById('unifyAllRow');
    if(unifyAllRow) unifyAllRow.style.display = '';
    if(multiStartEl){
      // 2026-09-12 수정 — 사용자 지적(강한 어조로): "통채로 접으라니까 아래는 왜 따로
      // 접어노는거야" — 메인 컨트롤(CONTROL_HTML) 레벨에 이미 이 박스 전체를 감싸는 바깥쪽
      // <details>를 따로 둬서 "통째로 접기"를 거기서 처리하므로, 이 안쪽 하위 <details>까지
      // 또 따로 접혀있으면 펼친 뒤에도 한 번 더 펼쳐야 하는 이중 접힘이 된다 — 통합 패널
      // 안에서는 이 안쪽 것은 항상 펼쳐진 상태로 고정하고, 중복되는 자체 summary(펼치기
      // 화살표)도 숨긴다.
      multiStartEl.open = true;
      const innerSummary = multiStartEl.querySelector('summary');
      if(innerSummary) innerSummary.style.display = 'none';
      let sib = multiStartEl.nextElementSibling;
      while(sib){
        const next = sib.nextElementSibling;
        sib.style.display = 'none';
        sib = next;
      }
      // 사용자 요청: "2번 스샷은 추가해줘야 몇개 생성해야 하는지 알꺼 같아" — 캐릭터별
      // 전체/완료 개수 필터 탭(전체·젠틀맨루즈·스틱맨·같이출연·캐릭터없음, 각 "N개/완료개")을
      // 몇 개 만들지 가늠하는 용도로 다시 보여준다. 원래 위치(번호 범위보다 한참 아래, 씬 목록
      // 바로 위)는 방금 다 숨겼으니, 범위 입력 바로 위로 옮겨와 보여준다.
      const filterRow = document.getElementById('sceneFilterRow');
      if(filterRow){
        filterRow.style.display = '';
        multiStartEl.parentNode.insertBefore(filterRow, multiStartEl);
      }
    }
  }
  applyUnifyAll();
}
restoreAndBoot();
tick();
</script>
</body></html>"""


# 2026-09-12 추가 — 계정 하나를 골라 씬 생성을 지시하는 위 HTML(단일 계정 제어 화면)과는
# 별개의 허브 페이지. 상단엔 계정 4개의 크롬 켜기 버튼, 그 아래엔 4계정 화면을 나란히(iframe)
# 그대로 띄운다 — 씬 생성 자체는 그 아래 각 계정 화면 안에서 진행한다.
CONTROL_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>메인 컨트롤</title>
<style>
  body{background:#111;color:#eee;font-family:system-ui,sans-serif;margin:0;padding:16px}
  h1{font-size:16px;margin:0 0 4px}
  .sub{color:#888;font-size:11px;margin-bottom:16px}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px}
  .card{background:#1a1a1a;border:1px solid #333;border-radius:10px;padding:12px}
  .card h2{font-size:13px;margin:0 0 6px;display:flex;align-items:center;gap:6px}
  .dot{width:8px;height:8px;border-radius:50%;display:inline-block}
  .dot.on{background:#6a6}
  .dot.off{background:#666}
  .row{display:flex;gap:6px;margin-top:8px}
  button,a.btn{flex:1;text-align:center;background:#275;color:#eee;border:none;border-radius:6px;padding:7px 8px;font-size:11px;font-weight:700;cursor:pointer;text-decoration:none;display:block}
  button:hover,a.btn:hover{background:#386}
  button.secondary{background:#333}
  button.secondary:hover{background:#444}
  .status{font-size:11px;color:#aaa;margin-top:6px;min-height:14px}
  /* 2026-09-12 수정 — 사용자 지적: "나란히 4개가 있어야지~왼쪽부터 순서데로 오른쪽으로 4개" —
     2x2(2줄)이 아니라 한 줄에 4개(9223→9224→9225→9226 왼쪽부터 순서대로) 나란히. */
  .embeds{margin-top:18px;display:grid;grid-template-columns:repeat(4, 1fr);gap:10px}
  @media (max-width: 1400px) { .embeds{grid-template-columns:1fr 1fr} }
  @media (max-width: 700px) { .embeds{grid-template-columns:1fr} }
  .embed{background:#1a1a1a;border:1px solid #333;border-radius:10px;overflow:hidden;display:flex;flex-direction:column}
  .embed .embed-title{padding:8px 12px;font-size:12px;font-weight:700}
  .embed iframe{width:100%;height:70vh;border:0;border-top:1px solid #333;background:#fff}
</style>
</head>
<body>
  <h1>🎛 메인 컨트롤</h1>
  <div class="sub">계정 4개를 한눈에 보고, 각자 크롬을 켜거나 그 계정 전용 화면을 열 수 있습니다. 씬 생성은 각 계정 화면에서 진행하세요.</div>
  <!-- 2026-09-12 추가 — 사용자 지적: "각 계정 서버 켜는건 어디있어" / "여기에 만들어 두라고
       하자나". 계정별 "크롬 켜기"의 짝으로, 이 대시보드 서버(8799) 자체도 여기서 재시작할 수
       있게 한다 — 이 페이지가 보인다는 건 이미 켜져 있다는 뜻이라 상시 "켜짐"으로 표시하고,
       고장/갱신이 필요할 때 쓰는 재시작 버튼만 둔다. -->
  <div class="card" style="max-width:280px;margin-bottom:14px">
    <h2><span class="dot on"></span>대시보드 서버 (8799)</h2>
    <div class="status">🟢 켜짐 — 이 화면이 보이면 항상 켜져 있는 상태입니다</div>
    <div class="row"><button onclick="restartServer(this)" class="secondary">🔄 서버 재시작</button></div>
  </div>
  <!-- 2026-09-14 (2차) 수정 — 사용자 지적: "메인컨트롤은 이미지,영상 플로우 자동화아니야??" —
       맞는 말이라 여기(Flow 자동화 전용 화면)에서 무관한 렌더링 도구 버튼을 뺐다. 아래
       "🎬 Remotion 렌더링 데이터" 참고 — /remotion 별도 페이지로 옮김. -->
  <div class="row" style="max-width:280px;margin-bottom:14px">
    <a class="btn secondary" href="/remotion" target="_blank" style="background:#333">🎬 Remotion 렌더링 데이터 갱신 →</a>
  </div>
  <!-- 2026-09-12 추가 — 사용자 요청: "여러계정 동시시작 기능은 각 계정에서는 빼고 상단에
       통합 1번만 있기로 했는데". 계정별 임베드 4개마다 반복되던 "⚡ 여러 계정 동시 시작"을
       여기 상단에 딱 하나만 남긴다 — 코드를 새로 만들지 않고, 같은 페이지를 ?panel=multistart
       모드로 하나 더 embed해서 재사용한다(그 모드에서는 설정 패널 + 이 기능만 보이고 나머지는
       자동으로 숨겨짐 — restoreAndBoot() 참고). 계정별 임베드 쪽은 이 섹션이 자동으로 숨겨진다. -->
  <!-- 2026-09-12 수정 — 사용자 지적: "상단에 따로 빼둔거 통채로 접으란 말인데". 안쪽
       ?panel=multistart 페이지 안의 "여러 계정 동시 시작" 하위 섹션만 접는 걸론 부족했다 —
       워크플로우/콘텐츠/비율/캐릭터까지 포함한 이 박스 전체를 여기(메인 컨트롤) 레벨에서
       <details>로 감싸서, 평소엔 제목 한 줄만 보이고 필요할 때만 펼친다. -->
  <details style="margin-bottom:14px" class="card">
    <summary style="cursor:pointer;font-size:13px;font-weight:700;padding:2px 0">⚡ 여러 계정 동시 시작 (통합)</summary>
    <div class="embed" style="max-width:700px;margin-top:10px">
      <iframe id="multiStartFrame" src="http://127.0.0.1:8799/?panel=multistart" style="height:520px"></iframe>
    </div>
  </details>
  <script>
    // 2026-09-12 추가 — 사용자 요청: "스크롤바 하지말고 다 보여줘". 고정 높이(px) 하나로는
    // 내용(콘텐츠 선택 후 프로젝트 목록이 늘어나는 등)에 따라 모자라거나 남을 수 있어서,
    // 같은 출처(127.0.0.1:8799)라 접근 가능한 iframe 내부 문서의 실제 높이를 주기적으로
    // 읽어와 iframe 자체의 높이를 거기 맞춰 늘려준다 — 내부에 스크롤바가 안 생기고 항상
    // 전체 내용이 다 보인다.
    (function () {
      const f = document.getElementById('multiStartFrame');
      function syncHeight() {
        try {
          // 2026-09-12 수정 — documentElement.scrollHeight는 한 번 크게 잡힌 값에서 내용이
          // 줄어도 다시 안 줄어드는(계정/프로젝트 블록을 숨긴 뒤에도 4000px대에 고정된 채
          // 안 줄어드는) 문제가 실측 확인됨 — body.scrollHeight는 실제 보이는 내용 기준으로
          // 정확히 줄어드는 걸 확인해서 이걸로 바꾼다.
          const h = f.contentDocument.body.scrollHeight;
          if (h && Math.abs(parseInt(f.style.height, 10) - h) > 4) f.style.height = h + 'px';
        } catch (e) {}
      }
      f.addEventListener('load', syncHeight);
      setInterval(syncHeight, 1000);
    })();
  </script>
  <div id="grid" class="grid"></div>
  <div class="embeds">
    __ACCOUNT_EMBEDS__
  </div>
<script>
const ACCOUNTS = __ACCOUNTS_JSON__;
async function refresh(){
  let chromeStatus = {}, workerStatus = {workers: []};
  try { chromeStatus = await (await fetch('/api/chrome_status')).json(); } catch(e) {}
  try { workerStatus = await (await fetch('/api/status')).json(); } catch(e) {}
  const grid = document.getElementById('grid');
  grid.innerHTML = ACCOUNTS.map(acc => {
    const on = !!chromeStatus[String(acc.port)];
    const w = (workerStatus.workers || []).find(x => x.port === acc.port);
    const running = w && (w.phase === 'submitting' || w.phase === 'waiting');
    const workLine = running ? `${w.current || ''} ${w.gen_percent || (w.gen_started === false ? '시작 대기 ' + (w.elapsed||0) + '초' : '진행 중')}` : (on ? '대기 중' : '꺼짐');
    // 2026-09-12 삭제 — 사용자 지적: "상단에서 계정화면 열기는 이제 삭제" — 아래에 이미 4계정
    // 화면이 전부 나란히(iframe) 떠 있으므로, 새 탭으로 여는 이 링크는 중복이라 없앴다.
    // 2026-09-12 추가 — 사용자 지적: "켜기는 있는데 끄기는 없어?" — 켜짐이면 끄기 버튼을,
    // 꺼짐이면 켜기 버튼을 보여준다(항상 둘 다 있는 게 아니라 상태에 맞는 하나만).
    return `<div class="card">
      <h2><span class="dot ${on ? 'on' : 'off'}"></span>${acc.name} (${acc.port})</h2>
      <div class="status">${workLine}</div>
      <div class="row">
        ${on
          ? `<button onclick="stopChrome(${acc.port}, this)" class="secondary">■ 크롬 끄기</button>`
          : `<button onclick="startChrome(${acc.port}, this)" class="secondary">▶ 크롬 켜기</button>`}
      </div>
    </div>`;
  }).join('');
}
async function startChrome(port, btn){
  btn.disabled = true; btn.textContent = '여는 중...';
  try{
    const r = await fetch('/api/start_chrome', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({port})});
    const d = await r.json();
    if(d.error) alert(d.error);
  }catch(e){ alert('요청 실패: ' + e); }
  setTimeout(refresh, 1500);
}
async function stopChrome(port, btn){
  if(!confirm(`${port} 계정의 크롬을 종료할까요? 진행 중인 생성 작업이 있으면 실패로 끊깁니다.`)) return;
  btn.disabled = true; btn.textContent = '끄는 중...';
  try{
    const r = await fetch('/api/stop_chrome', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({port})});
    const d = await r.json();
    if(d.error) alert(d.error);
  }catch(e){ alert('요청 실패: ' + e); }
  setTimeout(refresh, 1500);
}
async function restartServer(btn){
  if(!confirm('대시보드 서버를 재시작할까요? 잠시 접속이 끊겼다가 몇 초 후 자동으로 돌아옵니다.')) return;
  btn.disabled = true; btn.textContent = '재시작 중...';
  try{ await fetch('/api/restart_server', {method:'POST'}); }catch(e){}
  setTimeout(() => location.reload(), 3000);
}
refresh();
setInterval(refresh, 3000);
</script>
</body></html>"""

# ── 2026-09-14 추가 — Remotion 렌더링 데이터 갱신 전용 화면 ──────────────────────────
# 사용자 지적: "메인컨트롤은 이미지,영상 플로우 자동화아니야??" — 맞는 지적이라, Flow
# 자동화 화면(메인 컨트롤/계정별 화면)과는 분리된 별도 페이지(/remotion)로 뺐다. 바탕화면
# "렌더링 화면 열기" 아이콘(open_remotion.ps1)이 이 페이지와 Remotion 스튜디오(포트 3010)를
# 함께 켜준다.
REMOTION_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Remotion 렌더링 데이터</title>
<style>
  /* 2026-09-18 — 컷·자막 타임라인 편집기가 생기면서 이 페이지는 사이드패널(420px)보다
     넓은 화면(보통 브라우저 탭)에서 보는 걸 전제로 바뀌었다("비주얼 모습은 편집기처럼
     보이게") — 폭 제한을 1200px로 넓혔다. 사이드패널에서 열면 안에서 스크롤될 뿐 깨지진 않는다. */
  body{background:#111;color:#eee;font-family:system-ui,sans-serif;margin:0;padding:16px;max-width:1200px}
  h1{font-size:16px;margin:0 0 4px}
  .sub{color:#888;font-size:11px;margin-bottom:16px;line-height:1.5}
  .card{background:#1a1a1a;border:1px solid #333;border-radius:10px;padding:14px}
  button{width:100%;background:#275;color:#eee;border:none;border-radius:6px;padding:10px;font-size:13px;font-weight:700;cursor:pointer}
  button:hover{background:#386}
  button:disabled{opacity:.5;cursor:default}
  .status{font-size:12px;color:#aaa;margin-top:10px;white-space:pre-line}
  a{color:#9ad}
</style></head>
<body>
  <h1>🎬 Remotion 렌더링 데이터</h1>
  <div class="sub">스튜디오 미리보기: <a href="http://localhost:3010/EconVideo" target="_blank">localhost:3010/EconVideo</a></div>
  <!-- 2026-09-14 (3차) 수정 — 사용자 지적: "리모션에서 작업한걸 왜 13단계를 업데이트를 해
       13단계는 이제 끝난거야~ 리모션에서 원본 파일과 작업중 파일로 편집을 하는거지" +
       "갱신을 하면 원본파일로 돌리는거고~ 작업중 파일이 있어야지". 13번은 최초 1회 수입
       소스일 뿐, 그 이후로는 econ_cuts.json(작업중 파일)이 유일한 진짜 편집 대상이다.
       econ_cuts.original.json(원본 파일)은 13번에서 가져온 상태를 그대로 얼려둔 백업이고,
       두 버튼의 역할을 명확히 분리했다:
       1) "📥 13번에서 원본 새로고침" — DB를 다시 읽어 econ_cuts.original.json만 새로 쓴다.
          작업중 파일(econ_cuts.json)은 절대 건드리지 않는다 — 새 장면이 13번에 추가됐을
          때만 가끔 쓰는 것.
       2) "↩️ 작업 파일을 원본으로 되돌리기" — DB를 아예 안 건드리고, 그냥
          econ_cuts.original.json을 econ_cuts.json 위에 그대로 덮어쓴다(파일 복사).
          지금까지 직접 고친 타이밍·자막이 전부 사라지는 되돌리기라 확인을 받는다. -->
  <div class="card">
    <button onclick="refreshOriginal(this)">📥 13번에서 원본 새로고침</button>
    <div class="sub" style="margin:8px 0 0">13번에 완전히 새로운 장면을 추가로 등록했을 때만 누르세요. econ_cuts.original.json(원본)만 갱신하고, 지금 작업 중인 econ_cuts.json은 전혀 건드리지 않습니다.</div>
    <div id="remotionStatus" class="status"></div>
  </div>
  <div class="card" style="margin-top:10px">
    <button onclick="revertToOriginal(this)" style="background:#633">↩️ 작업 파일을 원본으로 되돌리기</button>
    <div class="sub" style="margin:8px 0 0">지금까지 econ_cuts.json에 직접 고친 타이밍·자막이 전부 사라지고, econ_cuts.original.json 상태로 되돌아갑니다. 평소 편집은 <code>remotion/src/econ/econ_cuts.json</code>을 직접 여세요(DB 호출 없이 그냥 텍스트 편집).</div>
    <div id="revertStatus" class="status"></div>
  </div>
  <!-- 2026-09-18 추가 — 사용자 지적: "니가 처음부터 이렇게 셋팅해둔거야"(econ_cuts.json 컷
       타이밍이 13번 DB 기록과 안 맞음 발견) → "14번의 값으로 모두 변경" 지시. 13번↔14번
       불일치를 발견했을 때 반대 방향(14→13)으로 되돌리는 버튼 — refreshOriginal(13→14)의
       거울상. -->
  <div class="card" style="margin-top:10px">
    <button onclick="syncTimesTo13(this)" style="background:#573">⏱ 14번 타이밍을 13번에 역반영</button>
    <div class="sub" style="margin:8px 0 0">지금 작업중 파일(econ_cuts.json)의 컷 시작/길이가 맞는 값이라고 보고, 13번 DB의 scenePrompts 안 "- 시간:" 기록만 그 값으로 고칩니다(대본·이미지프롬프트·URL 등 다른 내용은 안 건드림). 13번과 14번의 타이밍이 서로 달라진 걸 발견했을 때 누르세요.</div>
    <div id="syncTimesStatus" class="status"></div>
  </div>
  <!-- 2026-09-14 추가 — 사용자 요청: "렌더링 화면에 서버재시작 버튼을 넣어놔줘". 대시보드
       서버(status_dashboard.py) 코드를 고친 뒤 반영하려면 재시작이 필요한데, 지금까지는
       메인 컨트롤 화면까지 가야만 재시작 버튼이 있었다 — 여기서도 바로 재시작할 수 있게
       메인 컨트롤과 완전히 같은 /api/restart_server를 재사용한다. -->
  <!-- 2026-09-18 추가 — 사용자 지적: "저 텍스트로 직접 고치는걸 어떻게 찾아서 하냐고" +
       "영상이나 이미지 위치도 그런식으로 딱 찾아서 수정할수있으면". Remotion Studio가 타임라인
       드래그 편집을 지원하지 않는다는 게 실측으로 확인된 뒤, econ_cuts.json(작업중 파일)을
       raw JSON으로 찾아 고치는 대신 13번/14번 패널과 같은 "표에서 찾아서 그 자리에서 고치기"
       방식을 여기에도 만든다. 컷(이미지/영상 배정·줌·타이밍)과 자막(문구·타이밍)을 표로 보여주고,
       저장하면 econ_cuts.json에만 반영한다(원본 파일·DB는 안 건드림). "순서 바꾸기"는 배열
       순서가 아니라 두 컷의 이미지/영상 배정을 서로 맞바꾸는 것으로 구현한다 — 각 컷은 시작
       시각(s)이 고정 절대값이라, 드래그로 옮기는 것과 동일한 결과(그 시간대에 다른 그림이
       나온다)를 얻으려면 시간이 아니라 배정된 파일을 맞바꾸면 된다. -->
  <div class="card" style="margin-top:10px">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
      <div style="flex:1;font-size:13px;font-weight:700">🎞 컷·자막 편집기 (작업중 파일 직접 편집)</div>
      <button style="width:auto;padding:6px 10px" onclick="loadEditor()">🔄 불러오기</button>
    </div>
    <input id="cutSearch" placeholder="🔍 컷 번호·파일명·자막으로 찾기 (예: S42)" oninput="filterEditor()"
           style="width:100%;box-sizing:border-box;background:#111;color:#eee;border:1px solid #333;border-radius:6px;padding:8px;font-size:12px;margin-bottom:8px">
    <div class="sub">필름스트립을 왼쪽→오른쪽(영상 시작→끝) 순서로 보여줍니다. 카드의 "파일" 드롭다운을 바꾸면 그 시간대 그림만 바뀌고, 시간은 그대로입니다 — "◀▶"로 좌우 컷과 배정을 맞바꿔서 드래그 없이 순서를 조정하세요.</div>
    <div id="editorStatus" class="status"></div>
    <div style="font-size:11px;font-weight:700;color:#9ad;margin:10px 0 4px">타임라인 (트랙별 — 클릭하면 아래 편집 카드로 이동)</div>
    <div id="timelineScroll" style="overflow-x:auto;border:1px solid #333;border-radius:6px;background:#0b0b0b">
      <div id="timelineInner" style="position:relative"></div>
    </div>
    <div style="font-size:11px;font-weight:700;color:#9ad;margin:14px 0 4px">컷(이미지·영상) — 왼쪽부터 시간 순, 타임라인에서 클릭하면 여기로 스크롤됩니다</div>
    <div id="cutsBody" style="display:flex;overflow-x:auto;gap:8px;padding:4px 4px 12px;border:1px solid #333;border-radius:6px"></div>
    <div style="font-size:11px;font-weight:700;color:#9ad;margin:14px 0 4px">자막</div>
    <div style="max-height:280px;overflow:auto;border:1px solid #333;border-radius:6px">
      <table style="width:100%;border-collapse:collapse;font-size:11px">
        <thead style="position:sticky;top:0;background:#1a1a1a">
          <tr style="text-align:left"><th style="padding:4px">시작(초)</th><th>길이(초)</th><th>자막 텍스트</th></tr>
        </thead>
        <tbody id="subsBody"></tbody>
      </table>
    </div>
    <button style="margin-top:10px;background:#275" onclick="saveEditor(this)">💾 변경사항 저장 (작업중 파일에 반영)</button>
  </div>
  <div class="card" style="margin-top:10px">
    <button onclick="restartServer(this)" style="background:#333">🔄 서버 재시작</button>
    <div id="restartStatus" class="status"></div>
  </div>
<script>
let editorAssets = [];
let editorDurations = {}; // 2026-09-18(2차) — 파일명(확장자 포함, "assets/" 접두어 없이) → 원본 길이(초)
function baseName(path){ return (path || '').replace(/^assets\//, ''); }
function checkTrimRange(card){
  // "원본 영상의 길이가 어떤것인지 안나와 있자나~~... 그 값들이 다 들어가 있어야해" — 파일
  // 드롭다운이 바뀌거나 길이/트림시작을 고칠 때마다 원본 길이·사용 구간을 다시 계산해서
  // 보여주고, 트림 구간이 원본 길이를 넘으면 빨간 테두리로 경고한다(저장은 막지 않음 —
  // Remotion이 넘는 부분은 마지막 프레임에서 멈추거나 검게 나올 뿐 렌더 자체가 깨지진 않지만,
  // 의도한 그림이 아닐 수 있어 경고만 한다).
  const trimBox = card.querySelector('.trimBox');
  if(!trimBox || trimBox.style.display === 'none') return;
  const path = card.querySelector('.fFile').value;
  const dur = editorDurations[baseName(path)];
  const d = parseFloat(card.querySelector('.fD').value) || 0;
  const trimInput = card.querySelector('.fTrim');
  const trimStart = parseFloat(trimInput.value) || 0;
  const srcDurEl = card.querySelector('.srcDur');
  const rangeEl = card.querySelector('.trimRange');
  if(dur == null){
    srcDurEl.textContent = '원본 길이: 확인 안 됨';
    rangeEl.textContent = '';
    trimInput.style.borderColor = '';
    return;
  }
  srcDurEl.textContent = `원본 길이: ${dur.toFixed(1)}초`;
  const trimEnd = trimStart + d;
  rangeEl.textContent = `사용 구간: ${trimStart.toFixed(1)}~${trimEnd.toFixed(1)}초`;
  const outOfRange = trimStart < 0 || trimEnd > dur + 0.05;
  trimInput.style.borderColor = outOfRange ? '#e55' : '';
  rangeEl.style.color = outOfRange ? '#e88' : '#888';
  if(outOfRange) rangeEl.textContent += ' ⚠️ 원본 길이 초과';
}
function extIsVideo(name){ return /\\.(mp4|mov|webm)$/i.test(name || ''); }
function assetOptions(current){
  return editorAssets.map(a => `<option value="assets/${a}" ${('assets/'+a)===current?'selected':''}>${a}</option>`).join('');
}
function cutThumb(path){
  if(!path) return '';
  const url = '/remotion_assets/' + path.replace(/^assets\\//, '');
  if(extIsVideo(path)) return `<video src="${url}" muted preload="metadata" style="width:56px;height:32px;object-fit:cover;background:#000"></video>`;
  return `<img src="${url}" style="width:56px;height:32px;object-fit:cover;background:#000">`;
}
// ⚠️ 2026-09-18 실사고: 처음엔 이 카드들을 <table><tbody id="cutsBody"> 안의 <tr>/<td>로
// 만들었다가(그때는 잘 동작함), 사용자가 "왼쪽에서 오른쪽으로 나열"을 요청해서 컨테이너를
// <table> 없는 플레인 <div style="display:flex">로 바꿨는데, 카드 템플릿은 여전히
// <tr>/<td> 문자열을 그 div에 innerHTML로 꽂고 있었다 — 테이블 컨텍스트 밖의 tr/td는
// 브라우저가 정상적으로 안 만들어준다(파싱 규칙상 무시/변형됨). 지금은 div 기반 카드로
// 전부 다시 썼다 — 컨테이너 태그를 바꿀 때는 그 안에 채워 넣는 마크업도 반드시 같이 바꿀 것.
const PX_PER_SEC = 6;
function fmtTime(sec){
  const m = Math.floor(sec/60), s = Math.floor(sec%60);
  return `${m}:${String(s).padStart(2,'0')}`;
}
function renderTimeline(cuts, subs){
  // "영상들을 각 트랙별로, 이미지는 한 트랙에 나열, tts 트랙 하나, 자막 트랙 하나" — 옛
  // Shotcut 구조(나레이션 1트랙 + 영상클립마다 별도 트랙 + 이미지 통합 1트랙 + 자막 1트랙)를
  // 그대로 시각적으로 재현한다. 실제 드래그 편집은 안 되지만(Remotion Studio 실측 결과 이유로
  // Shotcut을 다시 쓰지 않기로 함), 클릭하면 아래 편집 카드로 스크롤 이동한다.
  const duration = Math.max(
    ...cuts.map(c => c.s + c.d), ...subs.map(s => s.s + s.d), 60
  );
  const width = Math.ceil(duration * PX_PER_SEC);
  const clipCuts = cuts.filter(c => c.clip);
  const imageCuts = cuts.filter(c => !c.clip);
  const rulerTicks = [];
  for(let t = 0; t <= duration; t += 30) rulerTicks.push(t);
  const rowStyle = 'position:relative;height:26px;border-top:1px solid #222';
  const labelStyle = 'position:sticky;left:0;z-index:2;display:inline-block;width:70px;background:#1a1a1a;color:#9ad;font-size:10px;padding:2px 4px;box-sizing:border-box';
  let html = `<div style="position:relative;width:${width}px">`;
  html += `<div style="position:relative;height:18px">${rulerTicks.map(t =>
    `<div style="position:absolute;left:${t*PX_PER_SEC}px;top:0;font-size:9px;color:#666;border-left:1px solid #333;padding-left:2px">${fmtTime(t)}</div>`).join('')}</div>`;
  // 나레이션 트랙
  html += `<div style="${rowStyle}"><span style="${labelStyle}">🎙 나레이션</span>
    <div style="position:absolute;left:70px;top:2px;height:20px;width:${width}px;background:#1d4a3a;border-radius:3px"></div></div>`;
  // 영상 클립 — 클립마다 자기 트랙(옛 Shotcut V2,V3,V4... 구조 재현)
  clipCuts.forEach(c => {
    html += `<div style="${rowStyle}"><span style="${labelStyle}" title="${c.id}">🎬 ${c.id}</span>
      <div class="tlBlock" data-id="${c.id}" onclick="jumpToCut('${c.id}')"
           style="position:absolute;left:${70+c.s*PX_PER_SEC}px;top:2px;height:20px;width:${Math.max(3,c.d*PX_PER_SEC)}px;background:#7a4a1d;border-radius:3px;cursor:pointer" title="${c.id} (${fmtTime(c.s)}~${fmtTime(c.s+c.d)})"></div></div>`;
  });
  // 이미지 트랙 — 전부 한 트랙에
  html += `<div style="${rowStyle}"><span style="${labelStyle}">🖼 이미지</span>`;
  imageCuts.forEach(c => {
    html += `<div class="tlBlock" data-id="${c.id}" onclick="jumpToCut('${c.id}')"
      style="position:absolute;left:${70+c.s*PX_PER_SEC}px;top:2px;height:20px;width:${Math.max(3,c.d*PX_PER_SEC)}px;background:#1d3a6a;border-radius:3px;cursor:pointer;border-right:1px solid #0b0b0b" title="${c.id} (${fmtTime(c.s)}~${fmtTime(c.s+c.d)})"></div>`;
  });
  html += `</div>`;
  // 자막 트랙
  html += `<div style="${rowStyle}"><span style="${labelStyle}">💬 자막</span>`;
  subs.forEach((s, i) => {
    html += `<div onclick="jumpToSub(${i})"
      style="position:absolute;left:${70+s.s*PX_PER_SEC}px;top:6px;height:12px;width:${Math.max(2,s.d*PX_PER_SEC)}px;background:#5a2d6a;border-radius:2px;cursor:pointer;border-right:1px solid #0b0b0b" title="${s.text||''}"></div>`;
  });
  html += `</div></div>`;
  document.getElementById('timelineInner').innerHTML = html;
}
function jumpToCut(id){
  document.getElementById('cutSearch').value = id;
  filterEditor();
  const card = document.getElementById('cutcard-' + id);
  if(card) card.scrollIntoView({behavior:'smooth', inline:'center', block:'nearest'});
}
function jumpToSub(i){
  document.getElementById('cutSearch').value = '';
  filterEditor();
  const row = document.querySelectorAll('#subsBody tr')[i];
  if(row) row.scrollIntoView({behavior:'smooth', block:'center'});
}
async function loadEditor(){
  const statusEl = document.getElementById('editorStatus');
  statusEl.textContent = '불러오는 중...';
  try{
    const r = await fetch('/api/remotion_working');
    const d = await r.json();
    if(d.error){ statusEl.textContent = '❌ ' + d.error; return; }
    editorAssets = d.assets || [];
    editorDurations = d.assetDurations || {};
    const cuts = (d.cuts || []).slice().sort((a,b) => a.s - b.s);
    const subs = (d.subs || []).slice().sort((a,b) => a.s - b.s);
    renderTimeline(cuts, subs);
    document.getElementById('cutsBody').innerHTML = cuts.map((c, i) => {
      const path = c.clip || c.image || '';
      // ⚠️ cam은 {from,to,cx,cy} 4개 값을 통째로 갖고 있다 — 카드에는 "줌(cam.to)" 숫자
      // 칸 하나만 두지만, 나머지(from/cx/cy, 예: 줌아웃·좌우 패닝 값)를 저장 시 기본값으로
      // 밀어버리면 안 되므로 원본 cam 객체 전체를 data-cam에 보존해뒀다가 저장 시 to만 덮어쓴다.
      const camObj = c.cam || {from: 1.0, to: 1.06, cx: 50, cy: 50};
      const trimStart = c.trimStart || 0;
      return `<div class="cutCard" id="cutcard-${c.id}" data-idx="${i}" data-cam='${JSON.stringify(camObj).replace(/'/g, "&#39;")}'
                   style="flex:0 0 128px;background:#161616;border:1px solid #333;border-radius:6px;padding:6px;font-size:10px">
        <div class="thumbCell">${cutThumb(path)}</div>
        <div style="font-weight:700;color:#9ad;margin:4px 0 2px" class="cutId">${c.id || ''}</div>
        <label style="display:block;margin-top:2px">시작(타임라인)<input type="number" step="0.1" class="fS" value="${c.s}" style="width:100%;box-sizing:border-box"></label>
        <label style="display:block;margin-top:2px">길이<input type="number" step="0.1" class="fD" value="${c.d}" style="width:100%;box-sizing:border-box" onchange="checkTrimRange(this.closest('.cutCard'))"></label>
        <label style="display:block;margin-top:2px">줌<input type="number" step="0.01" class="fCam" value="${c.cam && c.cam.to != null ? c.cam.to : ''}" style="width:100%;box-sizing:border-box" ${c.clip ? 'disabled title=\\'영상 클립은 줌 없음\\'' : ''}></label>
        <select class="fFile" onchange="onFileChange(this)" style="width:100%;margin-top:2px;font-size:10px">${assetOptions(path)}</select>
        <div class="trimBox" style="display:${extIsVideo(path) ? 'block' : 'none'}">
          <div class="srcDur" style="margin-top:3px;color:#888;font-size:9px"></div>
          <label style="display:block;margin-top:2px">원본 내 시작<input type="number" step="0.1" class="fTrim" value="${trimStart}" style="width:100%;box-sizing:border-box" onchange="checkTrimRange(this.closest('.cutCard'))"></label>
          <div class="trimRange" style="font-size:9px;color:#888;margin-top:2px"></div>
        </div>
        <div style="display:flex;gap:2px;margin-top:2px">
          <input type="text" class="fUrl" placeholder="URL" style="width:100%;font-size:9px">
          <button style="width:auto;padding:2px 5px;font-size:9px" onclick="replaceFromUrl(this)">받기</button>
        </div>
        <div style="display:flex;gap:2px;margin-top:2px">
          <button style="width:auto;flex:1;padding:2px" onclick="swapCut(${i},-1)">◀</button>
          <button style="width:auto;flex:1;padding:2px" onclick="swapCut(${i},1)">▶</button>
        </div>
      </div>`;
    }).join('');
    document.querySelectorAll('#cutsBody .cutCard').forEach(checkTrimRange);
    document.getElementById('subsBody').innerHTML = subs.map(s => `<tr>
        <td><input type="number" step="0.1" class="gS" value="${s.s}" style="width:60px"></td>
        <td><input type="number" step="0.1" class="gD" value="${s.d}" style="width:50px"></td>
        <td><input type="text" class="gText" value="${(s.text||'').replace(/"/g,'&quot;')}" style="width:100%;box-sizing:border-box"></td>
      </tr>`).join('');
    statusEl.textContent = `✅ 컷 ${cuts.length}개, 자막 ${subs.length}개 불러왔습니다.`;
  }catch(e){ statusEl.textContent = '❌ 요청 실패: ' + e; }
}
function onFileChange(sel){
  const isVideo = extIsVideo(sel.value);
  const card = sel.closest('.cutCard');
  card.querySelector('.thumbCell').innerHTML = cutThumb(sel.value);
  card.querySelector('.fCam').disabled = isVideo;
  const trimBox = card.querySelector('.trimBox');
  if(trimBox) trimBox.style.display = isVideo ? 'block' : 'none';
  checkTrimRange(card);
}
async function replaceFromUrl(btn){
  const card = btn.closest('.cutCard');
  const url = card.querySelector('.fUrl').value.trim();
  if(!url) return;
  const id = card.querySelector('.cutId').textContent.trim() || ('cut' + Date.now());
  const ext = (url.split('.').pop() || 'jpg').split('?')[0].slice(0, 4);
  const filename = `${id}_${Date.now()}.${ext}`;
  btn.disabled = true; btn.textContent = '받는중';
  try{
    const r = await fetch('/api/download_remotion_asset', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({url, filename})});
    const d = await r.json();
    if(d.error){ alert('❌ ' + d.error); }
    else {
      if(!editorAssets.includes(filename)) editorAssets.push(filename);
      const sel = card.querySelector('.fFile');
      sel.innerHTML = assetOptions(d.path);
      onFileChange(sel);
    }
  }catch(e){ alert('❌ 요청 실패: ' + e); }
  btn.disabled = false; btn.textContent = '받기';
}
function swapCut(i, dir){
  // ⚠️ cam.to 숫자 칸만 바꾸면 from/cx/cy(줌아웃·패닝 방향)가 원래 값과 안 맞게 뒤섞인다 —
  // data-cam에 보존해둔 전체 cam 객체째로 맞바꾸고, 화면에 보이는 to 값도 거기서 다시 읽는다.
  const cards = document.querySelectorAll('#cutsBody .cutCard');
  const j = i + dir;
  if(j < 0 || j >= cards.length) return;
  const a = cards[i], b = cards[j];
  const aFile = a.querySelector('.fFile'), bFile = b.querySelector('.fFile');
  const aVal = aFile.value, bVal = bFile.value;
  const aCamObj = a.dataset.cam, bCamObj = b.dataset.cam;
  // 트림 시작점도 "그 소스 파일에 속한 값"이라 파일 배정과 함께 맞바꿔야 한다 — 안 그러면
  // A컷의 트림값이 B컷에 배정된 새 파일에 잘못 남는다.
  const aTrim = a.querySelector('.fTrim'), bTrim = b.querySelector('.fTrim');
  const aTrimVal = aTrim ? aTrim.value : '0', bTrimVal = bTrim ? bTrim.value : '0';
  aFile.innerHTML = assetOptions(bVal); bFile.innerHTML = assetOptions(aVal);
  a.dataset.cam = bCamObj; b.dataset.cam = aCamObj;
  a.querySelector('.fCam').value = JSON.parse(a.dataset.cam).to;
  b.querySelector('.fCam').value = JSON.parse(b.dataset.cam).to;
  if(aTrim) aTrim.value = bTrimVal;
  if(bTrim) bTrim.value = aTrimVal;
  onFileChange(aFile); onFileChange(bFile);
}
function filterEditor(){
  // ⚠️ card.textContent로 통째로 검색하면 안 된다 — <select>의 textContent는 화면에 안
  // 보이는 나머지 <option> 전부(에셋 목록 전체)를 포함해서, "S05"를 쳐도 다른 파일명 목록에
  // 그 글자가 들어있는 모든 카드가 걸려버린다(실측으로 발견한 버그). id와 "현재 선택된"
  // 파일명만 비교 대상으로 삼는다.
  const q = document.getElementById('cutSearch').value.trim().toLowerCase();
  document.querySelectorAll('#cutsBody .cutCard').forEach(card => {
    const id = card.querySelector('.cutId').textContent;
    const sel = card.querySelector('.fFile');
    const selectedLabel = sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].text : '';
    const hay = (id + ' ' + selectedLabel).toLowerCase();
    card.style.display = !q || hay.includes(q) ? '' : 'none';
  });
  document.querySelectorAll('#subsBody tr').forEach(tr => {
    tr.style.display = !q || tr.textContent.toLowerCase().includes(q) ? '' : 'none';
  });
}
async function saveEditor(btn){
  const cuts = Array.from(document.querySelectorAll('#cutsBody .cutCard')).map(card => {
    const path = card.querySelector('.fFile').value;
    const camVal = card.querySelector('.fCam').value;
    const cut = {
      id: card.querySelector('.cutId').textContent.trim(),
      s: parseFloat(card.querySelector('.fS').value),
      d: parseFloat(card.querySelector('.fD').value),
    };
    if(extIsVideo(path)){
      cut.clip = path;
      const trimEl = card.querySelector('.fTrim');
      if(trimEl) cut.trimStart = parseFloat(trimEl.value) || 0;
    }
    else {
      cut.image = path;
      // data-cam에 보존된 from/cx/cy(줌아웃·패닝 방향)는 그대로 두고, 사람이 고친
      // "줌(cam.to)" 값만 덮어쓴다 — 통째로 새로 만들면 그 값들이 기본값으로 밀려버린다.
      const camObj = JSON.parse(card.dataset.cam || '{}');
      if(camVal !== '') camObj.to = parseFloat(camVal);
      cut.cam = camObj;
    }
    return cut;
  });
  const subs = Array.from(document.querySelectorAll('#subsBody tr')).map(tr => ({
    s: parseFloat(tr.querySelector('.gS').value),
    d: parseFloat(tr.querySelector('.gD').value),
    text: tr.querySelector('.gText').value,
  }));
  const statusEl = document.getElementById('editorStatus');
  btn.disabled = true;
  try{
    const r = await fetch('/api/save_remotion_working', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({cuts, subs})});
    const d = await r.json();
    if(d.error){ statusEl.textContent = '❌ ' + d.error; }
    else {
      statusEl.textContent = `✅ 저장 완료 — 컷 ${d.cuts}개, 자막 ${d.subs}개. Remotion Studio에서 새로고침하면 반영됩니다.`;
      loadEditor();
    }
  }catch(e){ statusEl.textContent = '❌ 요청 실패: ' + e; }
  btn.disabled = false;
}
loadEditor();
async function refreshOriginal(btn){
  const statusEl = document.getElementById('remotionStatus');
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = '새로고침 중...';
  statusEl.textContent = '';
  try{
    const r = await fetch('/api/refresh_remotion_original', {method:'POST'});
    const d = await r.json();
    if(d.error){ statusEl.textContent = '❌ ' + d.error; }
    else { statusEl.textContent = `✅ 원본 파일(econ_cuts.original.json) 갱신 완료 — 장면 ${d.cuts}개, 자막 ${d.subs}개, 새 파일 ${d.new_files}개 다운로드`; }
  }catch(e){ statusEl.textContent = '❌ 요청 실패: ' + e; }
  btn.disabled = false; btn.textContent = label;
}
async function revertToOriginal(btn){
  if(!confirm('작업 중인 econ_cuts.json에 직접 고친 내용이 전부 사라지고, econ_cuts.original.json 상태로 되돌아갑니다. 계속할까요?')) return;
  const statusEl = document.getElementById('revertStatus');
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = '되돌리는 중...';
  statusEl.textContent = '';
  try{
    const r = await fetch('/api/revert_remotion_to_original', {method:'POST'});
    const d = await r.json();
    if(d.error){ statusEl.textContent = '❌ ' + d.error; }
    else { statusEl.textContent = '✅ 원본으로 되돌렸습니다.'; }
  }catch(e){ statusEl.textContent = '❌ 요청 실패: ' + e; }
  btn.disabled = false; btn.textContent = label;
}
async function syncTimesTo13(btn){
  if(!confirm('작업중 파일(econ_cuts.json)의 컷 타이밍을 13번 DB의 scenePrompts "- 시간:" 기록에 덮어씁니다(다른 내용은 안 건드림). 계속할까요?')) return;
  const statusEl = document.getElementById('syncTimesStatus');
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = '반영 중...';
  statusEl.textContent = '';
  try{
    const r = await fetch('/api/sync_scene_times_to_13', {method:'POST'});
    const d = await r.json();
    if(d.error){ statusEl.textContent = '❌ ' + d.error; }
    else { statusEl.textContent = `✅ ${d.changed}/${d.total_blocks}개 컷의 시간 기록을 13번 DB에 반영했습니다.` + (d.missing_in_cuts.length ? ` ⚠️ econ_cuts.json에 없는 id: ${d.missing_in_cuts.join(', ')}` : ''); }
  }catch(e){ statusEl.textContent = '❌ 요청 실패: ' + e; }
  btn.disabled = false; btn.textContent = label;
}
async function restartServer(btn){
  if(!confirm('대시보드 서버를 재시작할까요? 잠시 접속이 끊겼다가 몇 초 후 자동으로 돌아옵니다.')) return;
  const statusEl = document.getElementById('restartStatus');
  btn.disabled = true; btn.textContent = '재시작 중...';
  try{ await fetch('/api/restart_server', {method:'POST'}); }catch(e){}
  statusEl.textContent = '재시작 중... 잠시 후 새로고침됩니다.';
  setTimeout(() => location.reload(), 3000);
}
</script>
</body></html>"""


# ── 2026-09-14 추가 — Remotion 렌더링 데이터(econ_cuts.json) 새로고침 ────────────────
# 사용자 지적: "내가 할수있게끔 해줘야지~~" — 지금까지 새 씬이 등록/수정될 때마다
# build_cuts_json.py + download_assets.py 두 스크립트를 Claude가 터미널에서 직접 돌려줘야
# 했다. 그 두 스크립트를 여기 함수 하나로 그대로 옮겨서, 메인 컨트롤 화면의 버튼 하나로
# 사용자가 직접(터미널 없이) 실행할 수 있게 한다 — 로직은 완전히 동일(같은 "### \S+" 분리
# 방식, 자막도 SRT가 아니라 각 장면 자신의 대본+시간으로 생성).
ECON_SITE_ID = "5ee08a21-bf63-4a8b-9733-1fff37045070"
ECON_UNIT_ID = "1788409260000-econ10"
ECON_NARRATION_URL = "https://iwxpjnwktxpscoktfpyl.supabase.co/storage/v1/object/public/honghub-files/d30207bb-ec15-485a-a810-67bafd697566.mp3"
REMOTION_DIR = HERE.parent / "remotion"


def _econ_moving_to_cam(moving: str):
    m = (moving or "").lower()
    cam = {"from": 1.0, "to": 1.06, "cx": 50, "cy": 50}
    if "quick zoom" in m or "rapid zoom" in m:
        cam["to"] = 1.28
    elif "zoom in" in m or "zooms in" in m or "pushes in" in m or "push in" in m:
        cam["to"] = 1.18
    elif "zoom out" in m or "zooms out" in m or "pulls back" in m or "pull back" in m:
        cam["from"], cam["to"] = 1.18, 1.0
    elif "whip-pan" in m or "whip pan" in m:
        cam["to"] = 1.1
        cam["cx"] = 65 if "right" in m else 35
    elif "pan left" in m:
        cam["cx"] = 40
        cam["to"] = 1.08
    elif "pan right" in m:
        cam["cx"] = 60
        cam["to"] = 1.08
    elif "hold" in m and "steady" in m:
        cam["to"] = 1.0
    return cam


def refresh_remotion_original():
    """2026-09-14 (3차) 수정 — 사용자 지적: "리모션에서 작업한걸 왜 13단계를 업데이트를 해
    13단계는 이제 끝난거야~ 리모션에서 원본 파일과 작업중 파일로 편집을 하는거지" +
    "갱신을 하면 원본파일로 돌리는거고~ 작업중 파일이 있어야지" + "13단계는 사용자가 가서
    다시하기전에는 건드릴 필요가 없어". 13번(HongHub)은 최초 1회 수입 소스일 뿐이다 — 이
    함수는 DB를 다시 읽어 econ_cuts.original.json("원본" — 13번 상태를 그대로 얼려둔 백업)
    만 새로 쓴다. 실제 렌더링에 쓰이는 작업중 파일(econ_cuts.json)은 여기서 절대 안 건드린다
    — 사용자가 13번에 정말 새 장면을 추가로 등록했을 때만 가끔 누르는 버튼이다."""
    r = httpx.get(f"{SUPA_URL}/rest/v1/hub_sites", params={"id": f"eq.{ECON_SITE_ID}", "select": "script_draft"},
                  headers=supa_headers(), timeout=30)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return {"error": "사이트를 찾을 수 없습니다."}
    unit = next((u for u in (rows[0].get("script_draft") or {}).get("units") or [] if u.get("id") == ECON_UNIT_ID), None)
    if not unit:
        return {"error": "콘텐츠(유닛)를 찾을 수 없습니다."}
    text = unit.get("scenePrompts") or ""
    # id 형식과 무관하게 항상 "### "로 시작하는 줄에서 나눈다(S01B 같은 문자 접미사 id도
    # 안전하게 나뉜다 — status_dashboard.py의 다른 파서들과 동일한 패턴).
    blocks = re.split(r"\n(?=### \S+)", text.strip()) if text.strip() else []
    scenes = []
    for b in blocks:
        m_id = re.search(r"### (\S+)\s*(.*)", b)
        m_time = re.search(r"- 시간:\s*(\d+):(\d+)-(\d+):(\d+)", b)
        m_image = re.search(r"- 장면이미지:\s*(\S+)", b)
        m_video = re.search(r"- 장면영상:\s*(\S+)", b)
        m_moving = re.search(r"- 무빙:\s*(.+)", b)
        m_script = re.search(r"^대본:\s*(.+)$", b, re.MULTILINE)
        if not (m_id and m_time):
            continue
        start = int(m_time.group(1)) * 60 + int(m_time.group(2))
        end = int(m_time.group(3)) * 60 + int(m_time.group(4))
        scenes.append({
            "id": m_id.group(1), "title": m_id.group(2).strip(), "start": start, "end": end,
            "image": m_image.group(1) if m_image else None,
            "video": m_video.group(1) if m_video else None,
            "moving": m_moving.group(1).strip() if m_moving else "",
            "script": m_script.group(1).strip() if m_script else "",
        })
    scenes.sort(key=lambda s: s["start"])

    cuts = []
    for s in scenes:
        d = s["end"] - s["start"]
        if d <= 0:
            continue
        cut = {"s": s["start"], "d": d, "id": s["id"], "title": s["title"]}
        if s["video"]:
            cut["clip"] = s["video"]
        elif s["image"]:
            cut["image"] = s["image"]
            cut["cam"] = _econ_moving_to_cam(s["moving"])
        cuts.append(cut)

    original_path = REMOTION_DIR / "src" / "econ" / "econ_cuts.original.json"

    # 원본 파일은 매번 통째로 새로 만든다(작업중 파일과 달리 "보존해야 할 손 편집"이
    # 없는, 항상 13번 최신 상태를 그대로 반영하는 스냅샷이므로).
    subs = []
    for s in scenes:
        d = s["end"] - s["start"]
        if s["script"] and d > 0:
            subs.append({"s": s["start"], "d": d, "text": s["script"]})

    assets_dir = REMOTION_DIR / "public" / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    def download(url: str, filename: str) -> str:
        dest = assets_dir / filename
        if not dest.exists():
            dr = httpx.get(url, timeout=60, follow_redirects=True)
            dr.raise_for_status()
            dest.write_bytes(dr.content)
            return f"assets/{filename}"
        return f"assets/{filename}"

    new_files = 0
    for c in cuts:
        if c.get("clip"):
            ext = c["clip"].split(".")[-1].split("?")[0]
            fname = f"{c['id']}.{ext}"
            if not (assets_dir / fname).exists():
                new_files += 1
            c["clip"] = download(c["clip"], fname)
        elif c.get("image"):
            ext = c["image"].split(".")[-1].split("?")[0]
            fname = f"{c['id']}.{ext}"
            if not (assets_dir / fname).exists():
                new_files += 1
            c["image"] = download(c["image"], fname)

    narration_local = download(ECON_NARRATION_URL, "narration.mp3")

    data = {"cuts": cuts, "subs": subs, "narrationUrl": narration_local}
    original_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "cuts": len(cuts), "subs": len(subs), "new_files": new_files}


def revert_remotion_to_original():
    """"↩️ 작업 파일을 원본으로 되돌리기" — DB는 아예 안 건드리고, econ_cuts.original.json을
    econ_cuts.json(작업중 파일, 실제 렌더링에 쓰이는 파일) 위에 그대로 복사한다. 지금까지
    econ_cuts.json에 직접 한 손 편집(타이밍 미세조정, 자막 문구/분리)은 전부 사라진다."""
    original_path = REMOTION_DIR / "src" / "econ" / "econ_cuts.original.json"
    working_path = REMOTION_DIR / "src" / "econ" / "econ_cuts.json"
    if not original_path.exists():
        return {"error": "원본 파일(econ_cuts.original.json)이 아직 없습니다 — 먼저 '13번에서 원본 새로고침'을 눌러주세요."}
    working_path.write_text(original_path.read_text(encoding="utf-8"), encoding="utf-8")
    return {"ok": True}


def sync_scene_times_to_13():
    """"⏱ 14번 타이밍을 13번에 역반영" — 2026-09-18 추가. 사용자가 econ_cuts.json(작업중
    파일)의 컷 타이밍을 직접 손으로 고친 뒤(예: S01을 5.5초 대신 4.0초로), 13번 DB의
    scenePrompts에 적힌 "- 시간:" 기록은 옛날 값 그대로 남아있어서 서로 안 맞는 게 발견됨
    ("14번의 값으로 모두 변경" 사용자 지시). refresh_remotion_original()의 정반대 방향 —
    econ_cuts.json이 지금 맞는 값(source of truth)이라고 보고, 그 값을 13번 DB의
    scenePrompts 텍스트 안 "- 시간:" 줄에만 반영한다. 다른 필드(대본/이미지프롬프트/URL 등)는
    절대 건드리지 않는다. 이 함수도 refresh_remotion_original과 마찬가지로 이미 이 서버가
    내부적으로 들고 있는 Supabase 자격증명을 그대로 재사용한다(직접 만든 별도 스크립트로
    빼서 credential을 새로 다루려 하지 않는다)."""
    working_path = REMOTION_DIR / "src" / "econ" / "econ_cuts.json"
    if not working_path.exists():
        return {"error": "작업중 파일(econ_cuts.json)이 아직 없습니다."}
    cuts = json.loads(working_path.read_text(encoding="utf-8")).get("cuts", [])
    by_id = {c["id"]: c for c in cuts}

    def fmt(t):
        m = int(t // 60)
        s = t - m * 60
        if abs(s - round(s)) < 1e-9:
            return f"{m}:{int(round(s)):02d}"
        return f"{m}:{s:04.1f}"

    r = httpx.get(f"{SUPA_URL}/rest/v1/hub_sites", params={"id": f"eq.{ECON_SITE_ID}", "select": "script_draft"},
                  headers=supa_headers(), timeout=30)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return {"error": "사이트를 찾을 수 없습니다."}
    sd = rows[0]["script_draft"]
    unit = next((u for u in sd.get("units") or [] if u.get("id") == ECON_UNIT_ID), None)
    if not unit:
        return {"error": "콘텐츠(유닛)를 찾을 수 없습니다."}
    sp = unit.get("scenePrompts") or ""

    blocks = re.split(r"(?=^### )", sp, flags=re.MULTILINE)
    changed = 0
    missing = []
    for i in range(1, len(blocks)):
        m_id = re.match(r"### (\S+)", blocks[i])
        cid = m_id.group(1) if m_id else None
        c = by_id.get(cid)
        if not c:
            missing.append(cid)
            continue
        new_time = fmt(c["s"]) + "-" + fmt(c["s"] + c["d"])
        new_block, n = re.subn(r"(- 시간:\s*)\S+", lambda mm: mm.group(1) + new_time, blocks[i], count=1)
        if n == 1 and new_block != blocks[i]:
            changed += 1
        blocks[i] = new_block
    unit["scenePrompts"] = "".join(blocks)

    patch = httpx.patch(f"{SUPA_URL}/rest/v1/hub_sites", params={"id": f"eq.{ECON_SITE_ID}"},
                         headers={**supa_headers(), "Content-Type": "application/json"},
                         content=json.dumps({"script_draft": sd}).encode("utf-8"), timeout=30)
    patch.raise_for_status()
    return {"ok": True, "changed": changed, "total_blocks": len(blocks) - 1, "missing_in_cuts": missing}


# ── 2026-09-18 추가 — 컷/자막 표 편집기 ──────────────────────────────────
# 사용자 지적: "저 텍스트로 직접 고치는걸 어떻게 찾아서 하냐고" — Remotion Studio
# 타임라인이 드래그 편집을 지원하지 않는다는 게 실측으로 확인된 뒤(사용자가 기억하고 있던
# "그래서 Shotcut으로 넘어갔었다"는 이유가 여전히 유효했음), econ_cuts.json(작업중 파일,
# 컷 83개+자막 227개)을 raw JSON 텍스트로 찾아 고치라고 하는 건 비현실적이라는 지적을 받았다.
# 13번/14번 패널이 이미 쓰고 있는 "표로 보여주고 그 자리에서 고친다" 방식을 여기에도 그대로
# 적용한다 — 컷과 자막을 각각 표로 보여주고, 저장하면 이 파일(작업중 파일)에만 반영한다
# (원본 파일이나 DB는 전혀 건드리지 않음 — 원본/작업중 파일 분리 원칙 그대로 유지).
def _ffprobe_path():
    exe = shutil.which("ffprobe")
    if exe:
        return exe
    fallback = Path("C:/Program Files/Shotcut/ffprobe.exe")
    return str(fallback) if fallback.exists() else None


def get_asset_durations(assets_dir: Path, filenames):
    """비디오 파일의 실제 길이(초)를 ffprobe로 재서 돌려준다 — 2026-09-18 추가, 사용자 지적:
    "원본 영상의 길이가 어떤것인지 안나와 있자나~~... 그 값들이 다 들어가 있어야해". 매번
    83개(중 영상은 일부) 파일을 다시 재는 건 느리니 assets 폴더에 캐시 파일(.duration_cache.json,
    파일명→길이)을 두고 이미 잰 파일은 다시 안 잰다 — 새 파일이 추가됐을 때만 그 파일만 잰다."""
    cache_path = assets_dir / ".duration_cache.json"
    cache = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    ffprobe = _ffprobe_path()
    changed = False
    for name in filenames:
        if not extIsVideoPy(name):
            continue
        if name in cache:
            continue
        fpath = assets_dir / name
        if not fpath.exists() or not ffprobe:
            continue
        try:
            out = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(fpath)],
                capture_output=True, text=True, timeout=20,
            )
            cache[name] = round(float(out.stdout.strip()), 2)
            changed = True
        except Exception:
            pass
    if changed:
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return cache


def extIsVideoPy(name: str) -> bool:
    return bool(re.search(r"\.(mp4|mov|webm)$", name or "", re.IGNORECASE))


def get_remotion_working():
    """econ_cuts.json(작업중 파일)의 현재 내용을 그대로 읽어 표 편집기에 내려준다.
    2026-09-18 추가 — assets 목록도 같이 내려줘서, 프론트가 "이미 받아둔 파일 중 하나로
    교체" 드롭다운을 채울 수 있게 한다(사용자 요청: "영상이나 이미지 위치도 찾아서 수정").
    2026-09-18(2차) 추가 — assetDurations(원본 파일 실제 길이)도 같이 내려준다(사용자 지적:
    "원본 영상의 길이가 어떤것인지 안나와 있자나... 어디부터 어디까지 사용할껀지 설정이
    되어있어야해")."""
    working_path = REMOTION_DIR / "src" / "econ" / "econ_cuts.json"
    if not working_path.exists():
        return {"error": "작업중 파일(econ_cuts.json)이 아직 없습니다 — 먼저 '13번에서 원본 새로고침' 후 '작업 파일을 원본으로 되돌리기'를 눌러주세요."}
    data = json.loads(working_path.read_text(encoding="utf-8"))
    assets_dir = REMOTION_DIR / "public" / "assets"
    assets = sorted(p.name for p in assets_dir.glob("*") if p.is_file() and p.name != ".duration_cache.json") if assets_dir.exists() else []
    durations = get_asset_durations(assets_dir, assets) if assets_dir.exists() else {}
    return {
        "cuts": data.get("cuts", []),
        "subs": data.get("subs", []),
        "narrationUrl": data.get("narrationUrl"),
        "captionStyle": data.get("captionStyle"),
        "assets": assets,
        "assetDurations": durations,
    }


def download_remotion_asset(url: str, filename: str):
    """편집기의 "URL로 교체" 기능 — 붙여넣은 URL을 remotion/public/assets/에 그 파일명으로
    받아둔다(이미 있으면 덮어씀). 이 함수는 파일만 받아둘 뿐 econ_cuts.json은 안 건드린다 —
    실제 컷에 배정하는 건 save_remotion_working이 담당(프론트가 이 응답의 경로를 그 컷의
    image/clip 필드에 넣은 뒤 저장 버튼으로 반영)."""
    if not url or not filename:
        return {"error": "url과 filename이 모두 필요합니다."}
    assets_dir = REMOTION_DIR / "public" / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
    dest = assets_dir / safe_name
    r = httpx.get(url, timeout=60, follow_redirects=True)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return {"ok": True, "path": f"assets/{safe_name}"}


def save_remotion_working(cuts, subs):
    """표 편집기에서 고친 컷/자막 배열을 작업중 파일에 반영한다. narrationUrl·captionStyle은
    이 편집기가 다루는 대상이 아니므로 기존 값을 그대로 유지한다(건드리지 않음)."""
    working_path = REMOTION_DIR / "src" / "econ" / "econ_cuts.json"
    if not working_path.exists():
        return {"error": "작업중 파일(econ_cuts.json)이 아직 없습니다."}
    data = json.loads(working_path.read_text(encoding="utf-8"))
    for c in cuts:
        c["s"] = float(c.get("s", 0))
        c["d"] = float(c.get("d", 0))
        if "cam" in c and c["cam"]:
            for k in ("from", "to", "cx", "cy"):
                if k in c["cam"] and c["cam"][k] is not None:
                    c["cam"][k] = float(c["cam"][k])
        # 2026-09-18(2차) 추가 — 영상 클립의 "원본 안에서 어디부터 쓸지"(트림 시작점, 초).
        # 사용자 지적: "14단계 보면 원본영상의 길이가 나와있고 그중 어디부터 어디까지
        # 사용할껀지 설정이 되어있어~ 그 값들이 다 들어가 있어야해". 트림 끝은 별도로 안 두고
        # trimStart+d(타임라인 길이)로 계산한다 — EconVideo.tsx가 렌더 시 이 값으로 계산.
        if c.get("clip"):
            c["trimStart"] = float(c.get("trimStart") or 0)
        elif "trimStart" in c:
            del c["trimStart"]
    for s in subs:
        s["s"] = float(s.get("s", 0))
        s["d"] = float(s.get("d", 0))
    data["cuts"] = cuts
    data["subs"] = subs
    working_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "cuts": len(cuts), "subs": len(subs)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path == "/api/sites":
            # 2026-09-10 수정 — 처음엔 hub_sites 전체(23개, 대부분 이 콘텐츠-유닛
            # 파이프라인과 무관한 블로그/툴 프로젝트)를 그대로 보여줘서 사용자가
            # "경제학·공학처럼 실제 콘텐츠 유닛이 있는 것만 가져와야지" 지적함 —
            # script_draft.units가 하나라도 있는 사이트만 필터링한다.
            try:
                r = httpx.get(f"{SUPA_URL}/rest/v1/hub_sites",
                               params={"select": "id,name,script_draft", "order": "sort_order"},
                               headers=supa_headers(), timeout=15)
                r.raise_for_status()
                sites = []
                for row in r.json():
                    units = (row.get("script_draft") or {}).get("units") or []
                    if units:
                        sites.append({"id": row["id"], "name": row["name"], "unit_count": len(units)})
                        site_name_cache[row["id"]] = row["name"]
                self._json(sites)
            except Exception as e:
                self._json({"error": str(e)}, 500)
        elif parsed.path == "/api/units":
            site_id = qs.get("site_id", [""])[0]
            try:
                r = httpx.get(f"{SUPA_URL}/rest/v1/hub_sites", params={"id": f"eq.{site_id}", "select": "script_draft"},
                               headers=supa_headers(), timeout=15)
                r.raise_for_status()
                rows = r.json()
                units = (rows[0].get("script_draft") or {}).get("units") or [] if rows else []
                out = [{"id": u.get("id"), "title": u.get("title", ""),
                        "scene_count": len(parse_scene_prompts(u.get("scenePrompts") or ""))}
                       for u in units]
                self._json(out)
            except Exception as e:
                self._json({"error": str(e)}, 500)
        elif parsed.path == "/api/scenes":
            # 2026-09-10 추가 — 콘텐츠 드롭다운만 선택해도(아직 "시작" 누르기 전에도) 몇 번
            # 씬이 몇 개나 있는지 순서대로 미리 볼 수 있어야 한다는 지적으로 추가.
            site_id = qs.get("site_id", [""])[0]
            unit_id = qs.get("unit_id", [""])[0]
            try:
                r = httpx.get(f"{SUPA_URL}/rest/v1/hub_sites", params={"id": f"eq.{site_id}", "select": "script_draft"},
                               headers=supa_headers(), timeout=15)
                r.raise_for_status()
                rows = r.json()
                units = (rows[0].get("script_draft") or {}).get("units") or [] if rows else []
                unit = next((u for u in units if u.get("id") == unit_id), None)
                scenes = parse_scene_prompts((unit or {}).get("scenePrompts") or "")
                # 2026-09-13 추가 — 사용자 요청: "탭으로 이미지/영상 구분해주고 영상쪽에
                # 영상프롬프트 보여주고". video_prompt/needs_video/video_url을 같이 내려줘서
                # 프론트가 별도 API 호출 없이 이미지/영상 두 탭을 한 응답으로 그릴 수 있게 한다.
                self._json([{"id": s["id"], "num": s["num"], "title": s["title"] or s["prompt"][:40], "prompt": s["prompt"],
                             "status": "done" if s["image_url"] else "pending",
                             "image_url": s["image_url"],
                             "video_prompt": s["video_prompt"], "needs_video": s["needs_video"],
                             "video_status": "done" if s["video_url"] else "pending",
                             "video_url": s["video_url"]} for s in scenes])
            except Exception as e:
                self._json({"error": str(e)}, 500)
        elif parsed.path == "/api/status":
            self._json(worker_status())
        elif parsed.path == "/api/ui_state":
            self._json(load_dashboard_state())
        elif parsed.path == "/api/chrome_status":
            self._json({str(p): is_port_open(p) for p in CHROME_ACCOUNTS})
        elif parsed.path == "/control":
            accounts_json = json.dumps(
                [{"port": p, "name": a["name"]} for p, a in sorted(CHROME_ACCOUNTS.items())],
                ensure_ascii=False,
            )
            embeds_html = "\n".join(
                f'<div class="embed"><div class="embed-title">{a["name"]} ({p}) 화면</div>'
                f'<iframe src="/?view={p}"></iframe></div>'
                for p, a in sorted(CHROME_ACCOUNTS.items())
            )
            html = CONTROL_HTML.replace("__ACCOUNTS_JSON__", accounts_json)
            html = html.replace("__ACCOUNT_EMBEDS__", embeds_html)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
        elif parsed.path == "/remotion":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(REMOTION_HTML.encode("utf-8"))
        elif parsed.path == "/api/remotion_working":
            result = get_remotion_working()
            self._json(result, 400 if result.get("error") else 200)
        elif parsed.path.startswith("/remotion_assets/"):
            # 2026-09-18 추가 — 컷 편집기 표에서 이미지/영상 썸네일을 보여주기 위한 정적 파일
            # 서빙(remotion 스튜디오(3010번 포트)와 이 대시보드(8799번 포트)는 별개 서버라
            # 여기서도 별도로 서빙해야 함). 경로 조작 방지: 파일명만 허용, 하위 폴더 이동 불가.
            name = os.path.basename(parsed.path[len("/remotion_assets/"):])
            fpath = REMOTION_DIR / "public" / "assets" / name
            if not fpath.exists() or not fpath.is_file():
                self.send_response(404)
                self.end_headers()
                return
            ctype, _ = mimetypes.guess_type(str(fpath))
            self.send_response(200)
            self.send_header("Content-Type", ctype or "application/octet-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(fpath.read_bytes())
        else:
            # 2026-09-12 추가 — 계정(포트) 선택을 서버 상태 기준으로 HTML에 직접 구워 넣는다.
            # localStorage(브라우저 프로필별로 분리됨)에만 의존하면 크롬창 4개 중 어느 걸로
            # 열든 그 창이 마지막으로 저장한 값만 보여서 "다른 창에서는 다른 계정으로 보인다"는
            # 문제가 생긴다 — 이 서버가 모든 창의 공통 소스이므로 여기서 고정한다.
            # 2026-09-12 추가 — 사용자 요청: "전체 계정 보기에서 각각 페이지 띄워서 통제할 수
            # 있게" — 워커 목록의 "🔗 이 계정 제어" 링크가 ?port=9223 같은 쿼리스트링으로 새 탭을
            # 여는데, 그 탭은 그 계정으로 고정해서 열려야 한다. 쿼리스트링이 있으면 서버 저장값
            # 대신 그걸 최우선으로 쓰고, 서버 저장값도 그걸로 갱신해서 다른 창에서도 일관되게 한다.
            # 2026-09-12 추가 — 메인 컨트롤 페이지가 계정 4개를 iframe으로 한 화면에 동시에
            # 보여주려면(?view=9223 등) 그 값이 "이 창이 지금 이 계정을 보고 있다"는 뜻일 뿐,
            # "앞으로 새 작업을 이 계정으로 시작하라"는 전역 선택값을 덮어쓰면 안 된다 — 4개
            # iframe이 동시에 로드되면서 서로 저장값을 계속 덮어쓰는 사고를 막기 위해 분리했다.
            # ?port=(계정 화면 열기 버튼)는 기존대로 전역 선택값을 바꾸고, ?view=(읽기 전용 임베드)는
            # 화면 표시만 그 계정으로 하고 서버 저장값은 건드리지 않는다.
            view_port = qs.get("view", [None])[0]
            query_port = qs.get("port", [None])[0]
            if view_port in ("9223", "9224", "9225", "9226"):
                saved_port = view_port
            elif query_port in ("9223", "9224", "9225", "9226"):
                save_dashboard_state({"port": query_port})
                saved_port = query_port
            else:
                saved_port = str(load_dashboard_state().get("port") or "9223")
            html = HTML.replace(f'<option value="{saved_port}">', f'<option value="{saved_port}" selected>', 1)
            html = html.replace(
                '<span id="savedPortLabel" style="color:#6a6;font-size:11px;font-weight:700"></span>',
                f'<span id="savedPortLabel" style="color:#6a6;font-size:11px;font-weight:700">✓ 서버에 저장된 계정: {saved_port}</span>',
                1,
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(body or b"{}")
        except Exception:
            data = {}
        if self.path == "/api/start":
            result = start_run(data.get("site_id", ""), data.get("unit_id", ""), data.get("scene_ids"),
                               data.get("ratio", "16:9"), bool(data.get("new_project")),
                               int(data.get("port", 9223)), data.get("characters") or [],
                               int(data.get("image_count", 1)), data.get("project_url"),
                               bool(data.get("agent_off", True)), data.get("mode", "image"),
                               data.get("video_ratio", "16:9"), data.get("video_model", "Omni 1.1 Flash"),
                               data.get("video_resolution", "720p"), data.get("video_duration", "8초"),
                               int(data.get("video_count", 1)))
            self._json(result, 400 if result.get("error") else 200)
        elif self.path == "/api/list_projects":
            result = list_flow_projects(int(data.get("port", 9223)))
            self._json(result, 400 if result.get("error") else 200)
        elif self.path == "/api/register_scenes":
            result = register_scenes(data.get("site_id", ""), data.get("unit_id", ""), data.get("scenes") or [])
            self._json(result, 400 if result.get("error") else 200)
        elif self.path == "/api/set_scene_image":
            result = set_scene_image_url(data.get("site_id", ""), data.get("unit_id", ""),
                                          data.get("scene_id", ""), data.get("image_url", ""))
            self._json(result, 400 if result.get("error") else 200)
        elif self.path == "/api/delete_scene_image":
            result = delete_scene_image(data.get("site_id", ""), data.get("unit_id", ""),
                                         data.get("scene_id", ""))
            self._json(result, 400 if result.get("error") else 200)
        elif self.path == "/api/set_scene_video":
            # 2026-09-13 추가 — set_scene_image와 동일한 역할의 영상용 엔드포인트.
            result = set_scene_video_url(data.get("site_id", ""), data.get("unit_id", ""),
                                          data.get("scene_id", ""), data.get("video_url", ""))
            self._json(result, 400 if result.get("error") else 200)
        elif self.path == "/api/delete_scene_video":
            result = delete_scene_video(data.get("site_id", ""), data.get("unit_id", ""),
                                         data.get("scene_id", ""))
            self._json(result, 400 if result.get("error") else 200)
        elif self.path == "/api/stop":
            self._json(stop_run(int(data.get("port", 9223))))
        elif self.path == "/api/ui_state":
            save_dashboard_state(data or {})
            self._json({"ok": True})
        elif self.path == "/api/start_chrome":
            result = start_chrome_for_port(int(data.get("port", 0)))
            self._json(result, 400 if result.get("error") else 200)
        elif self.path == "/api/stop_chrome":
            result = stop_chrome_for_port(int(data.get("port", 0)))
            self._json(result, 400 if result.get("error") else 200)
        elif self.path == "/api/restart_server":
            self._json(restart_server())
        elif self.path == "/api/refresh_remotion_original":
            result = refresh_remotion_original()
            self._json(result, 400 if result.get("error") else 200)
        elif self.path == "/api/revert_remotion_to_original":
            result = revert_remotion_to_original()
            self._json(result, 400 if result.get("error") else 200)
        elif self.path == "/api/sync_scene_times_to_13":
            result = sync_scene_times_to_13()
            self._json(result, 400 if result.get("error") else 200)
        elif self.path == "/api/save_remotion_working":
            result = save_remotion_working(data.get("cuts") or [], data.get("subs") or [])
            self._json(result, 400 if result.get("error") else 200)
        elif self.path == "/api/download_remotion_asset":
            result = download_remotion_asset(data.get("url", ""), data.get("filename", ""))
            self._json(result, 400 if result.get("error") else 200)
        else:
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
    print(f"[dashboard] http://127.0.0.1:{port} 에서 대기 중")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
