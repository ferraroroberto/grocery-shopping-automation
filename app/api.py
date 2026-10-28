"""FastAPI entrypoint for the PWA.

Composition root only: build the app, wire the middleware and the static
mount, and include one router per concern. Every route lives in
``app/routers/`` — add endpoints there, not here.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import email_poller
from app.api_common import STATIC_DIR
from app.middleware import BearerTokenMiddleware
from app.routers import audio, automation, email, inventory, product_search, stores, system, usage, voice
from app.static_files import BUILD_INFO, CachingStaticFiles
from src.compression import StreamSafeGZipMiddleware
from src.data import CONFIG
from src.webapp_config import EMAIL_POLLER_ENV, load_webapp_config


logger = logging.getLogger("email_poller")


@asynccontextmanager
async def _lifespan(_: FastAPI):
    # The poller is opt-in per process: only the tray sets EMAIL_POLLER_ENV, so a
    # worktree, fixture or dev instance never reads the real mailbox. The
    # pytest guard stays on top — a test run must never trigger a live Gmail
    # check, even with the flag inherited.
    if "PYTEST_CURRENT_TEST" in os.environ:
        pass
    elif os.environ.get(EMAIL_POLLER_ENV) == "1":
        email_poller.start_poller()
    else:
        logger.info(f"ℹ️ Email poller is off (not launched by the tray; set {EMAIL_POLLER_ENV}=1 to opt in)")
    yield


app = FastAPI(
    title=CONFIG["app"]["title"],
    version=CONFIG["app"]["version"],
    description=CONFIG["app"]["description"],
    lifespan=_lifespan,
)
app.state.webapp_config = load_webapp_config()
app.add_middleware(
    BearerTokenMiddleware,
    get_token=lambda: getattr(app.state.webapp_config, "auth_token", ""),
)
# Added last so it is the outermost layer: an auth refusal or a 304 passes through unchanged.
app.add_middleware(StreamSafeGZipMiddleware, bypass_paths=())
app.mount("/static", CachingStaticFiles(directory=STATIC_DIR, build_info=BUILD_INFO), name="static")

for _router in (
    system.router,
    inventory.router,
    stores.router,
    automation.router,
    product_search.router,
    audio.router,
    voice.router,
    email.router,
    usage.router,
):
    app.include_router(_router)
