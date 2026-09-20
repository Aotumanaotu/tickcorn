"""Cloud regressions: no real credentials or external market connections."""

import dataclasses
import json
import os
import signal
import socket
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.request

import pandas as pd
import pytest

from app.common.config import load_config
from app.common.private_files import dashboard_token, ProcessLock
from app.legacy.collector.service import CollectorService
from app.legacy.dashboard.control import CollectorManager
from app.core.features.derived import add_ts_and_minute
from app.core.statistics.event_stats import _count_block
from app.core.statistics.transition import next_genuine_direction, obi_conditional_table
from conftest import ROOT, build_synthetic_day


def test_no_deployment_values_in_defaults():
    cfg = load_config(ROOT / "config")
    assert cfg.ctp.fronts == ()
    assert cfg.ctp.broker_id == ""


def test_atomic_settings_and_forgetting(tmp_path):
    manager = CollectorManager(load_config(ROOT / "config", tmp_path))
    assert not manager.settings.instruments
    manager.update_settings({"user": "test-user", "password": "test-password", "remember": True})
    old = manager.settings_store.path.read_bytes()
    with pytest.raises(Exception):
        manager.update_settings({"user": "replacement", "fronts": "https://invalid.example"})
    assert manager.settings_store.path.read_bytes() == old
    assert manager.settings.user == "test-user"
    manager.update_settings({"remember": False})
    text = manager.settings_store.path.read_text()
    assert "test-user" not in text and "test-password" not in text
    assert not manager.settings_store.load().user
    assert not manager.settings_store.load().password
    assert stat.S_IMODE(manager.settings_store.path.stat().st_mode) == 0o600
    manager.update_settings({"fronts": "", "instruments": ""})
    assert manager.settings.fronts == manager.settings.instruments == []


def test_token_persists_and_data_lock(tmp_path):
    token = dashboard_token(tmp_path)
    assert len(token) >= 32 and dashboard_token(tmp_path) == token
    assert stat.S_IMODE((tmp_path / "dashboard-token.local.json").stat().st_mode) == 0o600
    first = ProcessLock(tmp_path / "collector.lock")
    try:
        with pytest.raises(RuntimeError):
            ProcessLock(tmp_path / "collector.lock")
    finally:
        first.close()
    ProcessLock(tmp_path / "collector.lock").close()


def test_exchange_clock_and_unit_denominator():
    df = build_synthetic_day(n_base=100)
    df = add_ts_and_minute(df)
    assert df.minute_of_day.iloc[0] == 9 * 60 + 30
    block = pd.DataFrame({
        "label": ["HIGH_CONFIDENCE_BOUNCE_UP", "GENUINE_QUOTE_MOVE_UP", "AMBIGUOUS"],
        "family": ["HIGH_CONFIDENCE_BOUNCE", "GENUINE_QUOTE_MOVE", "AMBIGUOUS"],
        "dl_ticks": [1, 2, 1], "db_ticks": [0, 2, 0], "da_ticks": [0, 2, 1],
    })
    result = _count_block(block)
    assert result["bounce_ratio"] == 0.5
    assert result["genuine_move_ratio"] == 0
    assert result["ambiguous_ratio"] == 0.5


def test_no_future_labels_across_batches():
    df = pd.DataFrame({"segment_id": [1, 1, 2, 2], "obi1": [0.5] * 4,
                       "label": ["NO_MOVE", "NO_MOVE", "GENUINE_QUOTE_MOVE_UP", "NO_MOVE"]})
    assert next_genuine_direction(df, 2).tolist() == [0, 0, 0, 0]
    assert obi_conditional_table(df, [-1, 1], 2)["n"].sum() == 0


