"""경제학(및 타 파이프라인) 12번(나레이션) 단계 — 미니맥스(MiniMax) TTS 연동, 2026-09-15 신규.

배경: 제미나이 TTS(3.1 Flash TTS Preview)로 긴 나레이션을 여러 구간(청크)으로 나눠 생성하면
이음매(청크 경계)에서 목소리가 튀는 문제가 있었다(seed/재현성 파라미터가 없는 게 구글 공식
확인된 한계). 미니맥스로 전환한 이유와 실제 방식:
  1) 글자수당 가격이 가장 저렴하다(비교 결과 100만 글자당 3만원대).
  2) ⚠️ 2026-09-15 재확인 — 공식 문서(https://platform.minimax.io/docs/api-reference/speech-t2a-http)에는
     seed/random_seed 파라미터가 없다. "시드 고정으로 이음매를 없앤다"는 이전 계획 문서의 전제는
     틀렸다 — 정정 필요. 실제 일관성 확보 방법은 아래 두 가지다:
       a) 텍스트 길이 한도가 10,000자(글자 수, 바이트 아님)로 제미나이(8,000바이트≈2,500자)보다
          훨씬 넉넉하다 — 15분 분량 한국어 나레이션(대략 5,000~7,000자)은 대부분 한 번의 호출로
          끝나서 애초에 이음매가 생기지 않는다. 이게 진짜 핵심 해결책이다.
       b) 그래도 넘칠 정도로 길면, 매 청크에 같은 voice_id(고정 보이스, 클로닝했다면 클론 보이스)를
          쓰는 것 자체가 일관성 장치다 — seed가 아니라 "같은 화자 정체성 재사용"으로 접근한다.
     그래도 청크 간 음량 차이는 남을 수 있어 ffmpeg loudnorm(EBU R128)으로 후처리한다(기존 계획대로).
  3) 보이스 클로닝 지원(10초~5분 레퍼런스 오디오) — 새 목소리를 매번 고를 필요 없이 기존 음성을
     그대로 재사용할 수 있다.

API 키는 Supabase `app_config.MINIMAX_API_KEY_MINTIMJANG33`에 등록되어 있다(2026-09-14).
GroupId는 전역(api.minimax.io) 엔드포인트에서는 불필요(마이랜드 중국 api.minimaxi.chat 엔드포인트만
쿼리 파라미터로 요구) — 공식 문서(speech-t2a-http)에 GroupId 언급이 없는 것으로 확인.

사용 예:
    # 1) 그냥 기본 보이스로 합성
    python minimax_tts.py synthesize --text-file script.txt --voice-id English_expressive_narrator --out narration.mp3

    # 2) 레퍼런스 오디오로 보이스 클로닝 후 그 목소리로 합성
    python minimax_tts.py clone-and-synthesize --ref-audio my_voice.mp3 --custom-voice-id gentleman-rouge-01 \
        --text-file script.txt --out narration.mp3

필요 패키지: httpx (이미 이 프로젝트 다른 스크립트에서 씀, 설치돼 있음).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

MINIMAX_BASE = "https://api.minimax.io/v1"
T2A_URL = f"{MINIMAX_BASE}/t2a_v2"
UPLOAD_URL = f"{MINIMAX_BASE}/files/upload"
VOICE_CLONE_URL = f"{MINIMAX_BASE}/voice_clone"

MAX_CHARS_PER_CALL = 9500  # 공식 한도 10,000자 — 여유를 두고 9,500자에서 자른다.

_SUPA_ENV: dict | None = None


def _supa_env() -> dict:
    """flow_econ_driver.py와 동일한 방식으로 Supabase 접속 정보를 읽는다."""
    global _SUPA_ENV
    if _SUPA_ENV is None:
        candidates = [
            Path(__file__).resolve().parent.parent / ".env.local",
            Path(r"C:\Users\user\Downloads\U-Short\.env.local"),
        ]
        env_path = next((p for p in candidates if p.exists()), None)
        if env_path is None:
            raise FileNotFoundError(
                "Supabase .env.local을 찾을 수 없습니다 — flow-media-pack/.env.local을 만드세요."
            )
        env = {}
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
        _SUPA_ENV = {"url": env["NEXT_PUBLIC_SUPABASE_URL"], "key": env["SUPABASE_SERVICE_ROLE_KEY"]}
    return _SUPA_ENV


def get_config_value(key: str) -> str:
    """Supabase app_config 테이블에서 값을 읽는다(HongHub·U-Thread 등 공유 프로젝트, app_config 테이블)."""
    env = _supa_env()
    headers = {"apikey": env["key"], "Authorization": f"Bearer {env['key']}"}
    r = httpx.get(
        f"{env['url']}/rest/v1/app_config",
        params={"key": f"eq.{key}", "select": "value"},
        headers=headers,
        timeout=30,
    )
    r.raise_for_status()
    rows = r.json()
    if not rows:
        raise RuntimeError(f"app_config에 '{key}'가 없습니다.")
    return rows[0]["value"]


def _minimax_api_key() -> str:
    return get_config_value("MINIMAX_API_KEY_MINTIMJANG33")


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_minimax_api_key()}", "Content-Type": "application/json"}


def split_text(text: str, max_chars: int = MAX_CHARS_PER_CALL) -> list[str]:
    """max_chars를 넘으면 문단(빈 줄) 경계, 그다음 문장(. ! ? 및 한글 종결 부호) 경계로 나눈다.
    대부분의 15분 나레이션(5,000~7,000자)은 이 한도 안이라 실제로는 그대로 한 조각으로 반환된다."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paragraphs:
        candidate = f"{buf}\n\n{p}" if buf else p
        if len(candidate) <= max_chars:
            buf = candidate
            continue
        if buf:
            chunks.append(buf)
            buf = ""
        if len(p) <= max_chars:
            buf = p
            continue
        # 문단 자체가 한도를 넘으면 문장 경계로 추가 분할.
        import re

        sentences = re.split(r"(?<=[.!?。！？])\s+", p)
        sub = ""
        for s in sentences:
            cand2 = f"{sub} {s}".strip() if sub else s
            if len(cand2) <= max_chars:
                sub = cand2
            else:
                if sub:
                    chunks.append(sub)
                sub = s
        if sub:
            buf = sub
    if buf:
        chunks.append(buf)
    return chunks


