#Requires -Version 5.1
<#
.SYNOPSIS
    Pre-ship verification gate for grocery-shopping-automation. One pass/fail pipeline.

.DESCRIPTION
    Runs, fail-fast:
      1. pytest        - the unit + e2e suite (tests/); it never reads live state, a real cart or a real browser profile
      2. static ratchet - scripts/static_ratchet.py through scripts/static_ratchet_gate.py:
                         fails only on a duplicate block (jscpd via npx), an unused
                         import/variable (ruff F401,F841) or a likely-bug or security
                         pattern (the curated ruff B and S families) that is not in
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
      5. structure ratchet - scripts/structure_ratchet.py through scripts/structure_ratchet_gate.py:
                         fails when a file passes 1,000 lines, a Python/JS function 100
                         lines or a directory 40 modules beyond
                         .structure-ratchet-baseline.json, when a src module imports app,
                         or when a root folder falls outside the shape and extra_roots
                         declared in .fleet.toml. A tree that cannot be measured (exit 3)
                         fails as NOT CONFIRMED. Not path-routed: it reads the whole tree.
      6. instruction ratchet - scripts/instruction_ratchet.py through scripts/instruction_ratchet_gate.py:
                         fails when a prompt-audit rule count in CLAUDE.md, AGENTS.md or a
                         SKILL.md rises above .instruction-ratchet-baseline.json, or an
                         instruction file passes its size cap. A file that cannot be linted
                         (exit 3) fails as NOT CONFIRMED. Not path-routed: one file's reader
                         depends on another (AGENTS.md pointing at CLAUDE.md).
      7. doc-refs ratchet - scripts/doc_refs_ratchet.py through scripts/doc_refs_ratchet_gate.py:
                         fails when README.md, CLAUDE.md or docs/**/*.md names a repo path,
                         python -m module, relative link or loopback port that does not
                         exist, beyond .doc-refs-ratchet-baseline.json. A scan that cannot
                         complete (exit 3) fails as NOT CONFIRMED. Not path-routed: a code
                         change can strand a doc reference without touching the doc.
      8. dead-code ratchet - scripts/dead_code_ratchet.py through scripts/dead_code_ratchet_gate.py:
                         fails when a Python function, class or method that nothing in the
                         tracked tree references is not in .dead-code-ratchet-baseline.json.
                         A tree that cannot be parsed (exit 3) fails as NOT CONFIRMED. Not
                         path-routed: deleting the last caller in one file strands a
                         definition in another.
      9. conventions ratchet - scripts/conventions_ratchet.py through scripts/conventions_ratchet_gate.py:
                         fails when a file's hit count for an enabled rule (no-window,
                         logging-not-print, no-drive-path) rises above
                         .conventions-ratchet-baseline.json. A tree that cannot be parsed
                         (exit 3) fails as NOT CONFIRMED. Not path-routed: a rule added to
                         .fleet.toml must see the whole tree.

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
Invoke-Stage "structure ratchet" { & $py -m scripts.structure_ratchet_gate }
Invoke-Stage "instruction ratchet" { & $py -m scripts.instruction_ratchet_gate }
Invoke-Stage "doc-refs ratchet" { & $py -m scripts.doc_refs_ratchet_gate }
Invoke-Stage "dead-code ratchet" { & $py -m scripts.dead_code_ratchet_gate }
Invoke-Stage "conventions ratchet" { & $py -m scripts.conventions_ratchet_gate }

Write-Host ""
Write-Host "[PASS] verify-before-ship" -ForegroundColor Green
