"""The results CLI's write actions run end to end (lock, validate, save)."""

from __future__ import annotations

import json
from pathlib import Path

from src.benchmark import results


def test_access_action_writes_the_store_file(tmp_path: Path) -> None:
    run = tmp_path / "run"
    code = results.main(["access", "--run", str(run), "--store", "dia", "--json",
                         json.dumps({"method": "chrome", "notes": "ok"})])
    assert code == 0
    doc = json.loads((run / "stores" / "dia.json").read_text(encoding="utf-8"))
    assert doc["access"] == {"method": "chrome", "notes": "ok"}
    assert not (run / "stores" / "dia.lock").exists()


def test_delivery_action_sorts_fee_tiers(tmp_path: Path) -> None:
    run = tmp_path / "run"
    record = {"delivers": "yes", "fee_tiers": [{"min_order": 80, "fee": 0}, {"min_order": 0, "fee": 7.5}]}
    assert results.main(["delivery", "--run", str(run), "--store", "dia", "--json", json.dumps(record)]) == 0
    doc = json.loads((run / "stores" / "dia.json").read_text(encoding="utf-8"))
    assert [t["min_order"] for t in doc["delivery"]["fee_tiers"]] == [0.0, 80.0]
