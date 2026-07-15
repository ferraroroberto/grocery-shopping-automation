"""Unit tests for the product-search display scorer (issue #87)."""

from src.product_match import PARTIAL_MATCH, STRONG_MATCH, label, normalize, score


def test_normalize_strips_accents_and_punctuation():
    assert normalize("Sandía baja en semillas!") == "sandia baja en semillas"
    assert normalize("  Melón   Galia  ") == "melon galia"


def test_score_full_coverage_scores_strong():
    # "sandia" fully covered by the Spanish name → strong band.
    s = score("sandia", "Sandía baja en semillas")
    assert s >= STRONG_MATCH
    assert label(s) == "strong"


def test_score_unrelated_name_scores_weak():
    s = score("sandia", "Detergente líquido")
    assert s < PARTIAL_MATCH
    assert label(s) == "weak"


def test_score_catalan_name_is_low_by_design():
    # Ametller returns Catalan names; string similarity is unreliable there —
    # this documents that the caller must not lean on the score for Ametller.
    assert score("aguacate", "Alvocat caixa 1kg") < STRONG_MATCH


def test_score_empty_inputs_are_zero():
    assert score("", "Sandía") == 0.0
    assert score("sandia", "") == 0.0


def test_label_bands():
    assert label(0.9) == "strong"
    assert label(0.45) == "partial"
    assert label(0.1) == "weak"
