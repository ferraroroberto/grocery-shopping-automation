"""Audit > Voice recording: the shared rec-wave strip on a synthetic mic stream."""

from __future__ import annotations

import json

import pytest

from tests.e2e._helpers import LOADED_JS, goto_mode

# A tone through an AudioContext stands in for the mic (no hardware, no recording);
# every context the page opens is counted so a second one per take shows up.
FAKE_MIC = """
(() => {
  window.__contexts = [];
  const Real = window.AudioContext;
  if (Real && !window.__noAudioContext) {
    window.AudioContext = class extends Real {
      constructor(...args) { super(...args); window.__contexts.push(this); }
    };
  }
  navigator.mediaDevices.getUserMedia = async () => {
    const Ctx = Real || window.webkitAudioContext;
    const ctx = new Ctx();
    const osc = ctx.createOscillator();
    const dest = ctx.createMediaStreamDestination();
    osc.connect(dest);
    osc.start();
    return dest.stream;
  };
  if (window.__noAudioContext) { delete window.AudioContext; delete window.webkitAudioContext; }
})();
"""


SPOKEN = "en la nevera, dos yogures."


def _fulfil(route, body) -> None:
    route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


def _stub_voice_service(page, finish: dict | None = None) -> None:
    page.route("**/api/audio/health", lambda r: _fulfil(r, {"hub_ok": True, "whisper_ok": True, "voice_ok": True}))
    page.route("**/api/audio/session", lambda r: _fulfil(r, {"session_id": "fake"}))
    page.route("**/api/audio/session/fake/chunk", lambda r: _fulfil(r, {}))
    page.route("**/api/audio/session/fake/events", lambda r: r.abort())
    page.route("**/api/audio/session/fake/finish**", lambda r: _fulfil(r, finish or {"silent": True}))


def _start_take(page, *, no_audio_context: bool = False, finish: dict | None = None) -> None:
    if no_audio_context:
        page.add_init_script("window.__noAudioContext = true;")
    page.add_init_script(FAKE_MIC)
    _stub_voice_service(page, finish)
    page.reload()
    page.wait_for_selector("[data-tab='audit']")
    page.wait_for_function(LOADED_JS)
    goto_mode(page, "audio")
    page.locator("#record-toggle").click()
    page.locator("#voice-live").wait_for(state="visible")


def _check_waveform_on_one_audio_context(page):
    _start_take(page)
    host = page.locator("#voice-wave")
    host.locator(".rec-wave").wait_for()
    page.wait_for_function("document.querySelector('#voice-wave').dataset.recWaveState === 'live'")
    assert host.locator(".rec-wave-bars > i").count() == 40
    assert page.locator("#voice-timer").inner_text().startswith("0:00")
    # The strip reads the take's stream: the tone lifts at least one bar off its sliver.
    page.wait_for_function(
        "[...document.querySelectorAll('#voice-wave .rec-wave-bars > i')]"
        ".some((bar) => parseFloat(bar.style.transform.replace('scaleY(', '')) > 0.2)"
    )
    # The fake mic builds its context unwrapped, so the one counted is the take's own.
    assert page.evaluate("window.__contexts.length") == 1
    # A hop to Manual and back re-attaches the strip on the same context.
    page.locator("#pane-audit [data-mode='audit']").click()
    page.locator("#pane-audit [data-mode='audio']").click()
    page.locator("#voice-wave .rec-wave").wait_for()
    assert page.evaluate("window.__contexts.length") == 1

    page.locator("#record-toggle").click()
    page.wait_for_function("!document.querySelector('#voice-wave .rec-wave')")
    page.wait_for_function("window.__contexts.every((c) => c.state === 'closed')")
    assert page.locator("#voice-wave .rec-wave").count() == 0


def _check_timer_runs_without_audio_context(page):
    _start_take(page, no_audio_context=True)
    assert page.locator("#voice-wave .rec-wave").count() == 0
    first = page.locator("#voice-timer").inner_text()
    page.wait_for_function(
        "(first) => document.querySelector('#voice-timer').textContent !== first", arg=first, timeout=5000
    )
    assert "saved on the PC" in page.locator("#voice-timer").inner_text()
    page.locator("#record-toggle").click()


@pytest.mark.e2e
def test_voice_take_shows_the_waveform_and_runs_the_timer(page, browser, server):
    """A take shows the shared waveform on one audio context, and where the browser has
    no AudioContext the take still runs its timer. The second take is a fresh page:
    it carries none of the first take's init scripts."""
    _check_waveform_on_one_audio_context(page)
    no_audio = browser.new_page(viewport={"width": 1100, "height": 950})
    try:
        no_audio.goto(server.url)
        _check_timer_runs_without_audio_context(no_audio)
    finally:
        no_audio.close()



def _check_record_stays_in_reach(page) -> None:
    """The checklist unfolded and scrolled into: Record still sits in the viewport,
    clear above the bottom-nav pill, with nothing painted over it."""
    page.evaluate("document.querySelectorAll('#pane-audit .checklist-zone').forEach((d) => { d.open = true; })")
    page.locator("#pane-audit .voice-checklist").evaluate("el => el.scrollIntoView({block: 'center'})")
    geometry = page.evaluate(
        """() => {
          const r = document.querySelector('#record-toggle').getBoundingClientRect();
          const nav = document.querySelector('nav.tabs').getBoundingClientRect();
          const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
          return {top: r.top, bottom: r.bottom, navTop: nav.top, viewport: innerHeight,
                  scrolled: window.scrollY, onTop: !!hit?.closest('#record-toggle')};
        }"""
    )
    assert geometry["scrolled"] > 0, geometry
    assert geometry["navTop"] > geometry["viewport"] / 2, f"no bottom pill on this phone: {geometry}"
    assert 0 <= geometry["top"] and geometry["bottom"] <= geometry["navTop"], geometry
    assert geometry["onTop"], geometry


@pytest.mark.e2e
def test_record_stays_in_reach_and_stop_matches_on_its_own(browser, server):
    """On a touch phone Record/Stop is pinned above the bottom-nav pill, so it stays
    under the thumb while the checklist scrolls; Stop transcribes the take and matches
    it without a Match tap."""
    context = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    phone = context.new_page()
    errors: list[str] = []
    phone.on("pageerror", lambda exc: errors.append(str(exc)))
    matched = []

    def match(route):
        matched.append(route.request.post_data_json)
        _fulfil(route, {"items": [], "zones_mentioned": [], "unmatched_mentions": [], "model": "gemini_pro"})

    try:
        phone.route("**/api/audio/match", match)
        phone.goto(server.url)
        _start_take(phone, finish={"transcript": SPOKEN})
        _check_record_stays_in_reach(phone)
        phone.locator("#record-toggle").click()
        phone.locator("#voice-results").wait_for()
        assert matched and matched[0]["transcript"] == SPOKEN
        assert phone.input_value("#transcript") == SPOKEN
        assert phone.locator("#record-toggle").inner_text().strip() == "Record"
        assert errors == [], f"JS errors: {errors}"
    finally:
        context.close()