def test_writer_flush_rows_and_lowercase_quotes(tmp_path):
    from test_collector import _fields
    cfg = load_config(ROOT / "config", tmp_path)
    cfg = dataclasses.replace(cfg, collector=dataclasses.replace(cfg.collector, flush_rows=2, flush_interval_s=60))
    svc = CollectorService(cfg, ["C2701"], "test-user", "test-password")
    writer = threading.Thread(target=svc._writer_loop)
    writer.start()
    try:
        svc.on_depth_market_data(_fields(inst="c2701"))
        svc.on_depth_market_data(_fields(inst="c2701", last=2301))
        deadline = time.monotonic() + 5
        while not svc.store.list_staging_partitions() and time.monotonic() < deadline:
            time.sleep(0.02)
        keys = svc.store.list_staging_partitions()
        assert keys and keys[0].instrument_id == "C2701"
        assert svc.store.read_partition(keys[0]).num_rows == 2
    finally:
        svc._writer_done.set()
        writer.join(5)
        svc._close_live_feed()
    assert not writer.is_alive()


def test_sigterm_stops_web_collector(tmp_path):
    """Exercise the actual container command and CTP shutdown against localhost."""
    import sys
    from app.gateway.native.ctp_binding import SHIM_PATH
    if not SHIM_PATH.exists():
        pytest.skip("local CTP SDK not supplied")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("DASHBOARD_TOKEN", None)
    proc = subprocess.Popen([sys.executable, "-m", "app", "--data-dir", str(tmp_path),
                             "serve", "--port", str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1).close()
                break
            except OSError:
                if time.monotonic() > deadline or proc.poll() is not None:
                    pytest.fail("server failed to become ready")
                time.sleep(0.1)
        token = json.loads((tmp_path / "dashboard-token.local.json").read_text())["token"]
        for endpoint, payload in [("settings", {"instruments": "C2701", "broker_id": "test",
                "user": "test-user", "password": "test-password", "fronts": "tcp://127.0.0.1:1"}),
                ("collect/start", {})]:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/{endpoint}",
                    data=json.dumps(payload).encode(), headers={"X-Auth-Token": token,
                                                              "Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5).close()
        time.sleep(0.3)
        proc.send_signal(signal.SIGTERM)
        output = proc.communicate(timeout=20)[0].decode()
        assert proc.returncode == 0, output
        assert token not in output and "test-password" not in output and "test-user" not in output
        from app.storage.metadata_db import MetadataDB
        assert MetadataDB(tmp_path / "metadata.db").list_batches()[0]["status"] == "finished"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


def test_native_callback_to_parquet_and_clean_dates(tmp_path):
    """Exercise the actual evt=9 dispatcher, including real CTP date formats."""
    import ctypes
    from app.gateway.native.ctp_binding import CTPDepthMarketData, CtpMdClient
    from app.analysis.pipeline import load_clean
    from app.storage.repo import StorageRepository
    cfg = load_config(ROOT / "config", tmp_path)
    svc = CollectorService(cfg, ["C2611"], "test-user", "test-password", raw_source="synthetic")
    client = CtpMdClient.__new__(CtpMdClient)
    client.handler = svc
    depth = CTPDepthMarketData()
    depth.InstrumentID = b"c2611"
    depth.TradingDay = depth.ActionDay = b"20260918"
    depth.UpdateTime = b"09:30:00"
    depth.LastPrice = depth.BidPrice1 = 2300
    depth.AskPrice1 = 2301
    depth.BidVolume1 = depth.AskVolume1 = 10
    writer = threading.Thread(target=svc._writer_loop)
    writer.start()
    try:
        client._on_event(9, ctypes.byref(depth), None, 0, 0)
        depth.UpdateMillisec = 500
        depth.LastPrice = 2301
        client._on_event(9, ctypes.byref(depth), None, 0, 0)
    finally:
        svc._writer_done.set()
        writer.join(5)
        svc._close_live_feed()
    assert not writer.is_alive() and svc._writer_error is None
    assert svc.live.instrument("C2611").snapshot()["msg_count"] == 2
    svc._finalize_all()
    keys = svc.store.list_partitions()
    assert len(keys) == 1 and keys[0].trading_day == "2026-09-18"
    raw = svc.store.read_partition(keys[0]).to_pandas()
    assert raw["action_day"].tolist() == ["20260918", "20260918"]
    clean = load_clean(StorageRepository(cfg), "C2611", "2026-09-18").df
    assert len(clean) == 2
    assert clean["action_day"].tolist() == ["2026-09-18", "2026-09-18"]
    assert clean["minute_of_day"].tolist() == [570, 570]
