"""이미 열린 탭을 **수동 녹화**한다 — 다른 프로세스가 그 탭을 조작하는 동안.

    .\\.venv\\Scripts\\python scripts/record_tab.py --out <mp4> --url-match labs.google \\
        [--stdin <스크립트>] -- <실행할 명령 ...>

★왜 gdigrab 이 아니라 이걸 쓰는가 (2026-09-04 실사고)
  gdigrab 은 **화면 영역**을 찍는다. 자동화는 CDP 로 특정 탭을 조작하므로 사용자가 같은 창에서
  다른 탭을 보고 있어도 **자동화는 계속 성공**한다 — 그런데 녹화본엔 사용자가 보던 화면이 찍힌다.
  실제로 25분 녹화본에 개인 ChatGPT 대화 목록·검색 이력이 들어갔다. 이 방식은 **탭 자체**를
  `Page.startScreencast` 로 찍으므로 화면에 뭐가 떠 있든 섞일 수 없다.

★`screencast/recorder.py` 의 `record()` 는 **새 탭을 만들어 자기가 조작**하는 용도라 여기 못 쓴다.
  프레임 수집·CFR 인코딩만 그쪽 것을 재사용한다.
"""
import argparse
import asyncio
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.video.screencast.recorder import CDP, _encode_cfr


def find_tab(cdp_http: str, url_match: str) -> dict:
    tabs = json.loads(urllib.request.urlopen(f"{cdp_http}/json/list", timeout=8).read())
    pat = re.compile(url_match)
    pages = [t for t in tabs if t.get("type") == "page" and pat.search(t.get("url") or "")]
    if not pages:
        raise SystemExit(f"★ /{url_match}/ 에 맞는 탭이 없다")
    return pages[-1]


async def open_own_window(cdp_http: str, url: str) -> dict:
    """URL 을 **새 창**으로 연다.

    ★`Page.startScreencast` 는 **그 창에서 활성 탭일 때만** 프레임을 준다(백그라운드 탭은
    Chrome 이 렌더를 얼린다). 사용자가 쓰는 창의 탭을 앞으로 끌면 작업 화면이 바뀌므로,
    Flow 를 전용 창으로 떼어낸다 — 그러면 사용자 창은 그대로 두고 녹화만 된다.
    """
    ver = json.loads(urllib.request.urlopen(f"{cdp_http}/json/version", timeout=8).read())  # noqa: ASYNC210 — 셋업 1회, 동시 코루틴 없음
    b = CDP(ver["webSocketDebuggerUrl"])
    await b.connect()
    try:
        r = await b.send("Target.createTarget", url=url, newWindow=True)
        # CDP.send 는 구현에 따라 result 를 벗겨 주기도 한다 — 둘 다 받는다.
        tid = (r.get("result", r) or {}).get("targetId")
        if not tid:
            raise SystemExit(f"★새 창 생성 응답 이상: {str(r)[:200]}")
    finally:
        await b.close()
    for _ in range(20):
        await asyncio.sleep(1.0)
        tabs = json.loads(urllib.request.urlopen(f"{cdp_http}/json/list", timeout=8).read())  # noqa: ASYNC210
        hit = [t for t in tabs if t.get("id") == tid]
        if hit and hit[0].get("webSocketDebuggerUrl"):
            urllib.request.urlopen(f"{cdp_http}/json/activate/{tid}", timeout=8).read()  # noqa: ASYNC210
            await asyncio.sleep(2.0)
            return hit[0]
    raise SystemExit("★새 창 생성 실패")


