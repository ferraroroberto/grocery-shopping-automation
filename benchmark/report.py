"""Render a scored benchmark run as a self-contained HTML report (issue #145).

    python -m benchmark.report benchmark_runs/2026-09-27     # → <run>/report.html

Everything numeric comes from ``scenarios.json`` / ``basket.json`` /
``stores/*.json`` and the run history (:mod:`benchmark.history`); judgment the
code can't derive (e.g. "your deli meat isn't what you thought", "produce
taste isn't measured") comes from an optional ``<run>/report_notes.json``
written by the orchestrator::

    {"findings": [{"title": "...", "text": "..."}], "caveats": [{"title": "...", "text": "..."}]}

The page follows the fleet design tokens (``~/.claude/design.md``), works in
light and dark, and never scrolls sideways on a phone (grid children get
``min-width: 0`` so wide tables scroll inside their own container). The
orchestrator publishes it as the one living Artifact whose URL is kept in
``benchmark_runs/_state/report.json``.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from benchmark import history  # noqa: E402
from benchmark import score as S  # noqa: E402

STORES_REGISTRY = Path(__file__).resolve().parent / "stores.json"
MOVE_MIN_SAVING = 1.5  # €/month — smaller moves are listed, not tabled
EVIDENCE_CHARS = 220

e = html.escape

CSS = """
:root{--canvas:#fff;--subtle:#f6f8fa;--card:#fff;--border:#d1d9e0;--fg:#1f2328;--muted:#656d76;--accent:#0969da;--accent-text:#0550ae;--success:#1a7f37;--success-text:#116329;--danger:#cf222e;--danger-text:#a40e26;--bar:#afb8c1;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--canvas:#0d1117;--subtle:#010409;--card:#161b22;--border:#30363d;--fg:#e6edf3;--muted:#7d8590;--accent:#2f81f7;--accent-text:#58a6ff;--success:#3fb950;--success-text:#56d364;--danger:#f85149;--danger-text:#ff7b72;--bar:#484f58;color-scheme:dark}}
:root[data-theme="dark"]{--canvas:#0d1117;--subtle:#010409;--card:#161b22;--border:#30363d;--fg:#e6edf3;--muted:#7d8590;--accent:#2f81f7;--accent-text:#58a6ff;--success:#3fb950;--success-text:#56d364;--danger:#f85149;--danger-text:#ff7b72;--bar:#484f58;color-scheme:dark}
body{background:var(--canvas);color:var(--fg);font:1rem/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding-inline:16px;padding-block:24px 48px;overflow-wrap:anywhere}
main{max-width:880px;margin:0 auto;display:grid;grid-template-columns:minmax(0,1fr);gap:24px}
main>*{min-width:0}
h1{font-size:2rem;line-height:1.15;letter-spacing:-.02em;margin:0;text-wrap:balance}
h2{font-size:1.25rem;line-height:1.25;margin:0 0 6px;text-wrap:balance}
h3{font-size:1rem;margin:0}
.eyebrow{font-size:.75rem;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin:0 0 8px}
.lede{font-size:1.0625rem;max-width:65ch;margin:10px 0 0}
.sub{color:var(--muted);font-size:.875rem;line-height:1.45;margin:4px 0}
section{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:18px 16px 14px;min-width:0}
.chart{display:grid;gap:12px;margin:14px 0 10px}
.bar-row{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,2fr);gap:12px;align-items:center}
.bar-label{display:grid;gap:2px;min-width:0}
.bar-label span{color:var(--muted);font-size:.8125rem;line-height:1.3}
.bar-track{display:flex;align-items:center;gap:8px;min-width:0}
.bar{height:22px;background:var(--bar);border-radius:0 4px 4px 0;flex:none}
.rec .bar{background:var(--accent)}
.bar-val{font-variant-numeric:tabular-nums;font-weight:600;white-space:nowrap;font-size:.9375rem}
.delta{color:var(--success-text);margin-left:6px;font-size:.8125rem}
.delta.up{color:var(--danger-text)}
.pill{display:inline-block;margin-left:6px;font-size:.6875rem;font-weight:600;padding:2px 7px;border-radius:9999px;background:color-mix(in srgb,var(--accent) 16%,transparent);color:var(--accent-text);vertical-align:1px}
.pill.muted{background:color-mix(in srgb,var(--muted) 16%,transparent);color:var(--fg)}
.plan{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr));gap:12px;margin-top:12px}
.store{border:1px solid var(--border);border-radius:12px;padding:12px;background:var(--subtle);min-width:0}
.store p{margin:4px 0 0}
.store ul{margin:8px 0 0;padding-left:18px;font-size:.875rem;line-height:1.5;columns:2 130px;column-gap:20px}
.moved{color:var(--accent-text);font-weight:600}
.scroll{overflow-x:auto;margin-top:10px;max-width:100%}
table{border-collapse:collapse;width:100%;font-size:.9rem;overflow-wrap:normal}
th{text-align:left;font-size:.75rem;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);padding:6px 8px;border-bottom:1px solid var(--border)}
td{padding:9px 8px;border-bottom:1px solid var(--border);vertical-align:top}
tr:last-child td{border-bottom:0}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td .sub{margin:2px 0 0}
a{color:var(--accent-text)}
.chip{display:inline-block;font-size:.75rem;font-weight:600;padding:2px 8px;border-radius:9999px;white-space:nowrap}
.chip.good{background:color-mix(in srgb,var(--success) 16%,transparent);color:var(--success-text)}
.chip.bad{background:color-mix(in srgb,var(--danger) 16%,transparent);color:var(--danger-text)}
.chip.flat{background:color-mix(in srgb,var(--muted) 16%,transparent);color:var(--fg)}
.facts{margin:8px 0 4px;padding-left:18px;display:grid;gap:8px;max-width:72ch}
.evidence{font-size:.8125rem;color:var(--muted)}
code{font-size:.85em;background:var(--subtle);padding:1px 5px;border-radius:6px}
footer{padding-inline:4px}
@media (max-width:560px){.bar-row{grid-template-columns:minmax(0,1fr)}h1{font-size:1.6rem}
/* phones: each table row becomes a card, first cell as its title, the rest as labelled values */
.scroll thead{display:none}
.scroll table,.scroll tbody{display:block}
.scroll tr{display:flex;flex-wrap:wrap;gap:4px 16px;padding:10px 0;border-bottom:1px solid var(--border)}
.scroll tr:last-child{border-bottom:0}
.scroll td{display:block;padding:0;border:0;text-align:left}
.scroll td:first-child{flex-basis:100%}
.scroll td:empty{display:none}
.scroll td[data-label]::before{content:attr(data-label);display:block;font-size:.6875rem;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--muted)}}
"""


def eur(x: Optional[float], d: int = 0) -> str:
    return "—" if x is None else f"€{x:,.{d}f}"


def delivery_text(d: dict) -> str:
    """'€3.99 · free from €140 · min €50' from a delivery record."""
    if d.get("delivers") == "no":
        return "Does not deliver here"
    tiers = sorted(d.get("fee_tiers") or [], key=lambda t: t["min_order"])
    if not tiers:
        return "Fee unknown"
    parts = []
    for i, t in enumerate(tiers):
        fee = "free" if t["fee"] == 0 else eur(t["fee"], 2)
        parts.append(fee if i == 0 else f"{fee} from {eur(t['min_order'])}")
    if d.get("min_order"):
        parts.append(f"min {eur(d['min_order'])}")
    return " · ".join(parts)


def store_names() -> dict[str, str]:
    reg = json.loads(STORES_REGISTRY.read_text(encoding="utf-8"))["stores"]
    return {k: v.get("name", k.title()) for k, v in reg.items()}


def plan_label(stores: list[str], names: dict[str, str]) -> str:
    return " + ".join(names.get(s, s) for s in stores)


def labelled(rows: str, headers: list[str]) -> str:
    """Tag each row's cells after the first with ``data-label`` (its column
    header), so the phone layout can show every row as a labelled card."""
    out = []
    for row in rows.split("<tr")[1:]:
        head, *cells = row.split("<td")
        out.append("<tr" + head + "".join(
            (f"<td data-label='{html.escape(headers[i])}'" if 0 < i < len(headers) else "<td") + c
            for i, c in enumerate(cells)))
    return "".join(out)


def _notes(run_dir: Path) -> dict:
    path = run_dir / "report_notes.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _facts(entries: list[dict]) -> str:
    return "".join(f"<li><strong>{e(n['title'])}</strong> {e(n.get('text', ''))}</li>" for n in entries)


def render(run_dir: Path) -> str:
    sc = json.loads((run_dir / "scenarios.json").read_text(encoding="utf-8"))
    basket, stores = S.load_run(run_dir)
    items = {i["key"]: i for i in basket["items"]}
    offers = S.build_offers(basket, stores)
    names = store_names()
    notes = _notes(run_dir)
    sq, rs = sc["status_quo"], sc.get("resplit_current")
    rec, top = sc.get("recommended"), sc.get("max_savings")
    base = sq["total_optimised"]

    # ── options chart: today, re-split of today's stores, best plan per store count
    options: list[tuple[str, str, float, list[str]]] = [("Today", plan_label(sorted(sq["per_store"]), names), base, [])]
    seen: set[tuple[str, ...]] = set()
    if rs:
        options.append(("Re-split today's stores", plan_label(rs["stores"], names), rs["total_optimised"], rs["stores"]))
        seen.add(tuple(rs["stores"]))
    for k, rows in sorted(sc["best"].items(), key=lambda kv: int(kv[0])):
        if rows and tuple(rows[0]["stores"]) not in seen:
            seen.add(tuple(rows[0]["stores"]))
            options.append((f"Best {k} stores", plan_label(rows[0]["stores"], names),
                            rows[0]["total_optimised"], rows[0]["stores"]))
    vmax = max(o[2] for o in options)
    bars = []
    for label, sub, v, st in options:
        is_rec = bool(rec) and st == rec["stores"]
        is_max = bool(top) and st == top["stores"] and not is_rec
        tags = ("<span class='pill'>Recommended</span>" if is_rec else "") + \
               ("<span class='pill muted'>Maximum</span>" if is_max else "")
        d = v - base
        dtxt = "" if label == "Today" else f"<span class='delta{' up' if d > 0 else ''}'>{'−' if d < 0 else '+'}{eur(abs(d))}</span>"
        bars.append(
            f"<div class='bar-row{' rec' if is_rec else ''}' title='{e(label)}: {eur(v, 2)} per month'>"
            f"<div class='bar-label'><strong>{e(label)}{tags}</strong><span>{e(sub)}</span></div>"
            f"<div class='bar-track'><div class='bar' style='width:{70 * v / vmax:.1f}%'></div>"
            f"<span class='bar-val'>{eur(v)}{dtxt}</span></div></div>")

    def changed(key: str, store: str) -> bool:
        """The plan buys a different product than today (other store, or a same-store swap)."""
        return store != sq["items"].get(key) or offers[key][store].status == "same_store_swap"

    # ── the recommended plan, store by store, and what moves vs today
    plan_html, move_rows, small, evidence = "", "", [], []
    if rec:
        cards = []
        for store, v in rec["per_store"].items():
            keys = sorted((k for k, s in rec["items"].items() if s == store), key=lambda k: items[k]["comida"])
            lis = "".join(f"<li{' class=moved' if changed(k, store) else ''}>{e(items[k]['comida'])}</li>"
                          for k in keys)
            cards.append(f"<div class='store'><h3>{e(names.get(store, store))}</h3>"
                         f"<p class='sub'>{len(keys)} items · {eur(v['goods'])} a month · "
                         f"{v['optimised_orders']:g} orders a month · {e(delivery_text(sc['deliveries'].get(store, {})))}</p>"
                         f"<ul>{lis}</ul></div>")
        plan_html = "".join(cards)
        moves = []
        for k, s in rec["items"].items():
            if changed(k, s):
                b = sq["items"][k]
                moves.append((S.cost(items[k], S.baseline_offer(items[k])) - S.cost(items[k], offers[k][s]),
                              k, b, s, offers[k][s]))
        moves.sort(key=lambda m: -m[0])
        for save, k, b, s, new in moves:
            if save < MOVE_MIN_SAVING:
                small.append(f"{items[k]['comida']} ({eur(save, 2)})")
                continue
            move_rows += (f"<tr><td><strong>{e(items[k]['comida'])}</strong><div class='sub'>tier {e(items[k]['tier'])}</div></td>"
                          f"<td>{e(names.get(b, b))} → <strong>{e(names.get(s, s))}</strong>{' (other product)' if s == b else ''}"
                          f"<div class='sub'><a href='{e(new.url)}'>{e(new.name)}</a></div></td>"
                          f"<td class='num'>{eur(save, 2)}</td></tr>")
            if items[k]["tier"] == "B":
                rec_doc = (stores.get(s, {}).get("items") or {}).get(k, {})
                ev = " ".join(str(rec_doc.get("evidence", "")).split())[:EVIDENCE_CHARS]
                evidence.append(f"<li><strong>{e(items[k]['comida'])}</strong> → {e(new.name)}"
                                f"<div class='evidence'>{e(ev)}</div></li>")

    # ── store scorecard
    srows = []
    for s in sorted(sc["single_store"], key=lambda x: x["common_subset"]["delta_pct"] or 0):
        st, dp = s["store"], s["common_subset"]["delta_pct"]
        cls = "flat" if dp is None or abs(dp) <= 2 else ("good" if dp < 0 else "bad")
        unv = len(sc.get("unverified", {}).get(st, []))
        srows.append(f"<tr><td><strong>{e(names.get(st, st))}</strong>"
                     f"<div class='sub'>{e(delivery_text(sc['deliveries'].get(st, {})))}</div></td>"
                     f"<td class='num'>{s['coverage_pct']:.0f}%</td>"
                     f"<td class='num'><span class='chip {cls}'>{'—' if dp is None else f'{dp:+.1f}%'}</span></td>"
                     f"<td class='num'>{unv or ''}</td></tr>")

    # ── upgrades and history
    ups = "".join(f"<li><strong>{e(items[u['key']]['comida'])}</strong> at {e(names.get(u['store'], u['store']))}: "
                  f"<a href='{e(u['url'])}'>{e(u['name'])}</a> · {eur(u['monthly'], 2)} a month "
                  f"({'+' if u['delta_vs_today'] > 0 else '−'}{eur(abs(u['delta_vs_today']), 2)} vs today)</li>"
                  for u in sc.get("upgrades", []))
    runs = history.load_history(history.HISTORY_PATH)
    hrows = "".join(
        f"<tr><td>{e(r['run_date'])}</td><td class='num'>{r['items']}</td>"
        f"<td class='num'>{eur(r['status_quo']['total_optimised'])}</td>"
        f"<td class='num'>{eur((r.get('recommended') or {}).get('total_optimised'))}</td>"
        f"<td>{e(plan_label((r.get('recommended') or {}).get('stores', []), names))}</td></tr>"
        for r in reversed(runs))
    diff = sc.get("diff")
    moves_txt = ""
    if diff:
        top_moves = sorted(diff["price_moves"], key=lambda m: -abs(m["pct"]))[:12]
        moves_txt = "".join(f"<li>{e(items.get(m['key'], {}).get('comida', m['key']))} at {e(names.get(m['store'], m['store']))}: "
                            f"{m['before']:.2f} → {m['now']:.2f} €/unit ({m['pct']:+.1f}%)</li>" for m in top_moves)

    saving = base - rec["total_optimised"] if rec else 0.0
    headline = notes.get("headline") or (
        f"Shop at {plan_label(rec['stores'], names)}" if rec else "No plan covers the whole basket")
    lede = (f"This plan saves about <strong>{eur(saving)} a month</strong> ({eur(12 * saving)} a year) against today, "
            f"with every item at the same or better quality.") if rec else ""
    if rec and top and top["stores"] != rec["stores"]:
        extra = len(top["stores"]) - len(rec["stores"])
        need = f"€{S.EXTRA_STORE_MIN_SAVING * extra:g} the {extra} extra stores have to earn" if extra > 1 else \
            f"€{S.EXTRA_STORE_MIN_SAVING:g} an extra store has to earn"
        lede += (f" Using {len(top['stores'])} stores would save another {eur(rec['total_optimised'] - top['total_optimised'])} "
                 f"a month, below the {need}.")
    if rs and rec and rs["stores"] != rec["stores"]:
        lede += (f" Staying with today's stores but re-splitting them saves {eur(base - rs['total_optimised'])} a month.")

    freq = basket.get("frequency", {})
    dates = freq.get("log_dates") or []
    period = f"{dates[0]} to {dates[-1]}" if dates else "the purchase logs"
    return f"""<title>Grocery Store Benchmark</title>
