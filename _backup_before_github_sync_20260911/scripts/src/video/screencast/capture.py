"""screencast 매니페스트 → _assets/screencast/NN-id/{raw.mp4,actions.json}.

broll/capture.py 의 영상 형제 — 정지 PNG 대신 CDP 뷰포트 녹화(recorder.py).
직렬 실행(디버그 크롬 1개를 공유하므로 병렬 녹화 불가 — 프레임이 섞인다).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .recorder import record

logger = logging.getLogger(__name__)


def load_manifest(path: Path) -> list[dict]:
    """[{id, url?, recipe:{viewport,fps,steps,mask?}}...]"""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def scene_props(asset_dir: Path, entry: dict) -> dict:
    """녹화 산출물(actions.json) → scene-designer 가 storyboard 씬에 그대로 병합할 계약.

    반환 = `{id, asset_recommendation}`. `id` 는 어느 나레이션 씬에 붙일지 매칭용(병합 대상 아님),
    `asset_recommendation` 은 storyboard 씬의 같은 키에 통째로 복사(flat — 소비측 remotion_props 가
    `rec["src"]` 식 flat 키를 읽음). src 는 프로젝트 절대경로 힌트 — public 복사/상대경로 재작성은
    remotion 스테이지(programmatic_video 동기화 + remotion_props.scene_to_screencast_props)가 처리.
    """
    acts = json.loads((asset_dir / "actions.json").read_text(encoding="utf-8"))
    return {
        "id": entry["id"],
        "asset_recommendation": {
            "layout": "Screencast",
            "src": str((asset_dir / "raw.mp4").as_posix()),
            "actions": acts["actions"],
            "videoW": acts["w"],
            "videoH": acts["h"],
            "url": entry.get("url", ""),
            "recordedSeconds": acts["duration"],  # 녹화 실길이(씬 TTS 길이와 대조용 — 씬 duration 아님)
        },
    }


def capture_all(manifest: list[dict], out_root: Path, cdp_http: str, recorder=record) -> dict:
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    stats: dict = {"captured": 0, "drops": [], "scenes": []}
    for seq, entry in enumerate(manifest, start=1):
        asset_dir = out_root / f"{seq:02d}-{entry['id']}"
        try:
            recorder(entry["recipe"], asset_dir, cdp_http)
            stats["captured"] += 1
            stats["scenes"].append(scene_props(asset_dir, entry))
        except Exception as exc:  # noqa: BLE001 — 한 컷 실패가 전체를 막지 않게
            logger.exception("screencast capture failed: %s", entry.get("id"))
            stats["drops"].append({"asset_id": entry.get("id"), "reason": str(exc)[:200]})
    # PD/scene-designer 가 소비할 씬 계약을 함께 남긴다.
    (out_root / "_screencast_scenes.json").write_text(
        json.dumps(stats["scenes"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return stats


MANIFEST_TEMPLATE = [
    {
        "id": "meta-token",
        "url": "developers.facebook.com/apps",
        "recipe": {
            "viewport": [1280, 720],
            "fps": 30,
            "mask": [],
            "steps": [
                {"action": "goto", "url": "https://developers.facebook.com/apps", "settle": 2.0},
                {"action": "move", "x": 0.5, "y": 0.3, "ms": 700},
                {"action": "click", "x": 0.5, "y": 0.3},
                {"action": "wait", "ms": 800},
            ],
        },
    }
]
