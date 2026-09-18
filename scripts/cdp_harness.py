"""browser-harness 대체 실행기.

flow-media 팩(scripts/flow_media.py 등)은 원래 저자 개인 도구인 `browser-harness` CLI에
파이프로 흘려 넣어 실행하도록 짜여 있다. 우리에겐 그 도구가 없어서, 같은 계약
(js·cdp·capture_screenshot·goto_url·page_info 를 전역으로 주입해 스크립트를 exec)을
구현하는 동등한 실행기를 이 파일로 대신한다. Chrome 원격디버깅 포트(CDP)에
`httpx`(탭 목록)+`websockets`(동기 클라이언트)로 직접 붙는다 — 추가 설치 불필요.

사용법 (flow-media-pack 폴더에서):
    BU_CDP_URL=http://127.0.0.1:9223 FLOW_JOB=<프로젝트>/flow.json \
      FLOW_STAGE=images FLOW_LIMIT=1 PYTHONUTF8=1 \
      python scripts/cdp_harness.py scripts/flow_media.py

stdin 으로도 된다 (원본 관례와 동일):
    ... | python scripts/cdp_harness.py
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys

import httpx
from websockets.sync.client import connect as ws_connect


class CDP:
    def __init__(self, base_url: str, prefer: tuple[str, ...] = ("flow.google.com", "labs.google")):
        self.base_url = base_url.rstrip("/")
        try:
            targets = httpx.get(f"{self.base_url}/json", timeout=10).json()
        except httpx.HTTPError as e:
            raise RuntimeError(
                f"★{self.base_url} 에 붙지 못했습니다 — 디버깅 포트를 연 Chrome이 떠 있는지 확인하세요.\n"
                f"  chrome --remote-debugging-port=9223 --user-data-dir=<전용프로필> "
                f"https://labs.google/fx/ko/tools/flow\n  원인: {e}"
            ) from e
        pages = [t for t in targets if t.get("type") == "page"]
        if not pages:
            raise RuntimeError("★연결 가능한 탭이 없습니다 — Chrome 창에 페이지가 하나는 떠 있어야 합니다.")
        target = next((t for t in pages if any(p in t.get("url", "") for p in prefer)), pages[0])
        self.target = target
        self.ws = ws_connect(target["webSocketDebuggerUrl"], max_size=None, open_timeout=15)
        self._id = 0
        self.send("Page.enable")
        self.send("Runtime.enable")

    # 2026-09-12 실사고 수정 — 9223 계정이 S52에서 90분 넘게 "waiting"인 채로 멈춰있었는데,
    # 실제로는 죽은 게 아니라 이 recv()가 타임아웃 없이 응답을 무한정 기다리고 있었다(Chrome
    # 탭이 멈추거나 CDP 프레임이 유실되면 이 소켓 읽기가 영원히 안 풀림) — 대시보드를 재시작해도
    # 이 프로세스 자체는 그 자리에서 계속 살아있었던 이유이기도 하다. RECV_TIMEOUT을 넘기면
    # 예외를 던져서, flow_econ_driver.py의 바깥 try/except가 "이 씬만 건너뛰고 다음으로"
    # 처리하도록 만든다 — 조용히 영원히 멈추는 대신 눈에 보이는 실패로 바꾼 것.
    RECV_TIMEOUT = 60

    def send(self, method: str, **params):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            try:
                raw = self.ws.recv(timeout=self.RECV_TIMEOUT)
            except TimeoutError as e:
                raise RuntimeError(
                    f"CDP {method} 응답 없음({self.RECV_TIMEOUT}초 초과) — Chrome 탭이 멈췄거나 "
                    f"연결이 끊겼을 수 있습니다."
                ) from e
            msg = json.loads(raw)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method} 실패: {msg['error']}")
                return msg.get("result", {})
            # id 없는 메시지는 이벤트 — 무시하고 다음 프레임을 기다린다

    # ── harness 가 주입해야 하는 5개 함수 ────────────────────────
    def js(self, expr: str):
        r = self.send("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(f"JS 오류: {r['exceptionDetails']}")
        return r.get("result", {}).get("value")

    def cdp(self, method: str, **params):
        return self.send(method, **params)

    def capture_screenshot(self, path: str, max_dim: int = 900):
        r = self.send("Page.captureScreenshot", format="png")
        data = base64.b64decode(r["data"])
        with open(path, "wb") as f:
            f.write(data)
        try:
            from PIL import Image

            im = Image.open(io.BytesIO(data))
            if max(im.size) > max_dim:
                ratio = max_dim / max(im.size)
                im = im.resize((max(1, int(im.width * ratio)), max(1, int(im.height * ratio))))
                im.save(path)
        except ImportError:
            pass  # Pillow 없으면 원본 해상도 그대로 둔다 — 콘택트시트 눈검수만 못 함

    def goto_url(self, url: str):
        self.send("Page.navigate", url=url)

    def page_info(self):
        return self.js("(()=>({url: location.href, title: document.title}))()") or {}

    # 2026-09-13 추가 — 영상 생성 시 씬 이미지를 "시작 프레임"으로 첨부하려면, Flow의 "업로드"
    # 기능(클릭하면 숨겨진 <input type=file>이 열려 OS 파일 선택창을 띄우는 방식)을 자동화로
    # 통과해야 한다. 일반적인 클릭 자동화로는 OS 네이티브 다이얼로그가 그대로 뜨어버려서(CDP로
    # 조작 불가) 여기서 멈춘다 — 크로미움 표준 해법은 Page.setInterceptFileChooserDialog를 켜서
    # 그 다이얼로그를 아예 안 띄우고 대신 Page.fileChooserOpened 이벤트로 backendNodeId를 받은
    # 뒤, DOM.setFileInputFiles로 그 input에 로컬 파일을 직접 꽂아 넣는 것 — 이 프로젝트에선
    # 파일 업로드 자동화가 이번이 처음이라 실기 검증 전이다.
    def enable_file_chooser_interception(self):
        self.send("Page.setInterceptFileChooserDialog", enabled=True)

    def wait_for_file_chooser(self, timeout: float = 10.0):
        """enable_file_chooser_interception() 켠 상태에서, 업로드 버튼을 누른 뒤 이 함수를
        불러 Page.fileChooserOpened 이벤트(파일 input의 backendNodeId 포함)를 기다린다.
        send()는 id 있는 응답만 처리하고 id 없는 이벤트 메시지는 버리므로, 이벤트를 직접
        받아야 하는 이 경우엔 send()를 안 거치고 소켓을 직접 읽는다."""
        import time as _time

        deadline = _time.time() + timeout
        while _time.time() < deadline:
            remaining = max(0.1, deadline - _time.time())
            try:
                raw = self.ws.recv(timeout=min(remaining, self.RECV_TIMEOUT))
            except TimeoutError:
                continue
            msg = json.loads(raw)
            if msg.get("method") == "Page.fileChooserOpened":
                return msg.get("params", {})
        raise RuntimeError("★파일 선택창(Page.fileChooserOpened) 이벤트를 못 받았다 — 업로드 버튼 클릭이 실제로 파일 input을 열었는지 확인 필요")

    def set_file_input_files(self, backend_node_id: int, file_paths: list[str]):
        self.send("DOM.setFileInputFiles", files=file_paths, backendNodeId=backend_node_id)


def main():
    cdp_url = os.environ.get("BU_CDP_URL", "http://127.0.0.1:9222")
    c = CDP(cdp_url)
    print(f"[harness] 연결됨 → {c.target.get('url')}", file=sys.stderr)

    if len(sys.argv) > 1:
        script_path = sys.argv[1]
        src = open(script_path, encoding="utf-8").read()
        src_name = script_path
    else:
        src = sys.stdin.read()
        src_name = "<stdin>"

    injected = {
        "js": c.js,
        "cdp": c.cdp,
        "capture_screenshot": c.capture_screenshot,
        "goto_url": c.goto_url,
        "page_info": c.page_info,
        "__name__": "__main__",
        "__file__": src_name,
    }
    exec(compile(src, src_name, "exec"), injected)


if __name__ == "__main__":
    main()