<style>{CSS}</style>
<main>
<header>
  <p class="eyebrow">Run of {e(sc['run_date'])} · {sc['items_scored']} items · delivery to {e(str(basket.get('postal_code', '')))} · {len(sc['stores'])} stores</p>
  <h1>{e(headline)}</h1>
  <p class="lede">{lede}</p>
</header>

<section>
  <h2>Monthly cost per option</h2>
  <p class="sub">Every product is compared on its price per kg, litre or piece, whatever its pack size. Your average monthly basket from {len(dates)} orders ({e(period)}), at this run's prices, delivery included. Every option uses the same ordering rule: each store is ordered from as rarely as twice a month when that avoids delivery fees. At today's ordering rhythm the current setup costs {eur(sq['total'])}.</p>
  <div class="chart" role="img" aria-label="Monthly cost per option">{''.join(bars)}</div>
  <p class="sub">Theoretical floor, with every item at its cheapest store and no delivery fees: {eur(sc['lower_bound_goods'])}.</p>
</section>

{f'''<section>
  <h2>The plan, store by store</h2>
  <p class="sub">Highlighted items change from what you buy today: another store, or a better-value product at the same store.</p>
  <div class="plan">{plan_html}</div>
</section>''' if plan_html else ''}

{f'''<section>
  <h2>What moves</h2>
  <p class="sub">Ranked by monthly saving. Every move cleared its quality bar on the product page.</p>
  <div class="scroll"><table>
    <thead><tr><th>Item</th><th>Move</th><th class="num">Saves a month</th></tr></thead>
    <tbody>{labelled(move_rows, ['Item', 'Move', 'Saves a month'])}</tbody>
  </table></div>
  {f'<p class="sub">Smaller moves, under €{MOVE_MIN_SAVING:.2f} a month each: {e(", ".join(small))}.</p>' if small else ''}
</section>''' if move_rows else ''}

