$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $PSScriptRoot
$Python = if ($env:MINI_CC_PYTHON) { $env:MINI_CC_PYTHON } else { "python" }
$OutDir = Join-Path $Repo ".mini_cc\terminal-bench-smoke"
$ReportDir = Join-Path $OutDir "report"
$TaskFile = Join-Path $OutDir "tasks.txt"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null
Set-Content -Path $TaskFile -Value "hello-world" -Encoding ASCII

$Template = 'python -m terminal_bench --output-dir {output_dir} --task-id {task_ids}'
& $Python -m mini_cc --terminal-bench-real-run $TaskFile --tb-command-template $Template --tb-output-dir $OutDir --benchmark-report-output $ReportDir --tb-dry-run --tb-preflight-only

Write-Host "Terminal-Bench smoke preflight artifact: $ReportDir\terminal-bench-preflight.json"
