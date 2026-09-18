"""자동화가 도는 동안 그 창을 화면 녹화한다 — 메이킹 영상용 스크린캐스트.

    .\\.venv\\Scripts\\python scripts/record_flow.py --out <mp4> [--title-re ...] \\
        [--seconds N] -- <실행할 명령 ...>

`--seconds` 만 주면 명령 없이 그 시간만 녹화한다(장비 시험용).
명령을 주면 **녹화를 켠 채 그 명령을 돌리고**, 끝나면 녹화를 정상 종료한다.

백엔드는 둘이고 **OpenScreen 이 있으면 그걸 쓴다**(oscap.available()):

  OpenScreen(WGC)  창 단위 캡처. **가려진 창도 찍힌다** → 앞으로 끌어올 필요 없음,
                   화면 밖으로 나가도 됨. 60fps 창 클라이언트 영역 고정이라 --fps/--pad 무시.
  gdigrab(폴백)    OpenScreen 미설치일 때. 배포 팩 수신자는 미설치일 수 있다.

★gdigrab 폴백일 때만 해당: 화면 영역을 찍으므로 창이 가려지면 가린 게 찍힌다 → 시작 전에
  대상 창을 앞으로 끌어온다. 창이 화면 밖으로 1px 이라도 나가면 통째로 거부하므로
  ("Capture area extends outside window area") **창을 옮기지 말고 영역을 자른다** —
  창을 옮기면 사용자 화면 배치가 바뀌고, UIA move_window 는 조용히 실패하기도 한다(실측).
★종료 방식이 백엔드마다 다르다.
  gdigrab     'q' 를 보내야 한다. kill 하면 moov atom 이 안 써져 파일이 깨진다.
  OpenScreen  **`--duration` 만 정상 종료된다** — stdin 'stop'·SIGINT/SIGTERM 은 도움말에
              적혀 있지만 실측에서 전부 안 멈췄다(2026-09-05). 그래서 길이를 아는
              `--seconds` 만 정상 종료하고 `<out>.cursor.json` 사이드카를 받는다.
              명령 동반 녹화는 강제로 끊는다 — 컨테이너가 fragmented-mp4 라 영상은
              멀쩡하지만 **cursor.json 은 안 나온다**.
  둘 다 `Recording` 이 with 문에서 알아서 처리한다.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.video.screencast import oscap, wincap


def find_window(title_re: str):
    from pywinauto import Desktop
    pat = re.compile(title_re)
    best = None
    for w in Desktop(backend="uia").windows():
        t = (w.window_text() or "").strip()
        if t and pat.search(t):
            r = w.rectangle()
            area = r.width() * r.height()
            if best is None or area > best[0]:
                best = (area, w, t)
    return (best[1], best[2]) if best else (None, None)


def video_size(path: Path) -> tuple[int, int]:
    """영상 실제 크기. 클라이언트 크기로 가정하지 않는다 — 어긋난 실측 사례가 있다."""
    out = subprocess.run(
        [shutil.which("ffprobe") or "ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--title-re", default=r"Google Flow.*Chrome")
    ap.add_argument("--seconds", type=float, default=0.0,
                    help="명령 없이 이 시간만 녹화(장비 시험)")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--pad", type=int, default=0)
    ap.add_argument("--stdin", default="",
                    help="이 파일을 명령의 stdin 으로 물린다 (browser-harness 는 스크립트를 stdin 으로 받는다)")
    ap.add_argument("cmd", nargs="*", help="-- 뒤에 실행할 명령")
    args = ap.parse_args()

    win, title = find_window(args.title_re)
    if win is None:
        print(f"★창을 못 찾음: /{args.title_re}/")
        return 1
    r = win.rectangle()
    print(f"대상 창: {title}")
    print(f"  {r.width()}x{r.height()} @({r.left},{r.top})")

    out = Path(args.out)
    rec = None
    has_os = oscap.available()
    if has_os:
        # 길이를 알 때만 --duration 으로 정상 종료된다(그때만 cursor.json 이 나온다).
        # 명령 동반 녹화는 길이를 모르므로 stop() 이 강제로 끊는다.
        rec = oscap.record_window_title(
            title, out, seconds=(None if args.cmd else args.seconds or None))
    geom = None
    if rec is not None:
        import win32gui
        hwnd = win.handle
        wr = win32gui.GetWindowRect(hwnd)
        corg = win32gui.ClientToScreen(hwnd, (0, 0))
        # cx/cy 는 창 사각형 기준 정규화, 영상은 클라이언트 영역 — 그 차이를 여기서 잰다.
        geom = {"win_size": (wr[2] - wr[0], wr[3] - wr[1]),
                "inset": (corg[0] - wr[0], corg[1] - wr[1])}
        clean = not args.cmd and args.seconds
        note = "cursor.json 사이드카" if clean else "★강제 종료 — cursor.json 없음"
        print(f"  백엔드: OpenScreen(WGC) — 가려져도 찍힌다 / 60fps 창영역 고정"
              f"(--fps·--pad 무시) / {note}")
    else:
        why = "OpenScreen 미설치" if not has_os else             "★제목을 지정할 수 없다(콜론 등) — 가려진 창은 못 찍는다"
        print(f"  백엔드: gdigrab ({why})")
        try:
            win.set_focus()
            time.sleep(1.0)
        except Exception as e:  # noqa: BLE001 — pywinauto 예외가 여러 갈래고, 포커스 실패는 치명적이지 않다
            print("  (포커스 실패 — 창이 가려지지 않았는지 직접 확인)", e)

        import pyautogui
        sw, sh = pyautogui.size()
        x0, y0 = max(0, r.left - args.pad), max(0, r.top - args.pad)
        x1, y1 = min(sw, r.right + args.pad), min(sh, r.bottom + args.pad)
        w, h = x1 - x0, y1 - y0
        if w != r.width() + 2 * args.pad or h != r.height() + 2 * args.pad:
            print(f"  화면({sw}x{sh}) 밖을 잘라냄 → 영역 {w}x{h} @({x0},{y0})")
        rec = wincap.record(out, region=(x0, y0, w, h), fps=args.fps)

    t0 = time.time()
    with rec:
        if args.cmd:
            print("  ▶", " ".join(args.cmd)[:140], f"< {args.stdin}" if args.stdin else "")
            if args.stdin:
                with open(args.stdin, "rb") as fh:
                    rc = subprocess.run(args.cmd, stdin=fh, env=os.environ.copy(), check=False).returncode
            else:
                rc = subprocess.run(args.cmd, env=os.environ.copy(), check=False).returncode
            print(f"  명령 종료 rc={rc}")
        else:
            time.sleep(args.seconds)
    dur = time.time() - t0
    size = out.stat().st_size if out.exists() else 0
    print(f"녹화 저장: {out}  {dur:.1f}s  {size / 1e6:.1f}MB")

    # 커서 좌표 → Remotion actions (Screencast.tsx 가 커서·자동줌을 그리는 입력).
    cj = out.with_name(out.name + ".cursor.json")
    if geom is not None and cj.exists():
        from src.video.screencast import cursor_actions
        vw, vh = video_size(out)
        n = cursor_actions.write_actions(
            cj, out.with_name(out.name + ".actions.json"),
            win_size=geom["win_size"], inset=geom["inset"], video_size=(vw, vh))
        print(f"  actions.json: {n}개 (영상 {vw}x{vh} 기준)")
    if size < 50_000:
        print("★파일이 너무 작다 — 녹화가 안 걸렸을 수 있다")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