{f'''<section>
  <h2>Quality evidence for moved meat, fish and eggs</h2>
  <p class="sub">Ingredient lists as published by the store.</p>
  <ul class="facts">{''.join(evidence)}</ul>
</section>''' if evidence else ''}

{f'''<section>
  <h2>Findings</h2>
  <ul class="facts">{_facts(notes.get('findings', []))}{f'<li><strong>Upgrades on offer.</strong><ul class="facts">{ups}</ul></li>' if ups else ''}</ul>
</section>''' if notes.get('findings') or ups else ''}

<section>
  <h2>Each store on the items it carries</h2>
  <p class="sub">Δ compares each store's price with today's, counting only the items it matched at equal quality. Under each store: its delivery fee, free-delivery threshold and minimum order. "Unverified" counts food matches left out because their ingredients couldn't be checked.</p>
  <div class="scroll"><table>
    <thead><tr><th>Store</th><th class="num">Coverage</th><th class="num">Δ vs today</th><th class="num">Unverified</th></tr></thead>
    <tbody>{labelled(''.join(srows), ['Store', 'Coverage', 'Δ vs today', 'Unverified'])}</tbody>
  </table></div>
</section>

<section>
  <h2>Over time</h2>
  <p class="sub">One row per benchmark run, each against the basket of its day.</p>
  <div class="scroll"><table>
    <thead><tr><th>Run</th><th class="num">Items</th><th class="num">Today's setup</th><th class="num">Recommended</th><th>Recommended stores</th></tr></thead>
    <tbody>{labelled(hrows, ['Run', 'Items', 'Today', 'Recommended', 'Stores']) or '<tr><td colspan="5">This is the first recorded run.</td></tr>'}</tbody>
  </table></div>
  {f'<p class="sub">Biggest price moves since {e(diff["previous_run"])}:</p><ul class="facts">{moves_txt}</ul>' if moves_txt else ''}
</section>

{f'''<section>
  <h2>Before you act</h2>
  <ul class="facts">{_facts(notes.get('caveats', []))}</ul>
</section>''' if notes.get('caveats') else ''}

<footer class="sub">Method: quality tiers (brand-locked; spec-locked deli, meat, fish and eggs; key-spec pantry; commodity) and pro-rata monthly costs. Re-run with <code>/supermarket-benchmark</code>; the method is in <code>docs/supermarket-benchmark.md</code>.</footer>
</main>
"""


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    out = args.run_dir / "report.html"
    out.write_text(render(args.run_dir), encoding="utf-8")
    print(f"✅ wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
