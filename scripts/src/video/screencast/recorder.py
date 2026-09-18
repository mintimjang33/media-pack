"""스크린캐스트 녹화 코어.

로그인된 디버그 크롬(CDP 9222)에 붙어 recipe 대로 브라우저를 조작하면서
Page.startScreencast 로 뷰포트를 프레임 스트림으로 받아 CFR mp4 로 인코드하고,
클릭·타이핑·네비 타임스탬프를 actions.json 으로 남긴다(Remotion 자동 줌 입력).

의존: websockets(자체 최소 CDP 클라이언트), ffmpeg(image2pipe), PIL 불필요.
설계: docs/superpowers/specs/2026-07-14-screencast-tool-and-persona-autopost-video-design.md
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import requests
import websockets

CURSOR_JS = (Path(__file__).parent / "cursor.js").read_text(encoding="utf-8")
DEFAULT_CDP = os.environ.get("BU_CDP_URL", "http://127.0.0.1:9222")

# clear 액션용 — 좌표(%d,%d) 아래의 contenteditable 을 찾아 ①내용 전체 선택 ②검증.
# 커서 오버레이(window.__cur) 는 pointer-events:none 이지만 마스크 박스가 위에 있을 수 있어
# elementFromPoint 결과에서 에디터를 closest() 로 거슬러 찾는다.
_CLEAR_SELECT_JS = """
(() => {
  const el = document.elementFromPoint(%d, %d);
  // ★ textarea·input 을 먼저 본다 — contenteditable 만 보던 시절엔 airy.so 처럼
  //   평범한 <textarea> 를 쓰는 페이지에서 '__NOEDITOR__' 로 죽었다(2026-08-20).
  const fld = el && el.closest('textarea,input[type="text"],input[type="search"],input:not([type])');
  if (fld) { fld.focus(); fld.select(); return 'OK'; }
  const ed = el && el.closest('[contenteditable="true"],[contenteditable=""]');
  if (!ed) return '__NOEDITOR__';
  ed.focus();
  const r = document.createRange();
  r.selectNodeContents(ed);
  const s = window.getSelection();
  s.removeAllRanges();
  s.addRange(r);
  return 'OK';
})()
"""

_CLEAR_VERIFY_JS = """
(() => {
  const el = document.elementFromPoint(%d, %d);
  const fld = el && el.closest('textarea,input[type="text"],input[type="search"],input:not([type])');
  if (fld) return fld.value.replace(/[\\s\\u200b\\ufeff]/g, '');
  const ed = el && el.closest('[contenteditable="true"],[contenteditable=""]');
  if (!ed) return '__NOEDITOR__';
  return ed.innerText.replace(/[\\s\\u200b\\ufeff]/g, '');
})()
"""


class CDP:
    """페이지 타깃 ws 에 직접 붙는 최소 async CDP 클라이언트(sessionId 불필요)."""

    def __init__(self, ws_url: str):
        self._ws_url = ws_url
        self._id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._handlers: dict[str, Any] = {}
        self._ws = None
        self._reader = None

    async def connect(self) -> None:
        self._ws = await websockets.connect(self._ws_url, max_size=None, ping_interval=None)
        self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                if "id" in msg and msg["id"] in self._pending:
                    fut = self._pending.pop(msg["id"])
                    if not fut.done():
                        fut.set_result(msg.get("result", {}))
                elif "method" in msg:
                    h = self._handlers.get(msg["method"])
                    if h:
                        h(msg.get("params", {}))
        except Exception:  # noqa: BLE001 — 소켓 종료 시 조용히
            pass

    def on(self, method: str, handler) -> None:
        self._handlers[method] = handler

    async def send(self, method: str, **params) -> dict:
        self._id += 1
        mid = self._id
        fut = asyncio.get_event_loop().create_future()
        self._pending[mid] = fut
        await self._ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        return await asyncio.wait_for(fut, timeout=30)

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
        if self._ws:
            await self._ws.close()


def _new_target(cdp_http: str, url: str = "about:blank") -> tuple[str, str]:
    """새 탭 생성 → (targetId, webSocketDebuggerUrl). 신버전 크롬은 PUT 필요."""
    r = requests.put(f"{cdp_http}/json/new?{url}", timeout=10)
    if r.status_code >= 400:
        r = requests.get(f"{cdp_http}/json/new?{url}", timeout=10)
    r.raise_for_status()
    j = r.json()
    return j["id"], j["webSocketDebuggerUrl"]


def _activate_target(cdp_http: str, target_id: str) -> None:
    """녹화 탭을 앞으로 올린다.

    ★없으면 조용히 저품질로 찍힌다 — 크롬은 숨은 탭의 컴포지터 프레임을 만들지 않아
    startScreencast 가 초당 4장쯤만 흘린다(2026-08-26 실측: 27초에 110프레임, 스크롤이
    뚝뚝 끊김). 실패해도 녹화 자체는 되므로 예외는 삼킨다.
    """
    with contextlib.suppress(Exception):
        requests.get(f"{cdp_http}/json/activate/{target_id}", timeout=10)


def _close_target(cdp_http: str, target_id: str) -> None:
    try:
        requests.get(f"{cdp_http}/json/close/{target_id}", timeout=10)
    except Exception:  # noqa: BLE001
        pass


def _ease_in_out_quad(p: float) -> float:
    """cursor.js / Screencast.tsx 와 동일식 — hover 이벤트·후처리 커서가 같은 궤적을 탄다."""
    return 2 * p * p if p < 0.5 else 1 - ((-2 * p + 2) ** 2) / 2


class Recorder:
    def __init__(self, cdp: CDP, w: int, h: int, scale: int = 2, cursor_mode: str = "post"):
        self.cdp = cdp
        self.w, self.h = w, h  # 논리(CSS) 뷰포트 — 클릭 좌표·actions.json·videoW/H 의 기준(scale 무관)
        self.scale = scale     # deviceScaleFactor — 물리 캡처 배율(2=레티나, 유튜브 1080p 다운샘플용 텍스트 선명도)
        # "post"(기본) = 커서를 프레임에 굽지 않고 move 로그(fx/fy/ms)만 남김 → Remotion 이 60fps 로 그림
        #   (CDP screencast 실효 15~25fps 에 커서가 갇히는 문제 해소 — Screen Studio 방식).
        # "baked" = 구방식(cursor.js 오버레이가 녹화에 잡힘) — Remotion 을 안 거치는 소비처용 폴백.
        self.cursor_mode = cursor_mode
        self.cx, self.cy = w // 2, h // 2  # 커서 현재 위치(post 모드 move 로그의 fx/fy)
        self.frames: list[tuple[float, bytes]] = []
        self.actions: list[dict] = []
        self.t0 = 0.0

    def _now(self) -> float:
        return time.monotonic() - self.t0

    def _log(self, kind: str, **extra) -> None:
        self.actions.append({"t": round(self._now(), 3), "type": kind, **extra})

    async def setup(self) -> None:
        await self.cdp.send("Page.enable")
        await self.cdp.send("Runtime.enable")
        await self.cdp.send(
            "Emulation.setDeviceMetricsOverride",
            width=self.w, height=self.h, deviceScaleFactor=self.scale, mobile=False,
        )
        # 모든 새 문서에 커서 오버레이 자동 주입 + 현재 문서에도 즉시.
        # post 모드는 __CUR_HIDE 플래그로 점(dot)·파문을 숨기고 mask() 만 살린다.
        src = ("window.__CUR_HIDE=1;" if self.cursor_mode == "post" else "") + CURSOR_JS
        await self.cdp.send("Page.addScriptToEvaluateOnNewDocument", source=src)
        await self.cdp.send("Runtime.evaluate", expression=src)

    def _on_frame(self, params: dict) -> None:
        self.frames.append((time.monotonic(), base64.b64decode(params["data"])))
        asyncio.create_task(self._ack(params.get("sessionId")))

    async def _ack(self, sid) -> None:
        try:
            await self.cdp.send("Page.screencastFrameAck", sessionId=sid)
        except Exception:  # noqa: BLE001
            pass

    async def start(self) -> None:
        self.cdp.on("Page.screencastFrame", self._on_frame)
        self.t0 = time.monotonic()
        await self.cdp.send(
            "Page.startScreencast",
            # 물리 캡처 = 논리×scale(레티나) → 유튜브 1080p 다운샘플 시 텍스트 선명. quality=92(화면 글자 ringing 억제).
            format="jpeg", quality=92,
            maxWidth=self.w * self.scale, maxHeight=self.h * self.scale, everyNthFrame=1,
        )

    async def stop(self) -> None:
        await self.cdp.send("Page.stopScreencast")
        await asyncio.sleep(0.2)  # 마지막 프레임 수신 여유

    async def _eval(self, expr: str, await_promise: bool = False) -> None:
        await self.cdp.send("Runtime.evaluate", expression=expr, awaitPromise=await_promise)

    async def _eval_value(self, expr: str):
        """Runtime.evaluate 결과값을 돌려받는다(clear 검증용).

        ⚠️ JS 예외·결과 누락을 조용히 None 으로 돌려주면 검증이 통과해버린다
        (검증기가 무음 실패를 만드는 셈) — 그 경우 반드시 raise.
        """
        r = await self.cdp.send("Runtime.evaluate", expression=expr, returnByValue=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(f"eval 실패: {str(r['exceptionDetails'])[:160]}")
        res = r.get("result") or {}
        if "value" not in res:
            raise RuntimeError(f"eval 결과 없음: {str(res)[:120]}")
        return res["value"]

    async def _reinject_cursor(self) -> None:
        await self._eval(("window.__CUR_HIDE=1;" if self.cursor_mode == "post" else "") + CURSOR_JS)

    async def _move_to(self, x: int, y: int, ms: int) -> None:
        """커서를 (x,y)로 이동. post=경로를 따라 실제 mouseMoved 디스패치(hover 반응이 녹화에 잡힘)
        + move 로그(t=시작, fx/fy/ms). baked=cursor.js JS 애니메이션(구방식)."""
        if self.cursor_mode == "post":
            self._log("move", fx=self.cx, fy=self.cy, x=x, y=y, ms=ms)
            entry = self.actions[-1]
            wall0 = time.monotonic()
            n = max(2, int(ms / 60))
            for i in range(1, n + 1):
                e = _ease_in_out_quad(i / n)
                mx = self.cx + (x - self.cx) * e
                my = self.cy + (y - self.cy) * e
                await self.cdp.send("Input.dispatchMouseEvent", type="mouseMoved", x=int(mx), y=int(my))
                await asyncio.sleep(ms / 1000 / n)
            # CDP 왕복 지연으로 실이동이 명목 ms 보다 길다 — Remotion 커서가 화면 hover 와
            # 같은 속도로 움직이게 실측값을 backfill(적대리뷰 R1).
            entry["ms"] = int((time.monotonic() - wall0) * 1000)
        else:
            await self._eval(f"window.__cur&&window.__cur.move({x},{y},{ms})", True)
            self._log("move", x=x, y=y)
        self.cx, self.cy = x, y

    async def run_step(self, step: dict, default_mask: list[dict]) -> None:
        act = step.get("action")
        if act == "goto":
            self._log("nav", url=step["url"])
            await self.cdp.send("Page.navigate", url=step["url"])
            await asyncio.sleep(step.get("settle", 1.2))
            await self._reinject_cursor()
            if default_mask:
                await self._eval(f"window.__cur&&window.__cur.mask({json.dumps(default_mask)})")
        elif act == "wait":
            await asyncio.sleep(step["ms"] / 1000)
        elif act == "move":
            x, y = int(step["x"] * self.w), int(step["y"] * self.h)
            await self._move_to(x, y, step.get("ms", 500))
        elif act == "click":
            x, y = int(step["x"] * self.w), int(step["y"] * self.h)
            await self._move_to(x, y, step.get("ms", 500))
            await self.cdp.send("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
            await self.cdp.send("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1)
            await self.cdp.send("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", clickCount=1)
            if self.cursor_mode != "post":
                await self._eval("window.__cur&&window.__cur.click()")
            self._log("click", x=x, y=y)
            await asyncio.sleep(step.get("after", 0.4))
        elif act == "click_label":
            # 좌표 대신 **접근성 레이블**로 누른다.
            # ★좌표 클릭은 뷰포트·패널 상태가 조금만 달라져도 빗나가는데, 화면은 그냥
            #   "안 눌린 채로" 찍힌다 — 실패가 조용하다(2026-09-01~02 실측: DATA LAYERS
            #   패널 y 가 313→213 으로 달라져 레이어가 안 켜진 클립을 그대로 썼다).
            #   못 찾으면 여기서 예외를 던져 **녹화를 실패시킨다**.
            label = step["label"]
            found = await self._eval_value(
                "(() => {"
                f"  const want = {json.dumps(label)};"
                "   const hit = [...document.querySelectorAll('button,[role=button],a')]"
                "     .filter(e => ((e.getAttribute('aria-label') || e.textContent || '').trim())"
                "       .toLowerCase().includes(want.toLowerCase()))"
                "     .filter(e => { const r = e.getBoundingClientRect();"
                "       return r.width > 0 && r.height > 0; });"
                "   if (!hit.length) return null;"
                "   hit[0].scrollIntoView({block:'center'});"
                "   const r = hit[0].getBoundingClientRect();"
                "   return [Math.round(r.x + r.width/2), Math.round(r.y + r.height/2)];"
                "})()"
            )
            if not found and not step.get("optional"):
                raise RuntimeError(f"click_label: {label!r} 에 맞는 보이는 요소가 없다")
            if not found:
                # optional 은 없으면 그냥 지나간다(패널이 이미 펴져 있는 경우 등)
                self._log("click_label_skip", label=label)
            else:
                x, y = int(found[0]), int(found[1])
                await self._move_to(x, y, step.get("ms", 500))
                await self.cdp.send("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
                await self.cdp.send("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1)
                await self.cdp.send("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", clickCount=1)
                if self.cursor_mode != "post":
                    await self._eval("window.__cur&&window.__cur.click()")
                self._log("click_label", label=label, x=x, y=y)
                await asyncio.sleep(step.get("after", 0.4))
        elif act == "clear":
            # (x,y) 지점의 contenteditable 입력창을 비운다. claude.ai 등은 입력 draft 를
            # 프로필에 영속시켜 레코더의 **새 탭에도 복원**된다 — 비우지 않고 type 하면
            # 기존 draft 와 섞여 중복 입력된다(2026-07-17 실측: 클립 통째로 못 씀).
            # ⚠️ Input.dispatchKeyEvent(commands=["selectAll"]) 는 이 경로(page target 직결)에선
            # 안 먹는다. DOM Range 로 요소 내용을 통째 선택한 뒤 Backspace 한 번이 확실하다
            # (트리플클릭은 1문단만 잡아 문단 수에 따라 샌다).
            x, y = int(step["x"] * self.w), int(step["y"] * self.h)
            await self._move_to(x, y, step.get("ms", 400))
            await self.cdp.send("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
            await self.cdp.send("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y,
                                button="left", clickCount=1)
            await self.cdp.send("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y,
                                button="left", clickCount=1)
            await asyncio.sleep(0.2)
            # 좌표로 클릭한 그 요소를 그대로 잡아 선택한다(셀렉터 추측 금지 — 다른 빈
            # contenteditable 을 검증해 통과하는 오판 차단).
            picked = await self._eval_value(_CLEAR_SELECT_JS % (x, y))
            if picked != "OK":
                raise RuntimeError(f"clear 실패 — ({x},{y})에 contenteditable 없음: {picked!r}")
            await self.cdp.send("Input.dispatchKeyEvent", type="rawKeyDown", key="Backspace",
                                code="Backspace", windowsVirtualKeyCode=8, nativeVirtualKeyCode=8)
            await self.cdp.send("Input.dispatchKeyEvent", type="keyUp", key="Backspace",
                                code="Backspace", windowsVirtualKeyCode=8, nativeVirtualKeyCode=8)
            await asyncio.sleep(0.35)
            # 무음 실패 차단: 안 비워졌으면 여기서 죽는다(중복 입력 클립이 렌더까지 가는 것보다 낫다).
            left = await self._eval_value(_CLEAR_VERIFY_JS % (x, y))
            if left != "":
                raise RuntimeError(f"clear 실패 — draft 잔존: {str(left)[:60]!r}")
            self._log("clear", x=x, y=y)
            await asyncio.sleep(step.get("after", 0.3))
        elif act == "type":
            self._log("type", text=step["text"])
            for ch in step["text"]:
                await self.cdp.send("Input.insertText", text=ch)
                await asyncio.sleep(step.get("cps", 0.04))
        elif act == "scroll":
            # 이징 분할 휠 — dy 를 ~60ms 간격 소량 델타로 나눠 부드러운 스크롤이 프레임에
            # 잡힌다(한 방 점프 제거, Screen Studio 룩). 휠 위치=현재 커서(정중앙 아님).
            dy = step.get("dy", 300)
            ms = int(step.get("ms", 700))
            x, y = int(self.cx), int(self.cy)
            self._log("scroll", dy=dy, ms=ms)
            n = max(2, int(ms / 60))
            sent = 0.0
            for i in range(1, n + 1):
                target = dy * _ease_in_out_quad(i / n)
                await self.cdp.send("Input.dispatchMouseEvent", type="mouseWheel", x=x, y=y, deltaX=0, deltaY=target - sent)
                sent = target
                await asyncio.sleep(ms / 1000 / n)
            await asyncio.sleep(step.get("after", 0.3))
        elif act == "drag":
            # 버튼을 누른 채 (x,y)→(tx,ty) 로 끈다. force-directed 그래프처럼 드래그에
            # 반응하는 캔버스용 — click 은 press/release 가 붙어 있어 끌리지 않는다.
            # 커서 오버레이는 move 로그를 그대로 쓴다(press/release 는 클릭 파문으로).
            x, y = int(step["x"] * self.w), int(step["y"] * self.h)
            # path=[[x,y],...] 는 경유점(원운동 등), 없으면 tx/ty 단일 목적지.
            way = step.get("path") or [[step["tx"], step["ty"]]]
            ms = int(step.get("ms", 900))
            await self._move_to(x, y, step.get("approach", 500))
            await self.cdp.send("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y,
                                button="left", clickCount=1)
            leg = max(60, int(ms / len(way)))
            for wx, wy in way:
                tx, ty = int(wx * self.w), int(wy * self.h)
                self._log("move", fx=self.cx, fy=self.cy, x=tx, y=ty, ms=leg)
                entry = self.actions[-1]
                wall0 = time.monotonic()
                n = max(2, int(leg / 60))
                sx, sy = self.cx, self.cy
                for i in range(1, n + 1):
                    e = _ease_in_out_quad(i / n)
                    await self.cdp.send("Input.dispatchMouseEvent", type="mouseMoved",
                                        x=int(sx + (tx - sx) * e), y=int(sy + (ty - sy) * e),
                                        button="left", buttons=1)
                    await asyncio.sleep(leg / 1000 / n)
                entry["ms"] = int((time.monotonic() - wall0) * 1000)
                self.cx, self.cy = tx, ty
            tx, ty = self.cx, self.cy
            # 놓기 전 잠깐 붙잡고 있으면 스프링이 따라붙는 게 프레임에 잡힌다.
            await asyncio.sleep(step.get("hold", 0.3))
            await self.cdp.send("Input.dispatchMouseEvent", type="mouseReleased", x=tx, y=ty,
                                button="left", clickCount=1)
            # 놓은 뒤 흔들리는 구간이 이 컷의 본체다 — after 를 넉넉히.
            await asyncio.sleep(step.get("after", 1.2))
        elif act == "key":
            await self.cdp.send("Input.dispatchKeyEvent", type="rawKeyDown", key=step["key"])
            await self.cdp.send("Input.dispatchKeyEvent", type="keyUp", key=step["key"])
        elif act == "mask":
            rects = step.get("rects", [])
            await self._eval(f"window.__cur&&window.__cur.mask({json.dumps(rects)})")


def _encode_cfr(frames: list[tuple[float, bytes]], fps: int, out: Path) -> float:
    """(mono_ts, jpeg) 프레임을 CFR mp4 로 인코드. 각 틱마다 last-known frame 을 샘플."""
    if len(frames) < 2:
        raise RuntimeError(f"프레임 부족({len(frames)}) — 화면 변화가 없었을 수 있음")
    t0 = frames[0][0]
    ts = [f[0] - t0 for f in frames]
    dur = ts[-1]
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    proc = subprocess.Popen(
        # image2pipe 는 프레임 해상도가 섞이면 libx264 가 죽는다(네비게이션 중 크기 변동 등).
        # -vf scale 로 첫 프레임 기준 강제 통일 + 짝수 보정.
        [ffmpeg, "-y", "-f", "image2pipe", "-framerate", str(fps), "-i", "-",
         "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
         # crf 18 = 시각적 준무손실(화면 텍스트 보존). 최종 Remotion h264 재인코드 대비 세대손실 여유.
         "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    # ★stderr 를 쓰는 동안 계속 비워야 한다 — 진행률 로그가 파이프(64KB)를 채우면 ffmpeg 이
    #   stderr 쓰기에서 멈추고 stdin 을 안 읽어 교착한다(2026-08-26 실사고: 27초/810프레임에서
    #   raw.mp4 48바이트로 무한 대기). 짧은 녹화는 로그가 적어 우연히 통과한다.
    sink: list[bytes] = []
    drain = threading.Thread(target=lambda: sink.append(proc.stderr.read()), daemon=True)
    drain.start()
    n = max(1, int(dur * fps))
    j = 0
    try:
        for i in range(n):
            tick = i / fps
            while j + 1 < len(ts) and ts[j + 1] <= tick:
                j += 1
            proc.stdin.write(frames[j][1])
        proc.stdin.close()
    except BrokenPipeError:
        pass
    proc.wait()
    drain.join(timeout=5)
    err = sink[0] if sink else b""
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        tail = (err or b"").decode("utf-8", "replace")[-800:]
        raise RuntimeError(f"ffmpeg 인코드 실패(rc={proc.returncode}): {tail}")
    return dur


async def _record(recipe: dict, out_dir: Path, cdp_http: str) -> dict:
    w, h = recipe.get("viewport", [1280, 800])
    fps = recipe.get("fps", 30)
    scale = int(recipe.get("deviceScaleFactor", 2))  # 기본 레티나(2×). 성능 이슈 시 recipe 로 1 지정.
    cursor_mode = recipe.get("cursor", "post")  # "post"(Remotion 커서) | "baked"(구방식)
    default_mask = recipe.get("mask", [])
    target_id, ws_url = _new_target(cdp_http)
    _activate_target(cdp_http, target_id)
    cdp = CDP(ws_url)
    try:
        await cdp.connect()
        rec = Recorder(cdp, w, h, scale, cursor_mode)
        await rec.setup()
        if default_mask:
            await rec._eval(f"window.__cur&&window.__cur.mask({json.dumps(default_mask)})")
        await rec.start()
        for step in recipe.get("steps", []):
            await rec.run_step(step, default_mask)
        await asyncio.sleep(0.4)
        await rec.stop()
    finally:
        await cdp.close()
        _close_target(cdp_http, target_id)

    out_dir.mkdir(parents=True, exist_ok=True)
    raw = out_dir / "raw.mp4"
    dur = _encode_cfr(rec.frames, fps, raw)
    # actions t 는 startScreencast 전송 시각(t0) 기준인데 영상 t=0 은 첫 프레임 "도착" 시각 —
    # 도착 지연(offset)만큼 빼서 재정렬해야 Remotion 오버레이(커서·줌·파문)가 화면과 정확히 맞고,
    # 마지막 액션이 영상 duration 을 넘겨 연출이 잘리는 무음 실패도 막는다(적대리뷰 R1).
    offset = rec.frames[0][0] - rec.t0
    for a in rec.actions:
        a["t"] = round(max(0.0, a["t"] - offset), 3)
    actions = {"fps": fps, "w": w, "h": h, "duration": round(dur, 3),
               "cursor": cursor_mode, "actions": rec.actions}
    (out_dir / "actions.json").write_text(json.dumps(actions, ensure_ascii=False, indent=2), encoding="utf-8")
    meta = {"frames": len(rec.frames), "duration": round(dur, 3), "raw": str(raw)}
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def record(recipe: dict, out_dir: Path, cdp_http: str = DEFAULT_CDP) -> dict:
    """동기 진입점. recipe(dict) → out_dir 에 raw.mp4 + actions.json + meta.json."""
    return asyncio.run(_record(recipe, Path(out_dir), cdp_http))
