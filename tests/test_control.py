"""Web control panel tests (settings store, manager lifecycle, API, auth)."""

from __future__ import annotations

import json
import stat
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from app.common.config import load_config
from app.collector.live import LiveState
from app.dashboard.control import CollectorManager, SettingsStore
from app.dashboard.server import DashboardServer
from conftest import ROOT


@pytest.fixture()
def env(tmp_path: Path):
    config = load_config(config_dir=ROOT / "config", data_dir_override=tmp_path)
    manager = CollectorManager(config)
    return config, manager, tmp_path


def test_settings_store_permissions_and_persistence(env):
    config, manager, tmp_path = env
    store = SettingsStore(tmp_path)
    s = store.load()
    s.user = "13800000000"
    s.password = "secret"
    s.remember = False
    store.save(s)
    # password must NOT be persisted when remember=False
    raw = json.loads(store.path.read_text())
    assert raw["password"] == ""
    mode = stat.S_IMODE(store.path.stat().st_mode)
    assert mode == 0o600

    s.remember = True
    store.save(s)
    raw = json.loads(store.path.read_text())
    assert raw["password"] == "secret"
    loaded = store.load()
    assert loaded.password == "secret"

    # masked() never exposes the password
    assert "secret" not in json.dumps(loaded.masked())


def test_manager_update_settings(env):
    config, manager, tmp_path = env
    out = manager.update_settings({
        "instruments": "c2701, c2705",
        "user": "13800000000",
        "password": "pw123",
        "fronts": "tcp://1.2.3.4:1234\ntcp://5.6.7.8:5678",
        "remember": False,
    })
    assert out["instruments"] == ["C2701", "C2705"]
    assert out["password"] == "********"
    assert manager.settings.fronts == ["tcp://1.2.3.4:1234",
                                       "tcp://5.6.7.8:5678"]
    # password kept when a later update omits it
    manager.update_settings({"instruments": "C2709"})
    assert manager.settings.password == "pw123"
    assert manager.settings.user == "13800000000"


def test_manager_start_requires_credentials(env):
    config, manager, tmp_path = env
    manager.settings.user = ""
    manager.settings.password = ""
    manager.settings.fronts = ["tcp://1.2.3.4:1"]
    with pytest.raises(Exception):
        manager.start()


def test_manager_start_stop_lifecycle(env):
    """Start against an immediately-refused front; service must run in a
    thread and stop cleanly (full CTP lifecycle exercised)."""
    config, manager, tmp_path = env
    manager.update_settings({
        "instruments": "C2701",
        "user": "13800000000",
        "password": "pw123",
        "fronts": "tcp://127.0.0.1:1",   # connection refused instantly
    })
    status = manager.start()
    assert status["running"] is True
    assert status["batch_id"].startswith("batch-")
    time.sleep(1.0)
    assert manager.status()["running"] is True

    status = manager.stop()
    assert status["running"] is False
    # a second start works after stop (thread exited)
    manager.start()
    manager.stop()


def _serve(config, manager, token):
    state = LiveState()
    server = DashboardServer(state, host="127.0.0.1", port=0,
                             manager=manager, auth_token=token or None)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.3)
    return server, server.port


def _req(url, method="GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, json.loads(resp.read().decode())


def test_control_api_endpoints(env):
    config, manager, tmp_path = env
    server, port = _serve(config, manager, token=None)
    try:
        # settings roundtrip
        code, s = _req(f"http://127.0.0.1:{port}/api/settings")
        assert code == 200 and "yaml_fronts" in s
        code, s = _req(f"http://127.0.0.1:{port}/api/settings", "POST", {
            "instruments": "C2701", "user": "13800000000",
            "password": "pw", "fronts": "tcp://127.0.0.1:1"})
        assert s["password"] == "********"

        # start + state + stop
        code, st = _req(f"http://127.0.0.1:{port}/api/collect/start", "POST", {})
        assert st["running"] is True
        code, payload = _req(f"http://127.0.0.1:{port}/api/state")
        assert payload["manager"]["running"] is True
        code, st = _req(f"http://127.0.0.1:{port}/api/collect/stop", "POST", {})
        assert st["running"] is False
    finally:
        server.shutdown()


def test_control_api_token_auth(env):
    config, manager, tmp_path = env
    server, port = _serve(config, manager, token="s3cret")
    try:
        # no token -> 401
        with pytest.raises(urllib.error.HTTPError) as e:
            _req(f"http://127.0.0.1:{port}/api/state")
        assert e.value.code == 401
        # header token -> ok
        code, _ = _req(f"http://127.0.0.1:{port}/api/state",
                       headers={"X-Auth-Token": "s3cret"})
        assert code == 200
        # query param -> ok
        code, _ = _req(f"http://127.0.0.1:{port}/api/state?token=s3cret")
        assert code == 200
        # index page also protected
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/")
        assert e.value.code == 401
    finally:
        server.shutdown()
