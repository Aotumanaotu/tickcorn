"""Feishu briefing / monitor settings entered from the web panel.

Stored in a LOCAL, gitignored, 0600-permission JSON file inside the same
data volume the collector uses, so the separate `monitor` container can read
it read-only. The App Secret is never echoed back to the browser.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from app.common.exceptions import AppError
from app.common.private_files import write_private_json
from app.common.logging import get_logger

logger = get_logger("dashboard.monitor_settings")

MONITOR_SETTINGS_FILENAME = "monitor-settings.local.json"
RECEIVE_ID_TYPES = ("chat_id", "open_id", "user_id", "union_id", "email")
_TIME_RE = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")
_TZ_RE = re.compile(r"[A-Za-z0-9_+\-/]{1,64}")


def parse_times(value: Any) -> list[str]:
    if isinstance(value, str):
        items = value.replace("，", ",").split(",")
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise AppError("发送时间应为逗号分隔的 HH:MM")
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        if not _TIME_RE.fullmatch(text):
            raise AppError("发送时间格式应为 HH:MM（24 小时制）")
        if text not in out:
            out.append(text)
    if not out:
        raise AppError("至少需要一个发送时间")
    if len(out) > 8:
        raise AppError("发送时间最多 8 个")
    return sorted(out)


def bounded_text(value: str, limit: int, label: str) -> str:
    value = value.strip()
    if len(value.encode("utf-8")) > limit:
        raise AppError(f"{label} 超出长度限制（{limit} 字节）")
    if any(ord(c) < 32 for c in value):
        raise AppError(f"{label} 包含控制字符")
    return value


@dataclass
class MonitorSettings:
    enabled: bool = False
    feishu_app_id: str = ""
    feishu_app_secret: str = ""      # only overwritten when a new value is sent
    feishu_receive_id: str = ""
    feishu_receive_id_type: str = "chat_id"
    report_times: list[str] = field(default_factory=lambda: ["08:00", "12:30", "21:30"])
    alert_only: bool = False
    title: str = "玉米快照采集"
    tz: str = "Asia/Shanghai"
    test_request: int = 0

    def masked(self) -> dict[str, Any]:
        d = asdict(self)
        d["feishu_app_secret"] = "********" if self.feishu_app_secret else ""
        d["has_secret"] = bool(self.feishu_app_secret)
        return d


class MonitorSettingsStore:
    """Load/save monitor settings under <data_dir>/monitor-settings.local.json."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / MONITOR_SETTINGS_FILENAME

    def load(self) -> MonitorSettings:
        if not self.path.exists():
            return MonitorSettings()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            s = MonitorSettings()
            s.enabled = bool(raw.get("enabled", False))
            s.feishu_app_id = str(raw.get("feishu_app_id") or "")
            s.feishu_app_secret = str(raw.get("feishu_app_secret") or "")
            s.feishu_receive_id = str(raw.get("feishu_receive_id") or "")
            s.feishu_receive_id_type = str(raw.get("feishu_receive_id_type") or "chat_id")
            times = raw.get("report_times")
            if isinstance(times, list) and times:
                s.report_times = [str(t) for t in times]
            s.alert_only = bool(raw.get("alert_only", False))
            s.title = str(raw.get("title") or "玉米快照采集")
            s.tz = str(raw.get("tz") or "Asia/Shanghai")
            try:
                s.test_request = int(raw.get("test_request") or 0)
            except (TypeError, ValueError):
                s.test_request = 0
            return s
        except Exception:
            logger.exception("failed to read %s; using defaults", self.path)
            return MonitorSettings()

    def save(self, s: MonitorSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_private_json(self.path, asdict(s))


def apply_payload(s: MonitorSettings, payload: dict[str, Any]) -> MonitorSettings:
    """Validate and apply a partial update; empty secret keeps the old one."""
    if "enabled" in payload:
        if not isinstance(payload["enabled"], bool):
            raise AppError("启用简报应为布尔值")
        s.enabled = payload["enabled"]
    if "alert_only" in payload:
        if not isinstance(payload["alert_only"], bool):
            raise AppError("仅异常时发送应为布尔值")
        s.alert_only = payload["alert_only"]
    for key in ("feishu_app_id", "feishu_app_secret", "feishu_receive_id",
                "feishu_receive_id_type", "title"):
        if key in payload and not isinstance(payload[key], str):
            raise AppError("简报配置字段类型错误")
    if "feishu_app_id" in payload:
        s.feishu_app_id = bounded_text(payload["feishu_app_id"], 128, "App ID")
    if "clear_secret" in payload and payload["clear_secret"]:
        s.feishu_app_secret = ""
    elif payload.get("feishu_app_secret"):
        s.feishu_app_secret = bounded_text(payload["feishu_app_secret"], 256, "App Secret")
    if "feishu_receive_id" in payload:
        s.feishu_receive_id = bounded_text(payload["feishu_receive_id"], 256, "接收方 ID")
    if "feishu_receive_id_type" in payload:
        value = payload["feishu_receive_id_type"].strip()
        if value not in RECEIVE_ID_TYPES:
            raise AppError("接收方类型无效")
        s.feishu_receive_id_type = value
    if "report_times" in payload:
        s.report_times = parse_times(payload["report_times"])
    if "tz" in payload:
        value = payload["tz"].strip()
        if not _TZ_RE.fullmatch(value):
            raise AppError("时区名称无效")
        s.tz = value
    if "title" in payload:
        s.title = bounded_text(payload["title"], 40, "标题") or s.title
    return s
