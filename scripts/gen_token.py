"""Generate or rotate the FastAPI/PWA bearer token."""

import argparse
import secrets
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.webapp_config import DEFAULT_CONFIG_PATH, load_webapp_config, save_webapp_config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or rotate the webapp bearer token.")
    parser.add_argument("--force", action="store_true", help="overwrite an existing token")
    parser.add_argument("--clear", action="store_true", help="clear the token and disable the auth gate")
    args = parser.parse_args()

    cfg = load_webapp_config()
    if args.clear:
        cfg.auth_token = ""
        save_webapp_config(cfg)
        print(f"Cleared auth_token in {DEFAULT_CONFIG_PATH}")
        print("The webapp auth gate is now off.")
        return 0

    if cfg.auth_token and not args.force:
        print(f"auth_token is already set in {DEFAULT_CONFIG_PATH}")
        print("Re-run with --force to rotate it, or --clear to disable the gate.")
        return 0

    cfg.auth_token = secrets.token_urlsafe(32)
    save_webapp_config(cfg)
    print(f"Wrote a new auth_token to {DEFAULT_CONFIG_PATH}")
    print("Restart the FastAPI webapp so it loads the new config.")
    print("Open the tokenised Cloudflare/Tailscale URL once on each device.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
