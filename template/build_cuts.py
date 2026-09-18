"""EP03 cuts.json + CUTLIST.md 빌더 — 다초점 렌즈.

정지 이미지 12장(Flow 나노바나나2) + 클립 7개(Flow Omni) + Remotion 한글/수치 레이어.
자막은 `_tts/words.json` 실측 타이밍에서 잘라 만든다 — 손으로 적지 않는다.
"""
import json
import shutil
import sys
from pathlib import Path

import os

# ── 팩 기준 경로 ──────────────────────────────────────────────────
# 이 파일을 **한 편 폴더**(대본·flow.json 이 있는 곳)에 복사해 쓴다.
# 팩 위치는 환경변수 FLOW_PACK 로 준다. 없으면 이 파일 기준 상위를 쓴다.
PACK = Path(os.environ.get("FLOW_PACK") or Path(__file__).resolve().parents[1])
PROJ = Path(__file__).resolve().parent          # 한 편 = 이 폴더
SLUG = PROJ.name
PUB = PACK / "remotion/public/engshorts" / SLUG  # 리모션이 읽는 자산 자리
sys.path.insert(0, str(PACK / "scripts"))
from src.video.eng_subtitle import chunk, em  # noqa: E402

ASSET_DIR = f"engshorts/{SLUG}"   # cuts.json 의 clip/base 상대경로 접두어

# ── 다운로드는 **최신 먼저**다. 콘택트 시트로 눈으로 교차확인한 매핑.
IMGS = {
    "anchor":  "flow_img_12.jpg",   # 앵커 — 어두운 작업대 위 렌즈
    "page":    "flow_img_11.jpg",   # 책 위 안경, 글씨 흐림
    "bifocal": "flow_img_10.jpg",   # 이중초점 D 세그먼트
    "seam":    "flow_img_09.jpg",   # 경계선이 빛을 받아 솔기로
    "edge":    "flow_img_08.jpg",   # 렌즈 측면 — 두께가 연속으로 변한다
    "grid":    "flow_img_07.jpg",   # 격자 위 렌즈 — 가운데만 선명
    "blanks":  "flow_img_06.jpg",   # 곡률이 커지는 블랭크 열
    "machine": "flow_img_05.jpg",   # 렌즈 가공기
    "framed":  "flow_img_04.jpg",   # 안경테에 든 완성 렌즈
    "stairs":  "flow_img_02.jpg",   # 계단 — 상 점프 은유
    "three":   "flow_img_01.jpg",   # 렌즈 3장 — 가입도가 커질수록 통로가 좁다
    "folded":  "flow_img_00.jpg",   # 접힌 안경 — 클로징
}
CLIPS = {f"CLIP0{i}": f"CLIP0{i}.mp4" for i in range(1, 8)}

SERIES = {
    1: "완성체", 2: "부위 확대", 3: "완성체",
    4: "환경", 5: "환경", 6: "단면", 7: "부위 확대",
    8: "실패체", 9: "규모 비교", 10: "단면", 11: "단면",
    12: "상태 변화", 13: "단면", 14: "상태 변화", 15: "규모 비교",
    16: "실패체", 17: "실패체",
    18: "규모 비교", 19: "공정", 20: "공정",
    21: "완성체", 22: "완성체", 23: "규모 비교", 24: "규모 비교",
    25: "환경", 26: "완성체", 27: "환경", 28: "완성체",
}

