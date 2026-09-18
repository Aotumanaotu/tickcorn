"""Command line interface: python -m app <command> ..."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import time
from pathlib import Path
from typing import Optional

from app import __version__
from app.common.config import AppConfig, load_config
from app.common.exceptions import AppError
from app.common.logging import get_logger, setup_logging

logger = get_logger("cli")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m app",
        description="Corn futures 1-tick microstructure research system "
                    "(Phase 1: collect / replay / classify / report)")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--config-dir", type=Path, default=None,
                   help="custom config directory (default: <project>/config)")
    p.add_argument("--data-dir", type=Path, default=None,
                   help="override data directory")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    sub = p.add_subparsers(dest="command", required=True)

    # ---------------- collect ----------------
    c = sub.add_parser("collect", help="collect CTP snapshots (SimNow)")
    c.add_argument("--instrument", "-i", action="append", required=True,
                   help="instrument id, e.g. C2701 (repeatable)")
    c.add_argument("--user", default=None,
                   help="SimNow user (default: env SIMNOW_USER)")
    c.add_argument("--password", default=None,
                   help="SimNow password (default: env SIMNOW_PASSWORD)")
    c.add_argument("--broker-id", default=None, help="authorized provider BrokerID")
    c.add_argument("--front", action="append", default=None,
                   help="override market front address (repeatable)")
    c.add_argument("--dashboard", dest="dashboard",
                   action=argparse.BooleanOptionalAction, default=True,
                   help="start the real-time dashboard (default: on)")
    c.add_argument("--dashboard-port", type=int, default=None)
    c.add_argument("--note", default="", help="note recorded with the batch")

    # ---------------- finalize ----------------
    c = sub.add_parser("finalize",
                       help="merge staging parts into immutable snapshots.parquet")
    c.add_argument("--instrument", default=None)
    c.add_argument("--trading-day", default=None)

    # ---------------- replay ----------------
    c = sub.add_parser("replay", help="replay stored snapshots")
    c.add_argument("--instrument", "-i", required=True)
    c.add_argument("--date", default=None, help="single trading day YYYY-MM-DD")
    c.add_argument("--from", dest="from_day", default=None)
    c.add_argument("--to", dest="to_day", default=None)
    c.add_argument("--speed", choices=["fast", "realtime"], default="fast")
    c.add_argument("--limit", type=int, default=None,
                   help="max snapshots to replay (testing)")
    c.add_argument("--dashboard", action="store_true",
                   help="serve the live dashboard while replaying (realtime speed)")

    # ---------------- analyze ----------------
    c = sub.add_parser("analyze", help="run the Phase-1 analysis pipeline")
    c.add_argument("--source", choices=["simnow_test", "simnow_standard"],
                   help="only analyze snapshots from this configured source")
    c.add_argument("--instrument", "-i", required=True)
    c.add_argument("--date", default=None, help="single trading day YYYY-MM-DD")
    c.add_argument("--from", dest="from_day", default=None)
    c.add_argument("--to", dest="to_day", default=None)
    c.add_argument("--loose", action="store_true",
                   help="loose classifier mode (asymmetric quote moves count "
                        "as genuine); default is strict")
    c.add_argument("--tick-size", type=float, default=None,
                   help="override tick size (default: from instruments.yaml)")
    c.add_argument("--output", type=Path, default=None,
                   help="output directory (default: data/reports/<run>)")

    # ---------------- dashboard (standalone) ----------------
    c = sub.add_parser("dashboard",
                       help="standalone dashboard tailing the live feed file")
    c.add_argument("--port", type=int, default=None)
    c.add_argument("--host", default=None)

    # ---------------- serve (web control panel) ----------------
    c = sub.add_parser("serve",
                       help="web control panel: configure & start/stop "
                            "collection from the browser")
    c.add_argument("--host", default=None,
                   help="bind address (default 127.0.0.1; use 0.0.0.0 for "
                        "docker/cloud, then ALWAYS set a token)")
    c.add_argument("--port", type=int, default=None)
    c.add_argument("--token", default=None,
                   help="access token (default: env DASHBOARD_TOKEN)")
    c.add_argument("--no-token", action="store_true",
                   help="allow no-token access (NOT recommended off-localhost)")

    # ---------------- info ----------------
    c = sub.add_parser("info", help="list instruments / days / batches / runs")
    c.add_argument("--instrument", default=None)

    # ---------------- verify ----------------
    c = sub.add_parser("verify", help="verify raw file integrity (sha256)")
    c.add_argument("--instrument", default=None)

    # ---------------- selftest ----------------
    c = sub.add_parser("selftest", help="check shim, struct layout, config")

    return p


def _load_config(args) -> AppConfig:
    return load_config(config_dir=getattr(args, "config_dir", None),
                       data_dir_override=getattr(args, "data_dir", None))


def _resolve_days(args, available: list[str]) -> list[str]:
    if args.date:
        days = [args.date]
    elif args.from_day or args.to_day:
        lo = args.from_day or (available[0] if available else None)
        hi = args.to_day or (available[-1] if available else None)
        days = [d for d in available if d and lo <= d <= hi]
    else:
        days = available[-1:] if available else []
    if not days:
        raise AppError("no trading days given (--date, or --from/--to) or "
                       "no data collected yet")
    return days


# ---------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------

def cmd_collect(args) -> int:
    config = _load_config(args)
    if args.front:
        object.__setattr__(config.ctp, "fronts", tuple(args.front))
    if args.broker_id:
        object.__setattr__(config.ctp, "broker_id", args.broker_id)

    user = args.user or os.environ.get(config.ctp.user_env)
    password = args.password or os.environ.get(config.ctp.password_env)
    if not user or not password:
        print("SimNow credentials required (env "
              f"{config.ctp.user_env} / {config.ctp.password_env}, "
              "or --user/--password):", file=sys.stderr)
        user = user or input("SimNow user: ").strip()
        password = password or getpass.getpass("SimNow password: ")
        if not user or not password:
            print("no credentials given; aborting", file=sys.stderr)
            return 2

    from app.collector.service import CollectorService
    svc = CollectorService(
        config=config,
        instruments=args.instrument,
        user=user,
        password=password,
        with_dashboard=args.dashboard,
        dashboard_port=args.dashboard_port,
        note=args.note,
    )
    svc.run()
    return 0


def cmd_finalize(args) -> int:
    from app.common.private_files import ProcessLock
    config = _load_config(args)
    with ProcessLock(config.paths.data_dir / "collector.lock"):
        return _cmd_finalize(args)


def _cmd_finalize(args) -> int:
    from app.storage.metadata_db import MetadataDB
    from app.storage.parquet_store import RawParquetStore
    config = _load_config(args)
    store = RawParquetStore(config.paths.raw_dir)
    db = MetadataDB(config.paths.metadata_db)
    keys = store.list_staging_partitions()
    if args.instrument:
        keys = [k for k in keys if k.instrument_id == args.instrument]
    if args.trading_day:
        keys = [k for k in keys if k.trading_day == args.trading_day]
    if not keys:
        print("no staging partitions to finalize")
        return 0
    for key in keys:
        r = store.finalize(key)
        if r is not None:
            db.register_raw_file(key.instrument_id, key.trading_day, r.path,
                                 r.rows, r.sha256, r.part_count, batch_id=None)
            print(f"finalized {key}: {r.rows} rows, sha256={r.sha256[:16]}...")
    return 0


def cmd_replay(args) -> int:
    from app.collector.live import LiveState
    from app.dashboard.server import DashboardServer
    from app.replay.replayer import Replayer
    from app.storage.repo import StorageRepository
    config = _load_config(args)
    repo = StorageRepository(config)
    replayer = Replayer(repo)
    available = repo.list_days(args.instrument)
    days = _resolve_days(args, available)
    df = replayer.load(args.instrument, days)
    if args.limit:
        df = df.head(args.limit)
    print(f"replaying {len(df)} snapshots of {args.instrument} "
          f"({days[0]}..{days[-1]}) at {args.speed} speed")

    state = LiveState(history_points=config.dashboard.history_points)
    state.set_connection("replaying", f"{args.instrument} {days[0]}")
    state.ctp_trading_day = days[0]
    tick_size = config.resolve_tick_size(args.instrument)
    count = [0]

    def consumer(row: "pd.Series") -> None:
        inst = row.get("instrument_id", args.instrument)
        ts = state.instrument(inst)
        bid, ask, last = (row.get("bid_price1"), row.get("ask_price1"),
                          row.get("last_price"))
        spread = ask - bid if bid and ask and ask > bid else None
        mid = (bid + ask) / 2 if spread is not None else None
        bv, av = row.get("bid_volume1"), row.get("ask_volume1")
        obi = ((bv - av) / (bv + av)) if (bv is not None and av is not None
                                          and (bv + av) > 0) else None
        label = row.get("label")
        d = row.get("direction")
        direction = int(d) if d is not None and d == d else 0
        ts.update({
            "local_receive_time_ns": int(row.get("exchange_ts_ns", 0)),
            "trading_day": row.get("trading_day"),
            "update_time": row.get("update_time"),
            "update_millisec": row.get("update_millisec"),
            "last_price": last, "bid_price1": bid, "ask_price1": ask,
            "bid_volume1": bv, "ask_volume1": av,
            "volume": row.get("volume"), "open_interest": row.get("open_interest"),
            "spread": spread, "mid_price": mid, "obi1": obi,
        }, label, direction,
           {"db_ticks": row.get("db_ticks"), "da_ticks": row.get("da_ticks"),
            "dl_ticks": row.get("dl_ticks"),
            "spread_ticks": (spread / tick_size
                             if spread is not None else None),
            "mid_price": mid, "obi1": obi})
        count[0] += 1
        if count[0] % 20000 == 0:
            print(f"  ... {count[0]:,} snapshots")

    dash = None
    if args.dashboard:
        port = config.dashboard.port
        dash = DashboardServer(state, host=config.dashboard.host, port=port,
                               refresh_ms=config.dashboard.refresh_ms)
        import threading
        threading.Thread(target=dash.serve_forever, daemon=True).start()
        print(f"dashboard: http://{config.dashboard.host}:{port}")

    # classify before replay so the dashboard shows event markers
    from app.classifier.jump_classifier import JumpEventClassifier
    clf = JumpEventClassifier(
        tick_size=config.resolve_tick_size(args.instrument))
    df = pd_concat_labels(df, clf)

    t0 = time.perf_counter()
    stats = replayer.run(df, consumer, speed=args.speed)
    dt = time.perf_counter() - t0
    print(f"replayed {stats.rows:,} snapshots in {dt:.2f}s "
          f"({stats.rows/max(dt,1e-9):,.0f}/s); gaps={stats.gaps}")
    if dash:
        state.set_connection("replay_done",
                             f"{stats.rows:,} snapshots replayed")
        print("replay finished; dashboard kept running (Ctrl+C to exit)")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            dash.shutdown()
    return 0


def pd_concat_labels(df, clf):
    import pandas as pd
    labels = clf.classify_dataframe(df)
    return pd.concat([df, labels], axis=1)


def cmd_analyze(args) -> int:
    from app.report.generator import AnalysisOptions, run_analysis
    from app.storage.repo import StorageRepository
    config = _load_config(args)
    repo = StorageRepository(config)
    available = repo.list_days(args.instrument)
    days = _resolve_days(args, available)
    options = AnalysisOptions(
        raw_source="ctp:" + args.source if args.source else None,
        strict=not args.loose,
        mid_tolerance_ticks=config.classifier.mid_tolerance_ticks,
        lookbacks=tuple(config.analysis.lookbacks),
        horizons=tuple(config.analysis.horizons),
        tick_size=args.tick_size,
    )
    result = run_analysis(config, args.instrument, days, options, output_dir=args.output)
    out = result.output_dir
    print(f"analysis written to {out}")
    print("  summary.html / summary.md / *.csv / figures/")
    s = result.summary
    ratio = s["bounce_ratio"]
    ratio_txt = "n/a" if ratio is None or ratio != ratio else f"{100*ratio:.1f}%"
    print(f"  1-tick last changes: {s['one_tick_last_changes']:,}  "
          f"bounce ratio: {ratio_txt}")
    return 0


def cmd_dashboard(args) -> int:
    from app.collector.live import JsonlTailSource, LiveState
    from app.dashboard.server import DashboardServer
    config = _load_config(args)
    state = LiveState(history_points=config.dashboard.history_points)
    state.set_connection("standalone", "tailing live feed file")
    tail = JsonlTailSource(config.paths.live_dir, state)
    dash = DashboardServer(
        state, host=args.host or config.dashboard.host,
        port=args.port or config.dashboard.port,
        refresh_ms=config.dashboard.refresh_ms, tail_source=tail)
    print(f"dashboard (standalone): http://{args.host or config.dashboard.host}:"
          f"{args.port or config.dashboard.port}")
    print("waiting for live feed data under "
          f"{config.paths.live_dir} ... (start `python -m app collect`)")
    dash.serve_forever()
    return 0


def cmd_serve(args) -> int:
    from app.collector.live import LiveState
    from app.dashboard.control import CollectorManager
    from app.dashboard.server import DashboardServer
    config = _load_config(args)
    manager = CollectorManager(config)

    from app.common.private_files import dashboard_token, ProcessLock
    import signal
    os.umask(0o077)
    host = args.host or config.dashboard.host
    port = args.port or config.dashboard.port
    if args.no_token and host not in ("127.0.0.1", "localhost", "::1"):
        raise AppError("无令牌模式仅允许绑定本机回环地址")
    lock = ProcessLock(config.paths.data_dir / "serve.lock")
    dash = None
    old_handlers = {}
    try:
        token = None if args.no_token else (args.token or os.environ.get("DASHBOARD_TOKEN")
                                            or dashboard_token(config.paths.data_dir))
        state = LiveState(history_points=config.dashboard.history_points)
        dash = DashboardServer(state, host=host, port=port,
                               refresh_ms=config.dashboard.refresh_ms,
                               manager=manager, auth_token=token)
        def stop_server(signum, frame):
            dash.shutdown()
        for sig in (signal.SIGINT, signal.SIGTERM):
            old_handlers[sig] = signal.signal(sig, stop_server)
        print(f"控制面板: http://{host}:{port}/", flush=True)
        print("请通过 SSH 隧道访问。在网页输入面板令牌和采集配置。", flush=True)
        if token:
            print("面板令牌保存在运行时数据目录，日志不显示令牌。", flush=True)
        dash.serve_forever()
    finally:
        if dash:
            dash.shutdown()
        try:
            manager.stop()
        finally:
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)
            lock.close()
    return 0


def cmd_info(args) -> int:
    from app.storage.metadata_db import MetadataDB
    from app.storage.parquet_store import RawParquetStore
    config = _load_config(args)
    store = RawParquetStore(config.paths.raw_dir)
    db = MetadataDB(config.paths.metadata_db)
    print("=== raw partitions ===")
    for k in store.list_partitions(args.instrument):
        final = store.final_path(k).exists()
        print(f"  {k.instrument_id:10s} {k.trading_day}  "
              f"{'finalized' if final else 'STAGING'}")
    print("\n=== instruments (metadata db) ===")
    for r in db.get_instruments():
        print(f"  {r['instrument_id']:10s} tick={r['tick_size']} "
              f"exchange={r['exchange']} last_seen={r['last_seen']}")
    print("\n=== recent batches ===")
    for r in db.list_batches(10):
        print(f"  {r['batch_id']} {r['started_at']} status={r['status']} "
              f"")
    print("\n=== recent analysis runs ===")
    for r in db.list_analysis_runs(10):
        print(f"  {r['run_id']} {r['instrument_id']} {r['trading_days']} "
              f"{r['status']} -> {r['output_dir']}")
    return 0


def cmd_verify(args) -> int:
    from app.storage.metadata_db import MetadataDB
    config = _load_config(args)
    db = MetadataDB(config.paths.metadata_db)
    bad = db.verify_raw_files(instrument_id=args.instrument)
    if not bad:
        print("all registered raw files verified OK (sha256)")
        return 0
    for r in bad:
        print(f"  PROBLEM: {r['instrument_id']} {r['trading_day']} "
              f"{r['check']}: {r['path']}")
    return 1


def cmd_selftest(args) -> int:
    import ctypes
    config = _load_config(args)
    ok = True

    print(f"[1] config ... ", end="")
    try:
        ts = config.resolve_tick_size("C2701")
        print(f"OK (C tick_size={ts}, hash={config.config_hash})")
    except Exception as e:
        print(f"FAIL: {e}"); ok = False

    print("[2] CTP shim ... ", end="")
    try:
        from app.collector.ctp_binding import (SHIM_PATH, CTPDepthMarketData,
                                               verify_struct_layout)
        lib = ctypes.CDLL(str(SHIM_PATH))
        verify_struct_layout(lib)
        lib.md_api_version.restype = ctypes.c_char_p
        print(f"OK (api {lib.md_api_version().decode()}, "
              f"{ctypes.sizeof(CTPDepthMarketData)} bytes, layout verified)")
    except Exception as e:
        print(f"FAIL: {e}"); ok = False

    print("[3] classifier spec cases ... ", end="")
    try:
        from app.classifier.jump_classifier import JumpEventClassifier
        clf = JumpEventClassifier(tick_size=1.0)
        r1 = clf.classify_pair(2300, 2301, 2300, 2300, 2301, 2301)
        r2 = clf.classify_pair(2300, 2301, 2300, 2301, 2302, 2301)
        r3 = clf.classify_pair(2300, 2301, 2300, 2299, 2300, 2299)
        r4 = clf.classify_pair(2300, 2301, 2300, 2299, 2302, 2300)
        assert r1.label.value == "HIGH_CONFIDENCE_BOUNCE_UP", r1
        assert r2.label.value == "GENUINE_QUOTE_MOVE_UP", r2
        assert r3.label.value == "GENUINE_QUOTE_MOVE_DOWN", r3
        assert r4.label.value == "AMBIGUOUS", r4
        print("OK (4/4)")
    except Exception as e:
        print(f"FAIL: {e}"); ok = False

    print("[4] storage dirs ... ", end="")
    try:
        for d in (config.paths.raw_dir, config.paths.processed_dir,
                  config.paths.reports_dir):
            d.mkdir(parents=True, exist_ok=True)
        print(f"OK ({config.paths.data_dir})")
    except Exception as e:
        print(f"FAIL: {e}"); ok = False

    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


COMMANDS = {
    "collect": cmd_collect,
    "finalize": cmd_finalize,
    "replay": cmd_replay,
    "analyze": cmd_analyze,
    "dashboard": cmd_dashboard,
    "serve": cmd_serve,
    "info": cmd_info,
    "verify": cmd_verify,
    "selftest": cmd_selftest,
}


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(level=args.log_level)
    try:
        return COMMANDS[args.command](args)
    except AppError as e:
        logger.error("%s", e)
        return 2
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        logger.error("%s", e)
        return 2
    except KeyboardInterrupt:
        logger.info("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
