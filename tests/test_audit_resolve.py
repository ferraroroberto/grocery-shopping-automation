"""Unit tests for the deterministic mention→row resolver (issue #132).

The fixture rows and the phrases are lifted from real audio-audit logs — these
are the exact pairs whisper produced on a walk, not invented near-misses.
"""

import pandas as pd
import pytest

from src.audit_resolve import AMBIGUITY_MARGIN, RESOLVE_THRESHOLD, phrase_score, resolve_phrase


@pytest.fixture()
def inventory() -> pd.DataFrame:
    rows = [
        (27, "noodles arroz", "estante"),
        (34, "chocolate negro 85", "estante"),
        (35, "chocolate negro 99", "estante"),
        (36, "copos avena", "estante"),
        (37, "crema cacahuete", "estante"),
        (38, "garbanzos", "estante"),
        (19, "garbanzos secos", "despensa"),
        (50, "tortitas legumbres", "estante"),
        (51, "tortitas maíz", "estante"),
        (44, "miel", "despensa"),
        (88, "actimel", "nevera"),
        (152, "pesto", "nevera"),
    ]
    return pd.DataFrame(
        {"comida": [r[1] for r in rows], "lugar": [r[2] for r in rows]},
        index=[r[0] for r in rows],
    )


ZONES = ["congelador", "despensa", "estante", "garaje", "nevera"]


@pytest.mark.parametrize(
    "phrase, expected",
    [
        ("Noodles de arroz", 27),      # stopword dropped
        ("Copos de arena", 36),        # avena → arena
        ("Crema de cacahuete", 37),
        ("Tortitas de legumbres", 50),  # must beat "tortitas maíz"
        ("Aximell", 88),               # phonetic drift, the weakest real match
        ("Pesto", 152),
        ("Ganzos", 38),                # the mention that used to vanish entirely
    ],
)
def test_resolves_real_whisper_drift(inventory, phrase, expected):
    resolution = resolve_phrase(phrase, inventory, zones=ZONES)
    assert resolution is not None, f"{phrase!r} should resolve"
    assert resolution.idx == expected
    assert resolution.score >= RESOLVE_THRESHOLD


def test_declines_a_phrase_that_matches_nothing(inventory):
    assert resolve_phrase("una cosa rara que no existe", inventory, zones=ZONES) is None


def test_declines_narration_that_merely_rhymes(inventory):
    """"Esto" scores 0.889 against *pesto* on letters alone — it is a filler word,
    and resolving it would quietly exclude pesto from the zero-list."""
    assert resolve_phrase("Esto", inventory, zones=ZONES) is None
    assert phrase_score("Esto", "pesto") > RESOLVE_THRESHOLD  # the trap is real


def test_declines_when_two_candidates_are_indistinguishable(inventory):
    """"Chocolate negro" fits 85 and 99 equally — guessing would write the wrong row."""
    assert resolve_phrase("Chocolate negro de", inventory, zones=ZONES) is None


def test_excluding_the_claimed_row_breaks_the_tie(inventory):
    """Once 99 has taken its own count, the leftover mention can only be 85."""
    resolution = resolve_phrase("Chocolate negro de", inventory, zones=ZONES, exclude=[35])
    assert resolution is not None and resolution.idx == 34


def test_zone_gate_excludes_rows_the_speaker_never_walked(inventory):
    assert resolve_phrase("Aximell", inventory, zones=["estante"]) is None
    assert resolve_phrase("Aximell", inventory, zones=["nevera"]).idx == 88


def test_no_zone_context_considers_every_row(inventory):
    assert resolve_phrase("Aximell", inventory).idx == 88


def test_empty_inputs_resolve_to_none(inventory):
    assert resolve_phrase("", inventory, zones=ZONES) is None
    assert resolve_phrase("   ", inventory, zones=ZONES) is None
    assert resolve_phrase("pesto", pd.DataFrame(columns=["comida", "lugar"])) is None


def test_ambiguity_margin_is_actually_enforced(inventory):
    """garbanzos vs garbanzos secos is the tightest real pair — it must still clear
    the margin, or the resolver would decline a mention it can safely place."""
    best = phrase_score("Ganzos", "garbanzos")
    runner_up = phrase_score("Ganzos", "garbanzos secos")
    assert best - runner_up > AMBIGUITY_MARGIN


def test_score_is_symmetric_on_identical_names():
    assert phrase_score("pesto", "pesto") == 1.0
    assert phrase_score("", "pesto") == 0.0
