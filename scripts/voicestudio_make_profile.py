"""VoiceStudio 클론 보이스 프로필을 레퍼런스 wav 로 (다시) 만든다.

    .\\.venv\\Scripts\\python scripts/voicestudio_make_profile.py \\
        --name "my-clone" --ref tools/voiceclone/ref_75.wav --ref-text tools/voiceclone/ref_75.txt

★VoiceStudio 프로필은 앱 데이터에 저장된다 — 드라이브를 정리하면 사라진다.
  레퍼런스 wav 와 전사는 레포에 남아 있으므로 언제든 재생성할 수 있다(이 스크립트).
★만든 뒤 나오는 voice_id 를 `VOICESTUDIO_VOICE` 로 넘겨 쓴다.
"""
import argparse
import json
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:3900"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--ref", required=True, help="레퍼런스 wav")
    ap.add_argument("--ref-text", default="", help="레퍼런스 전사 txt (권장)")
    ap.add_argument("--language", default="Korean")
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()

    ref = Path(args.ref)
    if not ref.exists():
        print(f"★레퍼런스 없음: {ref}")
        return 1
    text = Path(args.ref_text).read_text(encoding="utf-8").strip() if args.ref_text else ""
    print(f"레퍼런스 {ref.name} ({ref.stat().st_size // 1024}KB) · 전사 {len(text)}자")

    with ref.open("rb") as fh:
        r = httpx.post(
            f"{args.base}/profiles",
            data={"name": args.name, "ref_text": text, "language": args.language, "kind": "clone"},
            files={"ref_audio": (ref.name, fh, "audio/wav")},
            timeout=300.0,
        )
    print("HTTP", r.status_code)
    if r.status_code >= 400:
        print(r.text[:600])
        return 1
    body = r.json()
    print(json.dumps(body, ensure_ascii=False, indent=1)[:600])
    vid = body.get("id") or body.get("profile_id") or body.get("voice_id")
    if vid:
        print()
        print(f"★voice_id = {vid}")
        print(f'   쓰는 법: VOICESTUDIO_VOICE={vid}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
