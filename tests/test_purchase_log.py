"""Unit tests for the cart-automation purchase log (issue #70)."""

import json
from datetime import date
from pathlib import Path

from automation.models import CartItem
from automation.purchase_log import write_purchase_logs
from automation.run_automation import RunReport, _write_purchase_log_if_live


def test_write_purchase_logs_groups_by_store(tmp_path: Path):
    added = [
        CartItem("mercadona", "yogur", 2, "https://example.com/yogur"),
        CartItem("mercadona", "leche", 1, "https://example.com/leche"),
        CartItem("ametller", "pan", 3, "https://example.com/pan"),
    ]
    logs_dir = tmp_path / "logs"

    written = write_purchase_logs(added, logs_dir, today=date(2026, 7, 10))

    assert {p.name for p in written} == {
        "2026-07-10_mercadona.json",
        "2026-07-10_ametller.json",
    }
    mercadona_log = json.loads((logs_dir / "2026-07-10_mercadona.json").read_text(encoding="utf-8"))
    assert mercadona_log == {
        "date": "2026-07-10",
        "store": "mercadona",
        "items": [
            {"comida": "yogur", "comprar": 2, "buscador": "https://example.com/yogur"},
            {"comida": "leche", "comprar": 1, "buscador": "https://example.com/leche"},
        ],
    }
    ametller_log = json.loads((logs_dir / "2026-07-10_ametller.json").read_text(encoding="utf-8"))
    assert ametller_log["items"] == [
        {"comida": "pan", "comprar": 3, "buscador": "https://example.com/pan"}
    ]


def test_write_purchase_logs_empty_writes_nothing(tmp_path: Path):
    logs_dir = tmp_path / "logs"

    written = write_purchase_logs([], logs_dir, today=date(2026, 7, 10))

    assert written == []
    assert not logs_dir.exists()


def test_persist_purchase_log_skips_dry_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import src.data as data
    monkeypatch.setitem(data.CONFIG["automation"], "purchase_logs_dir", str(tmp_path / "logs"))
    monkeypatch.setattr("automation.run_automation.CONFIG", data.CONFIG)
    monkeypatch.setattr("automation.run_automation.REPO_ROOT", tmp_path)

    report = RunReport()
    report.added.append(CartItem("mercadona", "yogur", 2, "https://example.com/yogur"))

    written = _write_purchase_log_if_live(report, dry_run=True)

    assert written == []
    assert not (tmp_path / "logs").exists()


def test_persist_purchase_log_writes_on_live_run(tmp_path, monkeypatch):
    import src.data as data
    monkeypatch.setitem(data.CONFIG["automation"], "purchase_logs_dir", "logs")
    monkeypatch.setattr("automation.run_automation.CONFIG", data.CONFIG)
    monkeypatch.setattr("automation.run_automation.REPO_ROOT", tmp_path)

    report = RunReport()
    report.added.append(CartItem("mercadona", "yogur", 2, "https://example.com/yogur"))

    written = _write_purchase_log_if_live(report, dry_run=False)

    assert len(written) == 1
    assert written[0].parent == tmp_path / "logs"
    log = json.loads(written[0].read_text(encoding="utf-8"))
    assert log["store"] == "mercadona"
    assert log["items"] == [
        {"comida": "yogur", "comprar": 2, "buscador": "https://example.com/yogur"}
    ]
