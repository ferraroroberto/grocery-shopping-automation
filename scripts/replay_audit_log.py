"""Replay a stored audio-audit log through the current matcher.

Every applied audit writes its transcript **and** the raw LLM result to
``audio_audit_logs/*.json``. That is a real regression corpus, so a change to the
extraction prompt or to ``src/audit_resolve.py`` can be *measured* against past
walks instead of asserted (issue #132).

Two modes:

``--offline`` (default)
    Re-run only the deterministic resolver over the mentions the log already
    stored. No hub call, instant, free. Answers "which of the mentions this run
    lost would today's resolver recover?".

``--live``
    Re-run the whole extraction — current prompt, current model — against the
    current inventory, then diff it against what the log recorded. Costs a real
    hub call (budget up to 10 min on a long walk).

Usage:
    & .\\.venv\\Scripts\\python.exe scripts\\replay_audit_log.py --latest
    & .\\.venv\\Scripts\\python.exe scripts\\replay_audit_log.py audio_audit_logs\\2026-08-17_201200.json --live
    & .\\.venv\\Scripts\\python.exe scripts\\replay_audit_log.py --all --logs-dir E:\\...\\audio_audit_logs
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.audit_resolve import resolve_phrase  # noqa: E402
from src.data import CONFIG, load_inventory_data  # noqa: E402
from src.inventory_extract import ExtractionError, extract  # noqa: E402

logger = logging.getLogger("replay")


def _default_logs_dir() -> Path:
    return PROJECT_ROOT / CONFIG["audio_audit"]["logs_dir"]


def _pick_logs(args: argparse.Namespace) -> List[Path]:
    if args.log:
        return [Path(args.log)]
    logs_dir = Path(args.logs_dir) if args.logs_dir else _default_logs_dir()
    found = sorted(logs_dir.glob("*.json"))
    if not found:
        raise SystemExit(f"no audit logs in {logs_dir}")
    return found if args.all else found[-1:]


def replay_offline(log: Dict[str, Any], df) -> Dict[str, Any]:
    """Re-resolve the log's stored unmatched mentions with today's resolver."""
    result = log.get("result", {})
    zones = result.get("zones_mentioned", []) or []
    taken = {item["idx"] for item in result.get("items", []) if isinstance(item.get("idx"), int)}
    rows = []
    for mention in result.get("unmatched_mentions", []) or []:
        phrase = str(mention.get("phrase", ""))
        # Honour an idx the stored run already carried (new-format logs), else resolve.
        stored_idx = mention.get("idx")
        if isinstance(stored_idx, int) and stored_idx not in taken:
            taken.add(stored_idx)
            rows.append({"phrase": phrase, "idx": stored_idx, "how": "llm", "score": None,
                         "note": str(mention.get("note", ""))})
            continue
        resolution = resolve_phrase(phrase, df, zones=zones, exclude=taken)
        if resolution:
            taken.add(resolution.idx)
        rows.append({
            "phrase": phrase,
            "idx": resolution.idx if resolution else None,
            "how": "fuzzy" if resolution else "—",
            "score": resolution.score if resolution else None,
            "note": str(mention.get("note", "")),
        })
    return {"mentions": rows, "zones": zones}


def replay_live(log: Dict[str, Any], df, model: Optional[str]) -> Dict[str, Any]:
    """Re-run the full extraction against the hub with the current prompt."""
    cfg = CONFIG["audio_audit"]
    result = extract(
        log["transcript"],
        df,
        base_url=cfg["llm_base_url"],
        model=model or log.get("model") or cfg["llm_model"],
        max_tokens=cfg["llm_max_tokens"],
        timeout=cfg.get("llm_timeout", 600),
    )
    return {
        "items": result.items,
        "zones": result.zones_mentioned,
        "mentions": [
            {"phrase": m["phrase"], "idx": m["idx"], "how": m["resolved_by"] or "—",
             "score": m["match_score"], "note": m["note"]}
            for m in result.unmatched_mentions
        ],
    }


def _print_report(path: Path, log: Dict[str, Any], replayed: Dict[str, Any], df) -> int:
    """Print one log's before/after and return the number of recovered mentions."""
    stored = log.get("result", {})
    print(f"\n=== {path.name} · model {log.get('model', '?')} ===")
    print(f"transcript {len(log.get('transcript', ''))} chars · "
          f"stored: {len(stored.get('items', []))} items, "
          f"{len(stored.get('unmatched_mentions', []))} unmatched")
    if "items" in replayed:
        print(f"replayed: {len(replayed['items'])} items, {len(replayed['mentions'])} unmatched")

    comida = CONFIG["data"]["columns"]["comida"]

    # Live mode re-derives the counts too, so show where they moved — a count that
    # jumps to a different row between runs is the misattribution class of bug.
    if "items" in replayed:
        was = {i["idx"]: i for i in stored.get("items", [])}
        now = {i["idx"]: i for i in replayed["items"]}
        changes = [
            (idx, was.get(idx), now.get(idx))
            for idx in sorted(set(was) | set(now))
            if (was.get(idx) or {}).get("count") != (now.get(idx) or {}).get("count")
        ]
        # Always state the count, even when it is zero — "no block printed" must
        # never be mistaken for "not checked" (this diff is the only evidence a
        # count jumped to a neighbouring row).
        print(f"\n  count changes vs the stored run: {len(changes)}")
        for idx, before, after in changes:
            name = str(df.at[idx, comida]) if idx in df.index else f"idx {idx}"
            lhs = before["count"] if before else "—"
            rhs = after["count"] if after else "—"
            evidence = (after or before or {}).get("evidence", "")
            print(f"    {name[:30]:<32} {str(lhs):>3} → {str(rhs):<3}  {evidence[:34]}")

    recovered = 0
    if replayed["mentions"]:
        print(f"\n  {'phrase':<30} {'→ row':<28} {'how':<6} score")
        print("  " + "-" * 76)
    for row in replayed["mentions"]:
        if row["idx"] is None:
            target = "(unresolved)"
        else:
            target = f"{df.at[row['idx'], comida]} [{row['idx']}]" if row["idx"] in df.index else f"idx {row['idx']} (gone)"
            recovered += 1
        score = f"{row['score']:.3f}" if row["score"] is not None else "—"
        print(f"  {row['phrase'][:29]:<30} {target[:27]:<28} {row['how']:<6} {score}")
    if replayed["mentions"]:
        print(f"\n  resolved {recovered}/{len(replayed['mentions'])} mentions")
    return recovered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log", nargs="?", help="path to one audit log JSON")
    parser.add_argument("--latest", action="store_true", help="replay the newest log (default)")
    parser.add_argument("--all", action="store_true", help="replay every log in the logs dir")
    parser.add_argument("--logs-dir", help="override the audit-logs directory")
    parser.add_argument("--live", action="store_true", help="re-run the full LLM extraction via the hub")
    parser.add_argument("--model", help="hub model for --live (default: the log's own model)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    df = load_inventory_data()
    if df is None:
        print("❌ could not load the inventory", file=sys.stderr)
        return 1

    total = resolved = 0
    for path in _pick_logs(args):
        log = json.loads(path.read_text(encoding="utf-8"))
        try:
            replayed = replay_live(log, df, args.model) if args.live else replay_offline(log, df)
        except ExtractionError as exc:
            print(f"❌ {path.name}: {exc}", file=sys.stderr)
            continue
        resolved += _print_report(path, log, replayed, df)
        total += len(replayed["mentions"])

    print(f"\n{'=' * 40}\nTOTAL: {resolved}/{total} mentions resolved to a row")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
