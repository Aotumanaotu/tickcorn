"""Control-frame payload validation for the gateway process.

Pure logic, no I/O: the publisher hands raw control payloads here before
the runtime acts on them. Invalid input raises ValueError with Chinese
messages (legacy dashboard control style) so the API can surface them
directly to operators via the ack error field.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

_INSTRUMENT_RE = re.compile(r"[A-Z]{1,8}[0-9]{3,4}")
_MAX_INSTRUMENTS = 50
_MAX_FRONTS = 10


def _require_str(payload: dict, key: str, label: str, *, max_len: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}不能为空")
    value = value.strip()
    if len(value.encode("utf-8")) > max_len or any(ord(c) < 32 for c in value):
        raise ValueError(f"{label}超出长度限制或包含控制字符")
    return value


def _validate_instruments(raw: Any) -> list[str]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("合约列表不能为空")
    if len(raw) > _MAX_INSTRUMENTS:
        raise ValueError(f"一次最多订阅 {_MAX_INSTRUMENTS} 个合约")
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError("合约代码应为文本")
        inst = item.strip().upper()
        if not _INSTRUMENT_RE.fullmatch(inst):
            raise ValueError(f"合约格式无效: {item!r}")
        if inst not in out:
            out.append(inst)
    return out


def validate_connect_payload(payload: Any) -> dict:
    """Validate a connect control payload; returns normalized fields.

    Raises ValueError with a Chinese message on invalid input.
    """
    if not isinstance(payload, dict):
        raise ValueError("控制消息格式错误")
    fronts = payload.get("fronts")
    if not isinstance(fronts, list) or not fronts:
        raise ValueError("前置地址不能为空")
    if len(fronts) > _MAX_FRONTS:
        raise ValueError(f"最多配置 {_MAX_FRONTS} 个行情前置")
    clean_fronts: list[str] = []
    for front in fronts:
        if not isinstance(front, str) or not front.strip():
            raise ValueError("前置地址格式应为 tcp://主机:端口")
        front = front.strip()
        try:
            u = urlsplit(front)
            valid = (u.scheme == "tcp" and u.hostname and u.port
                     and not u.username and not u.password and not u.path
                     and not u.query and not u.fragment
                     and not any(c.isspace() for c in front))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(
                "前置地址格式应为 tcp://主机:端口，不包含账号或参数")
        if front not in clean_fronts:
            clean_fronts.append(front)
    broker_id = _require_str(payload, "broker_id", "BrokerID", max_len=10)
    user = _require_str(payload, "user", "账号", max_len=15)
    password = payload.get("password")
    if not isinstance(password, str) or not password:
        raise ValueError("密码不能为空")
    if len(password.encode("utf-8")) > 40 or any(ord(c) < 32 for c in password):
        raise ValueError("密码超出长度限制或包含控制字符")
    instruments = _validate_instruments(payload.get("instruments"))
    remember = payload.get("remember", False)
    if not isinstance(remember, bool):
        raise ValueError("记住密码应为布尔值")
    return {
        "fronts": clean_fronts,
        "broker_id": broker_id,
        "user": user,
        "password": password,
        "instruments": instruments,
        "remember": remember,
    }


def validate_subscribe_payload(payload: Any) -> dict:
    """Validate a subscribe/unsubscribe control payload."""
    if not isinstance(payload, dict):
        raise ValueError("控制消息格式错误")
    return {"instruments": _validate_instruments(payload.get("instruments"))}


def mask_user(user: str) -> str:
    """Mask an account id for logs / metadata (keep first char only)."""
    if not user:
        return ""
    return user[:1] + "***"
