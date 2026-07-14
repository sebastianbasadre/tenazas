$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$profile = Join-Path $HOME ".openclaw-linkedin-profile"
$tokenFile = Join-Path $profile "bridge-token"
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"

New-Item -ItemType Directory -Force -Path $profile | Out-Null
if (-not (Test-Path $tokenFile)) {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    [IO.File]::WriteAllText($tokenFile, [Convert]::ToHexString($bytes).ToLowerInvariant())
}

if (-not (Get-NetTCPConnection -LocalPort 9223 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $chrome -ArgumentList "--user-data-dir=`"$profile`" --profile-directory=Default --remote-debugging-port=9223 --remote-debugging-address=127.0.0.1 --no-first-run --no-default-browser-check https://www.linkedin.com/feed/"
    Start-Sleep -Seconds 5
}

$env:LINKEDIN_BRIDGE_HOST = "192.168.2.73"
if (-not (Get-NetTCPConnection -LocalPort 9224 -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalAddress -eq "192.168.2.73" })) {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match "linkedin_browser_bridge.py" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Process -FilePath "python" -ArgumentList "`"$root\tools\linkedin_browser_bridge.py`"" -WindowStyle Hidden
    Start-Sleep -Seconds 3
}
