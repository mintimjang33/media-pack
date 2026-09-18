"""EP03 나레이션 — VoiceStudio(VoxCPM2) 로컬 서버로 script.txt 전체를 합성.

산출: _tts/audio.mp3 · _tts/timing.json · _tts/words.json(단어 정렬)
★TTS 를 먼저 만들어 **실측 길이**로 장면표를 짠다(추정 초로 짜지 않는다).
★헬스체크는 `/v1/audio/capabilities` — `/v1/models` 는 404 라 서버가 떠 있어도 없다고 나온다.
"""
import json
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

from src.video.voicestudio_tts import (  # noqa: E402
    is_available,
    generate_scene_audio_voicestudio,
    list_profiles,
)

if not is_available():
    raise SystemExit("★VoiceStudio 서버(127.0.0.1:3900)가 응답하지 않는다 — 앱에서 서버를 켤 것")

profs = list_profiles()
print(f"보이스 {len(profs)}개")
import json as _j
for p in profs[:10]:
    print("  ", _j.dumps(p, ensure_ascii=False)[:110])

text = " ".join(l.strip() for l in (PROJ / "script.txt").read_text(encoding="utf-8").splitlines() if l.strip())
print(f"대본 {len(text)}자")

out = PROJ / "_tts"
r = generate_scene_audio_voicestudio({"id": 1, "narration": text}, out)
print(f"audio.mp3 {r['duration']:.2f}초 → {r['audio_path']}")

from src.video.word_align import align_words, transcribe_words  # noqa: E402

# ★여기부터는 **전사기**가 필요하다 — 자막 타이밍을 실측으로 뽑는 단계다.
#   없다고 그냥 죽으면 방금 만든 audio.mp3 까지 헛일처럼 보인다. 무엇을 하면 되는지 말한다.
try:
    wh = transcribe_words(r["audio_path"])
except ModuleNotFoundError as e:
    raise SystemExit(
        f"★자막 타이밍을 뽑을 전사기가 없습니다 ({e.name}).\n"
        f"  audio.mp3 는 이미 만들어졌습니다 → {r['audio_path']}\n"
        "  둘 중 하나만 하면 이어집니다:\n"
        "    1) pip install faster-whisper      (로컬·무료·첫 실행에 모델 내려받음)\n"
        "    2) set GROQ_API_KEY=<키>           (원격·빠름)\n"
        "  그 뒤 `python build_tts.py` 를 다시 돌리면 audio.mp3 는 그대로 두고 "
        "words.json 만 만듭니다."
    ) from e
words = align_words(text, wh)
(out / "words.json").write_text(
    json.dumps({"scene_id": "full", "words": words}, ensure_ascii=False, indent=1), encoding="utf-8")
miss = sum(1 for w in words if w.get("start") is None)
print(f"words.json {len(words)}단어 · 미정렬 {miss}개 · 전사 {len(wh)}단어")
