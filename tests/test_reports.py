"""Web report isolation, background lifecycle and private download boundaries."""

import io
import json
import threading
import urllib.error
import urllib.request
import zipfile

import pandas as pd
import pyarrow as pa
import pytest

from app.common.config import load_config
from app.common.exceptions import AppError
from app.common.schema import RAW_SCHEMA, PartitionKey
from app.legacy.dashboard.control import CollectorManager, SettingsStore
from app.legacy.dashboard.environments import PRESETS
from app.legacy.dashboard.reports import ReportStore, briefing
from app.storage.parquet_store import RawParquetStore
from app.analysis.pipeline import load_clean
from app.storage.repo import StorageRepository
from conftest import ROOT, build_synthetic_day
from test_control import _req, _serve


@pytest.fixture
def report_env(tmp_path):
    config = load_config(config_dir=ROOT / "config", data_dir_override=tmp_path)
    manager = CollectorManager(config)
    store = RawParquetStore(config.paths.raw_dir)
    standard = build_synthetic_day(n_base=100)
    standard["raw_source"] = "ctp:simnow_standard"
    test = standard.copy()
    test["raw_source"] = "ctp:simnow_test"
    test["batch_id"] = "batch-other"
    key = PartitionKey("C2701", "2026-09-18")
    store.write_staging_part(key, pa.Table.from_pandas(pd.concat([test, standard]), schema=RAW_SCHEMA), 1)
    store.finalize(key)
    return config, manager, store


def test_environment_presets_custom_and_mismatch(report_env):
    _, manager, _ = report_env
    for preset in PRESETS:
        payload = {k: preset[k] for k in ("source_kind", "broker_id")}
        payload["fronts"] = "\n".join(preset["fronts"])
        manager.update_settings(payload)
        assert SettingsStore(manager.config.paths.data_dir).load().fronts == preset["fronts"]
    with pytest.raises(AppError, match="不一致"):
        manager.update_settings({"source_kind": "simnow_standard"})
    assert manager.settings.source_kind == "simnow_test"
    with pytest.raises(AppError, match="交易前置"):
        manager.update_settings({"fronts": "tcp://182.254.243.31:40001"})
    manager.update_settings({"fronts": "tcp://192.0.2.1:1234", "broker_id": "custom",
                             "source_kind": "simnow_standard"})
    loaded = SettingsStore(manager.config.paths.data_dir).load()
    assert loaded.broker_id == "custom" and loaded.fronts == ["tcp://192.0.2.1:1234"]


def test_source_filter_before_cleaning_and_cache(report_env):
    config, _, _ = report_env
    repo = StorageRepository(config)
    for source in ("ctp:simnow_test", "ctp:simnow_standard"):
        result = load_clean(repo, "C2701", "2026-09-18", raw_source=source)
        assert len(result.df) == 102
        assert result.stats["input_rows"] == 102
        assert set(result.df.raw_source) == {source}
        again = load_clean(repo, "C2701", "2026-09-18", raw_source=source)
        pd.testing.assert_frame_equal(result.df, again.df)
    empty = load_clean(repo, "C2701", "2026-09-18", raw_source="absent")
    assert empty.df.empty


