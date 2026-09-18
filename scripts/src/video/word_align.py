"""각 씬 audio.mp3 의 단어 타임스탬프(faster-whisper)를 대본 텍스트에 정렬한다.

whisper 의 한글 단어 인식은 부정확할 수 있으나 단어 시각은 양호하다. 그래서
표시 텍스트는 깨끗한 대본을 쓰고, 시각만 whisper 에서 차용한다(문자 단위 difflib 정렬).
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "medium"  # 스파이크에선 small 도 타이밍은 충분. 운영 품질은 medium.
_GROQ_MODEL = "whisper-large-v3-turbo"  # 무료 티어·빠름. 정확도 우선이면 whisper-large-v3.

# 정렬용 정규화: 한글/영숫자/% 만 남기고 소문자화(공백·문장부호 제거).
_KEEP = re.compile(r"[0-9a-z가-힣%]+")


def _norm_chars(s: str) -> str:
    return "".join(_KEEP.findall(s.lower()))


def align_words(clean_text: str, whisper_words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """대본 단어 목록에 whisper 단어 시각을 전이한다.

    1) whisper 단어를 문자 단위로 펼쳐 (정규화문자, 시각) 타임라인 생성(단어 내 선형보간).
    2) 대본을 공백 단어로 나누고 각 단어의 정규화문자를 전역 문자열에 누적.
    3) difflib 로 대본문자열↔whisper문자열 매칭 → 각 대본문자에 시각 부여(미매칭은 보간/전후채움).
    4) 대본 단어별 start=문자 최소시각, end=문자 최대시각. 단조 보정.
    """
    disp_words = [w for w in clean_text.split() if w.strip()]
    if not disp_words:
        return []
    if not whisper_words:
        return [{"text": w, "start": None, "end": None} for w in disp_words]

    # 1) whisper 문자 타임라인
    w_chars: list[str] = []
    w_times: list[float] = []
    for w in whisper_words:
        nt = _norm_chars(str(w.get("text", "")))
        if not nt:
            continue
        s = float(w["start"])
        e = float(w["end"])
        dur = max(0.0, e - s)
        n = len(nt)
        for i, ch in enumerate(nt):
            w_chars.append(ch)
            w_times.append(s + dur * ((i + 0.5) / n))
    if not w_chars:
        return [{"text": w, "start": None, "end": None} for w in disp_words]

    # 2) 대본 문자열 + 문자→단어 인덱스
    c_chars: list[str] = []
    c_word_of: list[int] = []
    for wi, dw in enumerate(disp_words):
        for ch in _norm_chars(dw):
            c_chars.append(ch)
            c_word_of.append(wi)

    # 3) 문자 매칭 → 대본문자 시각
    c_time: list[float | None] = [None] * len(c_chars)
    sm = difflib.SequenceMatcher(a=c_chars, b=w_chars, autojunk=False)
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            c_time[blk.a + k] = w_times[blk.b + k]
    # 전방 채움 → 후방 채움(가장자리 미매칭 보정)
    last = None
    for i in range(len(c_time)):
        if c_time[i] is None:
            c_time[i] = last
        else:
            last = c_time[i]
    nxt = None
    for i in range(len(c_time) - 1, -1, -1):
        if c_time[i] is None:
            c_time[i] = nxt
        else:
            nxt = c_time[i]
    fallback = w_times[0]
    c_time = [t if t is not None else fallback for t in c_time]

    # 4) 단어별 시각 집계 + 단조 보정
    out: list[dict[str, Any]] = []
    for wi, dw in enumerate(disp_words):
        times = [c_time[i] for i in range(len(c_chars)) if c_word_of[i] == wi]
        if times:
            start, end = min(times), max(times)
        else:  # 정규화 후 빈 토큰(문장부호 단독 등) → 직전 끝에 붙임
            start = out[-1]["end"] if out else w_times[0]
            end = start
        if out and start < out[-1]["start"]:
            start = out[-1]["start"]
        if end < start:
            end = start
        out.append({"text": dw, "start": round(float(start), 3), "end": round(float(end), 3)})
    return out


def _transcribe_groq(audio_path: Path) -> list[dict[str, Any]]:
    """Groq Whisper API 로 단어 타임스탬프 추출(OpenAI 호환 transcriptions).
    키 없거나 응답 오류면 예외 → 호출부에서 로컬 폴백."""
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY 없음")
    import requests

    with open(audio_path, "rb") as f:
        resp = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (audio_path.name, f, "audio/mpeg")},
            data={
                "model": os.environ.get("GROQ_WHISPER_MODEL", _GROQ_MODEL),
                "language": "ko",
                "response_format": "verbose_json",
                "timestamp_granularities[]": "word",
            },
            timeout=120,
        )
    resp.raise_for_status()
    words = resp.json().get("words") or []
    return [
        {"text": w["word"], "start": round(float(w["start"]), 3), "end": round(float(w["end"]), 3)}
        for w in words
    ]


def _transcribe_local(audio_path: Path, model_size: str = _DEFAULT_MODEL) -> list[dict[str, Any]]:
    """faster-whisper(로컬 CPU) 로 단어 타임스탬프(인식 텍스트 포함) 추출."""
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(audio_path), language="ko", word_timestamps=True)
    words: list[dict[str, Any]] = []
    for seg in segments:
        for w in (seg.words or []):
            words.append({"text": w.word, "start": round(w.start, 3), "end": round(w.end, 3)})
    return words


def transcribe_words(audio_path: Path, model_size: str = _DEFAULT_MODEL) -> list[dict[str, Any]]:
    """단어 타임스탬프 추출. GROQ_API_KEY 있으면 Groq Whisper(빠름) 우선,
    키 없음/실패/빈 결과면 로컬 faster-whisper 로 폴백."""
    if os.environ.get("GROQ_API_KEY", "").strip():
        try:
            words = _transcribe_groq(audio_path)
            if words:
                return words
            logger.warning("Groq 단어 타임스탬프 비어있음 → 로컬 whisper 폴백")
        except Exception:  # noqa: BLE001 — 네트워크/쿼터/응답오류 전부 로컬 폴백
            logger.warning("Groq 전사 실패 → 로컬 whisper 폴백", exc_info=True)
    return _transcribe_local(audio_path, model_size)


def align_scene(scene_dir: Path, clean_text: str, model_size: str = _DEFAULT_MODEL) -> Path:
    """한 씬: audio.mp3 → words.json(대본 텍스트 + 정렬 시각). 캐시 있으면 스킵."""
    out_path = scene_dir / "words.json"
    audio = scene_dir / "audio.mp3"
    # audio.mp3 가 words.json 보다 최신이면(무음 트림/재녹음으로 audio 가 바뀐 경우) 캐시를 버리고
    # 재정렬한다 — 무조건 재사용하면 트림 후 stale 정렬로 자막↔음성 싱크가 어긋난다(리딩 무음만큼 밀림).
    if out_path.exists() and not (audio.exists() and audio.stat().st_mtime > out_path.stat().st_mtime):
        return out_path
    whisper_words = transcribe_words(audio, model_size) if audio.exists() else []
    words = align_words(clean_text, whisper_words)
    dur = words[-1]["end"] if words and words[-1]["end"] is not None else 0.0
    out_path.write_text(
        json.dumps({"scene_id": int(scene_dir.name) if scene_dir.name.isdigit() else 0,
                    "words": words, "duration_seconds": dur}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out_path


def align_all(video_dir: Path, model_size: str = _DEFAULT_MODEL) -> int:
    """storyboard 원문 narration 을 씬별로 정렬. 처리한 씬 수 반환."""
    sb = json.loads((video_dir / "storyboard.json").read_text(encoding="utf-8"))
    n = 0
    for s in sb.get("scenes", []):
        scene_dir = video_dir / "scenes" / f"{int(s['id']):03d}"
        if not (scene_dir / "audio.mp3").exists():
            continue
        align_scene(scene_dir, str(s.get("narration", "")).strip(), model_size)
        n += 1
    return n


def align_captions(
    video_dir: Path,
    *,
    whisper_model: str = _DEFAULT_MODEL,
    caption_mode: str = "animated",
) -> int:
    """카라오케 자막용 후처리: (무음트림 →) 단어정렬 → caption_track 생성.

    ``caption_mode != "animated"`` 이면 아무것도 안 하고 0 반환(애니메이션 자막일 때만 필요).
    무음 트림은 ``.audio_trimmed`` 마커로 멱등 — 정렬 *전에* 먼저 해 words.json 이 렌더에서
    베이크될 '트림된 audio' 와 같은 타임라인을 갖게 한다(리딩 무음만큼 자막 밀림 방지).

    ``produce tts``(cmd_tts) 와 ``produce all``(TtsStage) 가 공유하는 단일 소스.
    Returns: 정렬한 씬 수(caption_mode 비활성 시 0).
    """
    if caption_mode != "animated":
        return 0
    from src.video.caption_track import build_caption_track
    from src.video.grok_compose import trim_scene_silence

    scenes_dir = video_dir / "scenes"
    if scenes_dir.exists():
        for scene_dir in sorted(scenes_dir.iterdir()):
            ap = scene_dir / "audio.mp3"
            marker = scene_dir / ".audio_trimmed"
            if ap.exists() and not marker.exists():
                try:
                    if trim_scene_silence(ap):
                        marker.write_text("ok", encoding="utf-8")
                except Exception:  # noqa: BLE001 — 트림 실패는 비치명(원본 그대로 정렬)
                    pass
    print("\n단어 정렬(whisper) + 카라오케 트랙 생성 중...")
    n = align_all(video_dir, model_size=whisper_model)
    build_caption_track(video_dir)
    print(f"  {n}개 씬 단어정렬 완료 -> caption_track.json")
    return n