def synthesize_chunk(
    text: str,
    voice_id: str,
    model: str = "speech-2.8-hd",
    audio_format: str = "wav",
    sample_rate: int = 32000,
    speed: float = 1.0,
    vol: float = 1.0,
    pitch: int = 0,
) -> bytes:
    """MiniMax T2A v2로 텍스트 한 조각을 합성해 오디오 바이트를 반환한다.
    응답의 data.audio는 hex 인코딩 문자열이라 bytes.fromhex()로 디코딩해야 한다(base64 아님)."""
    if len(text) > 10000:
        raise ValueError(f"텍스트가 10,000자를 넘습니다({len(text)}자) — split_text()로 먼저 나누세요.")

    body = {
        "model": model,
        "text": text,
        "stream": False,
        "voice_setting": {"voice_id": voice_id, "speed": speed, "vol": vol, "pitch": pitch},
        "audio_setting": {"sample_rate": sample_rate, "format": audio_format, "channel": 1},
    }
    r = httpx.post(T2A_URL, headers=_auth_headers(), json=body, timeout=120)
    r.raise_for_status()
    data = r.json()
    base_resp = data.get("base_resp", {})
    if base_resp.get("status_code", 0) != 0:
        raise RuntimeError(f"MiniMax T2A 실패: {base_resp}")
    audio_hex = data.get("data", {}).get("audio")
    if not audio_hex:
        raise RuntimeError(f"MiniMax 응답에 audio가 없습니다: {json.dumps(data)[:500]}")
    return bytes.fromhex(audio_hex)


