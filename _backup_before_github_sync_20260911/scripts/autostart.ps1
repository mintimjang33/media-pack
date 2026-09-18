# 대시보드 서버(8799)와 Flow 자동화 크롬(9223)을 자동으로 켠다 — 이미 떠있으면 건드리지 않는다.
# 2026-09-10 추가 — "연결을 하면 자동으로 켜져야지"라는 요청으로, 로그온 시 자동 실행되도록
# Windows 작업 스케줄러에도 등록해서 사람이 매번 켤 필요 없게 만들었다.

$ErrorActionPreference = "SilentlyContinue"
$repo = "C:\Users\user\Downloads\flow-media-pack"

function Test-Port($port) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect("127.0.0.1", $port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(800)
        $c.Close()
        return $ok
    } catch { return $false }
}

# 1) 대시보드 서버
if (-not (Test-Port 8799)) {
    $env:PYTHONIOENCODING = "utf-8"
    Start-Process -WindowStyle Hidden -FilePath "python" `
        -ArgumentList "$repo\scripts\status_dashboard.py","8799" `
        -WorkingDirectory $repo
    Write-Output "Dashboard server started (8799)"
} else {
    Write-Output "Dashboard server already running (8799)"
}

# 2) Flow 자동화 크롬
if (-not (Test-Port 9223)) {
    Start-Process "C:\Program Files\Google\Chrome\Application\chrome.exe" -ArgumentList `
        "--remote-debugging-port=9223", `
        "--user-data-dir=C:\Users\user\flow-automation-chrome", `
        "--no-first-run", "--no-default-browser-check", `
        "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding", `
        "--disable-background-timer-throttling", `
        "https://flow.google.com/project/6f8960c1-1b98-4e73-b5c0-0e63010bf835"
    Write-Output "Flow automation chrome started (9223)"
} else {
    Write-Output "Flow automation chrome already running (9223)"
}
