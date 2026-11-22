"""The build readout's restart route is mounted behind the bearer gate.

``src/self_restart.py`` is vendored verbatim and tested in project-scaffolding; this covers the
app-side wiring. No test posts a same-origin restart through the real app, because that would run
the real ``tray.bat --restart``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import app
from app.middleware import require_authenticated
from src.self_restart import TRAY_RESTART, RestartTarget, restart_router

TOKEN = "bearer-token-value"
LAN_CLIENT = ("192.168.1.50", 50000)
PC_CLIENT = ("127.0.0.1", 50000)
ORIGIN = {"Origin": "http://testserver"}
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def gated():
    cfg = app.state.webapp_config
    saved = cfg.auth_token
    cfg.auth_token = TOKEN
    try:
        yield
    finally:
        cfg.auth_token = saved


def test_the_app_declares_the_tray_recipe_as_its_only_target(gated) -> None:
    res = TestClient(app, client=PC_CLIENT).get("/api/restart")
    assert res.status_code == 200
    assert [t["id"] for t in res.json()["targets"]] == ["app"]


def test_a_lan_caller_without_the_token_cannot_read_or_start_a_restart(gated) -> None:
    lan = TestClient(app, client=LAN_CLIENT)
    assert lan.get("/api/restart").status_code == 401
    assert lan.post("/api/restart", json={"target": "app"}, headers=ORIGIN).status_code == 401


def test_a_cross_origin_post_is_refused_even_with_the_token(gated) -> None:
    res = TestClient(app, client=LAN_CLIENT).post(
        "/api/restart", json={"target": "app"},
        headers={"Authorization": f"Bearer {TOKEN}", "Origin": "https://elsewhere.example"},
    )
    assert res.status_code == 403


def test_the_route_refuses_when_the_middleware_is_missing(tmp_path: Path) -> None:
    bare = FastAPI()
    bare.include_router(restart_router(
        targets=[RestartTarget("app", "Restart app", TRAY_RESTART)],
        status_dir=tmp_path / "restart", guard=require_authenticated, root=tmp_path,
        spawn=lambda spec, root: pytest.fail("a restart started without auth"),
    ))
    res = TestClient(bare).post("/api/restart", json={"target": "app"}, headers=ORIGIN)
    assert res.status_code == 401


def test_the_recipe_runs_the_repos_own_tray_bat() -> None:
    assert TRAY_RESTART[-2:] == (".\\tray.bat", "--restart")
    assert (ROOT / "tray.bat").is_file()
