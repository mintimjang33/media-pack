# 바탕화면 "메인 컨트롤 열기" 아이콘 — 대시보드 서버가 꺼져 있으면 켜고, /control(메인 컨트롤
# 페이지, 개별 계정 화면과는 다른 4계정 허브 화면)을 기본 브라우저로 연다.

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

if (-not (Test-Port 8799)) {
    $env:PYTHONIOENCODING = "utf-8"
    Start-Process -WindowStyle Hidden -FilePath $py -ArgumentList "$repo\scripts\status_dashboard.py","8799" -WorkingDirectory $repo
    Start-Sleep -Seconds 2
}

Start-Process "http://127.0.0.1:8799/control"