CUTS = [
    # (1) 훅 0.00~9.20
    dict(s=0.00, d=3.30, clip="CLIP01", clipFrom=0.0,
         ov=[dict(t="eng", at=dict(x=58, y=40), text="ONE LENS", delay=26)]),
    dict(s=3.30, d=2.90, base="grid", cam=dict(from_=1.0, to=1.08, cy=45),
         ov=[dict(t="ko", text="옆이 흐리다", y=13, delay=12, accent=True),
             dict(t="flow", arrows=[dict(from_=dict(x=50, y=48), to=dict(x=24, y=48)),
                                    dict(from_=dict(x=50, y=48), to=dict(x=76, y=48))],
                  delay=20, stagger=4, width=1.1)]),
    dict(s=6.20, d=3.00, base="anchor", cam=dict(from_=1.06, to=1.0),
         ov=[dict(t="ko", text="일부러 남긴 흐림", y=13, delay=10, accent=True),
             dict(t="eng", at=dict(x=60, y=44), text="BY DESIGN", delay=18)]),
    # (2) 문제 9.20~24.50
    dict(s=9.20, d=4.00, clip="CLIP02", clipFrom=0.0,
         ov=[dict(t="ko", text="마흔쯤 시작된다", y=13, delay=22),
             dict(t="dim", from_=dict(x=28, y=68), to=dict(x=72, y=68), label="가까운 글씨", delay=30)]),
    dict(s=13.20, d=3.40, base="page", cam=dict(from_=1.08, to=1.0),
         ov=[dict(t="ko", text="초점을 못 당긴다", y=13, delay=8),
             dict(t="flow", arrows=[dict(from_=dict(x=50, y=30), to=dict(x=50, y=62))],
                  delay=16, width=1.2),
             dict(t="eng", at=dict(x=58, y=40), text="PRESBYOPIA", delay=24)]),
    dict(s=16.60, d=4.00, base="bifocal", cam=dict(from_=1.0, to=1.07, cy=55),
         ov=[dict(t="ko", text="한 장에 도수 둘", y=13, delay=14),
             dict(t="flow", arrows=[dict(from_=dict(x=22, y=38), to=dict(x=44, y=38)),
                                    dict(from_=dict(x=22, y=62), to=dict(x=44, y=62))],
                  delay=22, stagger=5, width=1.0),
             dict(t="dim", from_=dict(x=24, y=72), to=dict(x=76, y=72),
                  label="위=멀리 / 아래=가까이", delay=30)]),
    dict(s=20.60, d=3.90, clip="CLIP03", clipFrom=0.1,
         ov=[dict(t="ko", text="이중초점", y=13, delay=20, accent=True),
             dict(t="dim", from_=dict(x=26, y=66), to=dict(x=74, y=66), label="도수 2개", delay=28),
             dict(t="eng", at=dict(x=58, y=34), text="SEGMENT", delay=34)]),
    # (3) 1차 해법 24.50~40.30
    dict(s=24.50, d=4.10, base="seam", cam=dict(from_=1.05, to=1.12, cy=50),
         ov=[dict(t="ko", text="경계에서 상이 튄다", y=13, delay=12, accent=True),
             dict(t="flow", arrows=[dict(from_=dict(x=40, y=60), to=dict(x=60, y=40))],
                  delay=20, width=1.4),
             dict(t="x", at=dict(x=50, y=50), size=15, delay=30)]),
    dict(s=28.60, d=4.00, base="stairs", cam=dict(from_=1.0, to=1.08, cy=60),
         ov=[dict(t="ko", text="발밑이 어긋난다", y=13, delay=10),
             dict(t="flow", arrows=[dict(from_=dict(x=46, y=34), to=dict(x=54, y=70))],
                  delay=18, width=1.3),
             dict(t="eng", at=dict(x=58, y=26), text="IMAGE JUMP", delay=26, warn=True)]),
    dict(s=32.60, d=3.80, clip="CLIP04", clipFrom=0.1,
         ov=[dict(t="ko", text="경계를 없앤다", y=13, delay=20, accent=True),
             dict(t="flow", arrows=[dict(from_=dict(x=50, y=28), to=dict(x=50, y=70))],
                  delay=28, width=1.2)]),
    dict(s=36.40, d=3.90, base="edge", cam=dict(from_=1.06, to=1.0),
         ov=[dict(t="ko", text="위에서 아래로 연속", y=13, delay=12),
             dict(t="dim", from_=dict(x=22, y=30), to=dict(x=22, y=68), label="10mm", delay=20),
             dict(t="eng", at=dict(x=60, y=52), text="CORRIDOR", delay=28)]),
    # (4) 새 문제 40.30~55.50
    dict(s=40.30, d=3.90, clip="CLIP05", clipFrom=0.1,
         ov=[dict(t="ko", text="이번엔 옆이 흐리다", y=13, delay=22),
             dict(t="flow", arrows=[dict(from_=dict(x=50, y=46), to=dict(x=22, y=46)),
                                    dict(from_=dict(x=50, y=46), to=dict(x=78, y=46))],
                  delay=30, stagger=4, width=1.1)]),
    dict(s=44.20, d=3.80, base="grid",
         ov=[dict(t="ko", text="민크비츠 정리", y=13, delay=10, accent=True),
             dict(t="profile", mode="decay", y=42, delay=16,
                  caption="가운데에서 옆으로", axis="난시")]),
    dict(s=48.00, d=3.80, base="grid", cam=dict(from_=1.05, to=1.12, cy=45),
         ov=[dict(t="dim", from_=dict(x=24, y=62), to=dict(x=76, y=62),
                  label="도수 변화율의 2배", delay=14),
             dict(t="flow", arrows=[dict(from_=dict(x=50, y=44), to=dict(x=20, y=44)),
                                    dict(from_=dict(x=50, y=44), to=dict(x=80, y=44))],
                  delay=22, stagger=3, width=1.3),
             dict(t="eng", at=dict(x=58, y=30), text="ASTIGMATISM", delay=30, warn=True)]),
    dict(s=51.80, d=3.70, base="blanks", cam=dict(from_=1.0, to=1.07),
         ov=[dict(t="ko", text="부드럽게 할수록", y=13, delay=12),
             dict(t="flow", arrows=[dict(from_=dict(x=20, y=58), to=dict(x=80, y=58))],
                  delay=20, width=1.2),
             dict(t="dim", from_=dict(x=24, y=70), to=dict(x=76, y=70), label="곡률 증가", delay=28)]),
    # (5) 반박 선점 55.50~59.40
    dict(s=55.50, d=2.00, base="grid",
         ov=[dict(t="ko", text="없애면 되지 않나?", y=13, delay=8)]),
    dict(s=57.50, d=1.90, base="grid", cam=dict(from_=1.04, to=1.1, cy=50),
         ov=[dict(t="x", at=dict(x=50, y=48), size=18, delay=6),
             dict(t="eng", at=dict(x=56, y=64), text="IMPOSSIBLE", delay=12, warn=True)]),
    # (6) 발상 전환 59.40~67.20
    dict(s=59.40, d=3.00, base="blanks",
         ov=[dict(t="ko", text="없애지 말고 옮긴다", y=13, delay=10, accent=True),
             dict(t="flow", arrows=[dict(from_=dict(x=50, y=50), to=dict(x=18, y=50)),
                                    dict(from_=dict(x=50, y=50), to=dict(x=82, y=50))],
                  delay=18, stagger=4, width=1.4)]),
    dict(s=62.40, d=2.40, base="machine", cam=dict(from_=1.0, to=1.06),
         ov=[dict(t="eng", at=dict(x=54, y=34), text="REDISTRIBUTE", delay=8)]),
    dict(s=64.80, d=2.40, base="machine", cam=dict(from_=1.06, to=1.0),
         ov=[dict(t="ko", text="자리를 정해준다", y=13, delay=6),
             dict(t="dim", from_=dict(x=26, y=64), to=dict(x=74, y=64), label="옆으로 2배 속도", delay=12)]),
    # (7) 작동 원리 67.20~81.00
    dict(s=67.20, d=4.00, clip="CLIP06", clipFrom=0.0,
         ov=[dict(t="ko", text="세로 통로만 남긴다", y=13, delay=22, accent=True),
             dict(t="flow", arrows=[dict(from_=dict(x=50, y=26), to=dict(x=50, y=72))],
                  delay=30, width=1.5)]),
    dict(s=71.20, d=3.60, base="framed",
         ov=[dict(t="profile", mode="stair", y=42, delay=8,
                  caption="통로 밖으로 몰아낸다", axis="난시", ghost="decay"),
             dict(t="flow", arrows=[dict(from_=dict(x=50, y=66), to=dict(x=20, y=66)),
                                    dict(from_=dict(x=50, y=66), to=dict(x=80, y=66))],
                  delay=22, stagger=4, width=1.2)]),
    dict(s=74.80, d=3.20, base="three", cam=dict(from_=1.0, to=1.08, cy=50),
         ov=[dict(t="ko", text="가입도가 클수록", y=13, delay=10),
             dict(t="dim", from_=dict(x=24, y=68), to=dict(x=76, y=68),
                  label="+0.75 ~ +3.00 D", delay=18)]),
    dict(s=78.00, d=3.00, base="three", cam=dict(from_=1.08, to=1.0),
         ov=[dict(t="ko", text="통로가 좁아진다", y=13, delay=8, accent=True),
             dict(t="flow", arrows=[dict(from_=dict(x=26, y=52), to=dict(x=44, y=52)),
                                    dict(from_=dict(x=74, y=52), to=dict(x=56, y=52))],
                  delay=14, stagger=3, width=1.3)]),
    # (8) 클로징 81.00~94.40
    dict(s=81.00, d=3.80, base="folded", cam=dict(from_=1.0, to=1.06),
         ov=[dict(t="ko", text="적응 기간이 든다", y=13, delay=12),
             dict(t="eng", at=dict(x=58, y=44), text="TRADE-OFF", delay=22)]),
    dict(s=84.80, d=3.60, base="framed", cam=dict(from_=1.07, to=1.0),
         ov=[dict(t="ko", text="옆은 고개를 돌린다", y=13, delay=10),
             dict(t="flow", arrows=[dict(from_=dict(x=42, y=56), to=dict(x=68, y=56))],
                  delay=18, width=1.2)]),
    dict(s=88.40, d=2.60, base="folded", cam=dict(from_=1.05, to=1.0),
         ov=[dict(t="ko", text="어디를 포기할지", y=13, delay=8, accent=True)]),
    dict(s=91.00, d=3.40, clip="CLIP07", clipFrom=0.2,
         ov=[dict(t="ko", text="이렇게 설계된 겁니다", y=13, delay=20, accent=True)]),
]


