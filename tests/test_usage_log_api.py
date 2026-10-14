"""The local usage log (#276): the vendored router mounted behind the app's auth,
writing only under the runtime-data directory, and the privacy contract held by
the markup that feeds it (constant ids, never row data)."""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

from src.usage_log import ID_PATTERN

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
# Everything this app serves outside the vendored copies (those render nothing of ours).
OWN_SOURCES = [STATIC / "index.html", *sorted((STATIC / "modules").rglob("*.js")), STATIC / "app.js"]
# The only interpolations a data-track value may carry: fixed action names, view
# modes, dialog ids and stepper kinds -- all developer constants.
CONSTANT_INTERPOLATIONS = {"${action}", "${lead.action}", "${kind}", "${mode}", "${id}"}
EVENT = {"kind": "tap", "id": "shop.fill-carts", "view": "shopping"}


def _batch(*events: dict) -> dict:
    return {"session_id": "6f9619ff-8b86-4011-b42d-00c04fc964ff", "device_class": "phone", "os_class": "ios", "events": list(events)}


def _db() -> Path:
    # <APP>_DATA_DIR names this app's own directory (no per-app subfolder).
    return Path(os.environ["GROCERY_SHOPPING_AUTOMATION_DATA_DIR"]) / "usage.sqlite3"


def test_events_land_in_the_runtime_data_file_and_summarise(client):
    res = client.post("/api/usage/events", json=_batch({"kind": "session_start"}, {"kind": "view", "id": "shopping"}, EVENT))
    assert res.status_code == 200
    assert res.json()["accepted"] == 3
    assert _db().is_file(), "the usage log must write under the runtime-data directory"

    summary = client.get("/api/usage/summary", params={"days": 1}).json()
    assert summary["views"]["shopping"] >= 1
    assert summary["controls"]["shop.fill-carts"] >= 1
    assert summary["devices"]["phone"] >= 1


def test_only_the_fixed_columns_are_stored(client):
    client.post("/api/usage/events", json=_batch({**EVENT, "text": "leche entera 1L", "url": "http://x/?q=leche"}))
    with sqlite3.connect(_db()) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(events)")]
        stored = " ".join(str(v) for row in conn.execute("SELECT * FROM events") for v in row)
    assert columns == ["id", "ts", "session_id", "kind", "view_id", "control_id", "device_class", "os_class"]
    assert "leche" not in stored and "http" not in stored


def test_ids_that_could_carry_content_are_dropped(client):
    bad = [{"kind": "tap", "id": "Fill the carts now"}, {"kind": "tap", "id": "/api/items?q=leche"}, {"kind": "tap", "id": "x" * 65}]
    res = client.post("/api/usage/events", json=_batch(*bad))
    assert res.status_code == 200
    assert res.json()["accepted"] == 0


def test_the_router_sits_behind_the_bearer_token(client):
    from app.api import app

    cfg = app.state.webapp_config
    orig = cfg.auth_token
    cfg.auth_token = "secret-token"
    try:
        # TestClient's host is not loopback, so the middleware demands the token.
        assert client.post("/api/usage/events", json=_batch(EVENT)).status_code == 401
        ok = client.post("/api/usage/events", json=_batch(EVENT), headers={"Authorization": "Bearer secret-token"})
        assert ok.status_code == 200
    finally:
        cfg.auth_token = orig


def test_every_data_track_id_is_a_constant():
    pattern = re.compile(r'data-track="([^"]*)"')
    seen: list[str] = []
    for path in OWN_SOURCES:
        for value in pattern.findall(path.read_text(encoding="utf-8")):
            seen.append(value)
            placeholders = set(re.findall(r"\$\{[^}]*\}", value))
            assert placeholders <= CONSTANT_INTERPOLATIONS, f"{path.name}: data-track={value!r} interpolates non-constant data"
            sample = re.sub(r"\$\{[^}]*\}", "x", value)
            assert ID_PATTERN.fullmatch(sample), f"{path.name}: {value!r} is not a valid id"
    assert len(seen) >= 20, "the main controls should be tracked"
