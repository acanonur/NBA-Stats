"""Tests for the position normalisers.

Defence by position is only as honest as its buckets, so these tests pin the three things that
make it honest:

* **The table is closed.** Exactly the strings the design lists are recognised; everything
  else (including near misses such as ``PG-SG``) is unknown rather than interpreted. Format
  (case, spacing, dash character) is tolerated; meaning is not guessed.
* **Weights are exact.** Every recognised position is a mapping of bucket to a multiple of one
  half summing to one, because the per-game allocation reconciles to the opponent's score with
  no rounding slack only if the weights are exact in binary.
* **Unknown is a first-class answer.** A corrupt row, a partial row, a missing code and an
  unrecognised label all resolve to ``None`` / unknown, so the worst a bad input can do is cost
  coverage, which the coverage gate then reports.
"""

from __future__ import annotations

import json
import math

import pytest

from nbastats.shared import positions as P

G = {"G": 1.0}
F = {"F": 1.0}
C = {"C": 1.0}
GF = {"G": 0.5, "F": 0.5}
FC = {"F": 0.5, "C": 0.5}

# ---------------------------------------------------------------------------- NBA table

NBA_TABLE = [
    # the design's table, verbatim
    ("G", G),
    ("PG", G),
    ("SG", G),
    ("Guard", G),
    ("F", F),
    ("SF", F),
    ("PF", F),
    ("Forward", F),
    ("C", C),
    ("Center", C),
    ("Centre", C),
    ("G-F", GF),
    ("F-G", GF),
    ("Guard-Forward", GF),
    ("Forward-Guard", GF),
    ("F-C", FC),
    ("C-F", FC),
    ("Forward-Center", FC),
    ("Center-Forward", FC),
]


@pytest.mark.parametrize("raw, expected", NBA_TABLE)
def test_every_position_in_the_design_table_maps_as_designed(raw, expected) -> None:
    assert dict(P.normalise_nba_position(raw)) == expected


@pytest.mark.parametrize("raw, expected", NBA_TABLE)
def test_weights_are_multiples_of_one_half_and_sum_to_exactly_one(raw, expected) -> None:
    weights = P.normalise_nba_position(raw)
    assert math.fsum(weights.values()) == 1.0
    assert all(w in (0.5, 1.0) for w in weights.values())
    assert P.is_valid_weights(weights)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("g", G),
        ("  Guard  ", G),
        ("GUARD-FORWARD", GF),
        ("g - f", GF),
        ("G/F", GF),
        ("F–C", FC),  # en dash
        ("c—f", FC),  # em dash
        ("forward-centre", FC),  # the same word as "center"
        ("Centre-Forward", FC),
    ],
)
def test_format_is_tolerated(raw, expected) -> None:
    assert dict(P.normalise_nba_position(raw)) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "PG-SG",  # not on the table: unknown, not interpreted
        "SF-PF",
        "G-C",
        "C-G",
        "G-F-C",
        "Point Guard",
        "Shooting Guard",
        "Swingman",
        "",
        "   ",
        "-",
        "N/A",
        None,
        42,
        3.5,
        ["G"],
        {"G": 1},
    ],
)
def test_anything_else_is_unknown(raw) -> None:
    assert P.normalise_nba_position(raw) is None


def test_normalised_weights_are_read_only() -> None:
    weights = P.normalise_nba_position("G-F")
    with pytest.raises(TypeError):
        weights["G"] = 1.0  # type: ignore[index]


# ----------------------------------------------------------------- the stored columns


def test_columns_roundtrip_a_normalised_position() -> None:
    assert dict(P.weights_from_columns(1, 0, 0)) == G
    assert dict(P.weights_from_columns(0.5, 0.5, 0.0)) == GF
    assert dict(P.weights_from_columns(0.0, 0.5, 0.5)) == FC
    # zeros are dropped so the result compares equal to the normaliser's
    assert P.weights_from_columns(0.5, 0.5, 0) == P.normalise_nba_position("G-F")


@pytest.mark.parametrize(
    "columns",
    [
        (None, None, None),  # all NULL is how "no position" is stored
        (1.0, None, None),  # a partial row breaks the table's rule
        (None, 1.0, 0.0),
        (0.6, 0.5, 0.0),  # sums to 1.1
        (0.4, 0.4, 0.0),  # sums to 0.8
        (1.5, -0.5, 0.0),  # sums to 1 but a weight is negative
        (math.nan, 0.5, 0.5),
        (math.inf, 0.0, 0.0),
        (True, 0, 0),  # booleans are not weights
        ("1", 0, 0),
        (0.0, 0.0, 0.0),  # sums to 0
    ],
)
def test_a_row_that_breaks_the_rule_is_unknown_not_repaired(columns) -> None:
    assert P.weights_from_columns(*columns) is None


def test_is_valid_weights() -> None:
    assert P.is_valid_weights({"G": 1.0})
    assert P.is_valid_weights({"G": 0.25, "F": 0.25, "C": 0.5})
    assert not P.is_valid_weights({})
    assert not P.is_valid_weights(None)
    assert not P.is_valid_weights({"G": 0.5})
    assert not P.is_valid_weights({"G": 1.5, "F": -0.5})