def load_words():
    w = json.loads((PROJ / "_tts/words.json").read_text(encoding="utf-8"))["words"]
    return [x for x in w if x.get("start") is not None]


def subs_for(words, s, e, first=False, last=False):
    """★단어는 시작 시각으로 **한 컷에만** 배정한다(겹침 판정은 중복을 만든다)."""
    lo = -1e9 if first else s
    hi = 1e9 if last else e
    txt = " ".join(x["text"] for x in words if lo <= x["start"] < hi)
    return chunk(txt.strip()) if txt.strip() else None


def fix(d):
    if isinstance(d, dict):
        return {("from" if k == "from_" else k): fix(v) for k, v in d.items()}
    if isinstance(d, list):
        return [fix(x) for x in d]
    return d


def main():
    PUB.mkdir(parents=True, exist_ok=True)
    for k, f in IMGS.items():
        src = PROJ / "output/clean" / f
        if not src.exists():
            raise SystemExit(f"★이미지 없음: {src}")
        shutil.copy2(src, PUB / f)
    for k, f in CLIPS.items():
        src = PROJ / "output/clips" / f
        if not src.exists():
            raise SystemExit(f"★클립 없음: {src}")
        shutil.copy2(src, PUB / f)

    words = load_words()
    cuts = []
    for c in CUTS:
        o = {"s": round(c["s"], 2), "d": round(c["d"], 2)}
        if "base" in c:
            o["base"] = c["base"]
        if "split" in c:
            o["split"] = list(c["split"])
        if "clip" in c:
            o["clip"] = f"{ASSET_DIR}/{CLIPS[c['clip']]}"
            o["clipFrom"] = c.get("clipFrom", 0.0)
        if "cam" in c:
            o["cam"] = fix(c["cam"])
        sub = subs_for(words, c["s"], c["s"] + c["d"],
                       first=(c is CUTS[0]), last=(c is CUTS[-1]))
        if sub:
            o["sub"] = sub
        if "ov" in c:
            o["ov"] = fix(c["ov"])
        cuts.append(o)

    data = {"images": {k: f"{ASSET_DIR}/{f}" for k, f in IMGS.items()}, "cuts": cuts}
    (PROJ / "cuts.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    by = {}
    for i in range(1, len(cuts) + 1):
        by.setdefault(SERIES.get(i, "미배정"), []).append(i)
    tot = cuts[-1]["s"] + cuts[-1]["d"]
    lines = ["# EP03 CUTLIST — 안경 렌즈 옆이 흐린 건 불량이 아닙니다", "",
             f"나레이션 실측 **{tot:.2f}초** · 컷 {len(cuts)}개"
             f" (클립 {sum(1 for c in cuts if 'clip' in c)} · 정지 {sum(1 for c in cuts if 'clip' not in c)})", "",
             "★이 파일은 `build_cuts.py` 가 생성한다 — 손으로 고치지 말 것.", "",
             "## 장면 계열 배분", "", "| 계열 | 장 수 | 컷 |", "|---|---|---|"]
    for k, v in by.items():
        lines.append(f"| {k} | {len(v)} | {', '.join(str(x) for x in v)} |")
    lines += ["", "## 핵심 시각 장치 3개", "",
              "1. **왜 어려운가의 증명** — CUT13 민크비츠 감쇠 곡선(가운데에서 옆으로)",
              "2. **딜레마** — CUT8 경계 X + CUT17 '없앨 수 없다' X",
              "3. **역발상의 시각 증명** — CUT22 감쇠 위에 겹치는 계단(통로 밖으로 몰아낸다)", "",
              "## 컷 표", "", "| # | 초 | 길이 | 소스 | 계열 | 자막 |", "|---|---|---|---|---|---|"]
    for i, c in enumerate(cuts, 1):
        srcv = c.get("clip", "").split("/")[-1] or c.get("base", "")
        sub = " / ".join(c.get("sub") or [])[:44]
        lines.append(f"| {i} | {c['s']:.2f} | {c['d']:.2f} | {srcv} | {SERIES.get(i,'')} | {sub} |")
    (PROJ / "CUTLIST.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    num = sum(1 for c in cuts for o in (c.get("ov") or [])
              if o.get("t") in ("dim", "dim2", "gauge", "profile"))
    gfx = sum(len(c.get("ov") or []) for c in cuts)
    print(f"그래픽 총 {gfx}개 / {len(cuts)}컷 ({gfx/len(cuts):.2f}개per컷)")
    print(f"수치 오버레이 {num}개 / {len(cuts)}컷 ({num/len(cuts):.2f}개per컷)")
    print(f"cuts.json — {len(cuts)}컷 · 클립 {sum(1 for c in cuts if 'clip' in c)} · 총 {tot:.2f}초")
    print(f"자산 복사 → {PUB}")
    bad = [f"C{i+1:02d} {c['d']}s" for i, c in enumerate(cuts) if not (1.4 <= c["d"] <= 6.0)]
    if bad:
        print("★컷 길이 이탈:", bad)
    over = [f"C{i+1:02d} {o['text']}({em(o['text']):.1f}em)"
            for i, c in enumerate(cuts) for o in (c.get("ov") or [])
            if o.get("t") == "ko" and em(o.get("text", "")) > 11.5]
    if over:
        raise SystemExit("★한글 라벨 폭 초과: " + " · ".join(over))
    longclip = [f"C{i+1:02d} {c['d']}s" for i, c in enumerate(cuts)
                if "clip" in c and c["d"] > 4.01]
    if longclip:
        raise SystemExit("★클립보다 긴 컷: " + " · ".join(longclip))


if __name__ == "__main__":
    main()
