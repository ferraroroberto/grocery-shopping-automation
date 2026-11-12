# Voice commands (Home Assistant)

`POST /api/voice/command` is the bridge for the Home Assistant Voice PE pucks: Home Assistant relays a spoken Spanish fragment, the app changes the shopping list, and the reply is one short Spanish sentence the puck speaks back. The route is `app/webapp/routers/voice.py`; parsing, applying and the spoken replies are `src/voice_command.py`.

## Who decides what

- **Home Assistant picks the operation.** Its deterministic sentence match chooses the `intent`; the app never infers it from the text.
- **The hub LLM only parses.** `parse_voice_items` sends the text and the inventory's item names to the model through the `local-llm-hub` hub and gets back items, quantities and ambiguous phrases. A returned item index that is not an inventory row is demoted to a new-item name, so the model cannot redirect a change to an arbitrary row.
- **Plain Python applies the change** to the spreadsheet and builds the reply.

## Request

```json
{ "intent": "add", "text": "leche, dos huevos y pan", "model": null }
```

| Field | Meaning |
|---|---|
| `intent` | Required: `add`, `target`, `stock` or `query`. Anything else is a `422`. |
| `text` | The spoken fragment. Required (non-blank) for `add`, `target` and `stock`; ignored for `query`. |
| `model` | Optional hub model id; defaults to `audio_audit.llm_model` in `src/config.json`, which also holds `llm_base_url`, `llm_max_tokens` and `llm_timeout`. |

## Intents

| Intent | Example | Effect |
|---|---|---|
| `add` | "leche, dos huevos y pan" | Raises a matched item's target (`cantidad`) by the spoken quantity (1 when none is spoken) so it appears on the list. A name that matches no row becomes a new row (target = quantity, nothing in stock, no store or product link) for the app or the product search to complete. |
| `target` | "pon el objetivo de leche a cuatro" | Sets a matched item's target to the spoken number. |
| `stock` | "no quedan nuggets" | Sets a matched item's current stock (`tenemos`) to the spoken number; "no queda", "se acabó" and "no hay" mean `0`. |
| `query` | (no text) | Reads the list back: how many items are to buy, per store, and the first eight names. |

For `target` and `stock`, an unmatched name is reported, never created, and a matched item with no spoken number is reported as needing one. An amount the speaker leaves vague ("algunos", "varios") is never guessed: for `add` it comes back in `unmatched` and the reply says the quantity was not understood.

## Response

`200` with:

```json
{
  "speech": "He añadido leche y 2 huevos a la lista.",
  "applied": [{ "name": "leche", "qty": 1, "action": "bumped" }],
  "unmatched": [],
  "model": "<the model that parsed it>"
}
```

- `speech` — the sentence Home Assistant speaks verbatim.
- `applied` — one entry per change, with `action` `bumped` or `created` (for `add`), or `target` or `stock`.
- `unmatched` — phrases that could not be actioned: ambiguous amounts for `add`; names not found or without a number for `target` and `stock`.
- `model` — present when the model was called; `query` and the busy reply omit it.

The spreadsheet is saved once per request, and only when something changed.

## Failure replies

| Situation | Answer |
|---|---|
| The spreadsheet is open in another program (locked) | `200` with `speech` "La lista está abierta en otro sitio; inténtalo en un momento." and empty `applied`, so the puck says something useful instead of its generic failure line. The same reply covers a lock that appears at save time. |
| Blank `text` for `add`, `target` or `stock` | `400`, `{"detail": "text is required for this intent"}`. |
| The hub or the model fails, or returns unusable JSON | `502` with the extraction error as `detail`. |
| The spreadsheet cannot be read or saved for another reason | `500`, or the data layer's status, with `detail`. |
| Nothing could be actioned | `200` with a speech line asking to try again ("No he entendido qué añadir…", "No he entendido nada…"). |

## Auth

The route sits behind the same bearer-token middleware as every other `/api` route. A caller on this PC needs no token; a call from another host (the Home Assistant machine, over the LAN or Tailscale) needs `Authorization: Bearer <token>` once `auth_token` is set. With no token set, the LAN stays open and any request that arrived through the Cloudflare tunnel is refused. Token setup: [running-and-access.md](running-and-access.md).
