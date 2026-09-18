"""Configuration loading and typed access.

Two YAML files under config/:
    settings.yaml    -- global settings (paths, ctp, classifier, sessions...)
    instruments.yaml -- per-product / per-instrument static parameters

Everything is loaded into immutable dataclasses; a deterministic hash of the
merged configuration is exposed for experiment provenance.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from app.common.exceptions import ConfigError


# ---------------------------------------------------------------------
# Typed config objects
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class PathsConfig:
    data_dir: Path

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def features_dir(self) -> Path:
        return self.data_dir / "features"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def live_dir(self) -> Path:
        return self.data_dir / "live"

    @property
    def ctp_flow_dir(self) -> Path:
        return self.data_dir / "ctp_flow"

    @property
    def metadata_db(self) -> Path:
        return self.data_dir / "metadata.db"


@dataclass(frozen=True)
class CtpConfig:
    fronts: tuple[str, ...]
    broker_id: str
    user_env: str
    password_env: str
    use_udp: bool
    use_multicast: bool
    production_mode: bool


@dataclass(frozen=True)
class CollectorConfig:
    flush_interval_s: float
    flush_rows: int
    finalize_on_start: bool
    live_feed_rows: int


@dataclass(frozen=True)
class DashboardConfig:
    host: str
    port: int
    refresh_ms: int
    history_points: int


@dataclass(frozen=True)
class ClassifierConfig:
    strict_symmetric_quote_move: bool
    mid_tolerance_ticks: float
    likely_bounce_requires_unit_move: bool


@dataclass(frozen=True)
class TimeRange:
    start: str  # "HH:MM"
    end: str    # "HH:MM"

    def contains(self, hhmm: str) -> bool:
        return self.start <= hhmm <= self.end


@dataclass(frozen=True)
class SessionsConfig:
    night: tuple[TimeRange, ...]
    day: tuple[TimeRange, ...]
    open_close_window_min: int


@dataclass(frozen=True)
class AnalysisConfig:
    lookbacks: tuple[int, ...]
    horizons: tuple[int, ...]
    obi_bucket_edges: tuple[float, ...]
    intraday_bin_minutes: int
    min_samples_test: int
    significance_level: float


@dataclass(frozen=True)
class ReportConfig:
    title_prefix: str


@dataclass(frozen=True)
class ProductInfo:
    product: str
    exchange: str
    tick_size: float
    name: str = ""


@dataclass(frozen=True)
class InstrumentsConfig:
    products: dict[str, ProductInfo]
    instrument_overrides: dict[str, dict[str, Any]]
    unknown_product_policy: str  # "error" | "warn_and_use"
    default_tick_size: Optional[float]


@dataclass(frozen=True)
class AppConfig:
    paths: PathsConfig
    ctp: CtpConfig
    collector: CollectorConfig
    dashboard: DashboardConfig
    classifier: ClassifierConfig
    sessions: SessionsConfig
    analysis: AnalysisConfig
    report: ReportConfig
    instruments: InstrumentsConfig
    config_hash: str
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    # ------------------------------------------------------------------
    def resolve_tick_size(self, instrument_id: str) -> float:
        """Tick size comes from config (never hard-coded)."""
        override = self.instruments.instrument_overrides.get(instrument_id)
        if override and "tick_size" in override:
            return float(override["tick_size"])
        product = parse_product(instrument_id)
        info = self.instruments.products.get(product)
        if info is not None:
            return info.tick_size
        if (self.instruments.unknown_product_policy == "warn_and_use"
                and self.instruments.default_tick_size):
            return float(self.instruments.default_tick_size)
        raise ConfigError(
            f"Unknown product '{product}' for instrument '{instrument_id}'. "
            f"Add it to config/instruments.yaml (products or instruments)."
        )

    def exchange_of(self, instrument_id: str) -> str:
        product = parse_product(instrument_id)
        info = self.instruments.products.get(product)
        if info is not None:
            return info.exchange
        override = self.instruments.instrument_overrides.get(instrument_id, {})
        return str(override.get("exchange", "UNKNOWN"))

    def session_of(self, hhmmss: str) -> str:
        """NIGHT / DAY / OUT_OF_SESSION from exchange update time."""
        hhmm = hhmmss[:5]
        if any(r.contains(hhmm) for r in self.sessions.night):
            return "NIGHT"
        if any(r.contains(hhmm) for r in self.sessions.day):
            return "DAY"
        return "OUT_OF_SESSION"


def parse_product(instrument_id: str) -> str:
    """Extract the alphabetic product prefix: 'C2701' -> 'C', 'sc2701' -> 'sc'."""
    m = re.match(r"^([A-Za-z]+)\d+$", instrument_id.strip())
    if not m:
        raise ConfigError(f"Cannot parse product from instrument id: {instrument_id!r}")
    return m.group(1).upper()


# ---------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------

def _require(d: dict[str, Any], key: str, ctx: str) -> Any:
    if key not in d:
        raise ConfigError(f"Missing required config key '{ctx}.{key}'")
    return d[key]


def load_config(
    config_dir: Path | str | None = None,
    data_dir_override: Path | str | None = None,
) -> AppConfig:
    config_dir = Path(config_dir) if config_dir else _default_config_dir()
    settings = _load_yaml(config_dir / "settings.yaml")
    instruments = _load_yaml(config_dir / "instruments.yaml")

    data_dir = Path(data_dir_override or _require(settings["paths"], "data_dir", "paths"))
    if not data_dir.is_absolute():
        data_dir = _project_root() / data_dir

    ctp = _require(settings, "ctp", "")
    collector = _require(settings, "collector", "")
    dash = settings.get("dashboard", {})
    clf = _require(settings, "classifier", "")
    ses = _require(settings, "sessions", "")
    ana = _require(settings, "analysis", "")
    rep = settings.get("report", {})

    cfg = AppConfig(
        paths=PathsConfig(data_dir=data_dir),
        ctp=CtpConfig(
            fronts=tuple(ctp["fronts"]),
            broker_id=str(ctp["broker_id"]),
            user_env=str(ctp.get("user_env", "SIMNOW_USER")),
            password_env=str(ctp.get("password_env", "SIMNOW_PASSWORD")),
            use_udp=bool(ctp.get("use_udp", False)),
            use_multicast=bool(ctp.get("use_multicast", False)),
            production_mode=bool(ctp.get("production_mode", True)),
        ),
        collector=CollectorConfig(
            flush_interval_s=float(collector.get("flush_interval_s", 5)),
            flush_rows=int(collector.get("flush_rows", 5000)),
            finalize_on_start=bool(collector.get("finalize_on_start", True)),
            live_feed_rows=int(collector.get("live_feed_rows", 20000)),
        ),
        dashboard=DashboardConfig(
            host=str(dash.get("host", "127.0.0.1")),
            port=int(dash.get("port", 8800)),
            refresh_ms=int(dash.get("refresh_ms", 1000)),
            history_points=int(dash.get("history_points", 240)),
        ),
        classifier=ClassifierConfig(
            strict_symmetric_quote_move=bool(clf.get("strict_symmetric_quote_move", True)),
            mid_tolerance_ticks=float(clf.get("mid_tolerance_ticks", 0.0)),
            likely_bounce_requires_unit_move=bool(
                clf.get("likely_bounce_requires_unit_move", True)),
        ),
        sessions=SessionsConfig(
            night=tuple(TimeRange(r["start"], r["end"]) for r in ses.get("night", [])),
            day=tuple(TimeRange(r["start"], r["end"]) for r in ses.get("day", [])),
            open_close_window_min=int(ses.get("open_close_window_min", 30)),
        ),
        analysis=AnalysisConfig(
            lookbacks=tuple(int(x) for x in ana.get("lookbacks", [1, 2, 3, 5, 10])),
            horizons=tuple(int(x) for x in ana.get("horizons", [1, 2, 3, 5])),
            obi_bucket_edges=tuple(
                float(x) for x in ana.get(
                    "obi_bucket_edges",
                    [-1.0, -0.8, -0.6, -0.4, -0.2, 0.0, 0.2, 0.4, 0.6, 0.8, 1.0])),
            intraday_bin_minutes=int(ana.get("intraday_bin_minutes", 30)),
            min_samples_test=int(ana.get("min_samples_test", 30)),
            significance_level=float(ana.get("significance_level", 0.05)),
        ),
        report=ReportConfig(title_prefix=str(rep.get("title_prefix", "Analysis Report"))),
        instruments=_load_instruments(instruments),
        config_hash="",
        raw=settings,
    )

    # Deterministic config hash for provenance.
    merged = {"settings": settings, "instruments": instruments}
    cfg_hash = hashlib.sha256(
        json.dumps(merged, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]

    object.__setattr__(cfg, "config_hash", cfg_hash)
    return cfg


def _load_instruments(d: dict[str, Any]) -> InstrumentsConfig:
    products: dict[str, ProductInfo] = {}
    for code, info in (d.get("products") or {}).items():
        if "tick_size" not in info:
            raise ConfigError(f"products.{code}: tick_size is required")
        products[str(code).upper()] = ProductInfo(
            product=str(code).upper(),
            exchange=str(info.get("exchange", "UNKNOWN")),
            tick_size=float(info["tick_size"]),
            name=str(info.get("name", "")),
        )
    return InstrumentsConfig(
        products=products,
        instrument_overrides=dict(d.get("instruments") or {}),
        unknown_product_policy=str(d.get("unknown_product_policy", "error")),
        default_tick_size=(float(d["default_tick_size"])
                           if d.get("default_tick_size") is not None else None),
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"Config file {path} must contain a mapping")
    return data


def _project_root() -> Path:
    """Project root = directory containing config/ and pyproject.toml.

    Works both for editable installs (src layout) and running from repo.
    """
    # When installed, app/__init__.py lives in <root>/src/app/
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "config" / "settings.yaml").exists():
            return parent
    # Fallback: env var or cwd
    return Path(os.environ.get("CORN_TICK_ROOT", Path.cwd()))


def _default_config_dir() -> Path:
    return _project_root() / "config"
