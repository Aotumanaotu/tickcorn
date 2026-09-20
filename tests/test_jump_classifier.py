"""JumpEventClassifier unit tests.

Includes the four cases required by the spec and a randomized parity
check between the pairwise reference implementation and the vectorized
batch implementation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.classifier.jump_classifier import JumpEventClassifier
from app.common.constants import EventLabel


@pytest.fixture(scope="module")
def clf():
    return JumpEventClassifier(tick_size=1.0)


# ---------------------------------------------------------------------
# Spec cases
# ---------------------------------------------------------------------

def test_case1_high_confidence_bounce(clf):
    """Case 1: 2300/2301 last 2300 -> 2300/2301 last 2301 => HIGH_CONFIDENCE_BOUNCE."""
    r = clf.classify_pair(2300, 2301, 2300, 2300, 2301, 2301)
    assert r.label == EventLabel.HIGH_CONFIDENCE_BOUNCE_UP
    assert r.direction == 1
    assert r.db_ticks == 0 and r.da_ticks == 0 and r.dl_ticks == 1


def test_case2_genuine_quote_move_up(clf):
    """Case 2: 2300/2301 -> 2301/2302 => GENUINE_QUOTE_MOVE_UP."""
    r = clf.classify_pair(2300, 2301, 2300, 2301, 2302, 2301)
    assert r.label == EventLabel.GENUINE_QUOTE_MOVE_UP
    assert r.direction == 1


def test_case3_genuine_quote_move_down(clf):
    """Case 3: 2300/2301 -> 2299/2300 => GENUINE_QUOTE_MOVE_DOWN."""
    r = clf.classify_pair(2300, 2301, 2300, 2299, 2300, 2299)
    assert r.label == EventLabel.GENUINE_QUOTE_MOVE_DOWN
    assert r.direction == -1


def test_case4_complex_change_ambiguous(clf):
    """Case 4: complex book change => AMBIGUOUS."""
    # bid down 1, ask up 1 (spread widens), last unchanged
    r = clf.classify_pair(2300, 2301, 2300, 2299, 2302, 2300)
    assert r.label == EventLabel.AMBIGUOUS


# ---------------------------------------------------------------------
# Additional semantics
# ---------------------------------------------------------------------

def test_bounce_down(clf):
    r = clf.classify_pair(2300, 2301, 2301, 2300, 2301, 2300)
    assert r.label == EventLabel.HIGH_CONFIDENCE_BOUNCE_DOWN
    assert r.direction == -1


def test_no_move(clf):
    r = clf.classify_pair(2300, 2301, 2300, 2300, 2301, 2300)
    assert r.label == EventLabel.NO_MOVE
    assert r.direction == 0


def test_no_move_with_last_invalid(clf):
    r = clf.classify_pair(2300, 2301, None, 2300, 2301, None)
    assert r.label == EventLabel.NO_MOVE


def test_invalid_quotes_ambiguous(clf):
    sentinel = 1.7976931348623157e308
    r = clf.classify_pair(0.0, 2301, 2300, 2300, 2301, 2301)
    assert r.label == EventLabel.AMBIGUOUS
    r = clf.classify_pair(2300, 2301, 2300, sentinel, 2301, 2301)
    assert r.label == EventLabel.AMBIGUOUS


def test_crossed_quotes_ambiguous(clf):
    r = clf.classify_pair(2300, 2301, 2300, 2302, 2301, 2301)
    assert r.label == EventLabel.AMBIGUOUS


def test_likely_bounce_reshape(clf):
    """Last +-1 tick, mid unchanged, quote reshaped symmetrically:
    2300/2302 last 2301 -> 2299/2303? mid changes... use:
    2300/2303 last 2300 -> 2299/2304? no.
    Clean case: bid -1, ask +1 (mid unchanged), last +1 inside quote."""
    # prev: bid 2300 ask 2302 (mid 2301), last 2300
    # cur:  bid 2299 ask 2303 (mid 2301), last 2301  -> dl=+1, dm=0
    r = clf.classify_pair(2300, 2302, 2300, 2299, 2303, 2301)
    assert r.label == EventLabel.LIKELY_BOUNCE_UP


def test_likely_bounce_inside_wide_spread(clf):
    """Quotes unchanged, last moves 1 tick inside a 2-tick spread."""
    # prev: 2300/2302 last 2300 ; cur: 2300/2302 last 2301
    r = clf.classify_pair(2300, 2302, 2300, 2300, 2302, 2301)
    assert r.label == EventLabel.LIKELY_BOUNCE_UP


def test_inside_spread_non_unit_ambiguous(clf):
    """Quotes unchanged, last jumps 2 ticks inside a 3-tick spread."""
    r = clf.classify_pair(2300, 2303, 2300, 2300, 2303, 2302)
    assert r.label == EventLabel.AMBIGUOUS


def test_last_outside_unchanged_quote_ambiguous(clf):
    """Quotes unchanged but last outside [bid, ask]."""
    r = clf.classify_pair(2300, 2301, 2300, 2300, 2301, 2299)
    assert r.label == EventLabel.AMBIGUOUS


def test_asymmetric_same_direction_strict_ambiguous(clf):
    """bid +1, ask +2 (same direction, different magnitudes):
    strict mode => AMBIGUOUS with hint."""
    r = clf.classify_pair(2300, 2301, 2300, 2301, 2303, 2301)
    assert r.label == EventLabel.AMBIGUOUS
    assert r.direction_hint == 1


def test_asymmetric_same_direction_loose_genuine():
    clf_loose = JumpEventClassifier(tick_size=1.0,
                                    strict_symmetric_quote_move=False)
    r = clf_loose.classify_pair(2300, 2301, 2300, 2301, 2303, 2301)
    assert r.label == EventLabel.GENUINE_QUOTE_MOVE_UP


def test_one_sided_move_with_mid_tick_strict_ambiguous(clf):
    """bid +2, ask flat: mid +1 tick, spread narrows 3->1 -> strict: AMBIGUOUS."""
    r = clf.classify_pair(2299, 2302, 2300, 2301, 2302, 2301)
    assert r.label == EventLabel.AMBIGUOUS
    assert r.direction_hint == 1


def test_one_sided_move_loose_genuine():
    clf_loose = JumpEventClassifier(tick_size=1.0,
                                    strict_symmetric_quote_move=False)
    r = clf_loose.classify_pair(2299, 2302, 2300, 2301, 2302, 2301)
    assert r.label == EventLabel.GENUINE_QUOTE_MOVE_UP


def test_non_tick_aligned_ambiguous(clf):
    r = clf.classify_pair(2300, 2301, 2300, 2300.5, 2301.5, 2300.5)
    assert r.label == EventLabel.AMBIGUOUS


def test_half_tick_product():
    """Product with 0.5 tick (e.g. some options) works correctly."""
    clf = JumpEventClassifier(tick_size=0.5)
    r = clf.classify_pair(2300.0, 2300.5, 2300.0, 2300.5, 2301.0, 2300.5)
    assert r.label == EventLabel.GENUINE_QUOTE_MOVE_UP
    r = clf.classify_pair(2300.0, 2300.5, 2300.0, 2300.0, 2300.5, 2300.5)
    assert r.label == EventLabel.HIGH_CONFIDENCE_BOUNCE_UP


def test_tick_size_validation():
    with pytest.raises(ValueError):
        JumpEventClassifier(tick_size=0)


# ---------------------------------------------------------------------
# Vectorized parity
# ---------------------------------------------------------------------

def _random_cases(rng, n):
    """Generate random (prev, cur) quote/last tuples including sentinels."""
    sentinel = 1.7976931348623157e308
    def price():
        roll = rng.random()
        if roll < 0.05:
            return sentinel
        if roll < 0.08:
            return 0.0
        # mostly on-grid prices near 2300, occasionally off-grid
        base = 2300 + int(rng.integers(-5, 6))
        if rng.random() < 0.03:
            base += 0.5
        return float(base)
    cases = []
    for _ in range(n):
        pb, pa = price(), price()
        if pb < 1e300 and pa < 1e300 and pa <= pb:
            pa = pb + 1.0
        cb, ca = price(), price()
        if cb < 1e300 and ca < 1e300 and ca <= cb:
            ca = cb + 1.0
        pl = price() if rng.random() < 0.97 else None
        cl = price() if rng.random() < 0.97 else None
        cases.append((pb, pa, pl, cb, ca, cl))
    return cases


@pytest.mark.parametrize("strict", [True, False])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_vectorized_parity(strict, seed):
    clf = JumpEventClassifier(tick_size=1.0, strict_symmetric_quote_move=strict)
    rng = np.random.default_rng(seed)
    cases = _random_cases(rng, 4000)

    # add hand-crafted edge cases
    cases += [
        (2300, 2301, 2300, 2300, 2301, 2301),
        (2300, 2301, 2301, 2300, 2301, 2300),
        (2300, 2301, 2300, 2301, 2302, 2301),
        (2300, 2301, 2300, 2299, 2300, 2299),
        (2300, 2301, 2300, 2299, 2302, 2300),
        (2300, 2302, 2300, 2300, 2302, 2301),
        (2300, 2303, 2300, 2300, 2303, 2302),
        (2300, 2303, 2300, 2300, 2303, 2301),
        (2300, 2301, 2300, 2300, 2301, 2299),
        (2300, 2302, 2300, 2299, 2303, 2301),
        (2300, 2302, 2300, 2302, 2302, 2301),
        (None, None, None, 2300, 2301, 2300),
        (2300, 2301, None, 2300, 2301, None),
        (2300, 2301, 2300, 2300.5, 2301.5, 2300.5),
    ]

    pb = np.array([np.nan if c[0] is None else float(c[0]) for c in cases])
    pa = np.array([np.nan if c[1] is None else float(c[1]) for c in cases])
    pl = np.array([np.nan if c[2] is None else float(c[2]) for c in cases])
    cb = np.array([np.nan if c[3] is None else float(c[3]) for c in cases])
    ca = np.array([np.nan if c[4] is None else float(c[4]) for c in cases])
    cl = np.array([np.nan if c[5] is None else float(c[5]) for c in cases])

    vec = clf.classify_arrays(pb, pa, pl, cb, ca, cl)

    mismatches = []
    for i, (a, b, c, d, e, f) in enumerate(cases):
        ref = clf.classify_pair(a, b, c, d, e, f)
        v_label = vec["label"][i]
        if v_label != ref.label.value:
            mismatches.append((i, (a, b, c, d, e, f), ref.label.value, v_label,
                               ref.reason, vec["reason"][i]))
    assert not mismatches, f"{len(mismatches)} mismatches:\n" + "\n".join(
        f"  case={m[1]} ref={m[2]} vec={m[3]} (ref_reason={m[4]}, "
        f"vec_reason={m[5]})" for m in mismatches[:12])


def test_classify_dataframe_first_row_blank(clf):
    df = pd.DataFrame({
        "bid_price1": [2300.0, 2300.0],
        "ask_price1": [2301.0, 2301.0],
        "last_price": [2300.0, 2301.0],
    })
    res = clf.classify_dataframe(df)
    assert res.iloc[0]["label"] is None or pd.isna(res.iloc[0]["label"])
    assert res.iloc[1]["label"] == "HIGH_CONFIDENCE_BOUNCE_UP"
