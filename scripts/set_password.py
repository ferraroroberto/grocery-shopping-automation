"""Set or clear the FastAPI/PWA login password."""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.webapp_config import DEFAULT_CONFIG_PATH, load_webapp_config, save_webapp_config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Set or clear the webapp login password.")
    parser.add_argument("password", nargs="?", help="password to set")
    parser.add_argument("--clear", action="store_true", help="clear the password prompt")
    args = parser.parse_args()

    cfg = load_webapp_config()
    if args.clear:
        cfg.auth_password = ""
        save_webapp_config(cfg)
        print(f"Cleared auth_password in {DEFAULT_CONFIG_PATH}")
        return 0

    if not args.password:
        parser.error("provide a password, or use --clear")
    if not cfg.auth_token:
        print("No auth_token is set yet. Run scripts\\gen_token.py first.")
        return 1

    cfg.auth_password = args.password
    save_webapp_config(cfg)
    print(f"Set auth_password in {DEFAULT_CONFIG_PATH}")
    print("Restart the FastAPI webapp so it loads the new config.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
