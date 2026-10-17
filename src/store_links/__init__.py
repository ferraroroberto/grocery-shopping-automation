"""Per-store product links, store picks and the cost simulator (issue #148).

UI-free. Each inventory row may carry one product URL per supermarket in an
optional ``url_<store>`` column (store keys from ``benchmark/stores.json``);
``super`` + ``buscador`` stay the *chosen* store and its URL, so the cart
automation and the email/audit flows keep working unchanged.

Prices and quantities come from the latest scored supermarket-benchmark run
(``benchmark_runs/<YYYY-MM-DD>/``); the cost maths is :mod:`benchmark.score`'s
own (``build_offers`` / ``price_assignment``), never re-implemented here.
Inventory rows are joined to benchmark items by
:func:`benchmark.build_basket.item_key` of ``comida``.

Every mutator persists through :func:`src.data.save_inventory_data` and rolls
the in-memory frame back when the save fails (e.g. the sheet is locked).

**Overrides and review state (#165)** live in
``<runs_dir>/_state/overrides.json`` (gitignored household data, never in the
xlsx and never written into the benchmark mappings)::

    {"items":   {<item key>: {<store>: {"name", "pack_size", "unit", "pack_price",
                                         "note", "updated", "benchmark_snapshot"}}},
     "checked": {<item key>: "<YYYY-MM-DD>"}}

An override is the household's own correction of one store's product for one
basket item. Any field left out (null) falls back to the benchmark's value, so
a price-only override keeps the benchmark's pack. :meth:`_RunContext.offer`
returns it as an ``Offer`` with status ``"override"``, so the simulator, the
per-item prices and the apply pack conversion all use it. ``benchmark_snapshot``
is the benchmark's ``{name, pack_size, unit, pack_price}`` when the override
was saved (null when the benchmark had none), so a later run that changes
those values is flagged ``benchmark_changed``. ``checked`` is the review
progress (any row's ``item_key(comida)``). A missing or unreadable file reads
as empty (⚠️ logged); a write sets an unreadable file aside first, and every
write is atomic (temp file + replace).

**Monthly cost what-if baseline (#183)** lives in
``<runs_dir>/_state/baseline.json``::

    {"set_at": "<ISO timestamp>", "run_date": "<YYYY-MM-DD>",
     "items": {<item key>: {"store", "name", "pack_size", "unit", "pack_price"}}}

:func:`set_baseline` freezes the list's current stores (and their price at
that moment) so a later :func:`simulate` prices its "today" side from there
instead of the benchmark's basket store — the switch becomes the new status
quo. It only applies while ``run_date`` still matches the latest run;
:func:`clear_baseline` (or a newer benchmark run) goes back to the benchmark's
status quo. It never touches the review reference points (``moved``,
``is_basket_store``, the quantity check's "before") — those keep comparing
against the benchmark until a new run.

Split by concern (#297); every public name is re-exported here, so importers
keep using ``src.store_links`` unchanged:

* :mod:`.registry` — the store registry, link kinds, run discovery, the errors
* :mod:`.state` — the overrides and baseline JSON stores
* :mod:`.context` — one scored run (:class:`_RunContext`) and the row ↔ item join
* :mod:`.edits` — inventory writes with rollback (import, row edits, store pick)
* :mod:`.simulation` — the monthly-cost simulator and the what-if baseline
* :mod:`.apply` — apply preview and apply, with pack-size conversion
* :mod:`.review` — per-item detail, flags, overrides, checked state, run status
"""

from __future__ import annotations

from benchmark.paths import runs_dir
from .apply import apply_changes, apply_preview
from .edits import import_latest, pick_store, set_fallback, set_store_url, store_url
from .registry import (
    STORES_REGISTRY_PATH,
    NoBenchmarkRunError,
    StoreLinkError,
    latest_run_dir,
    link_kind,
    load_mappings,
    load_registry,
)
from .context import rows_by_key
from .review import (
    CHECK_FLAGS,
    OVERRIDE_UNITS,
    REVIEW_INTERVAL_DAYS,
    UNIDADES_COLUMN,
    checks,
    item_detail,
    reset_override,
    run_status,
    set_item_checked,
    set_override,
)
from .simulation import (
    DEFAULT_FREQUENCY,
    FREQUENCIES,
    clear_baseline,
    recommended_picks,
    resolve_frequency,
    set_baseline,
    simulate,
)
from .state import baseline_path, load_baseline, load_overrides, overrides_path

__all__ = [
    "CHECK_FLAGS", "DEFAULT_FREQUENCY", "FREQUENCIES", "NoBenchmarkRunError", "OVERRIDE_UNITS",
    "REVIEW_INTERVAL_DAYS", "STORES_REGISTRY_PATH", "StoreLinkError", "UNIDADES_COLUMN",
    "apply_changes", "apply_preview", "baseline_path", "checks", "clear_baseline", "import_latest",
    "item_detail", "latest_run_dir", "link_kind", "load_baseline", "load_mappings", "load_overrides",
    "load_registry", "overrides_path", "pick_store", "recommended_picks", "reset_override",
    "resolve_frequency", "rows_by_key", "run_status", "runs_dir", "set_baseline", "set_fallback",
    "set_item_checked", "set_override", "set_store_url", "simulate", "store_url",
]
