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
import re
from urllib.parse import urlsplit
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

from app.common.config import AppConfig
from app.common.exceptions import AppError
from app.common.private_files import write_private_json
from app.common.logging import get_logger

logger = get_logger("dashboard.control")

SETTINGS_FILENAME = "settings.local.json"


@dataclass
class OperationalSettings:
    """Everything the web panel may edit."""

    instruments: list[str] = field(default_factory=list)
    fronts: list[str] = field(default_factory=list)
    broker_id: str = ""
    source_kind: str = "simnow_test"
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
                broker_id=str(raw.get("broker_id") or ""),
                source_kind=str(raw.get("source_kind") or "simnow_test"),
                user=str(raw.get("user") or "") if raw.get("remember") else "",
                password=str(raw.get("password") or "") if raw.get("remember") else "",
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
            payload["user"] = ""
            payload["password"] = ""
        write_private_json(self.path, payload)



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
                "stopping": bool(self._thread and self._thread.is_alive() and not self.running),
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
        """Validate a copy before replacing settings; empty password = keep."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise AppError("请先停止采集，再修改配置")
            s = dataclasses.replace(self.settings)
            for key in ("user", "password", "broker_id", "source_kind", "note"):
                if key in payload and not isinstance(payload[key], str):
                    raise AppError("配置字段类型错误")
            if "instruments" in payload:
                if not isinstance(payload["instruments"], str):
                    raise AppError("合约应为空格或逗号分隔的文本")
                s.instruments = list(dict.fromkeys(payload["instruments"].replace(",", " ").upper().split()))
                if len(s.instruments) > 50:
                    raise AppError("一次最多订阅 50 个合约")
                for inst in s.instruments:
                    if not re.fullmatch(r"[A-Z]{1,8}[0-9]{3,4}", inst):
                        raise AppError("合约格式无效")
                    self.config.resolve_tick_size(inst)
            if "fronts" in payload:
                if not isinstance(payload["fronts"], str):
                    raise AppError("前置地址应为文本")
                fronts = list(dict.fromkeys(x.strip() for x in payload["fronts"].splitlines() if x.strip()))
                if len(fronts) > 10:
                    raise AppError("最多配置 10 个行情前置")
                for front in fronts:
                    try:
                        u = urlsplit(front)
                        valid = (u.scheme == "tcp" and u.hostname and u.port and
                                 not u.username and not u.password and not u.path and
                                 not u.query and not u.fragment and not any(c.isspace() for c in front))
                    except ValueError:
                        valid = False
                    if not valid:
                        raise AppError("前置地址格式应为 tcp://主机:端口，不包含账号或参数")
                s.fronts = fronts
            for key in ("user", "broker_id", "source_kind", "note"):
                if key in payload:
                    setattr(s, key, payload[key].strip())
            if payload.get("clear_password"):
                s.password = ""
            elif payload.get("password"):
                s.password = payload["password"]
            for key, limit in (("user", 15), ("password", 40), ("broker_id", 10)):
                value = getattr(s, key)
                if len(value.encode("utf-8")) > limit or any(ord(c) < 32 for c in value):
                    raise AppError("账号、密码或 BrokerID 超出 CTP 字段长度或包含控制字符")
            if s.source_kind not in ("simnow_test", "simnow_standard"):
                raise AppError("请选择 SimNow 测试或标准环境")
            if "remember" in payload:
                if not isinstance(payload["remember"], bool):
                    raise AppError("记住账号密码应为布尔值")
                s.remember = payload["remember"]
            s.note = s.note[:200]
            self.settings_store.save(s)
            self.settings = s
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
            if not s.broker_id:
                missing.append("BrokerID")
            if missing:
                raise AppError("缺少: " + "、".join(missing))

            from app.collector.service import CollectorService
            service = CollectorService(
                config=self._effective_config(),
                instruments=s.instruments,
                user=s.user,
                password=s.password,
                raw_source="ctp:" + s.source_kind,
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
                    logger.error("collector thread failed (%s)", type(e).__name__)
                    with self._lock:
                        self._error = "采集失败，请检查配置、磁盘空间和服务日志（" + type(e).__name__ + "）"

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
            thread.join(timeout=90)
            if thread.is_alive():
                raise AppError("采集仍在停止并保存数据，请稍后查看状态")
        return self.status()

    # ------------------------------------------------------------------
    def _effective_config(self) -> AppConfig:
        """AppConfig clone with web-entered fronts applied."""
        s = self.settings
        if not s.fronts:
            return self.config
        new_ctp = dataclasses.replace(self.config.ctp,
                                      fronts=tuple(s.fronts), broker_id=s.broker_id)
        return dataclasses.replace(self.config, ctp=new_ctp)
