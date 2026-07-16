"""FastAPI entrypoint for the PWA.

Composition root only: build the app, wire the middleware and the static
mount, and include one router per concern. Every route lives in
``app/routers/`` — add endpoints there, not here.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import email_poller
from app.api_common import STATIC_DIR
from app.middleware import BearerTokenMiddleware
from app.routers import audio, automation, email, inventory, product_search, system, voice
from app.static_files import BUILD_INFO, CachingStaticFiles
from src.data import CONFIG
from src.webapp_config import load_webapp_config


@asynccontextmanager
async def _lifespan(_: FastAPI):
    # Don't spawn the real email-poll thread inside pytest's TestClient
    # startups — a test run must never trigger a live Gmail check.
    if "PYTEST_CURRENT_TEST" not in os.environ:
        email_poller.start_poller()
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
app.mount("/static", CachingStaticFiles(directory=STATIC_DIR, build_info=BUILD_INFO), name="static")

for _router in (
    system.router,
    inventory.router,
    automation.router,
    product_search.router,
    audio.router,
    voice.router,
    email.router,
):
    app.include_router(_router)
