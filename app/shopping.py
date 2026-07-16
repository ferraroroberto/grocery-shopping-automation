"""Shopping List mode — items to buy, grouped by supermarket, with quick-add."""

import time
from datetime import datetime

import pandas as pd
import streamlit as st

from app import automation_runner
from src.data import COLUMNS, get_supermarket_stats, get_unique_supermarkets


# Session-state keys for the 🤖 Run Automation section (issue #4). Documented
# here so the contract is in one place:
#   automation_store_select   — store selectbox value ("all" or a store key)
#   automation_dry_run        — dry-run checkbox value
#   automation_cart_mode      — cart-mode radio value ("keep" or "clean")
#   automation_clean_confirm  — destructive-confirm checkbox for clean mode
#   automation_run_btn        — Run button widget key
#   automation_process        — subprocess.Popen handle while a run is live
#   automation_output_lines   — deque[str], max 500 lines, drained by a thread
#   automation_reader_thread  — the stdout reader Thread
#   automation_started_at     — datetime the current run started


def _render_automation_controls(df: pd.DataFrame) -> None:
    """Idle state: store picker, dry-run toggle, command preview, Run button."""
    pending = df[df[COLUMNS["comprar"]] > 0]
    stores = sorted(pending[COLUMNS["super"]].astype(str).str.lower().unique())
    options = ["all"] + stores if stores else ["all"]

    c1, c2 = st.columns([3, 2])
    with c1:
        store = st.selectbox(
            "Store",
            options,
            format_func=lambda s: "All stores" if s == "all" else s.title(),
            key="automation_store_select",
        )
    with c2:
        dry_run = st.checkbox(
            "Dry run (don't actually add to cart)",
            value=True,
            key="automation_dry_run",
        )

    cart_mode = st.radio(
        "Cart mode",
        options=["keep", "clean"],
        format_func=lambda m: (
            "🧺 Keep cart (add on top of what's there)"
            if m == "keep"
            else "🧹 Clean cart (empty it first, then add)"
        ),
        horizontal=True,
        key="automation_cart_mode",
    )

    # Clean mode is destructive — it wipes manually-added extras. Gate a real
    # (non-dry) clean run behind an explicit confirmation checkbox.
    clean_confirmed = True
    if cart_mode == "clean":
        st.warning(
            "⚠️ Clean mode empties the store cart completely before adding the "
            "list — anything added to the cart by hand (not in the inventory) "
            "will be removed."
        )
        if not dry_run:
            clean_confirmed = st.checkbox(
                "Yes, empty the cart first",
                value=False,
                key="automation_clean_confirm",
            )

    st.code(
        " ".join(automation_runner.build_command(store, dry_run, cart_mode)),
        language="text",
    )

    if st.button(
        "▶ Run Automation",
        width="stretch",
        key="automation_run_btn",
        disabled=not clean_confirmed,
    ):
        process, output_lines, reader_thread = automation_runner.start_run(
            store, dry_run, cart_mode
        )
        st.session_state.automation_process = process
        st.session_state.automation_output_lines = output_lines
        st.session_state.automation_reader_thread = reader_thread
        st.session_state.automation_started_at = datetime.now()
        st.rerun()


def _render_automation_live(process) -> None:
    """Running state: stream output, offer Stop, then rerun to poll again.

    Each Streamlit pass renders one frame of output and reruns after a short
    sleep — so the Stop button stays clickable instead of being trapped behind
    a blocking loop.
    """
    lines = st.session_state.get("automation_output_lines")
    started = st.session_state.get("automation_started_at")
    elapsed = int((datetime.now() - started).total_seconds()) if started else 0

    st.info(f"⏳ Automation running… ({elapsed}s elapsed)")
    st.code("\n".join(lines) if lines else "(waiting for output…)", language="text")

    if st.button("🛑 Stop", width="stretch", key="automation_stop_btn"):
        automation_runner.stop_run(process)
        st.rerun()

    time.sleep(1.0)
    st.rerun()


def _render_automation_finished(process) -> None:
    """Finished state: show the final output, the exit status, and a Dismiss button."""
    lines = st.session_state.get("automation_output_lines")
    st.code("\n".join(lines) if lines else "(no output)", language="text")

    if process.returncode == 0:
        st.success("✅ Automation finished — exit 0. Review and pay in the browser.")
    else:
        st.error(f"❌ Automation exited with code {process.returncode}. See the log above.")

    if st.button("Dismiss", width="stretch", key="automation_dismiss_btn"):
        for key in (
            "automation_process",
            "automation_output_lines",
            "automation_reader_thread",
            "automation_started_at",
        ):
            st.session_state.pop(key, None)
        st.rerun()


