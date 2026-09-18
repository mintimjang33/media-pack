"""Flow 생성물 내려받기 — 캔버스/미디어 패널의 이미지·영상을 로컬 파일로.

★인증 URL 이라 `http_get`(쿠키 없는 순수 HTTP)으로는 못 받는다.
  페이지 **안에서** fetch → dataURL → 청크로 빼온다.

실행:
  FLOW_OUT=<저장경로> BU_CDP_URL=http://127.0.0.1:9222 PYTHONUTF8=1 \\
    browser-harness < scripts/flow_fetch_media.py
"""
import base64
import json
import os
import time
import urllib.request
from pathlib import Path

# ★이 파일은 browser-harness 가 전역(js·cdp…)을 주입한 상태로 실행된다.
#   그냥 `python` 으로 돌리면 NameError 만 떠서 받는 사람이 원인을 못 찾는다.
if "js" not in globals():
    raise SystemExit(
        "★이 파일은 단독 실행용이 아닙니다 — browser-harness 에 흘려 넣어야 합니다.\n"
        "  FLOW_OUT=<저장경로> BU_CDP_URL=http://127.0.0.1:9222 PYTHONUTF8=1 "
        "browser-harness < scripts/flow_fetch_media.py"
    )

OUT = Path(os.environ.get("FLOW_OUT", "work/_tmp/flow_media"))
MAX = int(os.environ.get("FLOW_MAX", "999"))  # 최신 N개만 — 전량 회수는 느리다
KIND = os.environ.get("FLOW_KIND", "")        # img|video 로 한정


def log(*a):
    print(*a, flush=True)


def _js(e):
    r = js(e)
    return r.get("result", r.get("value", r)) if isinstance(r, dict) else r


tabs = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json/list", timeout=8).read())
pages = [t for t in tabs if t.get("type") == "page" and
         ("labs.google" in (t.get("url") or "") or "flow.google.com" in (t.get("url") or ""))]
urllib.request.urlopen(f"http://127.0.0.1:9222/json/activate/{pages[-1]['id']}", timeout=8).read()
time.sleep(2)

# ── 생성물 목록. ★Flow 생성물은 파일명이 없다 — `media.getMediaUrlRedirect` 로 판정한다.
items = json.loads(_js(r"""(()=>{const out=[];
  [...document.querySelectorAll('img')].forEach(e=>{
    const s=e.src||''; if(!/getMediaUrlRedirect|\/asb\/|flow-content\.google/.test(s)) return;
    out.push({kind:'img', src:s, nw:e.naturalWidth, nh:e.naturalHeight});});
  [...document.querySelectorAll('video')].forEach(e=>{
    const s=e.src||e.currentSrc||''; if(!/getMediaUrlRedirect|\/asb\/|flow-content\.google/.test(s)) return;
    out.push({kind:'video', src:s, nw:e.videoWidth, nh:e.videoHeight});});
  const seen=new Set(); return JSON.stringify(out.filter(o=>{
    if(seen.has(o.src)) return false; seen.add(o.src); return true;}));})()"""))
if KIND:
    items = [i for i in items if i["kind"] == KIND]
items = items[:MAX]
log(f"회수 대상 {len(items)}개")
for i, it in enumerate(items):
    log(f"  [{i}] {it['kind']} {it['nw']}x{it['nh']}")

OUT.mkdir(parents=True, exist_ok=True)
for i, it in enumerate(items):
    # 페이지 안에서 받아 dataURL 로 (인증 쿠키가 붙는다)
    _js("""(async()=>{window.__dl='';const r=await fetch(%s);const b=await r.blob();
      window.__mime=b.type;
      window.__dl=await new Promise(res=>{const f=new FileReader();
        f.onload=()=>res(f.result.split(',')[1]); f.readAsDataURL(b);});
      return window.__dl.length;})()""" % json.dumps(it["src"]))
    for _ in range(40):
        n = _js("(()=>window.__dl?window.__dl.length:0)()")
        try:
            n = int(n)
        except Exception:
            n = 0
        if n:
            break
        time.sleep(1)
    mime = str(_js("(()=>window.__mime||'')()"))
    ext = {"image/png": ".png", "image/jpeg": ".jpg", "video/mp4": ".mp4"}.get(mime, ".bin")
    # 청크로 빼온다(한 번에 큰 문자열을 반환시키면 CDP 가 끊는 전례가 있다)
    CH = 200_000
    buf = []
    for off in range(0, n, CH):
        buf.append(str(_js(f"(()=>window.__dl.slice({off},{off+CH}))()")))
    s = "".join(buf)
    f = OUT / f"flow_{it['kind']}_{i:02d}{ext}"
    f.write_bytes(base64.b64decode(s))
    log(f"  → {f}  {f.stat().st_size//1024}KB  ({mime}, {it['nw']}x{it['nh']})")

_js("(()=>{window.__dl='';return 1;})()")
log("완료")
