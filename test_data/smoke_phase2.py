"""Phase 2 smoke test: hits the running claude-local-calls hub + whisper-server.

Pre-conditions:
    - LLM hub running on :8000  (claude-local-calls)
    - whisper-server running on :8090
"""

import json
import os
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.chdir(str(REPO_ROOT))

from src import data  # noqa: E402
from src.inventory_extract import ExtractionError, extract  # noqa: E402,F401
from src.transcribe_client import health_check as whisper_health  # noqa: E402

cfg = data.CONFIG["audio_audit"]

print(f"[INFO] hub={cfg['llm_base_url']}, model={cfg['llm_model']}")
print(f"[INFO] whisper={cfg['whisper_url']}, model={cfg['whisper_model']}")

assert whisper_health(cfg["whisper_url"], timeout=3), "whisper :8090 not reachable"
print("[OK] whisper-server :8090 reachable")

# Use the example fixture (read-only)
fixture = REPO_ROOT / cfg["test_fixture_path"]
df = pd.read_excel(fixture, engine="openpyxl")
df["cantidad"] = df["cantidad"].astype(int)
df["tenemos"] = df["tenemos"].astype(int)
df["comprar"] = (df["cantidad"] - df["tenemos"]).clip(lower=0)
candidates = df.copy()  # send all rows so items with cantidad=0 are still matchable
print(f"[OK] loaded {len(candidates)} candidate items from fixture")

# Hand-crafted Spanish narration referring to items expected in the example
# fixture (generic Spanish food names).
transcript = (
    "Vale, ahora estoy en el congelador. Tengo dos pollos enteros, "
    "tres salmones grandes, y ningún guisante. "
    "Ahora paso a la despensa. Tengo un arroz y dos pastas. "
    "Por último, en bajo escalera, tengo cero detergentes."
)
print(f"[INFO] simulated transcript: {transcript[:80]}…")

result = extract(
    transcript,
    candidates,
    base_url=cfg["llm_base_url"],
    model=cfg["llm_model"],
    max_tokens=cfg["llm_max_tokens"],
)
print(f"[OK] LLM returned {len(result.items)} matched items")
print(f"     zones_mentioned: {result.zones_mentioned}")
print(f"     unmatched: {len(result.unmatched_mentions)}")
for item in result.items:
    name = candidates.at[item["idx"], "comida"]
    lugar = candidates.at[item["idx"], "lugar"]
    print(
        f"     idx={item['idx']:3d}  count={item['count']}  zone={item['zone']:14s}  "
        f"comida={name}  (lugar={lugar})  evidence={item['evidence'][:40]!r}"
    )

# Sanity: at least one fridge/freezer item should match.
assert result.items, "expected at least one matched item from transcript"

print("\nPHASE 2 SMOKE TEST: PASS")

out = REPO_ROOT / "test_data" / "smoke_phase2_result.json"
out.write_text(
    json.dumps(
        {
            "items": result.items,
            "zones_mentioned": result.zones_mentioned,
            "unmatched_mentions": result.unmatched_mentions,
            "raw_text": result.raw_text,
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
print(f"[INFO] result written to {out}")
