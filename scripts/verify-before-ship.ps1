#Requires -Version 5.1
<#
.SYNOPSIS
    Pre-ship verification gate for grocery-shopping-automation. One pass/fail pipeline.

.DESCRIPTION
    Runs, fail-fast: the runtime write probe snapshot, pytest, the runtime write probe,
    then the static, docs, comment, structure, instruction, doc-refs, dead-code,
    conventions, runtime write and usage track ratchets. Every stage, its baseline and
    what makes it fail is described in docs/testing.md, the one full description;
    a detector or scan that cannot run (exit 3) fails as NOT CONFIRMED, never a pass.

    Anchors to the repo root, so run it from anywhere:  & .\scripts\verify-before-ship.ps1

    Verdict line: the last line on stdout is `GATE-RESULT: PASS` or
    `GATE-RESULT: FAIL`, on every path that can still run code. Never pipe the
    gate when its exit code matters (a pipeline reports its last command's
    status, so `... | tail -40` turns a red run green); a caller that cannot
    avoid a pipe asserts on that line. A run with no such line is not a pass.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

# gate-verdict:begin
# The verdict is the last stdout line of every path that can still run code. A
# terminating error under the "Stop" preference above would otherwise print to
# stderr only and exit 1, leaving a piped caller an empty, apparently clean run,
# so the trap reports it on stdout first. Exit codes stay 0 (pass) and 1 (fail).
function Write-GateResult($verdict) {
    Write-Host "GATE-RESULT: $verdict"
}

function Complete-Gate([int]$Code) {
    Write-GateResult $(if ($Code -eq 0) { "PASS" } else { "FAIL" })
    exit $Code
}

trap {
    Write-Host ""
    Write-Host "[FAIL] the gate aborted on an unhandled error:" -ForegroundColor Red
    Write-Host ("       {0}" -f $_.Exception.Message) -ForegroundColor Red
    Complete-Gate 1
}

function Invoke-Stage {
    param([string]$Name, [scriptblock]$Body)
    Write-Host ""
    Write-Host ">> $Name" -ForegroundColor Cyan
    & $Body
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] $Name (exit $LASTEXITCODE)" -ForegroundColor Red
        Complete-Gate 1
    }
    Write-Host "[PASS] $Name" -ForegroundColor Green
}
# gate-verdict:end

Set-Location (Join-Path $PSScriptRoot "..")

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "[FAIL] .venv not found at $py" -ForegroundColor Red
    Write-Host "       Create it and run: $py -m pip install -r requirements.txt" -ForegroundColor Red
    Complete-Gate 1
}

. (Join-Path $PSScriptRoot "box_lock_gate.ps1"); Enter-BoxLockGate

# Runtime write probe, first half: what already sits untracked or ignored under
# the code folders, so the probe after the tests names only what they wrote.
$runtimeSnapshot = Join-Path ([IO.Path]::GetTempPath()) "runtime-write-snapshot-$PID.json"
Invoke-Stage "runtime write probe (snapshot)" { & $py "scripts/runtime_write_ratchet.py" --snapshot $runtimeSnapshot }
Invoke-Stage "pytest"          { & $py -m pytest }
Invoke-Stage "runtime write probe" { & $py "scripts/runtime_write_ratchet.py" --probe $runtimeSnapshot }
Invoke-Stage "static ratchet"  { & $py -m scripts.static_ratchet_gate }
Invoke-Stage "docs ratchet"    { & $py -m scripts.docs_ratchet_gate }
Invoke-Stage "comment ratchet" { & $py -m scripts.comment_ratchet_gate }
Invoke-Stage "structure ratchet" { & $py -m scripts.structure_ratchet_gate }
Invoke-Stage "instruction ratchet" { & $py -m scripts.instruction_ratchet_gate }
Invoke-Stage "doc-refs ratchet" { & $py -m scripts.doc_refs_ratchet_gate }
Invoke-Stage "dead-code ratchet" { & $py -m scripts.dead_code_ratchet_gate }
Invoke-Stage "conventions ratchet" { & $py -m scripts.conventions_ratchet_gate }
Invoke-Stage "runtime write ratchet" { & $py "scripts/runtime_write_ratchet.py" }
Invoke-Stage "usage track ratchet" { & $py "scripts/usage_track_ratchet.py" }

Write-Host ""
Write-Host "[PASS] verify-before-ship" -ForegroundColor Green
Complete-Gate 0
