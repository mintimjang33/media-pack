# 바탕화면 "렌더링 화면 열기" 아이콘 — Remotion 스튜디오(포트 3010)가 꺼져 있으면 켜고,
# 대시보드 서버(포트 8799, "장면 갱신" 버튼이 있는 /remotion 페이지)도 꺼져 있으면 같이 켠 뒤
# 둘 다 브라우저 탭으로 연다. open_control.ps1과 완전히 같은 패턴(포트 확인 → 없으면 실행 →
# 브라우저 열기)을 그대로 따른다.
# 2026-09-14 수정 — 사용자 지적: "랜더링 화면열기를 하면 자동으로 서버도 켜저야지" — 로직
# 자체는 원래도 맞았지만(직접 실행해서 확인함), 고정 Start-Sleep(2초/5초)만 기다리고 바로
# 브라우저를 열어서 Remotion 스튜디오처럼 첫 번들링이 오래 걸리는 경우 "사이트에 연결할 수
# 없음"이 떠서 마치 서버가 안 켜진 것처럼 보였을 수 있다 — 고정 대기 대신 포트가 실제로
# 열릴 때까지(최대 60초) 반복 확인한 뒤에 브라우저를 연다.

$ErrorActionPreference = "SilentlyContinue"
$repo = "C:\Users\user\Downloads\flow-media-pack"
$py = "C:\Users\user\AppData\Local\Programs\Python\Python312\python.exe"

function Test-Port($port) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect("127.0.0.1", $port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(800)
        $c.Close()
        return $ok
    } catch { return $false }
}

function Wait-Port($port, $maxSeconds) {
    $elapsed = 0
    while (-not (Test-Port $port) -and $elapsed -lt $maxSeconds) {
        Start-Sleep -Seconds 1
        $elapsed += 1
    }
}

if (-not (Test-Port 8799)) {
    $env:PYTHONIOENCODING = "utf-8"
    Start-Process -WindowStyle Hidden -FilePath $py -ArgumentList "$repo\scripts\status_dashboard.py","8799" -WorkingDirectory $repo
    Wait-Port 8799 20
}

if (-not (Test-Port 3010)) {
    Start-Process -WindowStyle Hidden -FilePath "npx.cmd" -ArgumentList "remotion","studio","src/index.ts","--port","3010" -WorkingDirectory "$repo\remotion"
    Wait-Port 3010 60
}

Start-Process "http://127.0.0.1:8799/remotion"
Start-Process "http://localhost:3010/EconVideo"
