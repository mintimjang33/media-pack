"""OpenScreen CLI 백엔드 — WGC 창 녹화. wincap.py(gdigrab) 의 대체.

같은 with-문 계약을 쓴다:

    rec = oscap.record_window_title("내앱 - Chrome", "out.mp4", seconds=8)
    if rec is None: ...gdigrab 폴백...
    with rec:
        ...조작...

★gdigrab 과 다른 점: **가려진 창도 찍힌다**(WGC). 대상 창을 앞으로 끌어올 필요가 없고
  화면 밖으로 나가도 거부되지 않는다. 해상도는 창 클라이언트 영역, 60fps CFR 고정.
★설치돼 있지 않으면 `record_window_title` 이 `None` 을 돌려준다 — 배포 팩 수신자는
  미설치일 수 있으므로 호출측이 반드시 폴백해야 한다.

## 실측으로 확정한 것 (2026-09-05)

**종료 수단은 `--duration` 하나뿐이다.** CLI 도움말은 "stdin 에 stop, 또는 SIGINT/SIGTERM"
이라고 안내하지만 셋 다 실측에서 안 멈췄다(각 45초 대기 후 타임아웃):
  stdin "stop"(닫든 안 닫든) · CTRL_BREAK_EVENT · taskkill /T(비강제)
stderr 파이프를 안 읽어서 블록된 게 아닌지도 확인했다 — 배수해도 동일하다.

그래서 길이를 아는 녹화(`seconds`)는 `--duration` 으로 정상 종료시키고,
길이를 모르는 녹화(명령 동반)는 **강제 kill** 로 끊는다. 컨테이너가
fragmented-mp4 라 잘려도 파일이 유효하다(실측: 마지막 프레임까지 디코드됨).
단 강제 kill 이면 **cursor.json 사이드카가 안 써진다** — 이건 정상 종료 때만 나온다.

★kill 경로에도 `--duration` 상한을 함께 건다. 우리 kill 이 실패해도 녹화기가 스스로
  멈추게 하기 위한 것 — 안 걸면 고아 프로세스가 디스크를 계속 먹는다(실측: 30초 방치에
  11.7MB 까지 자랐다).

★`--window` 는 값에 **콜론이 있으면 즉사한다**(exit -1, 출력 0바이트). `window_token()`
  이 콜론 없는 조각으로 바꿔주고, 유일하게 지목 못 하면 None 을 돌려 폴백시킨다.

## Store(MSIX) 설치판의 함정 두 개 — 여기서 흡수한다

1) CLI 별칭을 등록하지 않는다 → 패키지 레지스트리에서 설치 경로를 읽는다.
2) 보고하는 저장 경로 `%APPDATA%\\openscreen\\...` 에는 **파일이 없다**(가상 경로).
   실제는 `%LOCALAPPDATA%\\Packages\\<family>\\LocalCache\\Roaming\\...`.
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time
import winreg
from pathlib import Path

_PKG_PREFIX = "EtienneLescot.OpenScreen_"
_REPO_KEY = (r"Software\Classes\Local Settings\Software\Microsoft\Windows"
             r"\CurrentVersion\AppModel\Repository\Packages")
_START_MARK = "capture started"
_OUTPATH_RE = re.compile(r'"outputPath":"(.*?)"')

#: kill 경로의 자기종료 상한(초). 우리 kill 이 실패해도 녹화기가 스스로 멈춘다.
MAX_SECONDS = 3600


def exe_path() -> Path | None:
    """설치된 Openscreen.exe. 없으면 None.

    ★`C:\\Program Files\\WindowsApps` 를 glob 하면 안 된다 — 이 폴더는 **열거 금지·직접
      접근 허용**이라 os.listdir 은 PermissionError 를 던지고 Path.glob 은 그걸 삼켜
      **조용히 빈 리스트**를 준다(실측). 미설치와 구분이 안 되는 죽은 가드가 된다.
      설치 경로는 패키지 레지스트리에서 읽는다(버전이 폴더명에 박혀 있으므로 필수).
    """
    env = os.environ.get("OPENSCREEN_EXE")
    if env:
        p = Path(env)
        return p if p.exists() else None
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REPO_KEY)
    except OSError:
        return None
    roots: list[str] = []
    with key:
        for i in range(winreg.QueryInfoKey(key)[0]):
            name = winreg.EnumKey(key, i)
            if not name.startswith(_PKG_PREFIX):
                continue
            try:
                with winreg.OpenKey(key, name) as sub:
                    roots.append(winreg.QueryValueEx(sub, "PackageRootFolder")[0])
            except OSError:
                continue
    for root in sorted(roots, reverse=True):  # 업데이트 중이면 두 버전이 함께 등록된다
        exe = Path(root) / "app" / "Openscreen.exe"
        if exe.exists():
            return exe
    return None


def available() -> bool:
    return exe_path() is not None


def _resolve_media(reported: str) -> Path | None:
    """보고된 경로 → 실제 파일. MSIX 리다이렉션을 보정한다."""
    if not reported:
        return None
    p = Path(reported)
    if p.exists():
        return p
    try:
        rel = p.relative_to(Path(os.environ["APPDATA"]))
    except (ValueError, KeyError):
        return None
    pkgs = Path(os.environ["LOCALAPPDATA"]) / "Packages"
    for cache in pkgs.glob("EtienneLescot.OpenScreen_*/LocalCache/Roaming"):
        cand = cache / rel
        if cand.exists():
            return cand
    return None


def list_windows() -> list[dict]:
    """`sources --json` 이 보는 창 목록 [{id, name}...]."""
    exe = exe_path()
    if exe is None:
        return []
    r = subprocess.run([str(exe), "sources", "--json"],
                       capture_output=True, timeout=90, check=False)
    for line in r.stdout.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("event") == "done":
            return ev.get("sources", {}).get("windows", [])
    return []


def window_token(title: str) -> str | None:
    """`--window` 에 넘길 값. 지정할 수 없으면 None(호출측은 gdigrab 으로 폴백).

    ★`--window` 는 값에 **콜론이 하나라도 있으면 즉사한다** — exit -1, stdout·stderr 둘 다
      비어 있어서 원인이 전혀 안 남는다(실측). 소스 ID(`window:12345:0`) 를 넘겨도 같다.
      제목에 콜론은 흔하다("12:30", "제목: 부제", "about:blank") → 콜론으로 쪼개 가장 긴
      조각을 대신 쓰고, 그 조각이 그 창을 **유일하게** 지목하는지 확인한다.
    """
    if ":" not in title:
        return title
    parts = [p.strip() for p in title.split(":") if p.strip()]
    if not parts:
        return None
    token = max(parts, key=len)
    hits = [w for w in list_windows() if token in w.get("name", "")]
    if len(hits) == 1 and hits[0].get("name") == title:
        return token
    return None


def _drop_source(src: Path, copied: Path) -> None:
    """앱 폴더의 원본을 지운다 — **복사본 크기가 일치할 때만**.

    `record` 는 출력 경로를 못 받아서 항상 앱 recordings 폴더에 쓴다. 우리가 out 으로
    복사해 온 뒤엔 그쪽은 중복이고, 안 지우면 캡처마다 쌓인다(실측: 시험분만 191MB).
    크기 대조를 통과 못 하면 **원본을 남긴다** — 복사가 어긋났을 때 잃지 않기 위해서다.
    """
    try:
        if copied.exists() and copied.stat().st_size == src.stat().st_size:
            src.unlink()
    except OSError:
        pass


class Recording:
    """with 문을 나가면 녹화를 끊고 산출물을 out 으로 옮긴다.

    stdout/stderr 는 전용 스레드가 계속 배수한다 — 파이프가 차서 녹화기가 멎는 걸 막고,
    stderr 의 시작 로그에서 저장 경로를 미리 건져둔다(강제 kill 이면 done 이벤트가 없다).
    """

    def __init__(self, proc: subprocess.Popen, out: Path, duration: float | None):
        self.proc = proc
        self.out = out
        self._duration = duration
        self._started = threading.Event()
        self._outpath = ""
        self._stdout: list[bytes] = []
        self._stderr: list[bytes] = []
        self._t_out = threading.Thread(target=self._drain_stdout, daemon=True)
        self._t_err = threading.Thread(target=self._drain_stderr, daemon=True)
        self._t_out.start()
        self._t_err.start()

    def _drain_stdout(self) -> None:
        for line in self.proc.stdout:
            self._stdout.append(line)

    def _drain_stderr(self) -> None:
        for line in self.proc.stderr:
            self._stderr.append(line)
            text = line.decode("utf-8", "replace")
            if not self._outpath:
                m = _OUTPATH_RE.search(text)
                if m:
                    try:
                        self._outpath = json.loads('"' + m.group(1) + '"')
                    except json.JSONDecodeError:
                        pass
            if _START_MARK in text:
                self._started.set()

    def wait_started(self, timeout: float = 30.0) -> None:
        """첫 프레임이 잡힐 때까지 블록. 고정 sleep 은 startup 이 길어지면 앞부분을 놓친다."""
        if not self._started.wait(timeout):
            self._force_kill()
            raise RuntimeError(
                "OpenScreen 이 녹화를 시작하지 못했다\n" + self._err_tail())

    def _err_tail(self, n: int = 2000) -> str:
        return b"".join(self._stderr).decode("utf-8", "replace")[-n:]

    def _force_kill(self) -> None:
        subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                       capture_output=True, check=False)
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass

    def _done_event(self) -> dict | None:
        for line in self._stdout:
            text = line.decode("utf-8", "replace").strip()
            if not text.startswith("{"):
                continue
            try:
                ev = json.loads(text)
            except json.JSONDecodeError:
                continue
            if ev.get("event") == "done":
                return ev
        return None

    def stop(self) -> Path:
        if self._duration is not None:
            # --duration 이 스스로 멈춘다 — 유일하게 done 이벤트와 cursor.json 이 나오는 경로
            try:
                self.proc.wait(timeout=self._duration + 120)
            except subprocess.TimeoutExpired:
                self._force_kill()
        elif self.proc.poll() is None:
            self._force_kill()
        self._t_out.join(timeout=15)
        self._t_err.join(timeout=15)

        done = self._done_event()
        reported = (done or {}).get("screenVideoPath") or self._outpath
        src = _resolve_media(reported)
        if src is None:
            raise RuntimeError(
                f"녹화 파일을 못 찾음 (보고된 경로: {reported!r})\n" + self._err_tail())

        self.out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, self.out)
        _drop_source(src, self.out)

        cur = _resolve_media((done or {}).get("cursorDataPath") or "") or \
            _resolve_media(reported + ".cursor.json")
        if cur is not None and cur.exists():
            dst = self.out.with_name(self.out.name + ".cursor.json")
            shutil.copy2(cur, dst)
            _drop_source(cur, dst)
        return self.out

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()
        return False


def record_window_title(title: str, out: str | Path,
                        seconds: float | None = None) -> Recording | None:
    """제목에 `title` 이 들어가는 첫 창을 녹화. 미설치면 None.

    `seconds` 를 주면 그 길이로 정상 종료한다(cursor.json 도 나온다).
    주지 않으면 stop() 이 강제로 끊는다 — 길이를 모르는 녹화용.
    """
    exe = exe_path()
    if exe is None:
        return None
    token = window_token(title)
    if token is None:
        return None
    out = Path(out)
    cap = seconds if seconds else MAX_SECONDS
    proc = subprocess.Popen(
        [str(exe), "record", "--window", token, "--duration", str(int(cap)), "--json"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    rec = Recording(proc, out, duration=seconds)
    rec.wait_started()
    time.sleep(0.3)
    return rec
