"""VoiceStudio TTS — 로컬 OpenAI 호환 TTS 서버 연동 (debpalash/VoiceStudio v0.5.1).

VoiceStudio 앱(127.0.0.1:3900)이 실행 중이어야 한다. 목소리는 VoiceStudio 앱에서
클론/등록하고, 본 모듈은 listing/generate 만 한다.

Voicebox 와 달리 VoiceStudio 의 ``/v1/audio/speech`` 는 **동기 단일 호출**이다
(POST 하나로 오디오 바이트가 바로 온다 — voicebox 처럼 생성 후 폴링할 필요가 없다).
단, 단어 단위 타임스탬프는 제공하지 않으므로 storyboard 의 "1장면 = 자막 한 줄"
규칙에 맞춰 씬 전체를 단일 timing 엔트리로 만든다(voicebox 와 동일 계약).

★실측 확인(2026-08-31): 같은 문장·같은 seed 로 두 번 호출해도 결과 오디오 길이가
다르다(재현 불가). 그래서 매 호출마다 다운로드 직후 ffprobe 로 실측 duration 을
다시 재고, 이미 audio.mp3+timing.json 이 있으면 재생성하지 않고 캐시를 그대로
쓴다(voicebox 와 동일 정책 — 재생성은 결과가 달라질 뿐이지 더 나아지지 않는다).

★첫 호출은 모델 콜드스타트로 최대 4~5분까지 걸릴 수 있다(사용자 실측 4분46초).
타임아웃을 넉넉히 잡는다.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

# 로컬 앱 URL — krea2_image.py(COMFY_URL) 와 동일하게 환경변수로 override 가능.
VOICESTUDIO_DEFAULT_URL = os.environ.get("VOICESTUDIO_URL", "http://127.0.0.1:3900")

# ★기본값은 **VoiceStudio 를 깔면 누구에게나 있는** 공개 보이스로 둔다.
#   alloy 는 OpenAI 호환 별칭이라 프로필을 하나도 안 만들어도 바로 소리가 난다.
#   내 목소리로 바꾸려면 voicestudio_make_profile.py 로 프로필을 만든 뒤
#   그 voice_id 를 VOICESTUDIO_VOICE 로 넘긴다(RUNBOOK §0-1).
#   ⛔여기에 특정인의 클론 ID 를 박아두지 말 것 — 받는 사람 PC 엔 없는 ID 라
#     "설치는 됐는데 소리가 안 난다"로 나온다. 목록은 GET /v1/audio/voices.
VOICESTUDIO_DEFAULT_VOICE = os.environ.get("VOICESTUDIO_VOICE", "alloy")

# 합성 엔진을 payload 에 **명시**한다. 생략하면 앱에서 마지막으로 고른 엔진
# (POST /engines/select) 이 조용히 쓰여서, 같은 코드가 실행 시점에 따라 다른
# 목소리를 내놓는다. 파이프라인 산출물은 앱 UI 상태에 의존하면 안 된다.
VOICESTUDIO_DEFAULT_MODEL = os.environ.get("VOICESTUDIO_MODEL", "voxcpm2")

# 헬스체크 timeout — /v1/audio/capabilities 는 생성 요청이 아니라 짧게 잡는다.
VOICESTUDIO_HEALTH_TIMEOUT_SEC = 3.0
# 목소리 목록 조회 timeout.
VOICESTUDIO_LIST_TIMEOUT_SEC = 10.0
# 생성 요청(POST /v1/audio/speech) timeout — 동기 단일 호출이라 폴링용 별도
# timeout 이 없다. ★voxcpm2(4.6GB) 콜드스타트 실측 705초(2026-09-01, VRAM 경합
# 상태). 웜 상태는 실시간의 약 9배(3.5초 오디오에 32.6초)라 긴 씬도 여유가 있다.
VOICESTUDIO_REQUEST_TIMEOUT_SEC = 1200.0


def is_available(base_url: str = VOICESTUDIO_DEFAULT_URL) -> bool:
    """VoiceStudio 서버가 살아있는지 확인.

    ★``/v1/models`` 는 404 다(OpenAI 호환이지만 이 엔드포인트는 구현 안 됨) —
    그걸로 헬스체크하면 서버가 떠 있어도 항상 "없음"으로 오판한다.
    ``/v1/audio/capabilities`` 가 실제로 200 을 반환하는 엔드포인트.
    """
    try:
        r = httpx.get(
            f"{base_url}/v1/audio/capabilities", timeout=VOICESTUDIO_HEALTH_TIMEOUT_SEC
        )
        return r.status_code == 200
    except (httpx.HTTPError, OSError):
        return False


def list_profiles(base_url: str = VOICESTUDIO_DEFAULT_URL) -> list[dict[str, Any]]:
    """VoiceStudio 서버에서 보이스 목록을 가져온다.

    ``GET /v1/audio/voices`` 는 ``{"voices": [...], "engines": [...]}`` 형태라
    voicebox(바로 리스트) 와 달리 ``voices`` 키를 한 번 벗겨야 한다.
    """
    r = httpx.get(f"{base_url}/v1/audio/voices", timeout=VOICESTUDIO_LIST_TIMEOUT_SEC)
    r.raise_for_status()
    data = r.json()
    return data.get("voices", []) if isinstance(data, dict) else []


def _vlog(scene_id: int | None, msg: str) -> None:
    """진단 로그 — stderr 로 흘려보내 잡 로그에 보존되게 한다(voicebox 와 동일 관례)."""
    tag = "[voicestudio" + (f" scene={scene_id:03d}" if scene_id is not None else "") + "]"
    print(f"{tag} {msg}", file=sys.stderr, flush=True)


def _post(base_url: str, payload: dict[str, Any]) -> httpx.Response:
    """단일 POST — 테스트 모킹 seam(fish_tts._post 와 동일 관례). httpx.Response 반환."""
    with httpx.Client(timeout=VOICESTUDIO_REQUEST_TIMEOUT_SEC) as client:
        return client.post(f"{base_url}/v1/audio/speech", json=payload)


def _write_audio_atomic(content: bytes, out_path: Path) -> None:
    """생성된 오디오를 atomic 하게 기록한다.

    ``out_path.part`` 에 먼저 쓰고 성공 시 rename — 다운로드 도중 실패해도
    기존 파일이 살아남는다(voicebox `_download_audio` 와 동일 관례).
    """
    tmp_path = out_path.with_suffix(out_path.suffix + ".part")
    tmp_path.write_bytes(content)
    tmp_path.replace(out_path)  # POSIX/Windows 모두 atomic.


def generate_scene_audio_voicestudio(
    scene: dict[str, Any],
    output_dir: Path,
    voice: str = "",
    base_url: str = VOICESTUDIO_DEFAULT_URL,
    language: str = "ko",
) -> dict[str, Any]:
    """VoiceStudio 로 단일 장면 오디오 생성.

    Args:
        scene: storyboard scene (id, narration 필수)
        output_dir: scenes/<id>/ 디렉토리
        voice: VoiceStudio 보이스 ID. 빈 값이면 ``VOICESTUDIO_DEFAULT_VOICE``
            (사용자 한국어 클론 cbfc9b08) 를 쓴다.
        base_url: VoiceStudio 서버 URL
        language: 생성 언어 코드 (채널이 한국어라 기본 ko — voicebox 와 동일)

    Returns:
        {audio_path, timing_path, duration}
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_path = output_dir / "audio.mp3"
    timing_path = output_dir / "timing.json"

    if audio_path.exists() and timing_path.exists():
        timing_data = json.loads(timing_path.read_text(encoding="utf-8"))
        return {
            "audio_path": audio_path,
            "timing_path": timing_path,
            "duration": timing_data["duration_seconds"],
        }

    voice_id = voice or VOICESTUDIO_DEFAULT_VOICE
    narration = scene["narration"]
    payload: dict[str, Any] = {
        "model": VOICESTUDIO_DEFAULT_MODEL,
        "input": narration,
        "voice": voice_id,
        "language": language,
        "response_format": "mp3",
    }

    _vlog(
        scene["id"],
        f"생성 요청 model={VOICESTUDIO_DEFAULT_MODEL} voice={voice_id} "
        f"chars={len(narration)}",
    )
    try:
        resp = _post(base_url, payload)
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        raise RuntimeError(f"장면 {scene['id']:03d} VoiceStudio 실패: {e}") from e

    if resp.status_code != 200:
        raise RuntimeError(
            f"장면 {scene['id']:03d} VoiceStudio 요청 실패 "
            f"({resp.status_code}): {resp.text[:500]}"
        )
    content = resp.content

    _write_audio_atomic(content, audio_path)
    _vlog(scene["id"], f"생성 완료 → {audio_path.name} ({len(content)} bytes)")

    # 결과 길이가 매번 달라지므로(재현 불가 실측) 다운로드한 파일에서 다시 측정한다.
    # ★배포본 한정 — 원본은 src.video.tts 에서 끌어오지만, 그 모듈은 TTS 엔진 6종을
    #   달고 있어 팩에 담으면 쓰지도 않을 의존이 줄줄이 붙는다. 여기선 이것만 있으면 된다.
    def _get_audio_duration(audio_path: Path) -> float:
        import subprocess

        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(audio_path)],
            capture_output=True, encoding="utf-8", errors="replace")
        try:
            return float(r.stdout.strip())
        except (TypeError, ValueError):
            return 0.0

    duration = _get_audio_duration(audio_path)

    timing_data = {
        "scene_id": scene["id"],
        "words": [
            {
                "text": narration,
                "start": 0.0,
                "end": round(duration, 3),
            }
        ],
        "duration_seconds": round(duration, 3),
    }
    timing_path.write_text(
        json.dumps(timing_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "audio_path": audio_path,
        "timing_path": timing_path,
        "duration": duration,
    }
