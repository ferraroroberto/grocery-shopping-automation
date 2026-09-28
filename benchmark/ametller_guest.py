"""Ametller Origen SCAPI over plain HTTP with a *guest* token (issue #145).

The storefront's own public SLAS client (from its SSR ``mobify-data`` config)
issues guest tokens through the standard PKCE flow — no credentials, no login,
no Chrome profile. That exposes Shopper Search + Shopper Products with the same
fields the logged-in session sees (``c_ao_ingredientes``, ``unitQuantity``,
``pricePerUnit``, EAN). Discovered by the 2026-09-27 benchmark research run;
preferred over the shared-profile token, which goes stale (HTTP 401).

    python -m benchmark.ametller_guest search "pechuga pavo"
    python -m benchmark.ametller_guest products 45832 45833
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sys
import time
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests

SHORT_CODE = "4jppt37a"
ORG_ID = "f_ecom_blzv_prd"
CLIENT_ID = "fd3c9db8-2a0d-4f4b-9e74-294e068f9ae4"  # public storefront client
SITE_ID = "ametller"
REDIRECT_URI = "https://www.ametllerorigen.com/callback"
BASE = f"https://{SHORT_CODE}.api.commercecloud.salesforce.com"
LOCALE = "es"  # plain "es" — region-qualified locales 400 (see automation/product_search.py)
_TIMEOUT = 30


class GuestSession:
    """Holds one guest token, renewing it shortly before expiry."""

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._expires_at = 0.0
        self._http = requests.Session()

    def _new_token(self) -> None:
        verifier = secrets.token_urlsafe(64)[:64]
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        resp = self._http.get(
            f"{BASE}/shopper/auth/v1/organizations/{ORG_ID}/oauth2/authorize",
            params={"response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
                    "hint": "guest", "code_challenge": challenge, "channel_id": SITE_ID},
            allow_redirects=False, timeout=_TIMEOUT,
        )
        if resp.status_code != 303:
            raise RuntimeError(f"Ametller guest authorize → HTTP {resp.status_code}")
        query = parse_qs(urlparse(resp.headers["Location"]).query)
        resp = self._http.post(
            f"{BASE}/shopper/auth/v1/organizations/{ORG_ID}/oauth2/token",
            data={"grant_type": "authorization_code_pkce", "code": query["code"][0],
                  "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI, "code_verifier": verifier,
                  "usid": query["usid"][0], "channel_id": SITE_ID},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Ametller guest token exchange → HTTP {resp.status_code}")
        tok = resp.json()
        self._token = tok["access_token"]
        self._expires_at = time.time() + float(tok.get("expires_in", 1800)) - 120

    def get(self, path: str, params: dict) -> dict:
        if not self._token or time.time() >= self._expires_at:
            self._new_token()
        resp = self._http.get(f"{BASE}{path}", params=params, timeout=_TIMEOUT,
                              headers={"Authorization": f"Bearer {self._token}"})
        if resp.status_code != 200:
            raise RuntimeError(f"Ametller GET {path} → HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def products(self, ids: list[str]) -> list[dict]:
        """Full product records for up to 24 ids per call (batched here)."""
        out: list[dict] = []
        for i in range(0, len(ids), 20):
            data = self.get(f"/product/shopper-products/v1/organizations/{ORG_ID}/products",
                            {"siteId": SITE_ID, "ids": ",".join(ids[i:i + 20]), "locale": LOCALE,
                             "allImages": "false"})
            out.extend(data.get("data", []) or [])
        return out

    def search(self, query: str, limit: int = 25) -> list[dict]:
        data = self.get(f"/search/shopper-search/v1/organizations/{ORG_ID}/product-search",
                        {"siteId": SITE_ID, "q": query, "limit": limit, "locale": LOCALE})
        return data.get("hits", []) or []


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or args[0] not in ("search", "products"):
        print(__doc__)
        return 2
    sys.stdout.reconfigure(encoding="utf-8")
    session = GuestSession()
    result = session.search(" ".join(args[1:])) if args[0] == "search" else session.products(args[1:])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
