"""One-time interactive login that prepares the shared Chrome profile.

Usage:
    python -m automation.bootstrap_session [--chrome-path PATH]

Launches a **plain, un-instrumented Chrome** (a normal ``chrome.exe`` process,
*not* Playwright) pointed at the dedicated profile directory
``automation/chrome_user_data/`` (gitignored, separate from your normal Chrome
profile). You log into each store in that window, then close it — Chrome writes
the sessions into the profile directory.

Why plain Chrome and not Playwright for this step: the store login pages are
protected by Google reCAPTCHA, which detects Playwright's CDP instrumentation
and refuses to issue a challenge ("Could not connect to the reCAPTCHA
service"). A normal Chrome process started with only ``--user-data-dir`` looks
exactly like a human's browser, so the login completes. Once logged in,
:func:`automation.browser.launch_context` can drive the same profile with
Playwright for cart operations, which are not reCAPTCHA-gated.

Re-run this whenever a store's session expires.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
from pathlib import Path

from automation.browser import USER_DATA_DIR

logger = logging.getLogger("automation.bootstrap_session")

# Stores to open as tabs in the bootstrap Chrome window.
_BOOTSTRAP_URLS: list[str] = [
    "https://tienda.mercadona.es",
    "https://www.ametllerorigen.com/es",
]


def _find_chrome() -> Path:
    """Locate the installed Chrome executable in the usual Windows locations."""
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Google/Chrome/Application/chrome.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "Could not find chrome.exe in the usual locations. Pass the path "
        "explicitly with --chrome-path."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-time grocery-store session bootstrap."
    )
    parser.add_argument(
        "--chrome-path",
        default=None,
        help="Absolute path to chrome.exe (auto-detected if omitted).",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def launch_bootstrap_chrome(chrome_path: str | Path | None = None) -> subprocess.Popen:
    """Spawn the plain, uninstrumented Chrome window used to (re)log into each store.

    Returns as soon as the process is spawned — it does not wait for the user
    to log in or close the window. Shared by the CLI ``main()`` (which adds an
    interactive confirmation pause) and the PWA's bootstrap-session endpoint
    (which has no terminal to prompt on).
    """
    resolved = Path(chrome_path) if chrome_path else _find_chrome()
    if not resolved.is_file():
        raise FileNotFoundError(f"Chrome not found at {resolved}")

    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("🚀 Grocery-store session bootstrap")
    logger.info("🌐 Chrome: %s", resolved)
    logger.info("📁 Dedicated profile: %s", USER_DATA_DIR)
    logger.info("   (this is SEPARATE from your normal Chrome profile)")

    # Plain Chrome — only --user-data-dir, no automation flags. This is what
    # makes the reCAPTCHA-protected login pages behave normally.
    return subprocess.Popen(
        [str(resolved), f"--user-data-dir={USER_DATA_DIR}", *_BOOTSTRAP_URLS]
    )


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    try:
        proc = launch_bootstrap_chrome(args.chrome_path)
    except FileNotFoundError as exc:
        logger.error("❌ %s", exc)
        return 2

    # Intentional print + input: this is the one interactive pause.
    print(
        "\n>>> A Chrome window opened with a tab per store.\n"
        ">>> Log into EACH store, then CLOSE that Chrome window completely.\n"
        ">>> Once it is fully closed, press Enter here...\n"
    )
    try:
        input()
    except KeyboardInterrupt:
        logger.warning("❌ Bootstrap cancelled.")
        return 2

    if proc.poll() is None:
        logger.warning("⚠️  Chrome is still running — waiting up to 30s for it to close...")
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            logger.error(
                "❌ Chrome is still open. Close it completely and re-run — the "
                "profile may not have saved while Chrome held the directory lock."
            )
            return 2

    logger.info("✅ Chrome profile saved → %s", USER_DATA_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
