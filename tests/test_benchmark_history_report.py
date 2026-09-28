"""History recording + HTML report rendering for the benchmark (issue #145)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from benchmark import history, report, score
from tests.test_benchmark_score import run_dir  # noqa: F401  (fixture)


@pytest.fixture()
def scored_run(run_dir: Path, tmp_path: Path, monkeypatch) -> Path:  # noqa: F811
    monkeypatch.setattr(history, "HISTORY_PATH", tmp_path / "_state" / "history.jsonl")
    monkeypatch.setattr(history, "PRICES_PATH", tmp_path / "_state" / "prices.csv")
    (run_dir / "scenarios.json").write_text(json.dumps(score.score(run_dir)), encoding="utf-8")
    return run_dir


def test_record_is_idempotent_per_run(scored_run: Path):
    history.record(scored_run, history.HISTORY_PATH, history.PRICES_PATH)
    history.record(scored_run, history.HISTORY_PATH, history.PRICES_PATH)
    runs = history.load_history(history.HISTORY_PATH)
    assert [r["run_date"] for r in runs] == ["2026-09-27"]
    assert runs[0]["recommended"]["stores"]
    with history.PRICES_PATH.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    # 3 baseline rows + every other-store record (mercadona ham, cheap × 3)
    assert len(rows) == 3 + 1 + 3
    assert {r["run_date"] for r in rows} == {"2026-09-27"}


def test_report_renders_plan_history_and_notes(scored_run: Path):
    history.record(scored_run, history.HISTORY_PATH, history.PRICES_PATH)
    (scored_run / "report_notes.json").write_text(json.dumps(
        {"findings": [{"title": "Deli surprise.", "text": "Not 98%."}],
         "caveats": [{"title": "Produce taste.", "text": "Not measured."}]}), encoding="utf-8")
    page = report.render(scored_run)
    assert page.startswith("<title>Grocery Store Benchmark</title>")
    assert "Recommended" in page and "Over time" in page
    assert "Deli surprise." in page and "Produce taste." in page
    assert "main>*{min-width:0}" in page  # phone: no sideways scroll from wide tables
    assert "overflow-wrap:normal" in page  # table cells never break mid-word
    assert "<td data-label='Coverage'" in page  # phone: rows render as labelled cards


def test_labelled_tags_every_cell_but_the_first():
    rows = "<tr><td>Dia</td><td class='num'>78%</td><td></td></tr>"
    assert report.labelled(rows, ["Store", "Coverage", "Unverified"]) == (
        "<tr><td>Dia</td><td data-label='Coverage' class='num'>78%</td><td data-label='Unverified'></td></tr>")


def test_delivery_text():
    assert report.delivery_text({"fee_tiers": [{"min_order": 0, "fee": 3.99}, {"min_order": 140, "fee": 0}],
                                 "min_order": 50}) == "€3.99 · free from €140 · min €50"
    assert report.delivery_text({"delivers": "no"}) == "Does not deliver here"
