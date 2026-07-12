"""Notify smoke test: build the Telegram notifier from this app's config
layer and confirm delivery works — or that it's a documented no-op when
unconfigured.

Usage:
    & .\\.venv\\Scripts\\python.exe tests\\smoke_notify.py ["custom message text"]
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.notify import NotifierError  # noqa: E402
from src.notify_config import build_notify_notifier, is_notify_configured  # noqa: E402

DEFAULT_MESSAGE = "✅ grocery-shopping-automation notify smoke test — delivery works."

message = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MESSAGE

if not is_notify_configured():
    print(
        "[SKIP] no Telegram credentials configured (config/notify_config.json or "
        "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID) — build_notify_notifier() returns None"
    )
    notifier = build_notify_notifier()
    assert notifier is None, "expected None when unconfigured"
    print("[OK] unconfigured path confirmed: notifier is None, nothing crashes")
else:
    notifier = build_notify_notifier()
    assert notifier is not None, "expected a notifier when credentials are set"
    try:
        notifier.send_text(message)
    except NotifierError as exc:
        raise AssertionError(f"Telegram delivery failed: {exc}") from exc
    print(f"[OK] delivered to configured Telegram chat: {message!r}")

print("\nNOTIFY SMOKE TEST: PASS")
