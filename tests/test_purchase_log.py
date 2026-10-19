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

    report = RunReport()
    report.record(CartItem("mercadona", "yogur", 2, "https://example.com/yogur"), ADDED_VERIFIED)

    written = _write_purchase_log_if_live(report, dry_run=True)

    assert written == []
    assert not (tmp_path / "logs").exists()


def test_persist_purchase_log_writes_on_live_run(tmp_path, monkeypatch):
    import src.data as data
    monkeypatch.setitem(data.CONFIG["automation"], "purchase_logs_dir", "logs")
    monkeypatch.setattr(data, "REPO_ROOT", tmp_path)

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


def test_read_last_fill_counts_the_newest_day_per_store(tmp_path: Path):
    """#258: Shop's glance reads the last fill day from the run records — a store
    filled twice that day counts its later run, an older day is left out."""
    from datetime import datetime

    from automation.purchase_log import read_last_fill, write_run_records
    from automation.report import ADDED_UNVERIFIED, ERROR

    logs_dir = tmp_path / "logs"
    assert read_last_fill(logs_dir) is None

    old, morning, evening = RunReport(), RunReport(), RunReport(mode="clean")
    old.record(CartItem("ametller", "pan", 1, "https://example.com/pan"), OUT_OF_STOCK)
    morning.record(CartItem("carrefour", "kiwi", 1, "https://example.com/kiwi"), OUT_OF_STOCK)
    evening.record(CartItem("carrefour", "kiwi", 1, "https://example.com/kiwi"), ADDED_VERIFIED)
    evening.record(CartItem("carrefour", "quinoa", 2, "https://example.com/quinoa"), ADDED_UNVERIFIED)
    evening.record(CartItem("Mercadona", "sal", 1, ""), ERROR, "no link")
    for report, at in ((old, datetime(2026, 9, 29, 19, 0, 0)), (morning, datetime(2026, 10, 6, 9, 0, 0)),
                       (evening, datetime(2026, 10, 6, 19, 17, 1))):
        write_run_records(report, logs_dir, "", now=at)
    (logs_dir / "runs" / "2026-10-07T080000_broken.json").write_text("{not json", encoding="utf-8")

    assert read_last_fill(logs_dir) == {
        "run": "2026-10-06T191701",
        "date": "2026-10-06",
        "stores": {
            "carrefour": {"in_cart": 1, "unconfirmed": 1, "missing": 0, "cart_mode": "clean"},
            "mercadona": {"in_cart": 0, "unconfirmed": 0, "missing": 1, "cart_mode": "clean"},
        },
    }


def _record_fill(logs_dir: Path, store: str, at, items: list[tuple[str, str]], mode: str = "keep") -> None:
    """One invented run record: ``items`` are (name, status) pairs."""
    from automation.purchase_log import write_run_records

    report = RunReport(mode=mode)
    for name, status in items:
        report.record(CartItem(store, name, 1, f"https://example.com/{name}"), status)
    write_run_records(report, logs_dir, "", now=at)


def test_read_fill_history_groups_by_day_newest_first(tmp_path: Path):
    """#323: one entry per fill day, a store filled twice counts its later run,
    only the misses are listed, a broken record is skipped, the limit applies."""
    from datetime import datetime

    from automation.purchase_log import read_fill_history, read_last_fill
    from automation.report import ADDED_UNVERIFIED

    logs_dir = tmp_path / "logs"
    assert read_fill_history(logs_dir) == []

    _record_fill(logs_dir, "carrefour", datetime(2026, 9, 29, 19, 0, 0), [("pan", ADDED_VERIFIED)])
    _record_fill(logs_dir, "carrefour", datetime(2026, 10, 6, 9, 0, 0), [("kiwi", OUT_OF_STOCK)])
    _record_fill(logs_dir, "carrefour", datetime(2026, 10, 6, 19, 17, 1),
                 [("kiwi", ADDED_VERIFIED), ("quinoa", ADDED_UNVERIFIED)], mode="clean")
    _record_fill(logs_dir, "mercadona", datetime(2026, 10, 6, 19, 17, 1),
                 [("sal", OUT_OF_STOCK), ("miel", ADDED_VERIFIED)])
    (logs_dir / "runs" / "2026-10-07T080000_broken.json").write_text("{not json", encoding="utf-8")

    history = read_fill_history(logs_dir)
    assert [d["date"] for d in history] == ["2026-10-06", "2026-09-29"]
    newest = history[0]
    assert (newest["run"], newest["time"]) == ("2026-10-06T191701", "19:17")
    assert newest["stores"]["carrefour"] == {
        "in_cart": 1, "unconfirmed": 1, "missing": 0, "cart_mode": "clean", "problems": [],
    }
    assert newest["stores"]["mercadona"]["problems"] == [{"item": "sal", "status": OUT_OF_STOCK}]
    # The glance reader is untouched: same day, same counts.
    last = read_last_fill(logs_dir)
    assert last["run"] == newest["run"]
    assert {k: {c: v[c] for c in ("in_cart", "unconfirmed", "missing")} for k, v in newest["stores"].items()} == {
        k: {c: v[c] for c in ("in_cart", "unconfirmed", "missing")} for k, v in last["stores"].items()
    }
    assert [d["date"] for d in read_fill_history(logs_dir, limit=1)] == ["2026-10-06"]


def test_read_fill_history_keeps_ten_days(tmp_path: Path):
    from datetime import datetime

    from automation.purchase_log import read_fill_history

    logs_dir = tmp_path / "logs"
    for day in range(1, 13):
        _record_fill(logs_dir, "carrefour", datetime(2026, 10, day, 18, 0, 0), [("pan", ADDED_VERIFIED)])
    dates = [d["date"] for d in read_fill_history(logs_dir)]
    assert dates == [f"2026-10-{day:02d}" for day in range(12, 2, -1)]


def test_fill_cleared_marker_round_trip_and_corruption(tmp_path: Path):
    from automation.purchase_log import CLEARED_KEEP, read_fill_cleared, write_fill_cleared

    logs_dir = tmp_path / "logs"
    assert read_fill_cleared(logs_dir) == set()
    write_fill_cleared(logs_dir, "2026-10-06T191701")
    write_fill_cleared(logs_dir, "2026-10-06T191701")  # idempotent
    assert read_fill_cleared(logs_dir) == {"2026-10-06T191701"}
    # A later fill carries a new stamp: the earlier mark does not cover it.
    assert "2026-10-13T180000" not in read_fill_cleared(logs_dir)
    write_fill_cleared(logs_dir, "2026-10-13T180000")
    assert read_fill_cleared(logs_dir) == {"2026-10-06T191701", "2026-10-13T180000"}

    marker = logs_dir / "state" / "fill_cleared.json"
    marker.write_text("{not json", encoding="utf-8")
    assert read_fill_cleared(logs_dir) == set()
    write_fill_cleared(logs_dir, "2026-10-20T090000")  # recovers over a corrupt file
    assert read_fill_cleared(logs_dir) == {"2026-10-20T090000"}

    for n in range(CLEARED_KEEP + 5):
        write_fill_cleared(logs_dir, f"2026-11-{n:04d}")
    assert len(read_fill_cleared(logs_dir)) == CLEARED_KEEP
