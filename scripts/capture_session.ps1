param([string]$Demo = "C:\sfo-session-demo", [string]$Work = "C:\sfo-session-work")
# Capture a real end-to-end CLI session against disposable demo data.
$ErrorActionPreference = "Continue"
foreach ($p in @($Demo, $Work)) { if (Test-Path $p) { Remove-Item -Recurse -Force $p -ErrorAction SilentlyContinue } }
New-Item -ItemType Directory -Force -Path $Demo | Out-Null
New-Item -ItemType Directory -Force -Path $Work | Out-Null

$cli = "$PSScriptRoot\..\.venv\Scripts\smart-organizer.exe"
$cfg = Join-Path $Work "config.json"

# Run the CLI without a PowerShell pipeline so an interrupted capture cannot
# truncate a long-running apply; stdout is collected to a file and replayed.
function Run-CLI([string[]]$CliArgs) {
    $stdout = Join-Path $Work "_stdout.txt"
    $stderr = Join-Path $Work "_stderr.txt"
    Start-Process -FilePath $cli -ArgumentList $CliArgs -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr -Wait -NoNewWindow
    if (Test-Path $stdout) { Get-Content $stdout }
    if ((Test-Path $stderr) -and (Get-Item $stderr).Length -gt 0) { Get-Content $stderr }
}

function Section($t) { Write-Output ""; Write-Output ("`$ " + $t) }

# Keep operation state out of the demo folder so it stays a clean sample tree.
& $cli config init $cfg --force | Out-Null
$data = Get-Content $cfg -Raw | ConvertFrom-Json
$data.storage_dir = (Join-Path $Work "state")
$data | ConvertTo-Json -Depth 10 | Set-Content $cfg -Encoding ascii

Section "smart-organizer demo `"$Demo`" --config `"$cfg`""
Run-CLI @("demo", $Demo, "--config", $cfg)
Section "smart-organizer scan `"$Demo`" --recursive --config `"$cfg`""
Run-CLI @("scan", $Demo, "--recursive", "--config", $cfg)
Section "smart-organizer preview `"$Demo`" --config `"$cfg`""
Run-CLI @("preview", $Demo, "--config", $cfg)
Section "smart-organizer duplicates `"$Demo`" --config `"$cfg`""
Run-CLI @("duplicates", $Demo, "--config", $cfg)
Section "smart-organizer organize `"$Demo`" --apply --config `"$cfg`""
$applyOut = Run-CLI @("organize", $Demo, "--apply", "--config", $cfg) | Out-String
Write-Output (($applyOut -split "`n" | Where-Object { $_ -match "moved:|Operation ID|Operation status|^Warnings|^  - " }) -join "`n")
$runId = [regex]::Match($applyOut, "(?m)^Operation ID:\s*([0-9a-f]{32})").Groups[1].Value
Section "smart-organizer history --config `"$cfg`""
Run-CLI @("history", "--config", $cfg)
Section "smart-organizer report $runId --format html --output report.html --config `"$cfg`""
Run-CLI @("report", $runId, "--format", "html", "--output", (Join-Path $Work "report.html"), "--config", $cfg)
Section "smart-organizer undo $runId --config `"$cfg`""
Run-CLI @("undo", $runId, "--config", $cfg)
Section "smart-organizer recover $runId --config `"$cfg`""
Run-CLI @("recover", $runId, "--config", $cfg)
Remove-Item (Join-Path $Work "_stdout.txt"), (Join-Path $Work "_stderr.txt") -Force -ErrorAction SilentlyContinue
Write-Output ""
Write-Output "(Disposable folders: $Demo and $Work -- remove them when done.)"