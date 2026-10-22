# Shopping: carts, fill history and the list

**Fill carts first:** the **Shop** tab opens on the **Carts** glance card — one row per store on the list with what to buy and how the last fill went ("12 to buy · 11 in cart", a "1 not in cart" chip when it left something out), the one **Fill carts** button, the run's progress and outcome, and the run log folded underneath. Its **Options** row opens a sheet with the store, cart mode, dry run and the clean-cart confirm, and says in plain words what will run. When a fill leaves items out, Shop's header names them and the **Shop** tab carries a count badge until you next open Shop. The last fill is read from the run records under `purchase_logs/runs/`, so it survives a restart — see [Browser Automation](browser-automation.md).

**Fill history and Mark purchase done:** the **History** row in the Carts card opens a full-height **Fill history** sheet — the last ten fill days, newest first ("Wed 14 Oct · 19:17"), a line per store with what is in the cart, a "not in cart" or "not confirmed" chip, a "Clean cart" note, and the missed items folded underneath with a plain reason. It reads the same run records, so there is nothing extra to log. When the latest fill left items out or unconfirmed, **Mark purchase done** appears under Fill carts: one tap, no confirm, and the red state lets go everywhere at once — Shop's header, the tab badge, the Carts card (the stores read "N to buy") and Home's Carts row ("Last fill Wed 14 Oct · done"). The mark is kept on the server in `purchase_logs/state/fill_cleared.json` (so every device sees it on its next load) and never touches a run record: History still shows the fill, with a **Done** chip. A newer fill is not covered by it and shows its own outcome again. There is no undo; the fill stays in History. The routes are `GET /api/automation/fills/history`, `POST /api/automation/fills/clear` (`{"run": <stamp>}`; 404 with no fill, 409 for a stale stamp, safe to repeat) and the `cleared` flag on `GET /api/automation/fills`.

**The list:** below the glance, the items to buy are grouped by supermarket, each group folded with its done/total count. A row has a leading Complete check (tap again to undo) and a trailing "Open buy link" icon.

**Add to a store:** the last row of each group adds an ad-hoc item (name + quantity) to that store's list. These are session-only and support the same Complete check.

**Store options:** the row under it opens the store sheet — the **already in cart** items/units counters (for things put in the cart before you opened the app; the group's totals update at once), the ad-hoc items with their **Remove**, and **Unmark all** for that store.

## 💾 Save / Export
Manual save to Excel or download as CSV, plus summary statistics.

