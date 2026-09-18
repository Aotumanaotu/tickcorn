"""Corn futures 1-tick microstructure research system (Phase 1).

Terminology (IMPORTANT for the associated paper):
    * CTP 行情是快照型行情（Market Snapshot），不是逐笔订单流
      (NOT true tick-by-tick order flow).
    * We observe "Snapshot Events" and "Quote Updates" only; orders,
      cancels and trades between two snapshots are NOT observable.
    * Bid-ask bounce inferred from snapshots is therefore graded by
      confidence: HIGH_CONFIDENCE_BOUNCE / LIKELY_BOUNCE, as opposed to
      GENUINE_QUOTE_MOVE and AMBIGUOUS.
"""

__version__ = "0.1.0"

APP_NAME = "corn-tick-microstructure"
