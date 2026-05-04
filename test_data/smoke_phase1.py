"""Phase 1 smoke test: load the example fixture, exercise bulk_apply_tenemos,
verify the fixture is not modified."""

import hashlib
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.chdir(str(REPO_ROOT))

from src import data  # noqa: E402

print("[OK] data module loaded")
print(f"[OK] audio_audit config keys: {list(data.CONFIG['audio_audit'].keys())}")

fixture = REPO_ROOT / data.CONFIG["audio_audit"]["test_fixture_path"]
assert fixture.exists(), f"fixture missing: {fixture}"
fixture_hash_before = hashlib.sha256(fixture.read_bytes()).hexdigest()

df = pd.read_excel(fixture, engine="openpyxl")
df["cantidad"] = df["cantidad"].astype(int)
df["tenemos"] = df["tenemos"].astype(int)
df["comprar"] = (df["cantidad"] - df["tenemos"]).clip(lower=0)
print(f"[OK] loaded {len(df)} rows from fixture")

sample = df[df["cantidad"] > 0].head(3).index.tolist()
print(f"[OK] sample indices: {sample}")
before = {i: (int(df.at[i, "tenemos"]), int(df.at[i, "comprar"])) for i in sample}
print(f"     before: {before}")

updates = {sample[0]: 9, sample[1]: 0, sample[2]: 7}

df_mem = data.bulk_apply_tenemos(df.copy(), updates, save=False)
after_mem = {i: (int(df_mem.at[i, "tenemos"]), int(df_mem.at[i, "comprar"])) for i in sample}
print(f"     after (in-mem): {after_mem}")
assert after_mem[sample[0]][0] == 9
assert after_mem[sample[1]][0] == 0
assert after_mem[sample[2]][0] == 7
for i in sample:
    cant = int(df_mem.at[i, "cantidad"])
    ten = int(df_mem.at[i, "tenemos"])
    assert int(df_mem.at[i, "comprar"]) == max(0, cant - ten), f"comprar mismatch at {i}"
print("[OK] in-memory updates correct, comprar recomputed")

with tempfile.TemporaryDirectory() as td:
    out = Path(td) / "out.xlsx"
    data.bulk_apply_tenemos(df.copy(), updates, save=True, xlsx_path=str(out))
    assert out.exists(), "did not write to xlsx_path override"
    df_round = pd.read_excel(out, engine="openpyxl")
    for i in sample:
        assert int(df_round.at[i, "tenemos"]) == updates[i]
print("[OK] xlsx_path override round-trip preserves updates")

fixture_hash_after = hashlib.sha256(fixture.read_bytes()).hexdigest()
assert fixture_hash_before == fixture_hash_after, "fixture was modified!"
print(f"[OK] fixture unchanged (sha256 prefix {fixture_hash_after[:12]})")

print("\nPHASE 1 SMOKE TEST: PASS")
