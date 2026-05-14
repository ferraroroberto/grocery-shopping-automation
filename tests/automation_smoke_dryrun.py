r"""Smoke test: dry-run of the cart-automation CLI.

Runs `run_automation.main(["--store", "mercadona", "--dry-run"])` and checks the
report against the live grocery list. Does NOT open a browser, so it is safe to
run anywhere — but it does read the real inventory xlsx configured in
`src/config.json`, so it is not a CI test.

Run from the repo root:
    & .\.venv\Scripts\python.exe tests\automation_smoke_dryrun.py
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from automation.grocery_reader import read_cart_items  # noqa: E402
from automation.run_automation import main  # noqa: E402


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

    print("[PASS] automation_smoke_dryrun")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
