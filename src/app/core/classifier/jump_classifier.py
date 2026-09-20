"""JumpEventClassifier: classify every snapshot transition (t-1 -> t).

Terminology reminder for the paper
----------------------------------
CTP delivers Market Snapshots, NOT tick-by-tick order flow. Between two
snapshots, orders/cancels/trades may have happened that we cannot observe.
Therefore bid-ask bounce inferred from snapshots is confidence-graded.

Decision tree (priority order; tick = configured tick size):
    0.  invalid / crossed quotes on either side  -> AMBIGUOUS(QUOTE_INVALID)
        (the processed layer normally filters these rows out beforehand)
    1.  db == da == dl == 0                       -> NO_MOVE
    2.  db == da != 0  (whole quote shifted)      -> GENUINE_QUOTE_MOVE_UP/DOWN
        loose mode: db>0 and da>0 (or both <0) even with different magnitudes
    3.  db == da == 0 (quotes unchanged):
          last toggles  bid <-> ask               -> HIGH_CONFIDENCE_BOUNCE_UP/DOWN
          last moves +-1 tick inside the quote    -> LIKELY_BOUNCE_UP/DOWN
          last moves inside the quote by >1 tick  -> AMBIGUOUS(INSIDE_SPREAD_NON_UNIT_MOVE)
              (unless likely_bounce_requires_unit_move=False)
          last outside the (unchanged) quote      -> AMBIGUOUS(LAST_OUTSIDE_UNCHANGED_QUOTE)
    4.  asymmetric quote change:
          |dl| == 1 tick, |dm| <= mid_tolerance,
          no same-direction whole-quote move,
          last stays within the quote             -> LIKELY_BOUNCE_UP/DOWN
          |dm| >= 1 tick (mid clearly moved):
              strict mode                         -> AMBIGUOUS(ASYMMETRIC_QUOTE_MOVE /
                                                               MID_MOVE_WITH_COMPLEX_QUOTE)
              loose mode                          -> GENUINE_QUOTE_MOVE_UP/DOWN
          anything else                           -> AMBIGUOUS(COMPLEX_QUOTE_CHANGE)

Where db/da/dl/dm are bid/ask/last/mid changes measured in ticks.
Conservative by design: when in doubt, AMBIGUOUS.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from app.common.constants import (
    CTP_DOUBLE_SENTINEL,
    PRICE_SENTINEL_THRESHOLD,
    AmbiguousReason,
    EventFamily,
    EventLabel,
    LABEL_META,
    State5,
)

# Absolute tolerance for price comparisons (prices ~1e3, doubles are exact
# for integer/half-integer values; this only guards fp noise).
_ABS_TOL = 1e-6


def is_valid_price(p: Optional[float]) -> bool:
    """A usable price: not None/NaN, positive, below the CTP sentinel."""
    if p is None:
        return False
    if isinstance(p, float) and (math.isnan(p) or math.isinf(p)):
        return False
    return 0.0 < p < PRICE_SENTINEL_THRESHOLD


@dataclass(frozen=True)
class ClassificationResult:
    """Result of classifying a single snapshot transition."""

    label: EventLabel
    family: EventFamily
    state5: Optional[State5]
    state8: str
    direction: int
    reason: Optional[AmbiguousReason]
    direction_hint: int
    db_ticks: float
    da_ticks: float
    dl_ticks: float
    dm_ticks: float
    spread_prev_ticks: float
    spread_cur_ticks: float


_FIRST_RESULT = ClassificationResult(
    label=EventLabel.NO_MOVE, family=EventFamily.NO_MOVE, state5=State5.NO_MOVE,
    state8="NO_MOVE", direction=0, reason=None, direction_hint=0,
    db_ticks=0.0, da_ticks=0.0, dl_ticks=0.0, dm_ticks=0.0,
    spread_prev_ticks=0.0, spread_cur_ticks=0.0,
)


def _meta(label: EventLabel, reason: Optional[AmbiguousReason] = None,
          direction_hint: int = 0, db: float = 0.0, da: float = 0.0,
          dl: float = 0.0, dm: float = 0.0, sp: float = 0.0,
          sc: float = 0.0) -> ClassificationResult:
    family, state5, direction = LABEL_META[label]
    return ClassificationResult(
        label=label, family=family, state5=state5, state8=_state8_of(label),
        direction=direction, reason=reason, direction_hint=direction_hint,
        db_ticks=db, da_ticks=da, dl_ticks=dl, dm_ticks=dm,
        spread_prev_ticks=sp, spread_cur_ticks=sc,
    )


def _state8_of(label: EventLabel) -> str:
    return {
        EventLabel.NO_MOVE: "NO_MOVE",
        EventLabel.HIGH_CONFIDENCE_BOUNCE_UP: "HIGH_BOUNCE_UP",
        EventLabel.HIGH_CONFIDENCE_BOUNCE_DOWN: "HIGH_BOUNCE_DOWN",
        EventLabel.LIKELY_BOUNCE_UP: "LIKELY_BOUNCE_UP",
        EventLabel.LIKELY_BOUNCE_DOWN: "LIKELY_BOUNCE_DOWN",
        EventLabel.GENUINE_QUOTE_MOVE_UP: "QUOTE_UP",
        EventLabel.GENUINE_QUOTE_MOVE_DOWN: "QUOTE_DOWN",
        EventLabel.AMBIGUOUS: "AMBIGUOUS",
    }[label]


class JumpEventClassifier:
    """Classify LastPrice / best-quote changes between consecutive snapshots.

    tick_size MUST come from instrument configuration (never hard-coded).
    All constructor parameters are recorded with every analysis run.
    """

    def __init__(
        self,
        tick_size: float,
        strict_symmetric_quote_move: bool = True,
        mid_tolerance_ticks: float = 0.0,
        likely_bounce_requires_unit_move: bool = True,
    ):
        if tick_size is None or tick_size <= 0:
            raise ValueError(f"tick_size must be positive, got {tick_size!r}")
        self.tick_size = float(tick_size)
        self.strict_symmetric_quote_move = bool(strict_symmetric_quote_move)
        self.mid_tolerance_ticks = float(mid_tolerance_ticks)
        self.likely_bounce_requires_unit_move = bool(likely_bounce_requires_unit_move)

    # ------------------------------------------------------------------
    # Pairwise (reference implementation)
    # ------------------------------------------------------------------
    def classify_pair(
        self,
        prev_bid: Optional[float],
        prev_ask: Optional[float],
        prev_last: Optional[float],
        cur_bid: Optional[float],
        cur_ask: Optional[float],
        cur_last: Optional[float],
    ) -> ClassificationResult:
        t = self.tick_size
        tol = _ABS_TOL

        pbv, pav, cbv, cav = (is_valid_price(x) for x in (prev_bid, prev_ask, cur_bid, cur_ask))
        plv, clv = is_valid_price(prev_last), is_valid_price(cur_last)
        quotes_ok = pbv and pav and cbv and cav and prev_ask > prev_bid and cur_ask > cur_bid

        if not quotes_ok:
            return _meta(EventLabel.AMBIGUOUS, AmbiguousReason.QUOTE_INVALID)

        db = (cur_bid - prev_bid) / t
        da = (cur_ask - prev_ask) / t
        dl = (cur_last - prev_last) / t if (plv and clv) else float("nan")
        mid_p = (prev_bid + prev_ask) / 2.0
        mid_c = (cur_bid + cur_ask) / 2.0
        dm = (mid_c - mid_p) / t
        sp = (prev_ask - prev_bid) / t
        sc = (cur_ask - cur_bid) / t

        # Every price must sit on the tick grid for tick-based reasoning.
        for p in (prev_bid, prev_ask, cur_bid, cur_ask):
            if abs(p / t - round(p / t)) > tol:
                return _meta(EventLabel.AMBIGUOUS, AmbiguousReason.NON_TICK_ALIGNED,
                             db=db, da=da, dl=dl, dm=dm, sp=sp, sc=sc)
        if plv and abs(prev_last / t - round(prev_last / t)) > tol:
            return _meta(EventLabel.AMBIGUOUS, AmbiguousReason.NON_TICK_ALIGNED,
                         db=db, da=da, dl=dl, dm=dm, sp=sp, sc=sc)
        if clv and abs(cur_last / t - round(cur_last / t)) > tol:
            return _meta(EventLabel.AMBIGUOUS, AmbiguousReason.NON_TICK_ALIGNED,
                         db=db, da=da, dl=dl, dm=dm, sp=sp, sc=sc)

        db0, da0 = abs(db) <= tol, abs(da) <= tol
        dl0 = (not plv) or (not clv) or abs(dl) <= tol
        dl_unit = plv and clv and (abs(dl - 1.0) <= tol or abs(dl + 1.0) <= tol)

        # 1. NO_MOVE ---------------------------------------------------
        if db0 and da0 and dl0:
            return _meta(EventLabel.NO_MOVE, db=db, da=da, dl=dl, dm=dm, sp=sp, sc=sc)

        # 2. whole-quote (symmetric) move -------------------------------
        if not db0 and not da0 and abs(db - da) <= tol:
            label = (EventLabel.GENUINE_QUOTE_MOVE_UP if db > 0
                     else EventLabel.GENUINE_QUOTE_MOVE_DOWN)
            return _meta(label, db=db, da=da, dl=dl, dm=dm, sp=sp, sc=sc)
        if (not self.strict_symmetric_quote_move and not db0 and not da0
                and ((db > 0 and da > 0) or (db < 0 and da < 0))):
            label = (EventLabel.GENUINE_QUOTE_MOVE_UP if db > 0
                     else EventLabel.GENUINE_QUOTE_MOVE_DOWN)
            return _meta(label, db=db, da=da, dl=dl, dm=dm, sp=sp, sc=sc)

        # 3. quotes unchanged -> bounce analysis ------------------------
        if db0 and da0:
            # dl != 0 here (NO_MOVE failed) and last is valid on both sides.
            if plv and clv:
                if abs(prev_last - prev_bid) <= tol and abs(cur_last - cur_ask) <= tol:
                    return _meta(EventLabel.HIGH_CONFIDENCE_BOUNCE_UP,
                                 db=db, da=da, dl=dl, dm=dm, sp=sp, sc=sc)
                if abs(prev_last - prev_ask) <= tol and abs(cur_last - cur_bid) <= tol:
                    return _meta(EventLabel.HIGH_CONFIDENCE_BOUNCE_DOWN,
                                 db=db, da=da, dl=dl, dm=dm, sp=sp, sc=sc)
                prev_in = prev_bid - tol <= prev_last <= prev_ask + tol
                cur_in = cur_bid - tol <= cur_last <= cur_ask + tol
                if prev_in and cur_in:
                    if dl_unit or not self.likely_bounce_requires_unit_move:
                        label = (EventLabel.LIKELY_BOUNCE_UP if dl > 0
                                 else EventLabel.LIKELY_BOUNCE_DOWN)
                        return _meta(label, db=db, da=da, dl=dl, dm=dm, sp=sp, sc=sc)
                    return _meta(EventLabel.AMBIGUOUS,
                                 AmbiguousReason.INSIDE_SPREAD_NON_UNIT_MOVE,
                                 direction_hint=(1 if dl > 0 else -1),
                                 db=db, da=da, dl=dl, dm=dm, sp=sp, sc=sc)
                return _meta(EventLabel.AMBIGUOUS,
                             AmbiguousReason.LAST_OUTSIDE_UNCHANGED_QUOTE,
                             direction_hint=(1 if dl > 0 else -1),
                             db=db, da=da, dl=dl, dm=dm, sp=sp, sc=sc)
            # last untrackable and quotes unchanged: nothing observable changed
            return _meta(EventLabel.NO_MOVE, db=db, da=da, dl=dl, dm=dm, sp=sp, sc=sc)

        # 4. asymmetric quote change ------------------------------------
        same_dir = (db > tol and da > tol) or (db < -tol and da < -tol)
        if (plv and clv and dl_unit and abs(dm) <= self.mid_tolerance_ticks + tol
                and not same_dir
                and prev_bid - tol <= prev_last <= prev_ask + tol
                and cur_bid - tol <= cur_last <= cur_ask + tol):
            label = (EventLabel.LIKELY_BOUNCE_UP if dl > 0
                     else EventLabel.LIKELY_BOUNCE_DOWN)
            return _meta(label, db=db, da=da, dl=dl, dm=dm, sp=sp, sc=sc)

        if abs(dm) >= 1.0 - tol:
            hint = 1 if dm > 0 else -1
            if not self.strict_symmetric_quote_move:
                label = (EventLabel.GENUINE_QUOTE_MOVE_UP if dm > 0
                         else EventLabel.GENUINE_QUOTE_MOVE_DOWN)
                return _meta(label, db=db, da=da, dl=dl, dm=dm, sp=sp, sc=sc)
            reason = (AmbiguousReason.ASYMMETRIC_QUOTE_MOVE if same_dir
                      else AmbiguousReason.MID_MOVE_WITH_COMPLEX_QUOTE)
            return _meta(EventLabel.AMBIGUOUS, reason, direction_hint=hint,
                         db=db, da=da, dl=dl, dm=dm, sp=sp, sc=sc)

        if plv and clv and not (cur_bid - tol <= cur_last <= cur_ask + tol):
            return _meta(EventLabel.AMBIGUOUS, AmbiguousReason.LAST_OUTSIDE_QUOTE,
                         direction_hint=(1 if dl > 0 else -1),
                         db=db, da=da, dl=dl, dm=dm, sp=sp, sc=sc)
        return _meta(EventLabel.AMBIGUOUS, AmbiguousReason.COMPLEX_QUOTE_CHANGE,
                     db=db, da=da, dl=dl, dm=dm, sp=sp, sc=sc)

    # ------------------------------------------------------------------
    # Vectorized (batch) implementation -- must match classify_pair exactly.
    # ------------------------------------------------------------------
    def classify_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add classification columns to a snapshot dataframe.

        Required columns: bid_price1, ask_price1, last_price.
        The first row gets an empty label (no previous snapshot).
        """
        required = {"bid_price1", "ask_price1", "last_price"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"classify_dataframe: missing columns {missing}")

        n = len(df)
        pb = _shift(df["bid_price1"].to_numpy(dtype=float))
        pa = _shift(df["ask_price1"].to_numpy(dtype=float))
        pl = _shift(df["last_price"].to_numpy(dtype=float))
        cb = df["bid_price1"].to_numpy(dtype=float)
        ca = df["ask_price1"].to_numpy(dtype=float)
        cl = df["last_price"].to_numpy(dtype=float)

        out = self.classify_arrays(pb, pa, pl, cb, ca, cl)
        res = pd.DataFrame(out, index=df.index)
        # First row has no previous snapshot -> empty classification.
        if n > 0:
            for col in ("label", "family", "state5", "state8", "reason", "direction"):
                res.iloc[0, res.columns.get_loc(col)] = None
        return res

    def classify_arrays(self, pb, pa, pl, cb, ca, cl) -> dict[str, list]:
        """Vectorized classification of aligned numpy arrays (t-1 values in
        pb/pa/pl, t values in cb/ca/cl). Rows where pb is NaN are treated as
        'no previous' and get empty labels."""
        t = self.tick_size
        tol = _ABS_TOL

        def _valid(x):
            return np.isfinite(x) & (x > 0) & (x < PRICE_SENTINEL_THRESHOLD)

        pbv, pav, cbv, cav = _valid(pb), _valid(pa), _valid(cb), _valid(ca)
        plv, clv = _valid(pl), _valid(cl)

        quotes_ok = (pbv & pav & cbv & cav
                     & (pa > pb + tol) & (ca > cb + tol))

        db = np.where(quotes_ok, (cb - pb) / t, np.nan)
        da = np.where(quotes_ok, (ca - pa) / t, np.nan)
        dl = np.where(plv & clv, (cl - pl) / t, np.nan)
        with np.errstate(over="ignore", invalid="ignore"):
            dm = np.where(quotes_ok, ((cb + ca) / 2.0 - (pb + pa) / 2.0) / t,
                          np.nan)
        sp = np.where(quotes_ok, (pa - pb) / t, np.nan)
        sc = np.where(quotes_ok, (ca - cb) / t, np.nan)
        no_bool = np.zeros(quotes_ok.shape, dtype=bool)

        # tick-grid check (invalid/absent prices do not constrain the grid,
        # matching the pairwise reference implementation)
        def _on_grid(x, v):
            g = np.abs(x / t - np.round(x / t)) <= tol
            return (~v) | g

        grid = (_on_grid(pb, pbv) & _on_grid(pa, pav) & _on_grid(cb, cbv)
                & _on_grid(ca, cav) & _on_grid(pl, plv) & _on_grid(cl, clv))

        db0 = np.abs(db) <= tol
        da0 = np.abs(da) <= tol
        dl0 = ~np.isfinite(dl) | (np.abs(dl) <= tol)
        dl_pos_unit = np.isfinite(dl) & (np.abs(dl - 1.0) <= tol)
        dl_neg_unit = np.isfinite(dl) & (np.abs(dl + 1.0) <= tol)
        dl_unit = dl_pos_unit | dl_neg_unit

        same_dir = ((db > tol) & (da > tol)) | ((db < -tol) & (da < -tol))
        sym = quotes_ok & ~db0 & ~da0 & (np.abs(db - da) <= tol)
        if self.strict_symmetric_quote_move:
            loose_genuine = no_bool
        else:
            loose_genuine = quotes_ok & grid & ~db0 & ~da0 & same_dir

        prev_in = (pl >= pb - tol) & (pl <= pa + tol)
        cur_in = (cl >= cb - tol) & (cl <= ca + tol)
        toggle_up = (np.abs(pl - pb) <= tol) & (np.abs(cl - ca) <= tol)
        toggle_down = (np.abs(pl - pa) <= tol) & (np.abs(cl - cb) <= tol)

        # ---------------- label assembly (priority = np.select order) ----
        invalid = ~quotes_ok
        offgrid = quotes_ok & ~grid
        no_move = (quotes_ok & grid & db0 & da0 & dl0)
        genuine_sym = quotes_ok & grid & sym
        genuine_loose = loose_genuine & ~sym
        quotes_unchanged = quotes_ok & grid & db0 & da0 & ~no_move
        high_up = quotes_unchanged & plv & clv & toggle_up
        high_down = quotes_unchanged & plv & clv & toggle_down & ~toggle_up
        last_valid_qunch = quotes_unchanged & plv & clv & ~toggle_up & ~toggle_down
        likely_in_quote = (last_valid_qunch & prev_in & cur_in
                           & (dl_unit | (not self.likely_bounce_requires_unit_move)))
        inside_non_unit = (last_valid_qunch & prev_in & cur_in
                           & ~dl_unit & self.likely_bounce_requires_unit_move)
        outside_unchanged = last_valid_qunch & ~(prev_in & cur_in)
        qunch_last_invalid = quotes_unchanged & ~(plv & clv)

        # asymmetric / one-sided quote change: quote moved but not classified
        # above (covers db!=0 xor da!=0, and different-magnitude moves)
        asym = quotes_ok & grid & (~db0 | ~da0) & ~sym & ~loose_genuine
        likely_reshaped = (asym & plv & clv & dl_unit
                           & (np.abs(dm) <= self.mid_tolerance_ticks + tol)
                           & ~same_dir & prev_in & cur_in)
        mid_move_mask = asym & (np.abs(dm) >= 1.0 - tol) & ~likely_reshaped
        if self.strict_symmetric_quote_move:
            genuine_mid = no_bool
            mid_move = mid_move_mask
        else:
            genuine_mid = mid_move_mask
            mid_move = no_bool
        last_outside = (asym & ~likely_reshaped & ~mid_move_mask
                        & plv & clv & ~cur_in)
        complex_change = (asym & ~likely_reshaped & ~mid_move_mask & ~last_outside)

        label = np.select(
            [no_move, genuine_sym | genuine_loose, high_up, high_down,
             likely_in_quote | likely_reshaped, genuine_mid, mid_move,
             inside_non_unit, outside_unchanged, last_outside, invalid, offgrid,
             qunch_last_invalid, complex_change],
            [EventLabel.NO_MOVE.value,
             EventLabel.GENUINE_QUOTE_MOVE_UP.value,  # placeholder, fixed below
             EventLabel.HIGH_CONFIDENCE_BOUNCE_UP.value,
             EventLabel.HIGH_CONFIDENCE_BOUNCE_DOWN.value,
             "", "", EventLabel.AMBIGUOUS.value, EventLabel.AMBIGUOUS.value,
             EventLabel.AMBIGUOUS.value, EventLabel.AMBIGUOUS.value,
             EventLabel.AMBIGUOUS.value, EventLabel.AMBIGUOUS.value,
             EventLabel.NO_MOVE.value, EventLabel.AMBIGUOUS.value],
            default=EventLabel.AMBIGUOUS.value,
        )

        # Signed variants: genuine up/down, likely up/down, genuine-mid up/down
        genuine_all = genuine_sym | genuine_loose
        label = np.where(
            genuine_all & (np.nan_to_num(db, nan=0.0) > 0),
            EventLabel.GENUINE_QUOTE_MOVE_UP.value, label)
        label = np.where(
            genuine_all & (np.nan_to_num(db, nan=0.0) < 0),
            EventLabel.GENUINE_QUOTE_MOVE_DOWN.value, label)
        if not self.strict_symmetric_quote_move:
            label = np.where(
                genuine_mid & (np.nan_to_num(dm, nan=0.0) > 0),
                EventLabel.GENUINE_QUOTE_MOVE_UP.value, label)
            label = np.where(
                genuine_mid & (np.nan_to_num(dm, nan=0.0) < 0),
                EventLabel.GENUINE_QUOTE_MOVE_DOWN.value, label)

        likely_all = likely_in_quote | likely_reshaped
        dl_sign = np.nan_to_num(dl, nan=0.0)
        label = np.where(
            likely_all & (dl_sign > 0), EventLabel.LIKELY_BOUNCE_UP.value, label)
        label = np.where(
            likely_all & (dl_sign < 0), EventLabel.LIKELY_BOUNCE_DOWN.value, label)

        direction_hint = np.select(
            [mid_move, (last_outside & ~genuine_mid) | outside_unchanged
             | inside_non_unit],
            [np.where(dm > 0, 1, -1),
             np.where(np.nan_to_num(dl, nan=0.0) > 0, 1, -1)],
            default=0,
        )

        reason = np.select(
            [invalid, offgrid, inside_non_unit, outside_unchanged,
             mid_move & same_dir, mid_move & ~same_dir, last_outside,
             complex_change],
            [AmbiguousReason.QUOTE_INVALID.value,
             AmbiguousReason.NON_TICK_ALIGNED.value,
             AmbiguousReason.INSIDE_SPREAD_NON_UNIT_MOVE.value,
             AmbiguousReason.LAST_OUTSIDE_UNCHANGED_QUOTE.value,
             AmbiguousReason.ASYMMETRIC_QUOTE_MOVE.value,
             AmbiguousReason.MID_MOVE_WITH_COMPLEX_QUOTE.value,
             AmbiguousReason.LAST_OUTSIDE_QUOTE.value,
             AmbiguousReason.COMPLEX_QUOTE_CHANGE.value],
            default="",
        )
        meta_map = {lab.value: (fam.value, s5.value if s5 else None, d)
                    for lab, (fam, s5, d) in LABEL_META.items()}
        family = [meta_map[l][0] if l else None for l in label]
        state5 = [meta_map[l][1] if l else None for l in label]
        direction = [meta_map[l][2] if l else None for l in label]
        state8 = [_state8_of(EventLabel(l)) if l else None for l in label]

        return {
            "label": list(label),
            "family": family,
            "state5": state5,
            "state8": state8,
            "direction": direction,
            "reason": [r or None for r in reason],
            "direction_hint": list(direction_hint),
            "db_ticks": db,
            "da_ticks": da,
            "dl_ticks": dl,
            "dm_ticks": dm,
            "spread_prev_ticks": sp,
            "spread_cur_ticks": sc,
        }


def _shift(arr: np.ndarray) -> np.ndarray:
    """[a,b,c] -> [nan,a,b]: t-1 values aligned to t rows."""
    out = np.full_like(arr, np.nan, dtype=float)
    if len(arr) > 1:
        out[1:] = arr[:-1]
    return out


__all__ = [
    "JumpEventClassifier",
    "ClassificationResult",
    "is_valid_price",
    "CTP_DOUBLE_SENTINEL",
]
