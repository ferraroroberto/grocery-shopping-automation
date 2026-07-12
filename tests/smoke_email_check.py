"""Email-check smoke test: run the real end-to-end pipeline once — fetch the
latest whitelisted confirmation email, parse it, match it against the
purchase log, and send a real Telegram summary.

Usage:
    & .\\.venv\\Scripts\\python.exe tests\\smoke_email_check.py [store]
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from automation.email_check import check_latest_confirmation  # noqa: E402

store = sys.argv[1] if len(sys.argv) > 1 else "ametller"

result = check_latest_confirmation(store)

print(f"store:              {result.store}")
print(f"checked:            {result.checked}")
print(f"message_id:         {result.message_id}")
print(f"already_processed:  {result.already_processed}")
print(f"notified:           {result.notified}")
print(f"reason:             {result.reason}")

if result.match is not None:
    print(f"\nmatched ({len(result.match.matched)}):")
    for item in result.match.matched:
        print(f"  [{item.method:5s} {item.confidence:.2f}] {item.website_name!r} -> {item.comida!r}")
    if result.match.dropped_comida:
        print(f"\ndropped (in purchase log, not in email): {result.match.dropped_comida}")
    if result.match.unmatched_website_names:
        print(f"\nunmatched (in email, no comida match): {result.match.unmatched_website_names}")

assert result.checked, "expected the check to actually run"

print("\nEMAIL CHECK SMOKE TEST: PASS")
