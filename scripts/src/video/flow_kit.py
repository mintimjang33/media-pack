"""구글 Flow(labs.google/fx/tools/flow) 브라우저 자동화 공용 키트.

이미지(Nano Banana 2 · **0크레딧**)와 동영상(Omni · 4s=7·6s=10·8s=12·10s=15 크레딧)을
편·채널 무관하게 만든다. 실좌표·함정은 `scripts/config/flow_ui_map.json` 의 `notes` 가 SoT.

★browser-harness 스크립트에서 쓴다 — harness 가 런타임에 주입하는 전역(`js`·`cdp`·
  `capture_screenshot`·`goto_url`·`page_info`)은 import 만으로는 없다. `Flow(...)` 에 넘긴다.

    from src.video.flow_kit import Flow
    F = Flow(js=js, cdp=cdp, screenshot=capture_screenshot, goto=goto_url, page=page_info,
             shots=Path("...")/"_shots")

실측 함정(전부 "성공했다고 보고하던" 실패다):
 1) `offsetParent` 로 가시성을 판정하면 **position:fixed 요소가 사라진다**('새 프로젝트' 버튼).
 2) 팝오버가 프롬프트창을 덮는다 — 열린 채 클릭하면 패널이 먹고 입력이 조용히 증발한다.
 3) 프롬프트창은 Slate — 자리표시자가 textContent 에 남아 **길이로 판정하면 안 된다**.
 4) 입력창 비우기가 실패하면 **앞의 잔여 문구가 프롬프트를 지배한다**. 비었는지 확인 후 삽입.
 5) 참조는 프롬프트를 **먼저 넣고** 붙인다 — 비우기가 참조까지 지운다.
 6) 이미지 모드는 목록 항목 클릭만으로 첨부가 끝난다(확인 버튼이 사라진다). 동영상은 확인 버튼이 남는다.
 7) 피커의 **큰 썸네일**을 누르면 편집화면으로 나간다 — 거기서 제출하면 새 생성이 아니라 편집이다.
 8) 피커 위치 인덱스는 밀린다(선택 항목이 맨 앞으로) — 원본은 **제목**으로 고정한다.
 9) 완료 판정은 파일명이 아니라 `img/video[src*=getMediaUrlRedirect]` **고유 개수**.
10) 비율 칩은 새 프로젝트에서 `crop_9_16` 이 없다 — 하단바에서 **장수(x1~x4)** 를 단 버튼이 설정 칩이다.
11) 생성물엔 ✦ 워터마크가 박힌다(우하단 x 82~92%·y 88~95%). Google AI 표식이라 **지우지 않는다**.
12) 프롬프트에 **부정문을 쓰지 않는다** — "No text" 를 넣으면 오히려 글자를 그린다.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

UI_MAP = Path("scripts/config/flow_ui_map.json")
HOME = "https://flow.google.com/"   # ★로그인 후 labs.google/fx/... 는 여기로 리다이렉트된다
CREDITS = {4: 7, 6: 10, 8: 12, 10: 15}

# ── JS 조각. ★전부 raw 문자열 — 일반 문자열이면 \r \n \t 가 진짜 제어문자가 되어
#    JS 정규식 리터럴이 개행으로 쪼개진다("Invalid regular expression: missing /").
_VIS = r"""const VIS=e=>{const b=e.getBoundingClientRect();
  if(b.width<1||b.height<1) return false; const s=getComputedStyle(e);
  return s.display!=='none' && s.visibility!=='hidden' && s.opacity!=='0';};"""

_CHIP = r"""(()=>{const H=innerHeight;
  const b=[...document.querySelectorAll('button,[role=button]')]
   .filter(e=>{const r=e.getBoundingClientRect();
     return r.top>H*0.72 && r.width>40 &&
       /(^|\s)x[1-4](\s|$)/.test((e.innerText||'').replace(/[\r\n]+/g,' '));});
  if(!b.length) return 'null';
  b.sort((p,q)=>q.getBoundingClientRect().width-p.getBoundingClientRect().width);
  const r=b[0].getBoundingClientRect();
  return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2),
    state:b[0].getAttribute('data-state')||'',
    t:b[0].innerText.replace(/[\r\n\t ]+/g,' ').trim().slice(0,48)});})()"""

_ADD = r"""(()=>{const c=[...document.querySelectorAll('button,[role=button]')]
  .filter(e=>e.offsetParent && (e.innerText||'').trim()==='프롬프트에 추가');
  if(!c.length) return 'null'; const r=c[0].getBoundingClientRect();
  return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)});})()"""

_ARROW = r"""(()=>{const H=innerHeight;
  const c=[...document.querySelectorAll('button,[role=button]')]
   .filter(e=>{const r=e.getBoundingClientRect();
     return r.top>H*0.9 && /arrow_forward/.test(e.innerText||'');});
  if(!c.length) return 'null'; const r=c[0].getBoundingClientRect();
  return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)});})()"""

_PLUS = r"""(()=>{const H=innerHeight;
  const c=[...document.querySelectorAll('button,[role=button]')]
   .filter(e=>{const r=e.getBoundingClientRect();
     return r.top>H*0.9 && /add_2/.test(e.innerText||'');});
  if(!c.length) return 'null'; const r=c[0].getBoundingClientRect();
  return JSON.stringify({x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)});})()"""

_EDIT = r"""(()=>{const ed=[...document.querySelectorAll('[contenteditable="true"]')]
  .filter(e=>e.offsetParent)[0]; if(!ed) return 'null';
  const b=ed.getBoundingClientRect();
  return JSON.stringify({x:Math.round(b.left+b.width/2),y:Math.round(b.top+b.height/2)});})()"""

_TXT = r"""(()=>{const e=[...document.querySelectorAll('[contenteditable="true"]')]
  .filter(x=>x.offsetParent)[0]; return e?(e.textContent||'').trim():'';})()"""

_CLEAR = r"""(()=>{const e=[...document.querySelectorAll('[contenteditable="true"]')]
  .filter(x=>x.offsetParent)[0];
  if(e){e.focus();document.execCommand('selectAll');document.execCommand('delete');}return 1;})()"""

_FOCUS = r"""(()=>{const ed=[...document.querySelectorAll('[contenteditable="true"]')]
  .filter(e=>e.offsetParent)[0]; if(!ed) return 0; ed.focus();
  const rg=document.createRange(); rg.selectNodeContents(ed); rg.collapse(false);
  const s=getSelection(); s.removeAllRanges(); s.addRange(rg); return 1;})()"""

# ★피커 열림 판정 — 신 UI 엔 '미디어 업로드' 문구가 없다. 확정 버튼으로 본다.
_PICKER = r"""(()=>[...document.querySelectorAll('button,[role=button]')]
  .some(e=>e.offsetParent && (e.innerText||'').trim()==='프롬프트에 추가')?'open':'closed')()"""

# ★미디어 URL 패턴은 도메인 이전으로 바뀌었다.
#   구: media.getMediaUrlRedirect · 신: flow.google.com/asb/… · flow-content.google/…
#   옛 패턴만 세면 **생성이 끝났는데도 영원히 대기**한다(CLIP02 가 600초 태우고 실패했다).
_COUNT = r"""(()=>{const s=new Set();
  document.querySelectorAll('img,video').forEach(e=>{
    const u=e.src||e.currentSrc||'';
    if(/getMediaUrlRedirect|\/asb\/|flow-content\.google/.test(u)) s.add(u.split('?')[0]);});
  return s.size;})()"""

# ★첨부 판정은 **컴포저 안쪽**만 본다.
#   "하단 30% 의 작은 img" 같은 화면 위치 휴리스틱은 캔버스 썸네일을 같이 세어
#   빈 슬롯을 '채워짐'으로 읽는다(신 UI 에서 6개로 오판, 2026-09-04).
#   컴포저 = 프롬프트 입력창(contenteditable)을 품은 가장 가까운 큰 조상.
_THUMB = r"""(()=>{const ed=[...document.querySelectorAll('[contenteditable="true"]')]
  .filter(e=>e.offsetParent)[0];
  if(!ed) return 0;
  let box=ed;
  for(let k=0;k<8;k++){
    if(!box.parentElement) break;
    box=box.parentElement;
    const r=box.getBoundingClientRect();
    if(r.width>300 && r.height>70 && r.height<innerHeight*0.45) break;
  }
  return [...box.querySelectorAll('img')]
    .filter(e=>{const r=e.getBoundingClientRect();
      return r.width>=18 && r.width<=110 && r.height>=18;}).length;})()"""

# ★목록은 **컨테이너의 자식이 아니다** — 신 UI 에서 좌측 목록과 우측 미리보기가 형제라
#   확정 버튼에서 올라간 컨테이너 안엔 항목이 하나도 없다(실측 0개). 그래서 문서 전역에서
#   "썸네일 + 짧은 제목 + 목록 행 크기" 로 찾는다. 이 모양은 피커가 열려 있을 때만 존재한다.
_LIST = r"""(()=>{const out=[];
  document.querySelectorAll('*').forEach(e=>{
    if(!e.offsetParent) return;
    if(!e.querySelector('img')) return;
    const r=e.getBoundingClientRect();
    if(r.width<150||r.width>340||r.height<34||r.height>72) return;
    const t=(e.innerText||'').trim(); if(!t||t.length>70) return;
    out.push({t:t, x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2),
              top:Math.round(r.top)});});
  const seen=new Set();
  return JSON.stringify(out.filter(o=>{if(seen.has(o.top))return false;seen.add(o.top);return true;})
    .sort((a,b)=>a.top-b.top));})()"""

# 목록의 스크롤 컨테이너 — 항목의 조상 중 실제로 스크롤되는 것.
# 제목으로 항목을 찾아 **보이는 곳으로 끌어온 뒤** 중심 좌표를 돌려준다.
# ★목록 맨 아래 항목은 컨테이너 밖으로 나가 있어, 그 좌표를 그대로 누르면
#   피커 바깥을 눌러 그냥 닫힌다(실측 — CLIP02 가 계속 여기서 죽었다).
_INTO_VIEW = r"""(()=>{const want=%s;
  const c=[...document.querySelectorAll('*')].filter(e=>{
    if(!e.offsetParent || !e.querySelector('img')) return false;
    const r=e.getBoundingClientRect();
    return r.width>=150 && r.width<=340 && r.height>=34 && r.height<=72 &&
           (e.innerText||'').trim()===want;});
  if(!c.length) return 'null';
  c[0].scrollIntoView({block:'center'});
  return 'ok';})()"""

_RECT_OF = r"""(()=>{const want=%s;
  const c=[...document.querySelectorAll('*')].filter(e=>{
    if(!e.offsetParent || !e.querySelector('img')) return false;
    const r=e.getBoundingClientRect();
    return r.width>=150 && r.width<=340 && r.height>=34 && r.height<=72 &&
           (e.innerText||'').trim()===want;});
  if(!c.length) return 'null';
  const r=c[0].getBoundingClientRect();
  return JSON.stringify({x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2),
                         top:Math.round(r.top), bottom:Math.round(r.bottom)});})()"""

_SCROLL = r"""(()=>{const items=[...document.querySelectorAll('*')].filter(e=>{
    if(!e.offsetParent || !e.querySelector('img')) return false;
    const r=e.getBoundingClientRect();
    return r.width>=150 && r.width<=340 && r.height>=34 && r.height<=72 &&
           (e.innerText||'').trim().length<=70;});
  if(!items.length) return 'NF';
  let n=items[0];
  for(let k=0;k<8;k++){
    if(!n.parentElement) break; n=n.parentElement;
    if(n.scrollHeight>n.clientHeight+20){ n.scrollTop=%d; return 'ok'; }
  }
  return 'NOSCROLL';})()"""


class Flow:
    """Flow 한 프로젝트를 조작하는 얇은 래퍼. 상태는 호출자가 들고 있는다."""

    def __init__(self, js, cdp, screenshot=None, goto=None, page=None, shots: Path | None = None,
                 log=print):
        self._js_raw, self.cdp = js, cdp
        self._shot, self._goto, self._page = screenshot, goto, page
        self.shots = Path(shots) if shots else None
        if self.shots:
            self.shots.mkdir(parents=True, exist_ok=True)
        self.log = log

    # ── 저수준 ────────────────────────────────────────────────
    def js(self, expr):
        r = self._js_raw(expr)
        return r.get("result", r.get("value", r)) if isinstance(r, dict) else r

    def _json(self, expr, default=None):
        try:
            return json.loads(self.js(expr))
        except Exception:
            return default

    def click_xy(self, x, y):
        """★합성 MouseEvent 는 안 먹는다. CDP Input 만 통한다."""
        self.cdp("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y, buttons=0)
        time.sleep(0.15)
        self.cdp("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y,
                 button="left", buttons=1, clickCount=1)
        time.sleep(0.08)
        self.cdp("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y,
                 button="left", buttons=0, clickCount=1)

    def shot(self, tag):
        if self.shots and self._shot:
            try:
                self._shot(str(self.shots / f"{tag}.png"), max_dim=900)
            except Exception as e:
                self.log("  (스샷 실패)", e)

    def vh(self):
        try:
            return int(self.js("(()=>innerHeight)()"))
        except Exception:
            return 1304

    # ── 라벨 탐색 ─────────────────────────────────────────────
    def rects(self, label, maxlen=24):
        """마지막 줄이 label 과 일치하는 요소들의 중심 좌표(위→아래).

        ★버튼 라벨 앞에 아이콘 리가처가 붙는다(`videocam\\n동영상`) → 마지막 줄로 맞춘다.
        """
        js_src = (r"""(()=>{const NL=String.fromCharCode(10); const want=%s;""" + _VIS + r"""
          const c=[...document.querySelectorAll('button,[role=button],a,div,span')].filter(VIS)
           .map(e=>{const L=(e.innerText||'').trim().split(NL).map(s=>s.trim()).filter(Boolean);
                    return {e,last:L.length?L[L.length-1]:'',all:(e.innerText||'').trim()};})
           .filter(o=>o.last===want && o.all.length<=%d);
          const seen=new Set(), out=[]; c.sort((a,b)=>a.all.length-b.all.length);
          for(const o of c){const b=o.e.getBoundingClientRect();
            const k=Math.round(b.left)+','+Math.round(b.top);
            if(seen.has(k)||b.width<4) continue; seen.add(k);
            out.push({x:Math.round(b.left+b.width/2),y:Math.round(b.top+b.height/2)});}
          out.sort((a,b)=>a.y-b.y); return JSON.stringify(out);})()""")
        return self._json(js_src % (json.dumps(label), maxlen), []) or []

    def scroll_to(self, label, maxlen=24):
        js_src = (r"""(()=>{const NL=String.fromCharCode(10); const want=%s;""" + _VIS + r"""
          const c=[...document.querySelectorAll('button,[role=button],a,div,span')].filter(VIS)
           .map(e=>({e, all:(e.innerText||'').trim()}))
           .filter(o=>{const L=o.all.split(NL).map(s=>s.trim()).filter(Boolean);
                       return L.length && L[L.length-1]===want && o.all.length<=%d;});
          if(!c.length) return 'NF';
          c.sort((a,b)=>a.all.length-b.all.length);
          c[0].e.scrollIntoView({block:'center'}); return 'ok';})()""")
        return self.js(js_src % (json.dumps(label), maxlen))

    def click(self, label, ymin=0, pick=0, wait=1.8):
        """라벨 클릭. 화면 밖이면 스크롤로 끌어온다."""
        vh = self.vh()
        cs = [c for c in self.rects(label) if c["y"] >= ymin]
        off_screen = not cs or all(not (0 <= c["y"] <= vh) for c in cs)
        if off_screen and self.scroll_to(label) == "ok":
            time.sleep(1.2)
            cs = [c for c in self.rects(label) if c["y"] >= ymin]
        cs = [c for c in cs if 0 <= c["y"] <= vh]
        if not cs:
            self.log(f"  ✗ {label} 없음")
            return False
        c = cs[min(pick, len(cs) - 1)]
        self.click_xy(c["x"], c["y"])
        self.remember(label, c["x"], c["y"])
        time.sleep(wait)
        return True

    # ── 좌표 기록기 (성공한 클릭만 라벨별로 갱신) ──────────────
    def remember(self, label, x, y, note=""):
        try:
            m = json.loads(UI_MAP.read_text(encoding="utf-8")) if UI_MAP.exists() else {}
        except Exception:
            m = {}
        m.setdefault("verified_clicks", {})[label] = {
            "x": x, "y": y, "note": note, "ts": time.strftime("%Y-%m-%d %H:%M")}
        UI_MAP.parent.mkdir(parents=True, exist_ok=True)
        UI_MAP.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── 프로젝트 ──────────────────────────────────────────────
    def open_project(self, url=None):
        """url 이 있으면 그 프로젝트로, 없으면 **새 프로젝트**를 만들어 URL 을 돌려준다."""
        cur = (self._page() or {}).get("url", "") if self._page else ""
        if url:
            if url not in cur:
                self._goto(url)
                time.sleep(11)
            return url
        self._goto(HOME)
        time.sleep(13)
        # ★'새 프로젝트' 는 position:fixed 라 offsetParent 가 null — VIS 판정이어야 잡힌다.
        if not self.click("새 프로젝트", wait=10):
            raise RuntimeError("새 프로젝트 버튼을 못 찾았다")
        u = (self._page() or {}).get("url", "")
        if "/project/" not in u:
            raise RuntimeError(f"프로젝트 진입 실패: {u}")
        return u

    # ── 칩·팝오버·크레딧 ──────────────────────────────────────
    def chip(self):
        return self._json(_CHIP, {"t": "NF", "state": "", "x": 0, "y": 0}) or \
            {"t": "NF", "state": "", "x": 0, "y": 0}

    def wait_chip(self, tries=6):
        for _ in range(tries):
            if self.chip()["t"] != "NF":
                return True
            time.sleep(2.5)
        return False

    _OPEN = r"""(()=>{const H=innerHeight;
      const n=[...document.querySelectorAll('button,[role=button],[role=radio],[role=option]')]
       .filter(e=>{const r=e.getBoundingClientRect();
         return r.width>8 && r.height>8 && r.top>H*0.40 && r.top<H*0.93 &&
                /^x[34]$/.test((e.innerText||'').trim());}).length;
      return n;})()"""

    def popover_open(self):
        """★신 UI(flow.google.com)엔 `data-state` 도 '생성 시 N크레딧' 문구도 없다.
        칩 위쪽에 장수 옵션(x3·x4)이 보이면 열린 것으로 본다."""
        if self.chip().get("state") == "open":
            return True
        try:
            return int(self.js(self._OPEN)) > 0
        except Exception:
            return False

    def open_popover(self, tries=4):
        for _ in range(tries):
            if self.popover_open():
                return True
            c = self.chip()
            if c["t"] == "NF":
                time.sleep(2)
                continue
            self.click_xy(c["x"], c["y"])
            time.sleep(2.2)
        return self.popover_open()

    def close_popover(self):
        if self.popover_open():
            c = self.chip()
            self.click_xy(c["x"], c["y"])
            time.sleep(1.4)

    def credit(self):
        r = self.js(r"""(()=>{const m=(document.body.innerText||'').match(/생성 시\s*([0-9]+)\s*크레딧/);
                    return m?parseInt(m[1]):null;})()""")
        try:
            return int(r)
        except Exception:
            return None

    def dismiss(self):
        """열린 피커를 닫는다.

        ⛔Escape 는 안 먹는다. 빈 곳을 클릭해야 한다.
        ⛔**고정 좌표를 쓰지 말 것** — 창 크기가 달라지면 그 자리가 미디어 카드라
          누르는 순간 상세 편집화면으로 나간다(실측). 좌측 사이드바 아래쪽 빈 칸은
          어느 크기에서도 비어 있다.
        """
        for fx, fy in ((0.06, 0.55), (0.06, 0.70), (0.5, 0.02)):
            if self.js(_PICKER) == "closed":
                return True
            w = int(self.js("(()=>innerWidth)()") or 1180)
            h = self.vh()
            self.click_xy(int(w * fx), int(h * fy))
            time.sleep(1.6)
        return self.js(_PICKER) == "closed"

    # ── 모드 ──────────────────────────────────────────────────
    POP_Y = 860  # 팝오버는 화면 하단 — 피커의 같은 라벨과 구분하는 경계

    def set_image_mode(self, ratio="9:16", batch=None):
        self.dismiss()
        self.wait_chip()
        self.open_popover()
        if "Nano Banana" not in self.chip()["t"]:
            self.click("이미지", ymin=self.POP_Y)
            time.sleep(2)
            self.open_popover()
        if ratio and ratio not in self.chip()["t"].replace("crop_9_16", "9:16").replace("crop_16_9", "16:9"):
            self.click(ratio, ymin=self.POP_Y)
        if batch:
            self.click(f"x{int(batch)}", ymin=self.POP_Y)
        t = self.chip()["t"]
        self.close_popover()
        return t

    def set_video_mode(self, ratio="9:16", res="720p", seconds=4, frames=True, batch=1):
        """설정 후 **크레딧으로 검증**한다 — 기대값과 다르면 진행하지 않는다."""
        self.dismiss()
        self.wait_chip()
        self.open_popover()
        if "동영상" not in self.chip()["t"]:
            self.click("동영상", ymin=self.POP_Y)
            time.sleep(2)
            self.open_popover()
        # ★길이 라벨이 UI 마다 다르다 — 신 도메인은 "4초", 구 도메인은 "4s".
        labels = (["프레임"] if frames else []) + [ratio, res]
        for lab in labels:
            self.click(lab, ymin=self.POP_Y)
        if not self.click(f"{int(seconds)}초", ymin=self.POP_Y):
            self.click(f"{int(seconds)}s", ymin=self.POP_Y)
        # ★장수(x1~x4)를 반드시 정한다 — 새 프로필 기본이 x2 면 크레딧이 **두 배**로 나간다(실측 14).
        if batch:
            self.click(f"x{int(batch)}", ymin=self.POP_Y)
        labels = []
        for lab in labels:
            self.click(lab, ymin=self.POP_Y)
        got, want = self.credit(), CREDITS.get(int(seconds), 0) * int(batch or 1)
        t = self.chip()["t"]
        self.close_popover()
        if got is not None and want is not None and got != want:
            raise RuntimeError(f"크레딧 불일치({got} ≠ {want}) — 설정이 안 먹었다. 칩={t}")
        # ★신 UI 엔 크레딧 문구가 없다 → **칩 문자열**이 유일한 검증 수단이다.
        need = [f"crop_{ratio.replace(':', '_')}", "동영상", f"x{int(batch or 1)}"]
        miss = [x for x in need if x not in t]
        if f"{int(seconds)}초" not in t and f"{int(seconds)}s" not in t:
            miss.append(f"{seconds}초")
        if miss:
            raise RuntimeError(f"설정이 안 먹었다 — 칩={t} · 빠진 것={miss}")
        return t

    # ── 프롬프트 ──────────────────────────────────────────────
    PLACEHOLDER = "무엇을 만들고 싶으신가요?"

    def prompt_text(self):
        return str(self.js(_TXT)).replace("﻿", "").strip()

    def type_prompt(self, text):
        """★팝오버·피커를 먼저 닫는다. 비었는지 확인한 뒤 삽입하고 startswith 로 검증한다."""
        self.dismiss()
        self.close_popover()
        p = self._json(_EDIT)
        if not p:
            self.log("  ✗ 프롬프트 입력창 없음")
            return False
        self.click_xy(p["x"], p["y"])
        time.sleep(0.7)
        act = str(self.js(r"""(()=>{const a=document.activeElement;
          return a?JSON.stringify({ce:a.isContentEditable}):'none';})()"""))
        if '"ce":true' not in act:
            self.js(_FOCUS)      # Slate 는 DOM selection 이 안에 있어야 insertText 를 받는다
            time.sleep(0.4)
        cur = ""
        for _ in range(3):
            self.js(_CLEAR)
            time.sleep(0.45)
            cur = self.prompt_text()
            if cur in ("", self.PLACEHOLDER):
                break
        else:
            self.log(f"  ✗ 입력창이 안 비워진다: {cur[:34]}")
            return False
        self.cdp("Input.insertText", text=text)
        time.sleep(1.1)
        ok = self.prompt_text().startswith(text[:30])
        if not ok:
            self.log("  ✗ 프롬프트 미입력")
        else:
            self.remember("프롬프트창", p["x"], p["y"], "★입력 전 팝오버·피커를 닫아야 한다")
        return ok

    # ── 피커·참조 ─────────────────────────────────────────────
    def open_picker(self, via="plus"):
        """참조/시작프레임 피커 열기. via='plus' = 프롬프트창 왼쪽 `+`, 'slot' = 시작 슬롯."""
        self.close_popover()
        if via == "slot":
            if not self.click("시작", ymin=1000, wait=3):
                return False
        else:
            b = self._json(_PLUS)
            if not b:
                self.log("  ✗ '+' 버튼 없음")
                return False
            self.click_xy(b["x"], b["y"])
            time.sleep(3)
        return self.js(_ADD) != "null"

    def picker_items(self):
        """스크롤하며 목록 전체를 모은다(제목 기준 중복 제거)."""
        seen, out = set(), []
        for top in (0, 150, 300, 450, 600, 800, 1100, 1500):
            self.js(_SCROLL % top)
            time.sleep(0.7)
            for it in (self._json(_LIST, []) or []):
                if it["t"] in seen:
                    continue
                seen.add(it["t"])
                out.append(dict(it, scroll=top))
        return out

    def ref_count(self):
        try:
            return int(self.js(_THUMB))
        except Exception:
            return 0

    def pick(self, title=None, via="plus", tries=2):
        """제목 접두어가 맞는 항목을 골라 참조/시작프레임으로 붙인다.

        ★위치 인덱스로 고르지 않는다 — 피커는 선택 항목을 맨 앞으로 올려 인덱스가 밀린다.
        ★한 번 실패해도 크레딧은 안 든다(제출 전) — 조용히 안 붙는 경우가 있어 재시도한다.
        """
        for k in range(tries):
            if self._pick_once(title, via):
                return True
            if k + 1 < tries:
                self.log(f"  · 첨부 재시도 {k + 2}/{tries}")
                self.dismiss()
                time.sleep(2.0)
        return False

    def _pick_once(self, title=None, via="plus"):
        if not self.open_picker(via=via):
            return False
        items = self.picker_items()
        if not items:
            self.log("  ✗ 피커 목록이 비었다")
            return False
        if title:
            key = title.rstrip("… ").strip()
            hit = next((i for i in items
                        if i["t"].rstrip("… ").strip().startswith(key[:26])), None)
            if hit is None:
                self.log(f"  ✗ 제목 못 찾음: {key[:32]}")
                self.log("     후보: " + " | ".join(i["t"][:24] for i in items[:6]))
                return False
        else:
            hit = items[0]
        # ★좌표를 그대로 쓰지 않는다 — 목록 밖으로 나간 항목을 누르면 피커가 그냥 닫힌다.
        self.js(_INTO_VIEW % json.dumps(hit["t"]))
        time.sleep(1.0)
        now = self._json(_RECT_OF % json.dumps(hit["t"]))
        if now is None:
            self.log("  ✗ 스크롤 후 항목 소실")
            return False
        vh = self.vh()
        if not (0 < now["top"] and now["bottom"] < vh * 0.92):
            self.log(f"  ✗ 항목이 여전히 화면 밖: top={now['top']} bottom={now['bottom']}")
            return False
        self.click_xy(now["x"], now["y"])
        time.sleep(2.0)
        a = self._json(_ADD)
        if a:                       # 동영상 모드엔 확인 버튼이 남는다
            self.click_xy(a["x"], a["y"])
            self.remember("프롬프트에 추가", a["x"], a["y"], "★이걸 눌러야 슬롯에 붙는다")
            time.sleep(2.8)
        ok = self.ref_count() > 0   # 이미지 모드는 클릭만으로 첨부가 끝난다
        self.log(f"  {'✓' if ok else '✗'} 첨부: {hit['t'][:34]}")
        return ok

    def clear_slot(self):
        """채워진 시작 슬롯 비우기 — 라벨이 '시작' → 'cancel' 로 바뀐다."""
        if not self.ref_count():
            return True
        self.click("cancel", ymin=1000, wait=1.5)
        time.sleep(1.5)          # 슬롯이 비워지고 '시작' 칩이 돌아올 시간
        return self.ref_count() == 0

    # ── 제출·완료 판정 ────────────────────────────────────────
    _TITLES = r"""(()=>{const out=new Set();
      document.querySelectorAll('*').forEach(e=>{
        if(!e.offsetParent || !e.querySelector('img')) return;
        const r=e.getBoundingClientRect();
        if(r.width<100 || r.width>320) return;
        const t=(e.innerText||'').trim();
        if(!t || t.length>70) return;
        out.add(t);});
      return JSON.stringify([...out]);})()"""

    def media_count(self):
        try:
            return int(self.js(_COUNT))
        except Exception:
            return 0

    def media_titles(self):
        """캔버스에 보이는 미디어 카드 제목 집합.

        ★완료 판정을 **개수**로 하면 틀린다 — 그리드가 가상화돼 DOM 노드 수가 실제
        미디어 수와 다르고, URL 패턴도 도메인 이전으로 바뀐다. 제목 집합의 **증가**가
        가장 안정적인 신호다(생성물 제목은 프롬프트에서 자동 생성된다).
        """
        try:
            return set(json.loads(self.js(self._TITLES)))
        except Exception:
            return set()

    def submit(self):
        """제출은 `arrow_forward`(→). 왼쪽 `add_2` 도 라벨이 '만들기' 라 라벨로 고르면 피커만 열린다."""
        self.dismiss()
        a = self._json(_ARROW)
        if not a:
            self.log("  ✗ → 버튼 없음")
            return False
        self.click_xy(a["x"], a["y"])
        self.remember("만들기(제출)", a["x"], a["y"], "arrow_forward")
        time.sleep(3.5)
        return True

    def wait_new(self, before, timeout=600, tag=""):
        """`before` 는 media_titles() 집합. 새 제목이 나타나면 완료."""
        if not isinstance(before, set):
            before = set()
        t0 = time.time()
        while time.time() - t0 < timeout:
            time.sleep(10)
            now = self.media_titles()
            new = now - before
            if new:
                self.log(f"  ✓ 생성 완료: {min(new)[:40]} ({time.time()-t0:.0f}s)")
                return now
            if int(time.time() - t0) % 60 < 11:
                self.log(f"  … {time.time()-t0:.0f}s (제목 {len(now)}개)")
        self.log(f"  ✗ 생성 확인 실패 [{tag}]")
        return set()

    def newest_title(self):
        """방금 만든 미디어의 Flow 자동 제목 — 참조로 다시 부를 때 쓴다."""
        if not self.open_picker():
            return ""
        items = self._json(_LIST, []) or []
        t = items[0]["t"] if items else ""
        self.dismiss()
        return t
