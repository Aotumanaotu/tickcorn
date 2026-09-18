"""Web-based operational settings + collector lifecycle control.

Research parameters (classifier mode, statistics, sessions, tick sizes ...)
stay in config/*.yaml and are hash-recorded with every analysis run.
Operational parameters (SimNow credentials, front addresses, instrument
list) are entered in the dashboard and stored in a LOCAL, gitignored,
0600-permission JSON file -- never in the repository or logs.

CollectorManager runs CollectorService in a worker thread so the web
panel can start/stop collection with one click.
"""

from __future__ import annotations

import dataclasses
import json
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

from app.common.config import AppConfig
from app.common.exceptions import AppError
from app.common.logging import get_logger

logger = get_logger("dashboard.control")

SETTINGS_FILENAME = "settings.local.json"


@dataclass
class OperationalSettings:
    """Everything the web panel may edit."""

    instruments: list[str] = field(default_factory=lambda: ["C2701"])
    fronts: list[str] = field(default_factory=list)
    user: str = ""
    password: str = ""               # only persisted when remember=True
    remember: bool = False           # persist credentials to disk (0600)
    note: str = ""

    def masked(self) -> dict[str, Any]:
        d = asdict(self)
        d["password"] = "********" if self.password else ""
        d["has_password"] = bool(self.password)
        return d


class SettingsStore:
    """Load/save operational settings under <data_dir>/settings.local.json."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / SETTINGS_FILENAME

    def load(self) -> OperationalSettings:
        if not self.path.exists():
            return OperationalSettings()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return OperationalSettings(
                instruments=list(raw.get("instruments") or []),
                fronts=list(raw.get("fronts") or []),
                user=str(raw.get("user") or ""),
                password=str(raw.get("password") or ""),
                remember=bool(raw.get("remember", False)),
                note=str(raw.get("note") or ""),
            )
        except Exception:
            logger.exception("failed to read %s; using defaults", self.path)
            return OperationalSettings()

    def save(self, s: OperationalSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(s)
        if not s.remember:
            payload["password"] = ""   # never persist unless explicitly asked
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)


class CollectorManager:
    """Starts/stops CollectorService in a background thread.

    One collection at a time (per process). All methods are thread-safe.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.settings_store = SettingsStore(config.paths.data_dir)
        self.settings = self.settings_store.load()
        if not self.settings.fronts:
            self.settings.fronts = list(config.ctp.fronts)

        self._lock = threading.RLock()
        self._service: Optional[Any] = None
        self._thread: Optional[threading.Thread] = None
        self._error: str = ""
        self._started_at: Optional[str] = None

    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        with self._lock:
            return (self._thread is not None and self._thread.is_alive()
                    and self._service is not None and not self._service._stop.is_set())

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "batch_id": self._service.batch_id if self._service else None,
                "instruments": [i for i in self._service.instruments]
                if self._service else [],
                "started_at": self._started_at,
                "error": self._error,
            }

    def get_live_state(self):
        with self._lock:
            return self._service.live if self._service else None

    # ------------------------------------------------------------------
    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply settings from the web form. Password left empty = keep."""
        with self._lock:
            s = self.settings
            if payload.get("instruments"):
                instruments = [str(x).strip().upper()
                               for x in str(payload["instruments"]).replace(
                                   ",", " ").split() if x.strip()]
                if not instruments:
                    raise AppError("合约列表为空")
                s.instruments = instruments
            if payload.get("fronts") is not None:
                fronts = [ln.strip() for ln in str(payload["fronts"]).splitlines()
                          if ln.strip() and not ln.strip().startswith("#")]
                if fronts:
                    s.fronts = fronts
            if payload.get("user") is not None:
                s.user = str(payload["user"]).strip()
            if payload.get("password"):
                s.password = str(payload["password"])
            elif payload.get("clear_password"):
                s.password = ""
            s.remember = bool(payload.get("remember", s.remember))
            if payload.get("note") is not None:
                s.note = str(payload["note"])[:200]
            self.settings_store.save(s)
            return s.masked()

    # ------------------------------------------------------------------
    def start(self) -> dict[str, Any]:
        with self._lock:
            if self.running:
                raise AppError("采集已在运行中")
            if self._thread is not None and self._thread.is_alive():
                raise AppError("上一个采集线程尚未退出，请稍候")

            s = self.settings
            missing = []
            if not s.instruments:
                missing.append("合约")
            if not s.user or not s.password:
                missing.append("SimNow 账号/密码")
            if not s.fronts:
                missing.append("前置地址")
            if missing:
                raise AppError("缺少: " + "、".join(missing))

            from app.collector.service import CollectorService
            service = CollectorService(
                config=self._effective_config(),
                instruments=s.instruments,
                user=s.user,
                password=s.password,
                with_dashboard=False,   # dashboard already running (serve)
                note=s.note or "started from web panel",
            )
            self._service = service
            self._error = ""
            self._started_at = time.strftime("%Y-%m-%d %H:%M:%S")

            def _run():
                try:
                    service.run()
                except Exception as e:      # noqa: BLE001 - report to panel
                    logger.exception("collector thread crashed")
                    with self._lock:
                        self._error = f"{type(e).__name__}: {e}"

            self._thread = threading.Thread(target=_run, name="collector",
                                            daemon=True)
            self._thread.start()
            return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            service = self._service
            thread = self._thread
        if service is not None:
            service.stop()
        if thread is not None:
            thread.join(timeout=60)
        return self.status()

    # ------------------------------------------------------------------
    def _effective_config(self) -> AppConfig:
        """AppConfig clone with web-entered fronts applied."""
        s = self.settings
        if not s.fronts:
            return self.config
        new_ctp = dataclasses.replace(self.config.ctp,
                                      fronts=tuple(s.fronts))
        return dataclasses.replace(self.config, ctp=new_ctp)
