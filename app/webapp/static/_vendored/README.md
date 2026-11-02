# `_vendored/` — fleet web-app component library

Byte-copyable UI components shared across every fleet web app (FastAPI + static PWA). This is the **UI-layer** counterpart to the tray's vendored Python primitives (`app/tray/single_instance.py`, `tray_lifecycle.ps1`): the same "copy verbatim, never fork per-app" channel, for HTML/CSS/JS.

## The split (single-home-by-altitude)

- **`fleet-config`** owns the *spec*: `design.md` + `design.dark.md` (the agent-readable visual identity + navigation contract, junctioned into `~/.claude`) and the `/design-sync` skill. The rules.
- **`project-scaffolding`** (here) owns the *implementation*: the actual HTML/CSS/JS snippets an app copies verbatim. The code.

A cloned web app inherits both: design **tokens** (wire your CSS custom properties to `~/.claude/design.md`) and **components** (vendor from here). It re-authors neither.

## Components

| Folder | What | Status |
| --- | --- | --- |
| [`nav/`](nav/) | Floating bottom-tab navigation (desktop segmented control → mobile pill). The fleet navigation contract. | ✅ |
| [`icons/`](icons/) | Inline Lucide icon sprite + `icon()` helper. The fleet's one icon set (24×24, 2px stroke, currentColor). | ✅ |
| [`card/`](card/) | The base elevated content group: `rounded.lg` surface + hairline border + the one-row header (glyph + bold title + muted meta + right-pinned meta). | ✅ |
| [`disclosure/`](disclosure/) | Collapsible `<details>`/summary card: 52px closed header, chevron pinned right, the shared `.card--collapsible` padding-zeroing modifier. | ✅ |
| [`modal/`](modal/) | The editor `<dialog>` shell: title + 34px × close, label/value rows, full-width primary with the AA disabled recipe, on-device-validated iOS anchoring/scroll-lock rules. | ✅ |
| [`empty-state/`](empty-state/) | Canonical zero-items block: feature-size glyph + one-line reason + optional quiet action; `emptyStateEl()` builder. | ✅ |
| [`switch/`](switch/) | The one boolean control (shadcn Switch shape, **green** on-track per design.md v2); `switchEl()`/`setSwitch()` builders. | ✅ |
| [`icon-tile/`](icon-tile/) | The Home-screen rounded-square: one `tile-*` fill + centered feature-size glyph. | ✅ |

_Each folder carries a `README.md` with files, a vendoring recipe, the markup contract, and its required design tokens — same shape as `nav/`. [`demo.html`](demo.html) is the component gallery: open it over HTTP to eyeball every component in light + dark; `tests/e2e/test_vendored_components.py` drives the same page and asserts each component's key computed styles in both themes._

## Adding a new vendored component

1. **Normalize** it from the best existing implementation in the fleet (don't invent a fresh one — pick the cleanest real usage and generalize it: discover from the DOM instead of hardcoding, take per-app values as parameters, depend only on design tokens).
2. Create `_vendored/<component>/` with the asset files + a `README.md` listing its files, the copy-verbatim recipe, the markup contract, and the **required design tokens** (referenced, never redefined).
3. Reference `~/.claude/design.md` for tokens; never copy the spec into the component.
4. Add a row to the table above and re-skin the source app to consume the vendored copy (tracked as a separate issue, not bundled here).

## Rules

- **Vendor verbatim.** Don't edit a component's shipped files per-app. Per-app variation is *markup* and *token values* only. To change a component, change it here and re-vendor downstream.
- **Tokens, not hardcoded values.** Components reference design-system CSS custom properties so one spec drives every app's look.
- **Web-app shape only.** This is FastAPI + static PWA guidance. Streamlit POC spikes are exempt.
