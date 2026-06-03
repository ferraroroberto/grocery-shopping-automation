"""Playwright browser factory for grocery-store cart automation.

Drives **real Chrome** (``channel="chrome"``) with a single **dedicated,
shared** persistent profile under ``automation/chrome_user_data/`` — created by
``bootstrap_session.py``. The user's normal Chrome profile is never opened,
read, or written.

Why real Chrome + a persistent profile (instead of cookie-export JSON):
Mercadona and Ametller both fingerprint Playwright's bundled Chromium and can
challenge or block the session. Real Chrome with a stable on-disk profile
presents a normal browser environment, and the human-driven bootstrap login
persists across runs without re-prompting.
"""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path

from playwright.sync_api import (
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)

logger = logging.getLogger(__name__)

# Single shared profile for every store (decided during issue #1 planning).
USER_DATA_DIR = Path(__file__).resolve().parent / "chrome_user_data"

# Chrome launch config — disable the flag that automation-aware sites sniff.
_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=Translate",
    "--no-default-browser-check",
    "--no-first-run",
]
_VIEWPORT = {"width": 1280, "height": 900}

# Playwright adds `--enable-automation` by default — that switch is what makes
# Chrome show the "automated test software is controlling Chrome" infobar and
# is a trivial bot tell. Dropping it makes the window present as a normal
# browser; `--disable-blink-features=AutomationControlled` above already clears
# the `navigator.webdriver` flag.
_IGNORE_DEFAULT_ARGS = ["--enable-automation", "--enable-blink-features=IdleDetection"]

# URL substrings that mark a "logged out / please sign in" redirect, per store.
# Checked case-insensitively against the URL after navigation settles.
_LOGIN_URL_MARKERS: dict[str, tuple[str, ...]] = {
    "mercadona": ("/login", "/signin", "/sign-in"),
    "ametller": ("/login", "/iniciar-sesion", "/account/login"),
}


class ProfileNotInitializedError(RuntimeError):
    """Raised when the shared Chrome profile has not been bootstrapped yet."""


class SessionExpiredError(RuntimeError):
    """Raised when a store redirects to its login page — the session is stale."""

    def __init__(self, store: str) -> None:
        super().__init__(
            f"'{store}' redirected to a login page — the saved Chrome profile "
            f"session has expired. Re-run `python -m automation.bootstrap_session` "
            f"and log in again."
        )
        self.store = store


def _profile_initialized(user_data_dir: Path) -> bool:
    """A persistent profile is ready once Chrome has written its Default subdir."""
    return user_data_dir.exists() and (user_data_dir / "Default").exists()


def _open_context(
    playwright: Playwright, *, headless: bool
) -> tuple[BrowserContext, Page]:
    """Launch the persistent Chrome context and return its context + first page.

    Shared by :func:`launch_context` and the bootstrap script. Does **not**
    check whether the profile is initialized — the bootstrap deliberately runs
    against an empty profile directory.
    """
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(USER_DATA_DIR),
        channel="chrome",
        headless=headless,
        args=_LAUNCH_ARGS,
        ignore_default_args=_IGNORE_DEFAULT_ARGS,
        # Playwright defaults chromium_sandbox to False, which injects
        # `--no-sandbox` and makes Chrome show a "this flag is not supported,
        # it affects stability and security" infobar — a bot tell. Enable the
        # sandbox so the window presents as a normal, sandboxed Chrome session.
        chromium_sandbox=True,
        viewport=_VIEWPORT,
    )
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    # launch_persistent_context already opens one default page.
    page = context.pages[0] if context.pages else context.new_page()
    return context, page


def human_delay(min_s: float = 0.5, max_s: float = 2.0) -> None:
    """Sleep a random duration to space out actions like a human would.

    Store handlers (issues #2 / #3) call this between navigations and clicks.
    """
    time.sleep(random.uniform(min_s, max_s))


def launch_context(
    *, headless: bool = False
) -> tuple[Playwright, BrowserContext, Page]:
    """Launch real Chrome on the shared persistent profile.

    Args:
        headless: Run without a visible window. Defaults to ``False`` — the
            store sites are best driven headed.

    Returns:
        ``(playwright, context, page)``. The caller owns cleanup: call
        ``context.close()`` then ``playwright.stop()`` (or use a try/finally).

    Raises:
        ProfileNotInitializedError: the profile has not been bootstrapped.
    """
    if not _profile_initialized(USER_DATA_DIR):
        raise ProfileNotInitializedError(
            f"Chrome profile at {USER_DATA_DIR} is empty or missing. "
            f"Run `python -m automation.bootstrap_session` first."
        )

    playwright = sync_playwright().start()
    context, page = _open_context(playwright, headless=headless)
    logger.info(
        "🌐 Chrome context started (channel=chrome, headless=%s, profile=%s)",
        headless,
        USER_DATA_DIR,
    )
    return playwright, context, page


def goto_with_login_check(
    page: Page, store: str, url: str, *, timeout_ms: int = 30000
) -> None:
    """Navigate ``page`` to ``url``, raising on a login redirect.

    Args:
        page: The page to navigate.
        store: Store key — selects which login-URL markers to check against.
        url: Destination URL.
        timeout_ms: Navigation timeout in milliseconds.

    Raises:
        SessionExpiredError: the store bounced the request to a sign-in page.
    """
    logger.debug("➡️ [%s] navigating to %s", store, url)
    page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
    current = (page.url or "").lower()
    markers = _LOGIN_URL_MARKERS.get(store.lower(), ())
    if any(marker in current for marker in markers):
        raise SessionExpiredError(store)
