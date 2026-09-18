"""OpenScreen `cursor.json` → Remotion `Screencast.tsx` 의 `actions[]`.

WGC 녹화(oscap.py)는 커서를 영상에 굽지 않고 좌표만 사이드카로 낸다. 그걸 CDP 경로가
이미 쓰는 계약(`actions[]`)으로 옮기면 **Remotion 은 한 줄도 안 고쳐도 된다** —
layouts/Screencast.tsx 가 move(fx/fy/ms)로 60fps 커서를, click 으로 자동 줌을 그린다.

## 좌표계 (실측 2026-09-05)

`cx/cy` 는 **창 사각형**(GetWindowRect) 기준 정규화다 — `cx*winW + winX` 가 커서
물리좌표와 **오차 0.0** 으로 일치하는 걸 확인했다. 반면 영상은 **클라이언트 영역**이라
그만큼 안쪽으로 밀려 있다(최대화 창 실측: 영상 2560x1392 = 클라이언트 크기 일치).
그래서 창 기준 좌표에서 클라이언트 원점 오프셋을 빼야 영상 좌표가 된다.

★`visible` 필드로 거르면 안 된다. 커서가 창 **안**에 있는데도 false 가 나온다(실측) —
  "범위 안"이 아니라 "이 창이 커서를 소유(위에 있음)" 쪽 의미로 보인다. 범위 판정은
  변환된 좌표가 영상 안에 드는지로 한다.
★`interactionType` 은 실측에서 `move` 만 관측됐다(클릭을 안 해봤다). move 가 아닌 값은
  클릭으로 넘기되 처음 보는 값은 알린다 — 클릭이 잡히면 자동 줌이 그대로 붙는다.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

#: 이만큼(px) 움직여야 새 move 를 끊는다. 미세 떨림을 한 구간으로 합쳐 actions 를 줄인다.
MIN_MOVE_PX = 6.0


def cursor_json_to_actions(
    cursor_path: str | Path,
    *,
    win_size: tuple[int, int],
    inset: tuple[int, int],
    video_size: tuple[int, int],
    min_move_px: float = MIN_MOVE_PX,
) -> list[dict]:
    """`win_size` = 창 사각형(정규화 기준), `inset` = 클라이언트 원점 - 창 원점,
    `video_size` = 영상(=클라이언트) 크기. 반환은 Screencast.tsx 의 actions[].
    """
    data = json.loads(Path(cursor_path).read_text(encoding="utf-8"))
    win_w, win_h = win_size
    dx, dy = inset
    vw, vh = video_size

    pts: list[tuple[float, float, float, str]] = []
    for s in data.get("samples") or []:
        try:
            x = s["cx"] * win_w - dx
            y = s["cy"] * win_h - dy
            t = s["timeMs"] / 1000.0
        except (KeyError, TypeError):
            continue
        if not (0 <= x <= vw and 0 <= y <= vh):
            continue  # 영상 밖 — visible 필드는 못 믿는다(모듈 docstring 참고)
        pts.append((t, x, y, str(s.get("interactionType") or "move")))
    if not pts:
        return []

    actions: list[dict] = []
    t0, x0, y0, _ = pts[0]
    # 첫 앵커 — 커서가 거의 안 움직여도 오버레이가 뜨게 한다(Screencast 는 첫 move 전엔 안 그린다).
    actions.append({"t": round(t0, 3), "type": "move", "fx": round(x0, 1), "fy": round(y0, 1),
                    "x": round(x0, 1), "y": round(y0, 1), "ms": 1})
    seen_other: set[str] = set()
    for t, x, y, kind in pts[1:]:
        if kind != "move":
            if kind not in seen_other:
                seen_other.add(kind)
            actions.append({"t": round(t, 3), "type": "click",
                            "x": round(x, 1), "y": round(y, 1)})
            continue
        if math.hypot(x - x0, y - y0) < min_move_px:
            continue
        ms = max(16, round((t - t0) * 1000))
        actions.append({"t": round(t0, 3), "type": "move",
                        "fx": round(x0, 1), "fy": round(y0, 1),
                        "x": round(x, 1), "y": round(y, 1), "ms": ms})
        t0, x0, y0 = t, x, y
    if seen_other:
        print(f"  [oscap] interactionType 신규 값 {sorted(seen_other)} → click 으로 변환")
    return actions


def write_actions(cursor_path: str | Path, out_path: str | Path, **kw) -> int:
    """cursor.json 을 읽어 actions.json 을 쓴다. 반환 = actions 개수."""
    actions = cursor_json_to_actions(cursor_path, **kw)
    Path(out_path).write_text(json.dumps(actions, ensure_ascii=False), encoding="utf-8")
    return len(actions)
