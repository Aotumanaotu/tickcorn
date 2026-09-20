"""End-to-end research API: source isolation, report download and monitor scope."""
import asyncio
import io
import json
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pyarrow as pa
import pytest
from httpx import ASGITransport, AsyncClient

from app.api.main import create_app
from app.auth.service import create_user
from app.common.config import load_config
from app.common.runtime import WebSettings
from app.common.schema import RAW_SCHEMA, PartitionKey
from app.storage.parquet_store import RawParquetStore
from app.common.private_files import write_private_json
from conftest import ROOT, build_synthetic_day


@pytest.fixture
async def research_client(tmp_path):
    config = load_config(config_dir=ROOT / 'config', data_dir_override=tmp_path)
    settings = WebSettings(database_url=f'sqlite+aiosqlite:///{tmp_path}/app.db',
                           jwt_secret='research-test-secret-0123456789abcdef', gateway_socket=tmp_path/'none.sock')
    app = create_app(config, settings)
    async with app.router.lifespan_context(app):
        async with app.state.session_factory() as session:
            await create_user(session, 'admin', 'admin-password', 'ADMIN')
            await create_user(session, 'viewer', 'viewer-password', 'VIEWER')
        async def gateway_status():
            return app.state.ingest.gateway_status
        app.state.ingest = SimpleNamespace(connected=True, gateway_status={'state': 'idle'}, request_status=gateway_status)
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            yield client, app, config
        if app.state.reports._thread:
            await asyncio.to_thread(app.state.reports._thread.join, 60)
        # The lifespan closes the original ingest client, not this stub.


async def login(client, name='admin'):
    result = await client.post('/api/v1/auth/login', json={'username': name, 'password': name+'-password'})
    assert result.status_code == 200
    return {'Authorization': 'Bearer '+result.json()['access_token']}


async def test_reports_generate_and_download_isolated_source(research_client):
    client, app, config = research_client
    headers = await login(client)
    store = RawParquetStore(config.paths.raw_dir)
    frames = []
    for source in ['ctp:simnow_standard', 'ctp:simnow_test', 'ctp', 'simulate']:
        frame = build_synthetic_day(n_base=50)
        frame['raw_source'] = source
        frame['batch_id'] = source
        frames.append(frame)
    key = PartitionKey('C2701', '2026-09-18')
    store.write_staging_part(key, pa.Table.from_pandas(pd.concat(frames), schema=RAW_SCHEMA), 1)
    payload = {'instrument': 'C2701', 'day': '2026-09-18', 'source': 'simnow_standard'}
    # Pending data and active collection must never be analysed as complete.
    assert (await client.post('/api/v1/research/reports', json=payload, headers=headers)).status_code == 400
    store.finalize(key)
    app.state.ingest.gateway_status = {'state': 'streaming', 'batch_id': 'active'}
    assert (await client.post('/api/v1/research/reports', json=payload, headers=headers)).status_code == 409
    app.state.ingest.gateway_status = {'state': 'idle'}
    result = await client.post('/api/v1/research/reports', json=payload, headers=headers)
    assert result.status_code == 202, result.text
    job_id = result.json()['id']
    await asyncio.to_thread(app.state.reports._thread.join, 60)
    jobs = (await client.get('/api/v1/research/reports', headers=headers)).json()['jobs']
    assert jobs[0]['status'] == 'ready', jobs
    result = await client.get(f'/api/v1/research/reports/{job_id}/zip', headers=headers)
    assert result.status_code == 200
    with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
        assert {'summary.html', 'summary.md', 'event_statistics.csv'} <= set(archive.namelist())
        assert json.loads(archive.read('run_metadata.json'))['raw_sources'] == ['ctp:simnow_standard']
    assert (await client.get(f'/api/v1/research/reports/{job_id}/zip')).status_code == 401
    assert (await client.get(f'/api/v1/research/reports/{job_id}/secrets', headers=headers)).status_code == 404
    viewer = await login(client, 'viewer')
    assert (await client.get('/api/v1/research/archives', headers=viewer)).status_code == 200
    assert (await client.get('/api/v1/research/reports', headers=viewer)).status_code == 403
    assert (await client.post('/api/v1/research/reports', json=payload, headers=viewer)).status_code == 403


async def test_monitor_secrets_scope_and_test_queue(research_client):
    client, app, config = research_client
    headers = await login(client)
    payload = {'enabled': True, 'feishu_app_id': 'test-app', 'feishu_app_secret': 'private-secret',
               'feishu_receive_id': 'test-chat', 'report_times': ['08:00', '21:30'], 'tz': 'Asia/Shanghai'}
    result = await client.put('/api/v1/monitor/settings', json=payload, headers=headers)
    assert result.status_code == 200, result.text
    assert 'private-secret' not in result.text
    assert result.json()['settings']['has_secret']
    result = await client.put('/api/v1/monitor/settings', json={'feishu_app_secret': ''}, headers=headers)
    assert result.status_code == 200
    assert app.state.monitor_settings.load().feishu_app_secret == 'private-secret'
    for invalid in [{'report_times': ['25:00']}, {'tz': 'Not/AZone'}, {'enabled': 'yes'}]:
        assert (await client.put('/api/v1/monitor/settings', json=invalid, headers=headers)).status_code == 400
    assert (await client.post('/api/v1/monitor/test', headers=headers)).status_code == 409
    write_private_json(config.paths.data_dir / 'monitor-status.local.json', {'heartbeat_at': time.time()*1000})
    assert (await client.post('/api/v1/monitor/test', headers=headers)).status_code == 202
    assert app.state.monitor_settings.load().test_request > 0
    token_header = {'X-Monitor-Token': app.state.monitor_token}
    app.state.ingest.gateway_status = {'state': 'streaming', 'batch_id': 'test', 'password': 'private-ctp'}
    result = await client.get('/api/v1/monitor/state', headers=token_header)
    assert result.status_code == 200
    assert 'private' not in result.text
    assert (await client.get('/api/v1/monitor/state')).status_code == 401
    assert (await client.get('/api/v1/monitor/settings', headers=token_header)).status_code == 401
    assert (await client.post('/api/v1/system/gateway/disconnect', headers=token_header)).status_code == 401
    viewer = await login(client, 'viewer')
    assert (await client.put('/api/v1/monitor/settings', json=payload, headers=viewer)).status_code == 403
