#Requires -Version 5.1
<#
.SYNOPSIS
    Pre-ship verification gate for grocery-shopping-automation. One pass/fail pipeline.

.DESCRIPTION
    Runs, fail-fast:
      1. pytest        - the unit + e2e suite (tests/); it never reads live state, a real cart or a real browser profile
      2. static ratchet - scripts/static_ratchet.py through scripts/static_ratchet_gate.py:
                         fails only on a duplicate block (jscpd via npx) or an unused
                         import/variable (ruff F401,F841) that is not in
                         .static-ratchet-baseline.json. A detector that cannot run
                         (exit 3) fails as NOT CONFIRMED, never a pass or a skip.
                         Needs Node and ruff in the .venv. Not path-routed: duplication
                         needs the whole tree.
      3. docs ratchet   - scripts/docs_ratchet.py through scripts/docs_ratchet_gate.py:
                         fails when README.md outgrows its byte budget or README.md /
                         docs/**/*.md gains an issue reference, a dated heading or a
                         changelog heading beyond .docs-ratchet-baseline.json. A scan that
                         cannot complete (exit 3) fails as NOT CONFIRMED. Not path-routed:
                         a count needs the whole tree.
      4. comment ratchet - scripts/comment_ratchet.py through scripts/comment_ratchet_gate.py:
                         fails when a file's count of `#NNN` issue references inside
                         comments rises above .comment-ratchet-baseline.json. Existing
                         debt is baselined and never blocks; it just cannot grow. A scan
                         that cannot complete (exit 3) fails as NOT CONFIRMED. Not
                         path-routed: the count needs the whole tree.

    Anchors to the repo root, so run it from anywhere:  & .\scripts\verify-before-ship.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "[FAIL] .venv not found at $py" -ForegroundColor Red
    Write-Host "       Create it and run: $py -m pip install -r requirements.txt" -ForegroundColor Red
    exit 1
}

function Invoke-Stage {
    param([string]$Name, [scriptblock]$Body)
    Write-Host ""
    Write-Host ">> $Name" -ForegroundColor Cyan
    & $Body
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] $Name (exit $LASTEXITCODE)" -ForegroundColor Red
        exit 1
    }
    Write-Host "[PASS] $Name" -ForegroundColor Green
}

Invoke-Stage "pytest"          { & $py -m pytest }
Invoke-Stage "static ratchet"  { & $py -m scripts.static_ratchet_gate }
Invoke-Stage "docs ratchet"    { & $py -m scripts.docs_ratchet_gate }
Invoke-Stage "comment ratchet" { & $py -m scripts.comment_ratchet_gate }

Write-Host ""
Write-Host "[PASS] verify-before-ship" -ForegroundColor Green
