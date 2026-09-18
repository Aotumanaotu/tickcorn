"""Canonical snapshot schema (raw parquet) and helpers.

The raw layer stores the FULL CTP DepthMarketData content (all fields the
API version provides), plus our own bookkeeping columns. Values are stored
EXACTLY as delivered by the exchange API -- including CTP sentinel values
(DBL_MAX == "no price"), which are only converted to NaN in the processed
layer. This keeps raw data faithful and immutable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import pyarrow as pa

# ---------------------------------------------------------------------
# Bookkeeping columns added by the collector
# ---------------------------------------------------------------------
BOOKKEEPING_COLUMNS: Final[list[tuple[str, pa.DataType]]] = [
    ("sequence_id", pa.int64()),            # per (batch, instrument) monotonic
    ("local_receive_time_ns", pa.int64()),  # time.time_ns() on receipt
    ("batch_id", pa.string()),              # collection batch (uuid)
    ("trading_session", pa.string()),       # NIGHT / DAY / OUT_OF_SESSION
    ("raw_source", pa.string()),            # e.g. "ctp:simnow"
]

# ---------------------------------------------------------------------
# CTP DepthMarketData columns (v6.7.13, snake_case renames)
# Double values are kept verbatim (sentinels included).
# ---------------------------------------------------------------------
CTP_COLUMNS: Final[list[tuple[str, pa.DataType]]] = [
    ("trading_day", pa.string()),
    ("action_day", pa.string()),
    ("instrument_id", pa.string()),
    ("exchange_id", pa.string()),
    ("exchange_inst_id", pa.string()),
    ("update_time", pa.string()),
    ("update_millisec", pa.int32()),
    ("last_price", pa.float64()),
    ("pre_settlement_price", pa.float64()),
    ("pre_close_price", pa.float64()),
    ("pre_open_interest", pa.float64()),
    ("open_price", pa.float64()),
    ("highest_price", pa.float64()),
    ("lowest_price", pa.float64()),
    ("volume", pa.int32()),
    ("turnover", pa.float64()),
    ("open_interest", pa.float64()),
    ("close_price", pa.float64()),
    ("settlement_price", pa.float64()),
    ("upper_limit_price", pa.float64()),
    ("lower_limit_price", pa.float64()),
    ("pre_delta", pa.float64()),
    ("curr_delta", pa.float64()),
    ("average_price", pa.float64()),
    ("banding_upper_price", pa.float64()),
    ("banding_lower_price", pa.float64()),
]
for _lvl in range(1, 6):
    CTP_COLUMNS.append((f"bid_price{_lvl}", pa.float64()))
    CTP_COLUMNS.append((f"bid_volume{_lvl}", pa.int32()))
    CTP_COLUMNS.append((f"ask_price{_lvl}", pa.float64()))
    CTP_COLUMNS.append((f"ask_volume{_lvl}", pa.int32()))

RAW_SCHEMA: Final[pa.Schema] = pa.schema(
    [pa.field(n, t, nullable=True) for n, t in BOOKKEEPING_COLUMNS + CTP_COLUMNS]
)

RAW_COLUMN_NAMES: Final[list[str]] = [n for n, _ in BOOKKEEPING_COLUMNS + CTP_COLUMNS]

# CTP struct field -> canonical column (used by the normalizer).
CTP_FIELD_TO_COLUMN: Final[dict[str, str]] = {
    "TradingDay": "trading_day",
    "ActionDay": "action_day",
    "InstrumentID": "instrument_id",
    "ExchangeID": "exchange_id",
    "ExchangeInstID": "exchange_inst_id",
    "UpdateTime": "update_time",
    "UpdateMillisec": "update_millisec",
    "LastPrice": "last_price",
    "PreSettlementPrice": "pre_settlement_price",
    "PreClosePrice": "pre_close_price",
    "PreOpenInterest": "pre_open_interest",
    "OpenPrice": "open_price",
    "HighestPrice": "highest_price",
    "LowestPrice": "lowest_price",
    "Volume": "volume",
    "Turnover": "turnover",
    "OpenInterest": "open_interest",
    "ClosePrice": "close_price",
    "SettlementPrice": "settlement_price",
    "UpperLimitPrice": "upper_limit_price",
    "LowerLimitPrice": "lower_limit_price",
    "PreDelta": "pre_delta",
    "CurrDelta": "curr_delta",
    "AveragePrice": "average_price",
    "BandingUpperPrice": "banding_upper_price",
    "BandingLowerPrice": "banding_lower_price",
}
for _lvl in range(1, 6):
    CTP_FIELD_TO_COLUMN[f"BidPrice{_lvl}"] = f"bid_price{_lvl}"
    CTP_FIELD_TO_COLUMN[f"BidVolume{_lvl}"] = f"bid_volume{_lvl}"
    CTP_FIELD_TO_COLUMN[f"AskPrice{_lvl}"] = f"ask_price{_lvl}"
    CTP_FIELD_TO_COLUMN[f"AskVolume{_lvl}"] = f"ask_volume{_lvl}"


@dataclass(frozen=True)
class PartitionKey:
    """Raw data partition: data/raw/instrument=<id>/trading_day=<YYYY-MM-DD>/"""

    instrument_id: str
    trading_day: str

    def dir(self, raw_root) -> "object":
        from pathlib import Path
        return (Path(raw_root) / f"instrument={self.instrument_id}"
                / f"trading_day={self.trading_day}")

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.instrument_id}/{self.trading_day}"
