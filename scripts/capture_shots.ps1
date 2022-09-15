param([string]$Demo = "C:\sfo-shots", [string]$Work = "C:\sfo-shots-work")
# Produce real CLI captures with UTF-8 console output so Unicode filenames render correctly.
$ErrorActionPreference = "Continue"
foreach ($p in @($Demo, $Work)) { if (Test-Path $p) { Remove-Item -Recurse -Force $p -ErrorAction SilentlyContinue } }
New-Item -ItemType Directory -Force -Path $Work | Out-Null
$env:PYTHONIOENCODING = "utf-8"

$cli = "$PSScriptRoot\..\.venv\Scripts\smart-organizer.exe"
$cfg = Join-Path $Work "config.json"
& $cli config init $cfg --force | Out-Null
$data = Get-Content $cfg -Raw | ConvertFrom-Json
$data.storage_dir = (Join-Path $Work "state")
$data | ConvertTo-Json -Depth 10 | Set-Content $cfg -Encoding ascii

function Run-CLI([string[]]$CliArgs, [string]$Name) {
    $so = Join-Path $Work "${Name}_out.txt"
    $se = Join-Path $Work "${Name}_err.txt"
    Start-Process -FilePath $cli -ArgumentList $CliArgs -RedirectStandardOutput $so -RedirectStandardError $se -Wait -NoNewWindow
    if ((Test-Path $se) -and (Get-Item $se).Length -gt 0) { Get-Content $se }
    if (Test-Path $so) { Get-Content $so -Encoding UTF8 }
}

Run-CLI @("demo", $Demo, "--config", $cfg) "demo" | Out-Null
Run-CLI @("scan", $Demo, "--recursive", "--config", $cfg) "scan" | Set-Content (Join-Path $Work "scan.txt") -Encoding UTF8
Run-CLI @("preview", $Demo, "--config", $cfg) "preview" | Set-Content (Join-Path $Work "preview.txt") -Encoding UTF8
Run-CLI @("duplicates", $Demo, "--config", $cfg) "duplicates" | Set-Content (Join-Path $Work "duplicates.txt") -Encoding UTF8
$applyOut = Run-CLI @("organize", $Demo, "--apply", "--config", $cfg) "apply" | Out-String
$runId = [regex]::Match($applyOut, "(?m)^Operation ID:\s*([0-9a-f]{32})").Groups[1].Value
$runId | Set-Content (Join-Path $Work "runid.txt") -Encoding ascii
Write-Host "run_id=$runId"
Write-Host "captures in $Work"