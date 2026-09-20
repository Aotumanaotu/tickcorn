"""Deterministic simulated market feed (dev / CI / demo; no CTP SDK).

Produces canonical 51-column snapshot rows with a fixed random seed so
runs are reproducible. Per-step behaviour (70/20/10):

    * 70%  LastPrice toggles bid <-> ask at an unchanged quote (bounce),
    * 20%  bid & ask both shift one tick in the same direction (genuine
           quote move),
    * 10%  one book side's volume changes (quote unchanged, session
           volume / turnover tick up).

Prices are kept as integer tick counts to avoid floating-point drift.
"""

from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from app.common.constants import CTP_DOUBLE_SENTINEL

_BEIJING = ZoneInfo("Asia/Shanghai")
_BASE_MID = 2300.0
_EMITTER = Callable[[dict], Awaitable[None]]


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


class _InstState:
    """Per-instrument simulated book (prices as tick counts from zero)."""

    __slots__ = ("mid_ticks", "last_ticks", "bid_volume", "ask_volume",
                 "volume", "turnover", "open_interest")

    def __init__(self, mid_ticks: int, last_ticks: int, rng: random.Random):
        self.mid_ticks = mid_ticks
        self.last_ticks = last_ticks
        self.bid_volume = rng.randint(10, 40)
        self.ask_volume = rng.randint(10, 40)
        self.volume = 1000
        self.turnover = _BASE_MID * self.volume
        self.open_interest = 200_000.0


class SimulatedFeed:
    """Seeded, cancellable snapshot generator for one or more instruments."""

    def __init__(self, instruments: list[str], tick_sizes: dict[str, float],
                 *, seed: int = 7, rate_hz: float = 2.0,
                 action_day: str | None = None):
        self._rng = random.Random(seed)
        self._rate_hz = max(0.1, float(rate_hz))
        self._action_day = action_day
        self._tick_sizes: dict[str, float] = {}
        self._states: dict[str, _InstState] = {}
        self._order: list[str] = []
        for inst in instruments:
            tick = tick_sizes.get(str(inst).upper())
            if tick is None:
                raise ValueError(f"缺少合约 {inst} 的 tick_size 配置")
            self.add_instrument(str(inst), float(tick))

    # ------------------------------------------------------------------
    def add_instrument(self, instrument_id: str, tick_size: float) -> None:
        inst = str(instrument_id).upper()
        if inst in self._states:
            return
        tick = float(tick_size)
        if tick <= 0:
            raise ValueError(f"合约 {inst} 的 tick_size 必须为正数")
        mid_ticks = int(round(_BASE_MID / tick))
        last_ticks = mid_ticks + (1 if self._rng.random() < 0.5 else -1)
        self._tick_sizes[inst] = tick
        self._states[inst] = _InstState(mid_ticks, last_ticks, self._rng)
        self._order.append(inst)

    def remove_instrument(self, instrument_id: str) -> None:
        inst = str(instrument_id).upper()
        self._states.pop(inst, None)
        self._tick_sizes.pop(inst, None)
        if inst in self._order:
            self._order.remove(inst)

    @property
    def instruments(self) -> list[str]:
        return list(self._order)

    # ------------------------------------------------------------------
    def _step(self, inst: str) -> _InstState:
        st = self._states[inst]
        rng = self._rng
        roll = rng.random()
        traded = 0
        if roll < 0.70:
            st.last_ticks = (st.mid_ticks + 1 if st.last_ticks < st.mid_ticks
                             else st.mid_ticks - 1)
            traded = rng.randint(1, 10)
        elif roll < 0.90:
            direction = 1 if rng.random() < 0.5 else -1
            st.mid_ticks += direction
            st.last_ticks = st.mid_ticks
            traded = rng.randint(1, 10)
        else:
            if rng.random() < 0.5:
                st.bid_volume = _clamp(
                    st.bid_volume + rng.choice((-1, 1)) * rng.randint(1, 10), 1, 50)
            else:
                st.ask_volume = _clamp(
                    st.ask_volume + rng.choice((-1, 1)) * rng.randint(1, 10), 1, 50)
            traded = rng.randint(0, 3)
        st.bid_volume = _clamp(st.bid_volume + rng.randint(-3, 3), 1, 50)
        st.ask_volume = _clamp(st.ask_volume + rng.randint(-3, 3), 1, 50)
        st.volume += traded
        st.turnover += st.last_ticks * self._tick_sizes[inst] * traded
        st.open_interest = max(1.0, st.open_interest + rng.randint(-20, 20))
        return st

    def _build_row(self, inst: str, st: _InstState) -> dict:
        tick = self._tick_sizes[inst]
        now = datetime.now(_BEIJING)
        day = self._action_day or now.strftime("%Y-%m-%d")
        row: dict = {
            "trading_day": day,
            "action_day": day,
            "instrument_id": inst,
            "exchange_id": "",
            "exchange_inst_id": inst,
            "update_time": now.strftime("%H:%M:%S"),
            "update_millisec": now.microsecond // 1000,
            "last_price": st.last_ticks * tick,
            "pre_settlement_price": CTP_DOUBLE_SENTINEL,
            "pre_close_price": CTP_DOUBLE_SENTINEL,
            "pre_open_interest": CTP_DOUBLE_SENTINEL,
            "open_price": CTP_DOUBLE_SENTINEL,
            "highest_price": CTP_DOUBLE_SENTINEL,
            "lowest_price": CTP_DOUBLE_SENTINEL,
            "volume": st.volume,
            "turnover": st.turnover,
            "open_interest": st.open_interest,
            "close_price": CTP_DOUBLE_SENTINEL,
            "settlement_price": CTP_DOUBLE_SENTINEL,
            "upper_limit_price": CTP_DOUBLE_SENTINEL,
            "lower_limit_price": CTP_DOUBLE_SENTINEL,
            "pre_delta": CTP_DOUBLE_SENTINEL,
            "curr_delta": CTP_DOUBLE_SENTINEL,
            "average_price": st.turnover / max(st.volume, 1),
            "banding_upper_price": CTP_DOUBLE_SENTINEL,
            "banding_lower_price": CTP_DOUBLE_SENTINEL,
            "bid_price1": (st.mid_ticks - 1) * tick,
            "bid_volume1": st.bid_volume,
            "ask_price1": (st.mid_ticks + 1) * tick,
            "ask_volume1": st.ask_volume,
            "local_receive_time_ns": time.time_ns(),
            "raw_source": "simulate",
        }
        for lvl in range(2, 6):
            row[f"bid_price{lvl}"] = CTP_DOUBLE_SENTINEL
            row[f"bid_volume{lvl}"] = 0
            row[f"ask_price{lvl}"] = CTP_DOUBLE_SENTINEL
            row[f"ask_volume{lvl}"] = 0
        return row

    # ------------------------------------------------------------------
    async def run(self, emit: _EMITTER) -> None:
        """Emit one canonical row every 1/rate_hz seconds (round-robin
        across instruments) until cancelled."""
        interval = 1.0 / self._rate_hz
        index = 0
        while True:
            if self._order:
                inst = self._order[index % len(self._order)]
                index += 1
                row = self._build_row(inst, self._step(inst))
                await emit(row)
            await asyncio.sleep(interval)
