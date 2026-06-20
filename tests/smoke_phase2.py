"""Phase 2 smoke test: hits the running claude-local-calls hub + whisper-server.

Pre-conditions:
    - claude-local-calls hub running on :8000  (run_hub.bat)
    - whisper-server running on :8090         (launchers/run_whisper.bat)
"""

import json
import sys
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import src.data as data  # noqa: E402
from src.inventory_extract import extract, ExtractionError  # noqa: E402
from src.net import is_port_open as whisper_health  # noqa: E402

cfg = data.CONFIG["audio_audit"]

print(f"[INFO] hub={cfg['llm_base_url']}, model={cfg['llm_model']}")
print(f"[INFO] whisper={cfg['whisper_url']}, model={cfg['whisper_model']}")

assert whisper_health(cfg["whisper_url"], timeout=3), "whisper :8090 not reachable"
print("[OK] whisper-server :8090 reachable")

# Use the fixture (read-only)
fixture = _REPO_ROOT / cfg["test_fixture_path"]
df = pd.read_excel(fixture, engine="openpyxl")
df["cantidad"] = df["cantidad"].astype(int)
df["tenemos"] = df["tenemos"].astype(int)
df["comprar"] = (df["cantidad"] - df["tenemos"]).clip(lower=0)
candidates = df.copy()  # send all rows so items with cantidad=0 are still matchable
print(f"[OK] loaded {len(candidates)} candidate items from fixture")

# Hand-crafted Spanish narration that hits items we know exist in the list:
#   - 'pollo' (ametller, congelador)
#   - 'salmon' (ametller, congelador)
#   - 'colacao' (mercadona, despensa)
#   - 'canela' (mercadona, despensa)
#   - 'amoniaco' (mercadona, bajo escalera)
transcript = (
    "Vale, ahora estoy en el congelador. Tengo dos pollos enteros, "
    "tres salmones grandes, y ningún pulpo. "
    "Ahora paso a la despensa. Tengo un colacao y dos canelas. "
    "Por último, en bajo escalera, tengo cero amoniacos."
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
    print(f"     idx={item['idx']:3d}  count={item['count']}  zone={item['zone']:14s}  "
          f"comida={name}  (lugar={lugar})  evidence={item['evidence'][:40]!r}")

# Sanity: at least one of each should match (relax the match by name-substring)
def find(name_part):
    return [it for it in result.items
            if name_part in str(candidates.at[it["idx"], "comida"]).lower()]

assert find("pollo"), "expected pollo to be matched"
assert find("salmon") or find("salmón"), "expected salmon to be matched"
assert find("colacao"), "expected colacao to be matched"

print("\nPHASE 2 SMOKE TEST: PASS")

# Optional: dump full result for inspection
out = _REPO_ROOT / "tests" / "smoke_phase2_result.json"
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
