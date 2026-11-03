"""The password route's lock-out and the guessing alert, on a fake clock and a fake notifier."""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from app.api import app
from src.login_alert import build_login_alert
from src.login_lockout import LoginLockout

PASSWORD = "correct-horse"
TOKEN = "bearer-token-value"
LAN_CLIENT = ("192.168.1.50", 50000)
PC_CLIENT = ("127.0.0.1", 50000)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeNotifier:
    """Records sends; ``sent`` is set by the alert thread, so a test waits on it rather than sleeping."""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.sent = threading.Event()

    def send_text(self, text: str) -> None:
        self.texts.append(text)
        self.sent.set()


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def notifier() -> FakeNotifier:
    return FakeNotifier()


@pytest.fixture()
def lan(clock: FakeClock, notifier: FakeNotifier):
    """A client on the LAN (not the PC itself), with a password set and a fresh lock-out."""
    cfg = app.state.webapp_config
    saved = (cfg.auth_password, cfg.auth_token, app.state.login_lockout)
    cfg.auth_password, cfg.auth_token = PASSWORD, TOKEN
    app.state.login_lockout = LoginLockout(clock=clock, on_alert=build_login_alert(lambda: notifier, "grocery"))
    try:
        with TestClient(app, client=LAN_CLIENT) as test_client:
            yield test_client
    finally:
        cfg.auth_password, cfg.auth_token, app.state.login_lockout = saved


def _login(client: TestClient, password: str, **headers: str):
    return client.post("/api/login", json={"password": password}, headers=headers)


def test_six_wrong_passwords_then_a_429_with_retry_after_then_success_after_the_wait(lan, clock):
    assert [_login(lan, "wrong").status_code for _ in range(6)] == [401] * 6
    locked = _login(lan, "wrong")
    assert locked.status_code == 429
    assert locked.headers["Retry-After"] == "2"
    # A locked client is refused before its password is read, so even the right one waits.
    assert _login(lan, PASSWORD).status_code == 429
    clock.now += 2
    ok = _login(lan, PASSWORD)
    assert ok.status_code == 200
    assert ok.json() == {"token": TOKEN}
    assert _login(lan, "wrong").status_code == 401  # the streak was cleared


def test_three_wrong_passwords_send_one_alert_with_no_secret(lan, notifier):
    for _ in range(3):
        assert _login(lan, "wrong-guess").status_code == 401
    assert notifier.sent.wait(5)
    for _ in range(2):
        _login(lan, "wrong-guess")
    assert len(notifier.texts) == 1
    text = notifier.texts[0]
    assert "grocery" in text and LAN_CLIENT[0] in text and "3" in text
    assert "wrong-guess" not in text and PASSWORD not in text and TOKEN not in text


def test_the_pc_itself_is_never_locked_out(clock, notifier):
    cfg = app.state.webapp_config
    saved = (cfg.auth_password, cfg.auth_token, app.state.login_lockout)
    cfg.auth_password, cfg.auth_token = PASSWORD, TOKEN
    app.state.login_lockout = LoginLockout(clock=clock, on_alert=build_login_alert(lambda: notifier, "grocery"))
    try:
        with TestClient(app, client=PC_CLIENT) as pc:
            assert [_login(pc, "wrong").status_code for _ in range(12)] == [401] * 12
            assert _login(pc, PASSWORD).status_code == 200
    finally:
        cfg.auth_password, cfg.auth_token, app.state.login_lockout = saved
    assert notifier.texts == []


def test_a_tunnel_request_over_loopback_is_not_the_pc_and_is_counted_by_its_edge_address(clock, notifier):
    cfg = app.state.webapp_config
    saved = (cfg.auth_password, cfg.auth_token, app.state.login_lockout)
    cfg.auth_password, cfg.auth_token = PASSWORD, TOKEN
    app.state.login_lockout = LoginLockout(clock=clock, on_alert=build_login_alert(lambda: notifier, "grocery"))
    try:
        with TestClient(app, client=PC_CLIENT) as tunnel:
            guesser = {"cf-connecting-ip": "203.0.113.9", "cf-ray": "abc"}
            owner = {"cf-connecting-ip": "198.51.100.7", "cf-ray": "def"}
            assert [_login(tunnel, "wrong", **guesser).status_code for _ in range(6)] == [401] * 6
            assert _login(tunnel, "wrong", **guesser).status_code == 429
            # One guesser never locks another public caller out.
            assert _login(tunnel, PASSWORD, **owner).status_code == 200
    finally:
        cfg.auth_password, cfg.auth_token, app.state.login_lockout = saved
    assert notifier.sent.wait(5)
    assert "203.0.113.9" in notifier.texts[0]


def test_a_lan_caller_cannot_mint_addresses_with_a_forged_header(lan):
    for i in range(6):
        assert _login(lan, "wrong", **{"cf-connecting-ip": f"203.0.113.{i}"}).status_code == 401
    assert _login(lan, "wrong", **{"cf-connecting-ip": "203.0.113.99"}).status_code == 429
