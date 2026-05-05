"""One-shot generator for `data/list.example.xlsx` — synthetic example
inventory shipped with the public repo so the app boots out of the box."""

from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = REPO_ROOT / "data" / "list.example.xlsx"

rows = [
    # super, buscador, lugar, comida, cantidad, tenemos
    ("mercadona", "", "nevera",         "leche",            2, 1),
    ("mercadona", "", "nevera",         "yogur",            4, 4),
    ("mercadona", "", "nevera",         "mantequilla",      1, 0),
    ("ametller",  "", "congelador",     "salmon",           2, 1),
    ("ametller",  "", "congelador",     "pollo",            3, 2),
    ("ametller",  "", "congelador",     "guisantes",        1, 1),
    ("mercadona", "", "despensa",       "arroz",            2, 2),
    ("mercadona", "", "despensa",       "pasta",            3, 1),
    ("mercadona", "", "estante",        "aceite",           2, 1),
    ("mercadona", "", "garaje",         "papel higienico",  4, 2),
    ("mercadona", "", "bajo escalera",  "detergente",       1, 0),
    ("mercadona", "", "bajo escalera",  "lavavajillas",     1, 1),
]

df = pd.DataFrame(rows, columns=["super", "buscador", "lugar", "comida", "cantidad", "tenemos"])
df["comprar"] = (df["cantidad"] - df["tenemos"]).clip(lower=0)

OUT.parent.mkdir(parents=True, exist_ok=True)
df.to_excel(OUT, index=False, engine="openpyxl")
print(f"[OK] wrote {OUT} ({len(df)} rows)")
