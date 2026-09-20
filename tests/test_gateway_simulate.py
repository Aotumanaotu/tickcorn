"""End-to-end gateway tests against the simulated feed.

No CTP SDK and no PostgreSQL required: the gateway runs in-process in
simulate mode, an IngestClient connects over the unix socket, and the
whole chain feed -> normalizer -> publisher -> archiver (parquet) is
exercised.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from app.common.config import load_config
from app.common.runtime import WebSettings
from app.core.events import EVENT_MICRO, EVENT_QUOTE
from app.gateway.process import run_gateway
from app.ingest.bus import EventBus
from app.ingest.client import GatewayUnavailableError, IngestClient

ROOT = Path(__file__).resolve().parents[1]

LABELS8 = {
    "NO_MOVE",
    "HIGH_CONFIDENCE_BOUNCE_UP",
    "HIGH_CONFIDENCE_BOUNCE_DOWN",
    "LIKELY_BOUNCE_UP",
    "LIKELY_BOUNCE_DOWN",
    "GENUINE_QUOTE_MOVE_UP",
    "GENUINE_QUOTE_MOVE_DOWN",
    "AMBIGUOUS",
}


@pytest.fixture()
async def gateway_env(tmp_path: Path):
    config = load_config(config_dir=ROOT / "config", data_dir_override=tmp_path)
    settings = WebSettings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/app.db",
        jwt_secret="test-secret",
        gateway_socket=tmp_path / "gw.sock",
    )
    try:
        from sqlalchemy.ext.asyncio import create_async_engine

        from app.storage.timescale import init_database

        engine = create_async_engine(settings.database_url)
        try:
            await init_database(engine, config)
        finally:
            await engine.dispose()
    except Exception:  # noqa: BLE001 - PG bookkeeping is optional here
        pass
    return config, settings


async def _wait_for(predicate, timeout_s: float, message: str) -> None:
    deadline = time.monotonic() + timeout_s
    while not predicate():
        if time.monotonic() > deadline:
            pytest.fail(message)
        await asyncio.sleep(0.05)


async def _stop_gateway(client: IngestClient, task: asyncio.Task) -> None:
    try:
        if client.connected:
            await asyncio.wait_for(client.send_control("shutdown", {}),
                                   timeout=5.0)
    except Exception:  # noqa: BLE001
        pass
    await client.stop()
    if not task.done():
        try:
            await asyncio.wait_for(task, timeout=20.0)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


async def _start_gateway(config, settings, instruments):
    task = asyncio.create_task(run_gateway(
        config, settings, simulate=True, simulate_instruments=instruments))
    await _wait_for(lambda: Path(settings.gateway_socket).exists(), 15.0,
                    f"gateway socket not created at {settings.gateway_socket}")
    return task


async def _start_client(settings) -> tuple[EventBus, IngestClient]:
    bus = EventBus()
    client = IngestClient(settings, bus)
    await client.start()
    await _wait_for(lambda: client.connected, 15.0,
                    "ingest client failed to connect to gateway")
    return bus, client


# ----------------------------------------------------------------------
async def test_simulate_stream_end_to_end(gateway_env, tmp_path):
    config, settings = gateway_env
    task = await _start_gateway(config, settings, ["C2611"])
    bus, client = await _start_client(settings)
    quotes: list[dict] = []
    micros: list[dict] = []

    async def collect(event: dict) -> None:
        if event.get("type") == EVENT_QUOTE:
            quotes.append(event)
        elif event.get("type") == EVENT_MICRO:
            micros.append(event)

    unsubscribe = await bus.subscribe(collect, queued=False)
    try:
        await _wait_for(
            lambda: len(quotes) >= 5 and len(micros) >= 1, 15.0,
            f"insufficient events: quotes={len(quotes)} micros={len(micros)}")

        quote = quotes[0]
        assert quote["instrument_id"] == "C2611"
        assert quote["seq"] >= 1
        assert quote["source"] == "simulate"
        data = quote["data"]
        assert data["bid1"] is not None and data["ask1"] is not None
        assert data["ask1"] > data["bid1"]
        assert data["spread"] == pytest.approx(data["ask1"] - data["bid1"])
        assert data["mid"] == pytest.approx(
            (data["bid1"] + data["ask1"]) / 2)
        assert data["obi1"] is not None and -1.0 <= data["obi1"] <= 1.0
        assert data["volume"] > 0 and data["last"] is not None

        micro = micros[0]
        assert micro["instrument_id"] == "C2611"
        assert micro["data"]["label"] in LABELS8
        assert micro["data"]["prev_quote"]["bid"] is not None
        assert micro["data"]["cur_quote"]["ask"] is not None

        await client.send_control("shutdown", {})
        rc = await asyncio.wait_for(task, timeout=20.0)
        assert rc == 0
    finally:
        await _stop_gateway(client, task)
        unsubscribe()

    raw_dir = tmp_path / "raw"
    parquet_files = list(raw_dir.rglob("*.parquet"))
    assert parquet_files, "no parquet output under data/raw/"
    finals = [p for p in parquet_files if p.name.startswith("snapshots")]
    assert finals, "finalize produced no snapshots parquet"
    rows = sum(pq.ParquetFile(p).metadata.num_rows for p in finals)
    assert rows >= 5


# ----------------------------------------------------------------------
async def test_status_frame_reports_simulate(gateway_env):
    config, settings = gateway_env
    task = await _start_gateway(config, settings, ["C2611"])
    bus, client = await _start_client(settings)
    try:
        await _wait_for(lambda: client.gateway_status is not None, 15.0,
                        "no status frame received")
        status = client.gateway_status
        assert status["state"] in {"connected", "streaming"}
        assert status["simulate"] is True
        assert status["instruments"].get("C2611") == "streaming"
        assert status["batch_id"]
        assert status["started_at"]
        assert status["events_published"] >= 0
    finally:
        await _stop_gateway(client, task)


# ----------------------------------------------------------------------
async def test_connect_rejected_in_simulate_mode(gateway_env):
    config, settings = gateway_env
    task = await _start_gateway(config, settings, ["C2611"])
    bus, client = await _start_client(settings)
    try:
        payload = {
            "fronts": ["tcp://127.0.0.1:12345"],
            "broker_id": "9999",
            "user": "simuser",
            "password": "secret",
            "instruments": ["C2611"],
        }
        with pytest.raises(GatewayUnavailableError) as excinfo:
            await client.send_control("connect", payload)
        assert "simulate" in str(excinfo.value)
        assert client.connected
    finally:
        await _stop_gateway(client, task)
