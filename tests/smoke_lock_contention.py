"""Save-failure rollback test — confirm bulk_apply_tenemos restores the
in-memory tenemos/comprar values when the xlsx write fails. Simulating the
Windows 'Excel has the file open' lock from Python is unreliable across
versions, so we trigger the same code path by writing to a path inside a
directory that doesn't exist — the FileNotFoundError follows the same
exception → snapshot-restore branch."""

import os
import sys
import tempfile
from pathlib import Path

import pandas as pd

GROCERY_DIR = Path(r"E:\automation\automation\system\grocery")
sys.path.insert(0, str(GROCERY_DIR))
os.chdir(str(GROCERY_DIR))

import streamlit as st  # noqa: E402  — bulk_apply_tenemos calls st.error/st.warning

# Streamlit normally raises NoSessionContext when called from a plain script;
# patch the two we use to no-ops.
st.error = lambda *a, **k: None  # type: ignore[assignment]
st.warning = lambda *a, **k: None  # type: ignore[assignment]

import data  # noqa: E402

fixture = GROCERY_DIR / "test_data" / "list_test_fixture.xlsx"
df = pd.read_excel(fixture, engine="openpyxl")
df["cantidad"] = df["cantidad"].astype(int)
df["tenemos"] = df["tenemos"].astype(int)
df["comprar"] = (df["cantidad"] - df["tenemos"]).clip(lower=0)

with tempfile.TemporaryDirectory() as td:
    sample = df[df["cantidad"] > 0].head(2).index.tolist()
    before = {i: (int(df.at[i, "tenemos"]), int(df.at[i, "comprar"])) for i in sample}

    bad_path = str(Path(td) / "does" / "not" / "exist" / "live.xlsx")
    df_attempt = data.bulk_apply_tenemos(
        df.copy(),
        {sample[0]: 99, sample[1]: 0},
        save=True,
        xlsx_path=bad_path,
    )
    after = {
        i: (int(df_attempt.at[i, "tenemos"]), int(df_attempt.at[i, "comprar"]))
        for i in sample
    }
    print(f"     before:                 {before}")
    print(f"     after-with-save-failed: {after}")
    assert after == before, "expected rollback to leave tenemos/comprar unchanged"
    print("[OK] rollback restored in-memory tenemos and comprar after save failure")

    # Now write to a real path: same call must succeed and update in-memory.
    good_path = str(Path(td) / "good.xlsx")
    df_ok = data.bulk_apply_tenemos(
        df.copy(),
        {sample[0]: 99, sample[1]: 0},
        save=True,
        xlsx_path=good_path,
    )
    assert int(df_ok.at[sample[0], "tenemos"]) == 99
    assert int(df_ok.at[sample[1], "tenemos"]) == 0
    assert Path(good_path).exists()
    print("[OK] subsequent valid save succeeds and updates apply")

print("\nSAVE FAILURE ROLLBACK TEST: PASS")