async def run(args) -> int:
    if args.new_window:
        tab = await open_own_window(args.cdp, args.new_window)
        print("전용 창으로 분리함 — 사용자 창은 건드리지 않는다")
    else:
        tab = find_tab(args.cdp, args.url_match)
        urllib.request.urlopen(f"{args.cdp}/json/activate/{tab['id']}", timeout=8).read()  # noqa: ASYNC210
        await asyncio.sleep(1.5)
    print(f"대상 탭: {(tab.get('title') or '')[:50]}\n  {tab.get('url', '')[:80]}")
    cdp = CDP(tab["webSocketDebuggerUrl"])
    frames: list[tuple[float, bytes]] = []

    async def _ack(sid):
        try:
            await cdp.send("Page.screencastFrameAck", sessionId=sid)
        except Exception:  # noqa: BLE001, S110 — ack 실패는 프레임 유실이 아니다(탭이 닫히는 경합)
            pass

    def on_frame(params):
        """★핸들러는 **동기**여야 한다 — CDP 리더가 `h(params)` 로 그냥 부른다.
        async 로 두면 코루틴이 실행되지 않아 프레임이 하나도 안 쌓인다(실측)."""
        frames.append((time.monotonic(), base64.b64decode(params["data"])))
        asyncio.create_task(_ack(params.get("sessionId")))

    await cdp.connect()
    cdp.on("Page.screencastFrame", on_frame)
    await cdp.send("Page.enable")
    await cdp.send("Page.startScreencast", format="jpeg", quality=args.quality,
                   maxWidth=args.width, maxHeight=args.height, everyNthFrame=1)
    print("  녹화 시작(탭 전용 — 화면에 뭐가 떠 있든 섞이지 않는다)")

    rc = 0
    t0 = time.monotonic()
    if args.cmd:
        print("  ▶", " ".join(args.cmd)[:140], f"< {args.stdin}" if args.stdin else "")
        loop = asyncio.get_running_loop()

        def _run():
            if args.stdin:
                with open(args.stdin, "rb") as fh:
                    return subprocess.run(args.cmd, stdin=fh, env=os.environ.copy(), check=False).returncode
            return subprocess.run(args.cmd, env=os.environ.copy(), check=False).returncode

        rc = await loop.run_in_executor(None, _run)
        print(f"  명령 종료 rc={rc}")
    elif args.scroll_js:
        await asyncio.sleep(args.scroll_at)
        expr = Path(args.scroll_js).read_text(encoding="utf-8")
        r = await cdp.send("Runtime.evaluate", expression=expr, returnByValue=True)
        val = (r.get("result", {}) or {}).get("value")
        print(f"  스크롤 JS 결과: {val}")
        # ★정지 화면은 Page.startScreencast 가 프레임을 안 보낸다(변화 없음) → CFR 인코딩이
        #   실제 경과시간이 아니라 마지막 프레임 시각에서 끊긴다. 남은 구간엔 눈에 안 띄는
        #   1px 스크롤 왕복으로 강제 리페인트를 계속 흘려 8초를 채운다.
        # ★일부 문서 사이트는 body 가 아니라 내부 div 가 스크롤 컨테이너라
        #   window.scrollBy 로는 리페인트가 안 걸린다. opacity 서브픽셀 토글은
        #   스크롤 구조와 무관하게 항상 컴포지터 프레임을 강제한다.
        remain = args.seconds - args.scroll_at
        t_end = time.monotonic() + max(0.0, remain)
        toggle = False
        while time.monotonic() < t_end:
            v = "0.9995" if toggle else "1"
            toggle = not toggle
            await cdp.send("Runtime.evaluate", expression=f"document.documentElement.style.opacity='{v}';")
            await asyncio.sleep(0.35)
    else:
        await asyncio.sleep(args.seconds)

    try:
        await cdp.send("Page.stopScreencast")
    finally:
        await cdp.close()

    if not frames:
        print("★프레임이 하나도 안 왔다 — 탭이 백그라운드로 얼어 있었을 수 있다")
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    dur = _encode_cfr(frames, args.fps, out)
    print(f"녹화 저장: {out}  {dur:.1f}s  {out.stat().st_size / 1e6:.1f}MB  "
          f"프레임 {len(frames)} (실경과 {time.monotonic() - t0:.0f}s)")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--url-match", default=r"flow\.google\.com|labs\.google")
    ap.add_argument("--cdp", default=os.environ.get("BU_CDP_URL", "http://127.0.0.1:9222"))
    ap.add_argument("--fps", type=int, default=12)
    ap.add_argument("--quality", type=int, default=80)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=1440)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--new-window", default="",
                    help="이 URL 을 전용 창으로 열어 녹화(사용자 창을 건드리지 않는다)")
    ap.add_argument("--stdin", default="")
    ap.add_argument("--scroll-js", default="", help="녹화 중 실행할 JS 표현식 파일(--cmd 없을 때만)")
    ap.add_argument("--scroll-at", type=float, default=2.0)
    ap.add_argument("cmd", nargs="*")
    args = ap.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
