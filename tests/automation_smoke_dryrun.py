r"""Smoke test: dry-run of the cart-automation CLI.

Runs `run_automation.main(["--store", "mercadona", "--dry-run"])` and checks the
report against the live grocery list. Does NOT open a browser, so it is safe to
run anywhere — but it does read the real inventory xlsx configured in
`src/config.json`, so it is not a CI test.

Also exercises the `--cart-mode {keep,clean}` plumbing (issue #19): both the CLI
flag (dry-run, no browser) and `automation_runner.build_command` argv assembly.

Run from the repo root:
    & .\.venv\Scripts\python.exe tests\automation_smoke_dryrun.py
"""

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app import automation_runner  # noqa: E402
from automation.grocery_reader import read_cart_items  # noqa: E402
from automation.run_automation import main  # noqa: E402


def _run_capturing(argv: list[str]) -> tuple[int, str]:
    """Run `main(argv)` capturing stdout; return ``(exit_code, output)``."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(argv)
    return code, buf.getvalue()


def run() -> int:
    expected = len(read_cart_items("mercadona"))
    print(f"[..] grocery list reports {expected} pending Mercadona item(s)")

    exit_code = main(["--store", "mercadona", "--dry-run"])
    assert exit_code == 0, f"dry-run exited {exit_code}, expected 0"
    print("[OK] dry-run exited 0 with no errors")

    # A limited dry-run should report exactly --limit items.
    limited = main(["--store", "mercadona", "--dry-run", "--limit", "3"])
    assert limited == 0, f"limited dry-run exited {limited}, expected 0"
    print("[OK] --limit 3 dry-run exited 0")

    # Default mode is keep — the summary reports it without a browser.
    code, out = _run_capturing(["--store", "mercadona", "--dry-run"])
    assert code == 0, f"default dry-run exited {code}, expected 0"
    assert "Cart mode:         keep" in out, "default mode should be 'keep'"
    print("[OK] default dry-run reports cart mode 'keep'")

    # Clean mode is honored in dry-run: the summary reports it, without opening
    # a browser. (The per-store "would empty the cart first" notice is a
    # logging line on stderr, so it is not asserted via captured stdout.)
    code, out = _run_capturing(
        ["--store", "mercadona", "--dry-run", "--cart-mode", "clean"]
    )
    assert code == 0, f"clean dry-run exited {code}, expected 0"
    mode_line = next((ln for ln in out.splitlines() if "Cart mode:" in ln), "")
    assert "clean" in mode_line, f"clean mode should be reported, got {mode_line!r}"
    print("[OK] --cart-mode clean dry-run reports clean mode, no browser opened")

    # build_command argv assembly (issue #19): mode always threaded through.
    keep_cmd = automation_runner.build_command("mercadona", True, "keep")
    assert keep_cmd[-2:] == ["--cart-mode", "keep"], keep_cmd
    clean_cmd = automation_runner.build_command("all", False, "clean")
    assert clean_cmd[-2:] == ["--cart-mode", "clean"], clean_cmd
    assert "--store" not in clean_cmd, "store 'all' should not add --store"
    assert "--dry-run" not in clean_cmd, "dry_run False should not add --dry-run"
    # Default cart_mode is keep when omitted.
    default_cmd = automation_runner.build_command("mercadona", True)
    assert default_cmd[-2:] == ["--cart-mode", "keep"], default_cmd
    print("[OK] build_command threads --cart-mode through (keep/clean/default)")

    print("[PASS] automation_smoke_dryrun")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
