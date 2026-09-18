"""구글 Flow 미디어 생성기 — 편·채널 무관 공용 드라이버. browser-harness 스크립트.

    FLOW_JOB=<flow.json> [FLOW_ONLY=IMG03] [FLOW_LIMIT=3] [FLOW_STAGE=images|clips|all] \
      BU_CDP_URL=http://127.0.0.1:9222 PYTHONUTF8=1 browser-harness < scripts/flow_media.py

`flow.json` 계약 (경로는 job 파일이 있는 폴더 기준):

    {
     "ratio": "9:16",                       // 또는 "16:9"
     "style": "공통 룩 문장 — 모든 이미지 프롬프트 앞에 붙는다",
     "ref_hint": "참조를 물릴 때 덧붙이는 문장(선택)",
     "images": [
       {"id": "IMG01", "anchor": true,  "prompt": "..."},
       {"id": "IMG02", "prompt": "..."}     // anchor 를 참조로 물려 룩을 통일
     ],
     "clips": [                             // 없으면 이미지까지만
       {"id": "CLIP01", "image_id": "IMG02", "prompt": "...", "seconds": 4, "res": "720p"}
     ]
    }

산출 · 상태
  `<job과 같은 폴더>/_flow_state.json` — project_url · 이미지별 Flow 제목 · 완료 목록.
  ★클립은 `image_id` 로 원본을 가리킨다 — 드라이버가 그 이미지의 **Flow 제목**을 상태에서
  찾아 피커에서 제목으로 고른다(위치 인덱스는 밀린다).
  회수는 `scripts/flow_fetch_media.py` 가 따로 한다(FLOW_MAX·FLOW_KIND 로 한정 가능).

크레딧: 이미지 **0** · 동영상 4s=7 · 6s=10 · 8s=12 · 10s=15.
함정·실좌표는 `scripts/src/video/flow_kit.py` 머리말과 `scripts/config/flow_ui_map.json`.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path("scripts").resolve()))
from src.video.flow_kit import CREDITS, Flow

# ★환경변수가 없으면 KeyError 만 뜬다 — 받는 사람은 무슨 뜻인지 모른다.
if not os.environ.get("FLOW_JOB"):
    raise SystemExit(
        "★FLOW_JOB 이 없습니다 — 만들 편의 flow.json 경로를 주세요.\n"
        "  예) FLOW_JOB=my-episode/flow.json BU_CDP_URL=http://127.0.0.1:9222 "
        "PYTHONUTF8=1 browser-harness < scripts/flow_media.py\n"
        "  이 파일은 단독 실행용이 아니라 browser-harness 에 흘려 넣는 스크립트입니다."
    )
JOB_PATH = Path(os.environ["FLOW_JOB"]).resolve()
JOB = json.loads(JOB_PATH.read_text(encoding="utf-8"))
HERE = JOB_PATH.parent
STATE_PATH = HERE / "_flow_state.json"
ONLY = os.environ.get("FLOW_ONLY", "")
LIMIT = int(os.environ.get("FLOW_LIMIT", "999"))
STAGE = os.environ.get("FLOW_STAGE", "all")
RATIO = JOB.get("ratio", "9:16")


def log(*a):
    print(*a, flush=True)


F = Flow(js=js, cdp=cdp, screenshot=capture_screenshot, goto=goto_url, page=page_info,
         shots=HERE / "_shots", log=log)

state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}
state.setdefault("titles", {})
state.setdefault("done", {})


def save():
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


# ★먼저 Flow 탭으로 **명시 전환**한다.
#   하니스의 "현재 탭"은 사용자가 브라우저를 쓰면 딴 데로 흘러간다. 그 상태로 goto_url 을 부르면
#   **사용자가 보던 탭을 Flow 로 이동시켜 버린다**(2026-09-04 실사고 계열).
import urllib.request as _u

_cdp = os.environ.get("BU_CDP_URL", "http://127.0.0.1:9222")
try:
    _tabs = json.loads(_u.urlopen(f"{_cdp}/json/list", timeout=8).read())
    _flow = [t for t in _tabs if t.get("type") == "page"
         and ("labs.google" in (t.get("url") or "") or "flow.google.com" in (t.get("url") or ""))]
    if _flow:
        switch_tab(_flow[-1]["id"])
        log("Flow 탭으로 전환:", (_flow[-1].get("title") or "")[:40])
    else:
        log("★Flow 탭이 없다 — 새로 연다")
except Exception as e:
    log("★탭 전환 실패:", e)

# ── 프로젝트 확보
state["project_url"] = F.open_project(state.get("project_url"))
save()
log("프로젝트:", state["project_url"])
made = 0


# ══ 이미지 ════════════════════════════════════════════════════
def run_images():
    global made
    imgs = JOB.get("images") or []
    if not imgs:
        return
    log("")
    log(f"[이미지] {len(imgs)}개 · 0크레딧")
    log("  칩:", F.set_image_mode(RATIO, JOB.get("batch")))
    F.shot("img_00_setup")
    style, hint = JOB.get("style", ""), JOB.get("ref_hint", "")
    anchor = next((i for i in imgs if i.get("anchor")), imgs[0])
    for im in imgs:
        if im["id"] in state["done"] or (ONLY and im["id"] != ONLY):
            continue
        if made >= LIMIT:
            log(f"  (LIMIT {LIMIT})")
            return
        is_anchor = im is anchor or im.get("anchor")
        log(f"  [{im['id']}] {'앵커' if is_anchor else '참조 물림'}")
        c = F.chip()
        if "Nano Banana" not in c["t"]:
            log(f"  ✗ 칩이 이미지 모델이 아니다: {c['t']}")
            F.shot(im["id"] + "_FAIL_chip")
            return
        prompt = " ".join(x for x in (style, im["prompt"], "" if is_anchor else hint) if x)
        # ★프롬프트를 **먼저** 넣는다 — 입력창 비우기가 붙여둔 참조까지 지운다.
        if not F.type_prompt(prompt):
            F.shot(im["id"] + "_FAIL_prompt")
            return
        if not is_anchor:
            at = state["titles"].get(anchor["id"]) or F.newest_title()
            if not F.pick(at):
                log("  ✗ 참조 첨부 실패")
                F.shot(im["id"] + "_FAIL_ref")
                return
        F.shot(im["id"] + "_a_ready")
        before = F.media_titles()
        if not F.submit():
            F.shot(im["id"] + "_FAIL_submit")
            return
        if not F.wait_new(before, tag=im["id"]):
            F.shot(im["id"] + "_FAIL_wait")
            return
        # ★Flow 자동 제목을 기록해 둔다 — 클립 단계가 이 제목으로 원본을 고른다.
        state["titles"][im["id"]] = F.newest_title()
        state["done"][im["id"]] = {"kind": "image", "ts": time.strftime("%H:%M"),
                                   "title": state["titles"][im["id"]]}
        save()
        log(f"  제목: {state['titles'][im['id']][:40]}")
        made += 1


# ══ 클립 ══════════════════════════════════════════════════════
def run_clips():
    global made
    clips = JOB.get("clips") or []
    if not clips:
        return
    secs = {int(c.get("seconds", 4)) for c in clips}
    if len(secs) > 1:
        log(f"★클립 길이가 섞여 있다 {sorted(secs)} — 길이별로 나눠 실행할 것(설정 검증이 크레딧 기준)")
        return
    sec = secs.pop()
    res = clips[0].get("res", "720p")
    cost = CREDITS.get(sec)
    todo = [c for c in clips if c["id"] not in state["done"] and (not ONLY or c["id"] == ONLY)]
    log("")
    log(f"[클립] {len(todo)}개 × {sec}초 = **{len(todo)*cost}크레딧**")
    if not todo:
        return
    log("  칩:", F.set_video_mode(RATIO, res, sec))   # 크레딧 불일치면 여기서 예외
    F.shot("clip_00_setup")
    for cl in todo:
        if made >= LIMIT:
            log(f"  (LIMIT {LIMIT})")
            return
        title = cl.get("title") or state["titles"].get(cl.get("image_id", ""))
        if not title:
            log(f"  ✗ {cl['id']}: 원본 제목을 모른다(image_id={cl.get('image_id')})")
            return
        log(f"  [{cl['id']}] ← {title[:32]}")
        if not F.clear_slot():
            log("  ✗ 슬롯 비우기 실패")
            return
        if not F.pick(title, via="slot"):
            F.shot(cl["id"] + "_FAIL_pick")
            return
        if not F.type_prompt(cl["prompt"]):
            F.shot(cl["id"] + "_FAIL_prompt")
            return
        got = F.credit()
        if got not in (None, cost):
            log(f"  ✗ 크레딧 변동: {got} ≠ {cost}")
            return
        F.shot(cl["id"] + "_a_ready")
        before = F.media_titles()
        if not F.submit():
            F.shot(cl["id"] + "_FAIL_submit")
            return
        if not F.wait_new(before, tag=cl["id"]):
            F.shot(cl["id"] + "_FAIL_wait")
            return
        state["done"][cl["id"]] = {"kind": "clip", "src": title, "ts": time.strftime("%H:%M")}
        save()
        made += 1


if STAGE in ("images", "all"):
    run_images()
if STAGE in ("clips", "all"):
    run_clips()

log("")
imgs_n = len(JOB.get("images") or [])
clips_n = len(JOB.get("clips") or [])
done_i = sum(1 for v in state["done"].values() if v.get("kind") == "image")
done_c = sum(1 for v in state["done"].values() if v.get("kind") == "clip")
log(f"이미지 {done_i}/{imgs_n} · 클립 {done_c}/{clips_n} · 상태 {STATE_PATH}")
