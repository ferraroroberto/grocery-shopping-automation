# Troubleshooting

| Issue | Fix |
|-------|-----|
| Excel file not found | Verify the path in `src/config.json` |
| Permission error on save | Close Excel before running the app |
| Interface appears broken | Clear browser cache |
| Config errors | Validate `src/config.json` is well-formed JSON |
| Microphone shows "An error has occurred" on mobile | App must be opened over **HTTPS** — see [HTTPS setup](running-and-access.md#https-setup-required-for-microphone-access-on-mobile) |
| Browser says "Your connection is not private" on desktop | Re-run `& .\.venv\Scripts\python.exe src\gen_ssl_cert.py` — it installs the cert into Windows trust store |
| Browser says "Your connection is not private" on mobile | Self-signed cert warning — tap **Advanced → Proceed** once per device |

