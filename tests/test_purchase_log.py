"""Unit tests for the cart-automation purchase log (issue #70)."""

import json
from datetime import date
from pathlib import Path

from automation.models import CartItem
from automation.purchase_log import write_purchase_logs
from automation.report import ADDED_VERIFIED, OUT_OF_STOCK
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
    report.record(CartItem("mercadona", "yogur", 2, "https://example.com/yogur"), ADDED_VERIFIED)

    written = _write_purchase_log_if_live(report, dry_run=True)

    assert written == []
    assert not (tmp_path / "logs").exists()


def test_persist_purchase_log_writes_on_live_run(tmp_path, monkeypatch):
    import src.data as data
    monkeypatch.setitem(data.CONFIG["automation"], "purchase_logs_dir", "logs")
    monkeypatch.setattr("automation.run_automation.CONFIG", data.CONFIG)
    monkeypatch.setattr("automation.run_automation.REPO_ROOT", tmp_path)

    report = RunReport()
    report.record(CartItem("mercadona", "yogur", 2, "https://example.com/yogur"), ADDED_VERIFIED)
    report.record(CartItem("mercadona", "sal", 1, "https://example.com/sal"), OUT_OF_STOCK, "out of stock")

    written = _write_purchase_log_if_live(report, dry_run=False, console_text="console line")

    purchase, record, console = written
    assert purchase.parent == tmp_path / "logs"
    log = json.loads(purchase.read_text(encoding="utf-8"))
    assert log["store"] == "mercadona"
    assert log["items"] == [
        {"comida": "yogur", "comprar": 2, "buscador": "https://example.com/yogur"}
    ]  # only what is in the cart
    run = json.loads(record.read_text(encoding="utf-8"))
    assert record.parent == tmp_path / "logs" / "runs"
    assert [(i["comida"], i["status"]) for i in run["items"]] == [("yogur", "added_verified"), ("sal", "out_of_stock")]
    assert console.read_text(encoding="utf-8") == "console line"


def test_two_runs_on_one_day_merge_the_purchase_log_and_keep_two_records(tmp_path: Path):
    from datetime import datetime

    from automation.purchase_log import write_run_records

    logs_dir = tmp_path / "logs"
    first, second = RunReport(), RunReport()
    first.record(CartItem("carrefour", "kiwi", 1, "https://example.com/kiwi"), ADDED_VERIFIED)
    second.record(CartItem("carrefour", "quinoa", 2, "https://example.com/quinoa"), ADDED_VERIFIED)
    second.record(CartItem("carrefour", "kiwi", 2, "https://example.com/kiwi"), ADDED_VERIFIED)

    for report, at in ((first, datetime(2026, 10, 6, 18, 0, 0)), (second, datetime(2026, 10, 6, 19, 17, 1))):
        write_purchase_logs(report.added, logs_dir, today=at.date())
        write_run_records(report, logs_dir, "", now=at)

    log = json.loads((logs_dir / "2026-10-06_carrefour.json").read_text(encoding="utf-8"))
    assert [(i["comida"], i["comprar"]) for i in log["items"]] == [("kiwi", 2), ("quinoa", 2)]
    assert sorted(p.name for p in (logs_dir / "runs").glob("*.json")) == [
        "2026-10-06T180000_carrefour.json", "2026-10-06T191701_carrefour.json",
    ]
    # The existing reader still picks the day's log, not a run record.
    from automation.item_matching import load_latest_purchase_log
    assert {i.comida for i in load_latest_purchase_log("carrefour", logs_dir)} == {"kiwi", "quinoa"}