def test_report_api_generation_auth_and_download(report_env):
    config, manager, _ = report_env
    manager.settings.user = "private-user"
    manager.settings.password = "private-password"
    manager.monitor.feishu_app_secret = "private-feishu-secret"
    server, port = _serve(config, manager, token="private-token")
    base = f"http://127.0.0.1:{port}"
    headers = {"X-Auth-Token": "private-token"}
    try:
        for endpoint in ("/api/environments", "/api/briefing", "/api/reports"):
            with pytest.raises(urllib.error.HTTPError) as exc:
                _req(base + endpoint)
            assert exc.value.code == 401
        code, presets = _req(base + "/api/environments", headers=headers)
        assert code == 200 and len(presets["presets"]) == 4
        _, brief = _req(base + "/api/briefing", headers=headers)
        assert "private-" not in brief["text"]
        payload = {"instrument": "C2701", "day": "2026-09-18", "source": "simnow_standard"}
        with pytest.raises(urllib.error.HTTPError) as exc:
            _req(base + "/api/reports", "POST", payload, headers | {"Origin": "https://other.invalid"})
        assert exc.value.code == 403
        code, job = _req(base + "/api/reports", "POST", payload, headers)
        assert code == 202
        manager.reports._thread.join(timeout=45)
        assert not manager.reports.busy
        _, reports = _req(base + "/api/reports", headers=headers)
        assert reports["jobs"][0]["status"] == "ready", reports
        assert reports["jobs"][0]["snapshots"] == 102
        assert ReportStore(config).list()[0]["status"] == "ready"
        url = base + f"/api/reports/{job['id']}/zip"
        with pytest.raises(urllib.error.HTTPError) as exc:
            _req(url)
        assert exc.value.code == 401
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers)) as response:
            assert "attachment" in response.headers["Content-Disposition"]
            archive_bytes = response.read()
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            names = archive.namelist()
            assert "summary.html" in names and "summary.md" in names
            assert "job.json" not in names
            metadata = json.loads(archive.read("run_metadata.json"))
            assert metadata["raw_sources"] == ["ctp:simnow_standard"]
            for name in names:
                assert b"private-" not in archive.read(name)
        for tail in ("settings.local.json", "../../settings.local.json", "md/extra"):
            with pytest.raises(urllib.error.HTTPError):
                _req(base + f"/api/reports/{job['id']}/" + tail, headers=headers)
        # Even a local symlink cannot make a report endpoint expose settings.
        html = config.paths.reports_dir / job["id"] / "artifacts" / "summary.html"
        html.unlink()
        html.symlink_to(manager.settings_store.path)
        with pytest.raises(AppError):
            manager.reports.download(job["id"], "html")
    finally:
        server.shutdown()


def test_report_busy_failure_retry_and_interrupted(report_env, monkeypatch):
    config, manager, _ = report_env
    entered, release = threading.Event(), threading.Event()

    def fail(*args, **kwargs):
        entered.set()
        release.wait(10)
        raise RuntimeError("private failure must not be exported")

    monkeypatch.setattr("app.analysis.session_report.run_analysis", fail)
    payload = {"instrument": "C2701", "day": "2026-09-18", "source": "simnow_test"}
    job = manager.create_report(payload)
    assert entered.wait(5)
    try:
        assert ReportStore(config).list()[0]["status"] == "interrupted"
        with pytest.raises(AppError, match="正在生成"):
            manager.create_report(payload)
        with pytest.raises(AppError, match="报告正在生成"):
            manager.start()
        with pytest.raises(AppError, match="尚未"):
            manager.reports.download(job["id"], "html")
    finally:
        release.set()
        manager.reports._thread.join(5)
    assert manager.reports.list()[0]["status"] == "failed"
    assert "private failure" not in json.dumps(manager.reports.list())
    manager.create_report(payload)
    manager.reports._thread.join(5)
    assert len(manager.reports.list()) == 2


def test_report_rejects_active_collection_staging_and_bad_inputs(report_env):
    config, manager, store = report_env
    payload = {"instrument": "C2701", "day": "2026-09-18", "source": "simnow_test"}
    for bad in ({"instrument": "../../"}, {"day": "2026-99-18"},
                {"source": []}, {"source": "all"}, {"day": "2026-09-19"}):
        with pytest.raises(AppError):
            manager.create_report(payload | bad)
    release = threading.Event()
    manager._thread = threading.Thread(target=lambda: release.wait(5))
    manager._thread.start()
    try:
        with pytest.raises(AppError, match="停止采集"):
            manager.create_report(payload)
    finally:
        release.set()
        manager._thread.join(5)
    # Empty staging directories do not create pending-data warnings.
    key = PartitionKey("C2701", "2026-09-18")
    store.staging_dir(key).mkdir(exist_ok=True)
    assert "待整理分区：0" in briefing(manager)["text"]
    store.write_staging_part(key, pa.Table.from_pandas(build_synthetic_day(n_base=10), schema=RAW_SCHEMA), 2)
    assert "待整理分区：1" in briefing(manager)["text"]
    with pytest.raises(AppError, match="待归档"):
        manager.create_report(payload)
