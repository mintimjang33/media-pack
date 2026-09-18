"""신 UI(flow.google.com) 에서 생성된 **영상**을 로컬로 받는다. browser-harness 스크립트.

    FLOW_OUT=<폴더> BU_CDP_URL=http://127.0.0.1:9223 PYTHONUTF8=1 \\
      browser-harness < scripts/flow_download_clips.py

★신 UI 는 그리드에 `<video>` 를 두지 않는다(썸네일 img + play 배지뿐). 상세를 열어도
  video 요소가 없어서 `flow_fetch_media.py` 의 "페이지 안 fetch" 방식이 안 통한다.
  대신 상세 화면의 **다운로드 버튼**을 쓰고, 저장 위치는 CDP 로 못박는다
  (`Page.setDownloadBehavior` — 브라우저 기본 다운로드 폴더로 흩어지지 않게).
"""
import json
import os
import time
from pathlib import Path

# ★browser-harness 가 전역(js·cdp·page_info…)을 주입한 상태로 실행된다.
#   그냥 `python` 으로 돌리면 NameError 만 떠서 원인을 못 찾는다.
if "cdp" not in globals():
    raise SystemExit(
        "★이 파일은 단독 실행용이 아닙니다 — browser-harness 에 흘려 넣어야 합니다.\n"
        "  FLOW_OUT=<폴더> BU_CDP_URL=http://127.0.0.1:9223 PYTHONUTF8=1 "
        "browser-harness < scripts/flow_download_clips.py"
    )

OUT = Path(os.environ.get("FLOW_OUT", "work/_tmp/flow_clips")).resolve()
OUT.mkdir(parents=True, exist_ok=True)


def log(*a):
    print(*a, flush=True)


def _js(e):
    r = js(e)
    return r.get("result", r.get("value", r)) if isinstance(r, dict) else r


def real_click(x, y):
    cdp("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y, buttons=0); time.sleep(.15)
    cdp("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", buttons=1, clickCount=1); time.sleep(.08)
    cdp("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", buttons=0, clickCount=1)


CARDS = r"""(()=>{const out=[];
  document.querySelectorAll('*').forEach(e=>{
    if(!e.offsetParent || !e.querySelector('img')) return;
    const r=e.getBoundingClientRect();
    if(r.width<100||r.width>320||r.height<150) return;
    const t=(e.innerText||'').trim();
    if(!t || t.length>70) return;
    if(!/play_circle|play_arrow/.test(t)) return;
    out.push({t:t.replace(/play_circle|play_arrow/g,'').trim(),
              x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2),
              top:Math.round(r.top), left:Math.round(r.left)});});
  const seen=new Set();
  return JSON.stringify(out.filter(o=>{const k=o.top+'_'+o.left;
    if(seen.has(k))return false; seen.add(k); return true;})
    .sort((a,b)=>a.top-b.top||a.left-b.left));})()"""

DL_BTN = r"""(()=>{const c=[...document.querySelectorAll('button,[role=button]')]
  .filter(e=>e.offsetParent &&
    /download|다운로드/.test((e.innerText||'')+(e.getAttribute('aria-label')||'')));
  if(!c.length) return 'null'; const r=c[0].getBoundingClientRect();
  return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)});})()"""

BACK = r"""(()=>{const NL=String.fromCharCode(10);
  const c=[...document.querySelectorAll('button,[role=button]')]
   .filter(e=>e.offsetParent && /arrow_back|뒤로|완료/.test(e.innerText||''))
   .map(e=>{const r=e.getBoundingClientRect();
     return {t:(e.innerText||'').split(NL).pop().trim(),
             x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)};});
  return JSON.stringify(c);})()"""

# ★저장 위치를 CDP 로 못박는다 — 안 하면 브라우저 기본 폴더로 흩어진다.
# 상세 화면이 열려 있으면 카드가 안 보인다 — 프로젝트 화면으로 먼저 돌아간다.
PROJ = os.environ.get("FLOW_PROJECT_URL", "")
# ★단순 포함 검사로는 안 된다 — 편집 화면 URL 이 프로젝트 URL 을 **접두어로 포함**한다
#   (/project/<id>/edit/<media>). 그러면 "이미 도착"으로 오판해 카드가 0개가 된다.
_cur = page_info().get("url") or ""
if PROJ and _cur.rstrip("/") != PROJ.rstrip("/"):
    goto_url(PROJ); time.sleep(11)

cdp("Page.setDownloadBehavior", behavior="allow", downloadPath=str(OUT))
log("저장 위치:", OUT)

cards = json.loads(_js(CARDS))
log(f"영상 카드 {len(cards)}개")
for c in cards:
    log("  ·", c["t"][:44])

before = {p.name for p in OUT.iterdir()}
got = []
_have = " ".join(before)
for i, c in enumerate(cards, 1):
    key = c["t"].rstrip("… ").replace(" ", "_")[:26]
    if key and key in _have:
        log(f"[{i}/{len(cards)}] {c['t'][:40]} — 이미 받음, 건너뜀")
        continue
    log(f"[{i}/{len(cards)}] {c['t'][:40]}")
    # 카드는 상세를 닫으면 위치가 되돌아온다 — 매번 다시 읽는다.
    cur = json.loads(_js(CARDS))
    hit = next((x for x in cur if x["t"] == c["t"]), None)
    if hit is None:
        log("  ✗ 카드 소실"); continue
    real_click(hit["x"], hit["y"]); time.sleep(4)
    b = _js(DL_BTN)
    if b == "null":
        log("  ✗ 다운로드 버튼 없음")
    else:
        p = json.loads(b)
        real_click(p["x"], p["y"])
        # 새 파일이 생길 때까지
        for _ in range(90):
            time.sleep(2)
            now = {q.name for q in OUT.iterdir()}
            new = [n for n in (now - before) if not n.endswith(".crdownload")]
            if new:
                got.append((c["t"], new[0]))
                before = now
                log(f"  ✓ {new[0]}")
                break
        else:
            log("  ✗ 파일이 안 생김")
    # ★'뒤로' 클릭은 그리드로 안정적으로 돌아오지 않는다 — 다음 카드 클릭이 통째로 헛돈다.
    #   프로젝트 URL 로 **강제 복귀**한다(느리지만 확실하다).
    if PROJ:
        goto_url(PROJ); time.sleep(9)
    else:
        for bk in json.loads(_js(BACK)):
            if bk["t"] in ("뒤로", "완료"):
                real_click(bk["x"], bk["y"]); time.sleep(2.5); break

log("")
log(f"받은 파일 {len(got)}개")
for t, n in got:
    log(f"  {n}  ← {t[:44]}")
(OUT / "_map.json").write_text(json.dumps(
    [{"title": t, "file": n} for t, n in got], ensure_ascii=False, indent=1), encoding="utf-8")
