"""Market normalizer: CTP canonical rows -> standard market events.

This is the ONLY place (outside app.gateway.native) that understands the
51-column canonical raw schema. Business modules downstream consume the
envelopes from core.events.

The scalar derived-feature math here (spread / mid / obi1 / microprice)
intentionally mirrors app.core.features.derived (vectorized). The vectorized
version is the offline/reporting path; this one is the hot streaming path.
Semantics must stay identical: any change must be applied to both and
covered by tests/test_normalizer.py.

Sentinels: CTP uses DBL_MAX-style sentinels for missing prices. Raw parquet
keeps them verbatim (audit layer); this normalizer converts them to None so
business code never sees them.
"""

from __future__ import annotations

import math
from typing import Callable, Optional

from app.common.constants import PRICE_SENTINEL_THRESHOLD
from app.common.schema import CTP_FIELD_TO_COLUMN  # noqa: F401  (doc reference)
from app.common.timeutils import exchange_ts_ns, normalize_ctp_day
from app.core.classifier.jump_classifier import JumpEventClassifier
from app.core.events import (DATA_LIVE, EVENT_MICRO, EVENT_QUOTE,
                             envelope, micro_data, quote_data)

_LABEL_COLUMNS = None  # typing placeholder


def valid_price(p) -> bool:
    """True if p is a usable price (not None/NaN/inf/CTP sentinel)."""
    if p is None or isinstance(p, bool):
        return False
    try:
        pv = float(p)
    except (TypeError, ValueError):
        return False
    if math.isnan(pv) or math.isinf(pv):
        return False
    return 0.0 < pv < PRICE_SENTINEL_THRESHOLD


def _clean_price(p):
    return float(p) if valid_price(p) else None


def _f(x):
    """Volume/interest fields: int-ish or None."""
    return None if x is None else float(x)


def compute_derived(row: dict) -> dict:
    """Scalar derived values for one canonical row (sentinels -> None)."""
    last = _clean_price(row.get("last_price"))
    bid1 = _clean_price(row.get("bid_price1"))
    ask1 = _clean_price(row.get("ask_price1"))
    bv1 = _f(row.get("bid_volume1"))
    av1 = _f(row.get("ask_volume1"))
    spread = (ask1 - bid1) if (bid1 is not None and ask1 is not None) else None
    mid = (bid1 + ask1) / 2 if spread is not None else None
    obi1 = None
    if bv1 is not None and av1 is not None and (bv1 + av1) > 0:
        obi1 = (bv1 - av1) / (bv1 + av1)
    microprice = None
    if spread is not None and bv1 is not None and av1 is not None and (bv1 + av1) > 0:
        microprice = (ask1 * bv1 + bid1 * av1) / (bv1 + av1)
    return {
        "last": last, "bid1": bid1, "ask1": ask1,
        "bid2": _clean_price(row.get("bid_price2")),
        "ask2": _clean_price(row.get("ask_price2")),
        "bid3": _clean_price(row.get("bid_price3")),
        "ask3": _clean_price(row.get("ask_price3")),
        "bid4": _clean_price(row.get("bid_price4")),
        "ask4": _clean_price(row.get("ask_price4")),
        "bid5": _clean_price(row.get("bid_price5")),
        "ask5": _clean_price(row.get("ask_price5")),
        "spread": spread, "mid": mid, "obi1": obi1, "microprice": microprice,
    }


class MarketDataNormalizer:
    """Stateful, per-instrument pairwise normalizer + classifier.

    tick_size_resolver: callable instrument_id -> tick size (raises/returns
        None for unknown instruments; callers decide policy).
    classifier_kwargs: forwarded to JumpEventClassifier (strict mode etc.).
    """

    def __init__(self, tick_size_resolver: Callable[[str], Optional[float]],
                 **classifier_kwargs):
        self._tick_size_resolver = tick_size_resolver
        self._classifier_kwargs = classifier_kwargs
        self._classifiers: dict[str, JumpEventClassifier] = {}
        self._tick_sizes: dict[str, float] = {}
        self._prev: dict[str, Optional[tuple]] = {}

    def tick_size(self, instrument_id: str) -> Optional[float]:
        if instrument_id not in self._tick_sizes:
            try:
                ts = self._tick_size_resolver(instrument_id)
            except Exception:
                ts = None
            self._tick_sizes[instrument_id] = ts
        return self._tick_sizes[instrument_id]

    def reset(self, instrument_id: str | None = None) -> None:
        """Drop pairwise state (e.g. after a data gap or reconnect)."""
        if instrument_id is None:
            self._prev.clear()
        else:
            self._prev.pop(instrument_id, None)

    def normalize_row(self, row: dict, source: str = "",
                      data_mode: str = DATA_LIVE) -> list[dict]:
        """One canonical CTP row -> [quote event] + optional [micro event].

        The micro event is emitted only when a classification could be made
        (i.e. from the second row onwards for an instrument).
        """
        inst = row.get("instrument_id")
        derived = compute_derived(row)
        try:
            ts_ns = exchange_ts_ns(
                normalize_ctp_day(str(row.get("action_day") or "")),
                str(row.get("update_time") or ""),
                int(row.get("update_millisec") or 0))
        except (ValueError, TypeError):
            ts_ns = None
        base = dict(
            instrument_id=inst,
            seq=row.get("sequence_id"),
            exchange_ts_ns=ts_ns,
            local_ts_ns=row.get("local_receive_time_ns"),
            source=source or row.get("raw_source", ""),
            data_mode=data_mode,
        )
        events = [envelope(EVENT_QUOTE, data=quote_data(row, derived), **base)]

        bid1, ask1, last = derived["bid1"], derived["ask1"], derived["last"]
        prev = self._prev.get(inst)
        self._prev[inst] = (bid1, ask1, last)
        if prev is None or inst is None:
            return events
        pb, pa, pl = prev
        if None in (pb, pa, pl) or None in (bid1, ask1, last):
            return events

        tick = self.tick_size(inst)
        if tick is None:
            return events
        clf = self._classifiers.get(inst)
        if clf is None:
            clf = JumpEventClassifier(tick_size=tick, **self._classifier_kwargs)
            self._classifiers[inst] = clf
        result = clf.classify_pair(pb, pa, pl, bid1, ask1, last)
        data = micro_data(
            result,
            prev_quote={"bid": pb, "ask": pa, "last": pl},
            cur_quote={"bid": bid1, "ask": ask1, "last": last})
        events.append(envelope(EVENT_MICRO, data=data, **base))
        return events

    def classify_kwargs(self) -> dict:
        return dict(self._classifier_kwargs)
