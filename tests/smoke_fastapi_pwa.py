"""FastAPI PWA smoke test.

Uses a copy of the committed fixture by temporarily pointing the existing
data-layer config at it, so the live grocery spreadsheet is never read or
written.
"""

import shutil
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import src.data as data  # noqa: E402
from app.api import app  # noqa: E402

fixture = _REPO_ROOT / "tests" / "list_test_fixture.xlsx"
original_xlsx = data.CONFIG["data"]["xlsx_file"]

temp_dir = tempfile.TemporaryDirectory()
temp_xlsx = Path(temp_dir.name) / "inventory_fixture_copy.xlsx"
shutil.copyfile(fixture, temp_xlsx)
data.CONFIG["data"]["xlsx_file"] = str(temp_xlsx)

# Redirect audit logs to the temp dir so the smoke run never litters the repo's
# audio_audit_logs/ (the apply step now writes a JSON audit log).
original_logs_dir = data.CONFIG["audio_audit"]["logs_dir"]
data.CONFIG["audio_audit"]["logs_dir"] = str(Path(temp_dir.name) / "audio_logs")

try:
    client = TestClient(app)
    original_token = app.state.webapp_config.auth_token
    original_password = app.state.webapp_config.auth_password
    app.state.webapp_config.auth_token = ""
    app.state.webapp_config.auth_password = ""

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    print("[OK] health route")

    index = client.get("/")
    assert index.status_code == 200
    assert "Inventory Helper" in index.text
    assert "/static/app.js" in index.text
    print("[OK] PWA shell route")

    manifest = client.get("/manifest.json")
    assert manifest.status_code == 200
    assert manifest.json()["display"] == "standalone"
    print("[OK] manifest route")

    inventory = client.get("/api/inventory")
    assert inventory.status_code == 200
    payload = inventory.json()
    assert payload["summary"]["total_items"] > 0
    assert payload["summary"]["shopping_units"] >= 0
    assert payload["items"]
    assert payload["columns"]["comida"] in payload["items"][0]
    assert payload["audio"]["default_model"]
    assert isinstance(payload["audio"]["models"], list) and payload["audio"]["models"]
    assert payload["audio"]["default_model"] in payload["audio"]["models"]
    assert isinstance(payload["audio"]["clamp"], int)
    print(f"[OK] inventory route returned {payload['summary']['total_items']} items")

    audio_health = client.get("/api/audio/health")
    assert audio_health.status_code == 200
    assert set(audio_health.json()) >= {"hub_ok", "whisper_ok", "hub_url", "whisper_url"}
    print("[OK] audio health route")

    access = client.get("/api/access")
    assert access.status_code == 200
    assert access.json()["local"].startswith("http")
    assert access.json()["lan"].startswith("http")
    print("[OK] access metadata route")

    csv_export = client.get("/api/export.csv")
    assert csv_export.status_code == 200
    assert "text/csv" in csv_export.headers["content-type"]
    assert payload["columns"]["comida"] in csv_export.text
    print("[OK] CSV export route")

    first = payload["items"][0]
    item_id = first["id"]
    current_delta = client.post(f"/api/items/{item_id}/current-delta", json={"delta": 1})
    assert current_delta.status_code == 200
    changed = next(item for item in current_delta.json()["items"] if item["id"] == item_id)
    assert changed[payload["columns"]["tenemos"]] == first[payload["columns"]["tenemos"]] + 1
    print("[OK] current quantity delta route")

    target_delta = client.post(f"/api/items/{item_id}/target-delta", json={"delta": 1})
    assert target_delta.status_code == 200
    changed = next(item for item in target_delta.json()["items"] if item["id"] == item_id)
    assert changed[payload["columns"]["cantidad"]] == first[payload["columns"]["cantidad"]] + 1
    print("[OK] target quantity delta route")

    set_current = client.post(f"/api/items/{item_id}/current", json={"value": 0})
    assert set_current.status_code == 200
    changed = next(item for item in set_current.json()["items"] if item["id"] == item_id)
    assert changed[payload["columns"]["tenemos"]] == 0
    print("[OK] set current quantity route")

    edited_payload = {
        "super": first[payload["columns"]["super"]],
        "lugar": first[payload["columns"]["lugar"]],
        "comida": f"{first[payload['columns']['comida']]} smoke",
        "cantidad": first[payload["columns"]["cantidad"]],
        "tenemos": first[payload["columns"]["tenemos"]],
        "buscador": first[payload["columns"]["buscador"]] or "",
    }
    edited = client.put(f"/api/items/{item_id}", json=edited_payload)
    assert edited.status_code == 200
    changed = next(item for item in edited.json()["items"] if item["id"] == item_id)
    assert changed[payload["columns"]["comida"]].endswith(" smoke")
    print("[OK] edit item route")

    new_payload = {
        "super": first[payload["columns"]["super"]],
        "lugar": first[payload["columns"]["lugar"]],
        "comida": "smoke test item",
        "cantidad": 2,
        "tenemos": 1,
        "buscador": "smoke test search",
    }
    added = client.post("/api/items", json=new_payload)
    assert added.status_code == 200
    added_item = next(item for item in added.json()["items"] if item[payload["columns"]["comida"]] == "smoke test item")
    deleted = client.delete(f"/api/items/{added_item['id']}")
    assert deleted.status_code == 200
    assert all(item[payload["columns"]["comida"]] != "smoke test item" for item in deleted.json()["items"])
    print("[OK] add and delete item routes")

    applied = client.post(
        "/api/audio/apply",
        json={
            "updates": {str(item_id): 1},
            "transcript": "una unidad de prueba",
            "model": "gemini_pro",
            "matches": {"items": [{"idx": item_id, "count": 1}], "zones_mentioned": [], "unmatched_mentions": []},
        },
    )
    assert applied.status_code == 200
    changed = next(item for item in applied.json()["items"] if item["id"] == item_id)
    assert changed[payload["columns"]["tenemos"]] == 1
    log_path = applied.json().get("audio_log_path", "")
    assert log_path and Path(log_path).exists(), "audio apply should write an audit log"
    print(f"[OK] audio apply route (audit log: {Path(log_path).name})")

    automation = client.get("/api/automation/status")
    assert automation.status_code == 200
    assert automation.json()["running"] is False
    print("[OK] automation status route")

    app.state.webapp_config.auth_token = "test-token"
    app.state.webapp_config.auth_password = "test-password"
    locked = client.get("/api/inventory")
    assert locked.status_code == 401
    header_auth = client.get("/api/inventory", headers={"Authorization": "Bearer test-token"})
    assert header_auth.status_code == 200
    query_auth = client.get("/api/inventory?token=test-token")
    assert query_auth.status_code == 200
    login = client.post("/api/login", json={"password": "test-password"})
    assert login.status_code == 200
    assert login.json() == {"token": "test-token"}
    print("[OK] bearer token and password auth routes")
finally:
    data.CONFIG["data"]["xlsx_file"] = original_xlsx
    data.CONFIG["audio_audit"]["logs_dir"] = original_logs_dir
    app.state.webapp_config.auth_token = original_token
    app.state.webapp_config.auth_password = original_password
    temp_dir.cleanup()

print("\nFASTAPI PWA SMOKE TEST: PASS")
