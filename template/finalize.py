"""silent.mp4 + 나레이션 → final.mp4. 라우드니스 −14 LUFS / −1 dBTP 2패스."""
import json
import re
import subprocess
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
OUT = PROJ / "output"
SIL = OUT / "silent.mp4"
AUD = PROJ / "_tts/audio.mp3"
FIN = OUT / "final.mp4"


def run(*a, **kw):
    return subprocess.run(a, capture_output=True, text=True, **kw)


def dur(p):
    r = run("ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", str(p))
    return float(r.stdout.strip())


for f in (SIL, AUD):
    if not f.exists():
        raise SystemExit(f"★없음: {f}")
print(f"silent {dur(SIL):.2f}s · audio {dur(AUD):.2f}s")

# 1패스 — 측정
m = run("ffmpeg", "-i", str(AUD), "-af", "loudnorm=I=-14:TP=-1.0:LRA=11:print_format=json",
        "-f", "null", "-")
j = re.findall(r"\{[^{}]*input_i[^{}]*\}", m.stderr, re.S)
if not j:
    raise SystemExit("★loudnorm 측정 실패")
st = json.loads(j[-1])
print("측정:", {k: st[k] for k in ("input_i", "input_tp", "input_lra")})

af = (f"loudnorm=I=-14:TP=-1.0:LRA=11:measured_I={st['input_i']}:"
      f"measured_TP={st['input_tp']}:measured_LRA={st['input_lra']}:"
      f"measured_thresh={st['input_thresh']}:offset={st['target_offset']}:linear=true")

r = run("ffmpeg", "-y", "-i", str(SIL), "-i", str(AUD),
        "-map", "0:v:0", "-map", "1:a:0", "-af", af,
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-shortest", "-movflags", "+faststart", str(FIN))
if r.returncode != 0:
    raise SystemExit("★mux 실패:\n" + r.stderr[-1200:])

print(f"final.mp4 {dur(FIN):.2f}s · {FIN.stat().st_size/1e6:.1f}MB")
# 무음 검사 — ★`-v error` 로 덮지 말 것(전례)
v = run("ffmpeg", "-i", str(FIN), "-af", "volumedetect", "-f", "null", "-")
mm = re.search(r"mean_volume:\s*(-?[\d.]+)", v.stderr)
print("mean_volume:", mm.group(1) if mm else "측정 실패")