# ------------------------------------------------------------------------- EuroLeague


@pytest.mark.parametrize(
    "code, bucket",
    [(1, "G"), (2, "F"), (3, "C"), ("1", "G"), (" 2 ", "F"), ("3", "C"), (3.0, "C")],
)
def test_registration_codes_map_to_buckets(code, bucket) -> None:
    assert P.euroleague_position_bucket(code) == bucket


@pytest.mark.parametrize("code", [0, 4, -1, "0", "x", "", None, True, False, 1.5, [1], "1.0"])
def test_other_registration_codes_are_unknown(code) -> None:
    assert P.euroleague_position_bucket(code) is None


def test_workbook_labels() -> None:
    for label, bucket in [("PG", "G"), ("SG", "G"), ("SF", "F"), ("PF", "F"), ("C", "C")]:
        assert P.workbook5_label(label) == label
        assert P.workbook5_to_gfc(label) == bucket
    assert P.workbook5_label(" pg ") == "PG"
    for bad in ("G", "F", "Guard", "", None, 1, "PG-SG"):
        assert P.workbook5_label(bad) is None
        assert P.workbook5_to_gfc(bad) is None


def test_resolution_prefers_the_registration_then_the_box_score() -> None:
    both = P.resolve_euroleague_position(
        registration_code=2, box_score_code=1, workbook_label="PG", has_official_registrations=True
    )
    assert dict(both.weights) == F and both.basis == P.BASIS_LISTED
    box_only = P.resolve_euroleague_position(
        registration_code=None, box_score_code="3", has_official_registrations=True
    )
    assert dict(box_only.weights) == C and box_only.basis == P.BASIS_LISTED
    # a bad registration code falls through to the box score, not to unknown
    fallthrough = P.resolve_euroleague_position(registration_code=9, box_score_code=1)
    assert dict(fallthrough.weights) == G and fallthrough.basis == P.BASIS_LISTED


def test_the_workbook_label_is_used_only_when_the_store_has_no_official_registrations() -> None:
    kwargs = dict(registration_code=None, box_score_code=None, workbook_label="SF")
    with_official = P.resolve_euroleague_position(has_official_registrations=True, **kwargs)
    assert with_official.weights is None and with_official.basis == P.BASIS_UNKNOWN
    without = P.resolve_euroleague_position(has_official_registrations=False, **kwargs)
    assert dict(without.weights) == F and without.basis == P.BASIS_WORKBOOK_LISTING
    # even then, an official code outranks the label
    official = P.resolve_euroleague_position(
        registration_code=1, workbook_label="SF", has_official_registrations=False
    )
    assert dict(official.weights) == G and official.basis == P.BASIS_LISTED
    # a label that is not one of the five is not a position
    junk = P.resolve_euroleague_position(workbook_label="Guard", has_official_registrations=False)
    assert junk.weights is None


def test_workbook5_scheme_takes_only_the_workbook_label() -> None:
    got = P.resolve_euroleague_position(
        registration_code=1, workbook_label="PF", scheme=P.SCHEME_WORKBOOK5
    )
    assert dict(got.weights) == {"PF": 1.0}
    assert got.basis == P.BASIS_WORKBOOK_LISTING
    assert got.bucket == "PF"
    none = P.resolve_euroleague_position(registration_code=1, scheme=P.SCHEME_WORKBOOK5)
    assert none.weights is None and none.bucket == P.UNKNOWN


def test_unknown_scheme_is_an_error_not_a_default() -> None:
    with pytest.raises(ValueError):
        P.resolve_euroleague_position(registration_code=1, scheme="gfc5")
    with pytest.raises(ValueError):
        P.buckets_for_scheme("nope")


def test_bucket_of_a_split_position_is_unknown() -> None:
    assert P.Resolution(weights=P.normalise_nba_position("G-F"), basis="listed").bucket == "unknown"


# ------------------------------------------------------------------------------ schemes


def test_schemes_and_labels() -> None:
    assert P.buckets_for_scheme("gfc") == ("G", "F", "C")
    assert P.buckets_for_scheme("workbook5") == ("PG", "SG", "SF", "PF", "C")
    assert P.all_buckets("gfc") == ("G", "F", "C", "unknown")
    assert P.all_buckets("workbook5")[-1] == "unknown"
    assert P.bucket_label("G") == "Guards"
    assert P.bucket_label("unknown") == "Position unknown"
    with pytest.raises(ValueError):
        P.bucket_label("X")
    for bucket in P.all_buckets("workbook5"):
        assert P.bucket_label(bucket)


def test_scheme_contract_is_json_ready() -> None:
    contract = P.scheme_contract()
    assert json.loads(json.dumps(contract)) == contract
    assert contract["unknownBucket"] == "unknown"
    assert contract["schemes"]["gfc"] == ["G", "F", "C"]
    assert contract["registrationCodes"] == {"1": "G", "2": "F", "3": "C"}
    assert contract["bases"] == ["listed", "workbookListing", "unknown"]