def _loudnorm(in_path: Path, out_path: Path) -> None:
    """EBU R128 라우드니스 정규화 — 청크 간 음량 차이를 없앤다(작업 로그의 완화책 그대로)."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(in_path), "-filter:a", "loudnorm", "-c:a", "pcm_s16le", str(out_path)],
        check=True,
        capture_output=True,
    )


def _concat_wavs(parts: list[Path], out_path: Path) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{p.as_posix()}'\n")
        list_path = Path(f.name)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(out_path)],
            check=True,
            capture_output=True,
        )
    finally:
        list_path.unlink(missing_ok=True)


def synthesize_to_file(
    text: str,
    voice_id: str,
    out_path: Path,
    model: str = "speech-2.8-hd",
    normalize: bool = True,
) -> Path:
    """텍스트 전체(청크 자동 분할 포함)를 합성해 out_path(.wav)에 저장한다.
    청크가 1개면 정규화 없이 그대로 저장, 2개 이상이면 각각 정규화 후 이어붙인다."""
    chunks = split_text(text)
    audio_fmt = "wav"

    if len(chunks) == 1:
        audio = synthesize_chunk(chunks[0], voice_id=voice_id, model=model, audio_format=audio_fmt)
        out_path.write_bytes(audio)
        if normalize:
            tmp = out_path.with_suffix(".raw.wav")
            out_path.rename(tmp)
            _loudnorm(tmp, out_path)
            tmp.unlink(missing_ok=True)
        return out_path

    print(f"  [minimax_tts] 텍스트가 길어 {len(chunks)}개 청크로 나눠 호출합니다(각 청크에 같은 voice_id 재사용).")
    tmpdir = Path(tempfile.mkdtemp(prefix="minimax_tts_"))
    normed_parts: list[Path] = []
    try:
        for i, chunk in enumerate(chunks, start=1):
            print(f"  [minimax_tts] 청크 {i}/{len(chunks)} ({len(chunk)}자) 합성 중...")
            audio = synthesize_chunk(chunk, voice_id=voice_id, model=model, audio_format=audio_fmt)
            raw_path = tmpdir / f"part_{i:02d}_raw.wav"
            raw_path.write_bytes(audio)
            normed_path = tmpdir / f"part_{i:02d}.wav"
            _loudnorm(raw_path, normed_path)
            normed_parts.append(normed_path)
        _concat_wavs(normed_parts, out_path)
    finally:
        for p in normed_parts:
            p.unlink(missing_ok=True)
        for p in tmpdir.glob("*_raw.wav"):
            p.unlink(missing_ok=True)
        try:
            tmpdir.rmdir()
        except OSError:
            pass
    return out_path


# ── 보이스 클로닝 ──────────────────────────────────────────────────────────

def upload_reference_audio(file_path: Path) -> str:
    """레퍼런스 오디오(10초~5분, mp3/m4a/wav, 20MB 이하)를 업로드하고 file_id를 반환한다."""
    headers = {"Authorization": f"Bearer {_minimax_api_key()}"}
    with file_path.open("rb") as f:
        r = httpx.post(
            UPLOAD_URL,
            headers=headers,
            data={"purpose": "voice_clone"},
            files={"file": (file_path.name, f)},
            timeout=60,
        )
    r.raise_for_status()
    data = r.json()
    file_id = data.get("file", {}).get("file_id") or data.get("file_id")
    if not file_id:
        raise RuntimeError(f"업로드 응답에 file_id가 없습니다: {json.dumps(data)[:500]}")
    return str(file_id)


def clone_voice(file_id: str, custom_voice_id: str) -> str:
    """업로드된 레퍼런스로 voice_id를 생성한다. 반환값은 이후 synthesize_*의 voice_id로 그대로 쓴다."""
    body = {"file_id": file_id, "voice_id": custom_voice_id}
    r = httpx.post(VOICE_CLONE_URL, headers=_auth_headers(), json=body, timeout=60)
    r.raise_for_status()
    data = r.json()
    base_resp = data.get("base_resp", {})
    if base_resp.get("status_code", 0) != 0:
        raise RuntimeError(f"MiniMax 보이스 클로닝 실패: {base_resp}")
    return custom_voice_id


# ── CLI ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="MiniMax TTS 연동 (HongHub 경제학 파이프라인 12번)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_syn = sub.add_parser("synthesize", help="텍스트를 지정 voice_id로 바로 합성")
    p_syn.add_argument("--text-file", required=True, type=Path)
    p_syn.add_argument("--voice-id", required=True)
    p_syn.add_argument("--out", required=True, type=Path)
    p_syn.add_argument("--model", default="speech-2.8-hd")
    p_syn.add_argument("--no-normalize", action="store_true")

    p_clone = sub.add_parser("clone-voice", help="레퍼런스 오디오로 보이스만 클로닝(합성은 안 함)")
    p_clone.add_argument("--ref-audio", required=True, type=Path)
    p_clone.add_argument("--custom-voice-id", required=True)

    p_cs = sub.add_parser("clone-and-synthesize", help="보이스 클로닝 후 그 목소리로 바로 합성")
    p_cs.add_argument("--ref-audio", required=True, type=Path)
    p_cs.add_argument("--custom-voice-id", required=True)
    p_cs.add_argument("--text-file", required=True, type=Path)
    p_cs.add_argument("--out", required=True, type=Path)
    p_cs.add_argument("--model", default="speech-2.8-hd")
    p_cs.add_argument("--no-normalize", action="store_true")

    args = parser.parse_args()

    if args.cmd == "synthesize":
        text = args.text_file.read_text(encoding="utf-8")
        out = synthesize_to_file(
            text, voice_id=args.voice_id, out_path=args.out, model=args.model, normalize=not args.no_normalize
        )
        print(f"완료: {out}")

    elif args.cmd == "clone-voice":
        file_id = upload_reference_audio(args.ref_audio)
        voice_id = clone_voice(file_id, args.custom_voice_id)
        print(f"완료: voice_id={voice_id} (앞으로 이 값을 --voice-id로 재사용)")

    elif args.cmd == "clone-and-synthesize":
        file_id = upload_reference_audio(args.ref_audio)
        voice_id = clone_voice(file_id, args.custom_voice_id)
        print(f"보이스 클로닝 완료: voice_id={voice_id}")
        text = args.text_file.read_text(encoding="utf-8")
        out = synthesize_to_file(
            text, voice_id=voice_id, out_path=args.out, model=args.model, normalize=not args.no_normalize
        )
        print(f"완료: {out}")


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPStatusError as e:
        print(f"HTTP 오류: {e.response.status_code} {e.response.text[:500]}", file=sys.stderr)
        sys.exit(1)
