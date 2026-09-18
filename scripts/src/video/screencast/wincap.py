"""윈도우 네이티브 앱 화면 녹화 — ffmpeg gdigrab.

같은 폴더의 recorder.py 는 CDP(브라우저 뷰포트) 전용이라 브라우저 밖을 못 찍는다.
이 모듈은 데스크톱 앱(IDE·설치 마법사·네이티브 툴) 촬영을 담당한다.
조작은 pywinauto(UIA)가, 커서 이동은 pyautogui 가 맡는다.

    from pywinauto import Desktop
    win = Desktop(backend="uia").window(title_re=r".*내앱.*")
    with record_window(win, "out.mp4"):
        ...조작...
"""

import shutil
import subprocess
import time
from pathlib import Path


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


class Recording:
    """with 문으로 쓰면 예외가 나도 ffmpeg 이 반드시 정상 종료된다.

    (실측 사고: 스크립트가 중간에 죽자 ffmpeg 이 계속 돌아 mp4 에 moov atom 이
    안 써지고 고아 프로세스가 남았다.)
    """

    def __init__(self, proc: subprocess.Popen, out: Path):
        self.proc = proc
        self.out = out

    def stop(self, timeout: float = 15.0) -> Path:
        """'q' 를 보내 정상 종료 — moov atom 이 제대로 써진다(kill 하면 파일이 깨진다)."""
        if self.proc.poll() is None:
            try:
                self.proc.communicate(input=b"q", timeout=timeout)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                self.proc.wait(timeout=5)
        return self.out

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()
        return False


def record(out: str | Path, region=None, fps: int = 30, cursor: bool = True) -> Recording:
    """region=(x, y, w, h) 없으면 전체 화면. 반환 즉시 녹화가 돈다."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    cmd = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "gdigrab", "-framerate", str(fps),
           "-draw_mouse", "1" if cursor else "0"]
    if region:
        x, y, w, h = region
        # gdigrab 은 폭·높이가 홀수면 인코더가 거부한다
        w, h = w - (w % 2), h - (h % 2)
        cmd += ["-offset_x", str(x), "-offset_y", str(y), "-video_size", f"{w}x{h}"]
    cmd += ["-i", "desktop",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", str(out)]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    time.sleep(1.0)  # 첫 프레임이 잡힐 때까지
    if proc.poll() is not None:
        raise RuntimeError(proc.stderr.read().decode("utf-8", "replace"))
    return Recording(proc, out)


def record_window(win, out: str | Path, pad: int = 0, **kw) -> Recording:
    """pywinauto 래퍼 창의 사각형만 녹화."""
    r = win.rectangle()
    return record(out, region=(r.left - pad, r.top - pad,
                               r.width() + pad * 2, r.height() + pad * 2), **kw)


def type_text(ctrl, text: str, cps: float = 14.0, prefix: str = "") -> None:
    """촬영용 타이핑 — 키 이벤트 대신 컨트롤 값을 점진적으로 덮어쓴다.

    키 입력(type_keys)은 글자 유실이 난다. 실측 2회:
      'ffmpeg gdigrab' → 'ffmupeg dgdigrab'  (한 번에 전송)
      'ffmpeg gdigrab' → 'gdigrab'           (글자당 전송, 앞부분 소실)
    매 프레임 누적 문자열을 넣으면 화면엔 타이핑처럼 보이면서 최종 텍스트가
    항상 정확하다. 촬영 자산은 정확도가 속도보다 우선이라 이 방식을 쓴다.

    ⚠️ ValuePattern 을 지원하지 않는 캔버스형 앱에는 못 쓴다 — 그런 앱은
    type_keys 를 느리게 보내고 프레임으로 결과를 확인해야 한다.
    """
    delay = 1.0 / cps
    # WindowSpecification 은 없는 속성도 지연 프록시로 돌려주므로 getattr 로는
    # 판별이 안 된다 → 반드시 wrapper_object() 를 해석한 뒤 검사한다.
    w = ctrl.wrapper_object() if hasattr(ctrl, "wrapper_object") else ctrl
    if hasattr(type(w), "set_edit_text"):
        setter = w.set_edit_text
    else:
        # WinUI3(Win11 메모장 등)의 Document 는 EditWrapper 가 아니라 UIAWrapper 라
        # set_edit_text 가 없다 → ValuePattern 을 직접 쓴다
        setter = w.iface_value.SetValue
    for i in range(1, len(text) + 1):
        setter(prefix + text[:i])
        time.sleep(delay)