def _render_automation_section(df: pd.DataFrame) -> None:
    """🤖 Run Automation — spawn the cart-filling subprocess and stream its output."""
    process = st.session_state.get("automation_process")

    with st.container(border=True):
        st.markdown("#### 🤖 Run Automation")
        st.caption(
            "Fills the store carts from this list via a Chrome automation. "
            "You still confirm and pay in the browser."
        )

        if automation_runner.is_running(process):
            _render_automation_live(process)
        elif process is not None:
            _render_automation_finished(process)
        else:
            _render_automation_controls(df)


def main(df: pd.DataFrame) -> None:
    """Render the shopping list mode interface.

    Session-state keys (``bought_items``, ``extra_shopping_items``,
    ``extra_bought_items``, ``extra_item_counter``) are initialised once by
    ``app.app._init_session_state`` before any mode renders.
    """
    shopping_items = df[df[COLUMNS["comprar"]] > 0].copy()

    base_supermarkets = get_unique_supermarkets(shopping_items) if not shopping_items.empty else []
    all_supermarkets = sorted(set(base_supermarkets) | set(st.session_state.extra_shopping_items.keys()))

    if shopping_items.empty and not all_supermarkets:
        st.success("🎉 All stocked up — nothing to buy!")
        return

    total_items = len(shopping_items)
    total_qty = int(shopping_items[COLUMNS["comprar"]].sum()) if not shopping_items.empty else 0
    bought_count = len([i for i in shopping_items.index if i in st.session_state.bought_items])
    bought_qty = int(
        shopping_items[shopping_items.index.isin(st.session_state.bought_items)][COLUMNS["comprar"]].sum()
    ) if not shopping_items.empty else 0

    for sm, extras in st.session_state.extra_shopping_items.items():
        total_items += len(extras)
        total_qty += sum(e["qty"] for e in extras)
        extra_bought = st.session_state.extra_bought_items.get(sm, set())
        for e in extras:
            if e["id"] in extra_bought:
                bought_count += 1
                bought_qty += e["qty"]

    c1, c2 = st.columns([6, 1])
    with c1:
        progress = f" · ✅ {bought_count}/{total_items} unique · {bought_qty}/{total_qty} units" if bought_count > 0 else ""
        st.caption(f"🛒 {total_items} unique · {total_qty} units · {len(all_supermarkets)} store(s){progress}")
    with c2:
        if bought_count > 0 and st.button("🗑️", help="Unmark all"):
            st.session_state.bought_items.clear()
            st.session_state.extra_bought_items.clear()
            st.rerun()

    missing_url_items = []
    if not shopping_items.empty:
        for idx in shopping_items.index:
            raw_buy_url = shopping_items.at[idx, COLUMNS["buscador"]]
            buy_url = raw_buy_url.strip() if isinstance(raw_buy_url, str) else ""
            if not buy_url:
                item_name = shopping_items.at[idx, COLUMNS["comida"]]
                supermarket = shopping_items.at[idx, COLUMNS["super"]]
                missing_url_items.append(f"{item_name} ({supermarket})")

    if missing_url_items:
        st.warning(f"⚠️ {len(missing_url_items)} item(s) are missing a buy link and have the Buy button disabled.")
        with st.expander("Show items missing links"):
            st.markdown("\n".join(f"- {item}" for item in missing_url_items))

    _render_automation_section(df)

    supermarket_stats = get_supermarket_stats(shopping_items, st.session_state.bought_items) if not shopping_items.empty else {}

    for supermarket in all_supermarkets:
        stats = supermarket_stats.get(supermarket, {"total_unique": 0, "total_quantity": 0, "got_it_unique": 0, "got_it_quantity": 0})
        sm_items = (
            shopping_items[shopping_items[COLUMNS["super"]] == supermarket]
            .sort_values(COLUMNS["comida"], key=lambda s: s.str.lower())
            if not shopping_items.empty else pd.DataFrame()
        )
        extras = st.session_state.extra_shopping_items.get(supermarket, [])
        extra_bought_set = st.session_state.extra_bought_items.get(supermarket, set())

        total_u = stats["total_unique"] + len(extras)
        total_q = stats["total_quantity"] + sum(e["qty"] for e in extras)
        done_u = stats["got_it_unique"] + len([e for e in extras if e["id"] in extra_bought_set])
        done_q = stats["got_it_quantity"] + sum(e["qty"] for e in extras if e["id"] in extra_bought_set)

        done_txt = f" · ✅ {done_u}/{total_u}" if done_u > 0 else ""
        label = f"🏪 {supermarket.title()} — {total_u} items · {total_q} units{done_txt}"

        with st.expander(label, expanded=True):
            for idx in sm_items.index:
                item_name = sm_items.at[idx, COLUMNS["comida"]]
                qty_to_buy = sm_items.at[idx, COLUMNS["comprar"]]
                raw_buy_url = sm_items.at[idx, COLUMNS["buscador"]]
                buy_url = raw_buy_url.strip() if isinstance(raw_buy_url, str) else ""
                is_bought = idx in st.session_state.bought_items

                col1, col2, col3 = st.columns([5, 2, 2])

                with col1:
                    if is_bought:
                        st.markdown(f"~~{item_name}~~ · {qty_to_buy}×")
                    else:
                        st.markdown(f"**{item_name}** · {qty_to_buy}×")

                with col2:
                    if buy_url:
                        st.link_button(
                            "🔄 Again" if is_bought else "🛒 Buy",
                            buy_url,
                            width="stretch",
                        )
                    else:
                        st.button(
                            "🔄 Again" if is_bought else "🛒 Buy",
                            key=f"buy_disabled_{idx}",
                            width="stretch",
                            disabled=True,
                        )

                with col3:
                    if is_bought:
                        if st.button("↩️ Undo", key=f"unmark_{idx}", width="stretch"):
                            st.session_state.bought_items.remove(idx)
                            st.rerun()
                    else:
                        if st.button(
                            "✅ Got it",
                            key=f"mark_{idx}",
                            width="stretch",
                            type="secondary",
                        ):
                            st.session_state.bought_items.add(idx)
                            st.rerun()

            for e in extras:
                eid = e["id"]
                is_extra_bought = eid in extra_bought_set
                col1, col2, col3 = st.columns([5, 2, 2])
                with col1:
                    label_txt = f"~~{e['name']}~~ · {e['qty']}×" if is_extra_bought else f"**{e['name']}** · {e['qty']}×"
                    st.markdown(f"{label_txt} _+_")
                with col2:
                    if st.button("🗑️ Remove", key=f"extra_del_{eid}", width="stretch"):
                        st.session_state.extra_shopping_items[supermarket] = [
                            x for x in extras if x["id"] != eid
                        ]
                        if supermarket in st.session_state.extra_bought_items:
                            st.session_state.extra_bought_items[supermarket].discard(eid)
                        if not st.session_state.extra_shopping_items[supermarket]:
                            del st.session_state.extra_shopping_items[supermarket]
                        st.rerun()
                with col3:
                    if is_extra_bought:
                        if st.button("↩️ Undo", key=f"extra_unmark_{eid}", width="stretch"):
                            st.session_state.extra_bought_items[supermarket].discard(eid)
                            st.rerun()
                    else:
                        if st.button("✅ Got it", key=f"extra_mark_{eid}", width="stretch", type="secondary"):
                            if supermarket not in st.session_state.extra_bought_items:
                                st.session_state.extra_bought_items[supermarket] = set()
                            st.session_state.extra_bought_items[supermarket].add(eid)
                            st.rerun()

            st.divider()
            with st.form(key=f"qa_form_{supermarket}", clear_on_submit=True):
                qa1, qa2, qa3 = st.columns([5, 1, 2])
                with qa1:
                    new_name = st.text_input(
                        "Item",
                        placeholder="Item name…",
                        label_visibility="collapsed",
                        key=f"qa_item_{supermarket}",
                    )
                with qa2:
                    new_qty = st.number_input(
                        "Qty",
                        value=1,
                        min_value=1,
                        step=1,
                        label_visibility="collapsed",
                        key=f"qa_qty_{supermarket}",
                    )
                with qa3:
                    if st.form_submit_button("➕ Add", width="stretch"):
                        if new_name.strip():
                            item_id = st.session_state.extra_item_counter
                            st.session_state.extra_item_counter += 1
                            if supermarket not in st.session_state.extra_shopping_items:
                                st.session_state.extra_shopping_items[supermarket] = []
                            st.session_state.extra_shopping_items[supermarket].append(
                                {"id": item_id, "name": new_name.strip(), "qty": int(new_qty)}
                            )
                            st.rerun()
