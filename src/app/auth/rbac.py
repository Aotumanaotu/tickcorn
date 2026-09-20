"""Role-based access control: roles, permissions and their mapping.

Permissions are coarse-grained capability flags; every user carries exactly
one role and inherits that role's permission set. ``role_has`` accepts the
raw string stored on ``users.role`` so callers never need to coerce.
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    """User roles, ordered from least to most privileged."""

    ADMIN = "ADMIN"
    RESEARCHER = "RESEARCHER"
    TRADER = "TRADER"
    VIEWER = "VIEWER"


class Permission(str, Enum):
    """Discrete capabilities checked by API dependencies."""

    VIEW_MARKET = "VIEW_MARKET"
    RUN_ANALYSIS = "RUN_ANALYSIS"
    GENERATE_REPORT = "GENERATE_REPORT"
    RUN_REPLAY = "RUN_REPLAY"
    EDIT_STRATEGY = "EDIT_STRATEGY"
    RUN_BACKTEST = "RUN_BACKTEST"
    SUBMIT_ORDER = "SUBMIT_ORDER"
    ENABLE_AUTO_TRADING = "ENABLE_AUTO_TRADING"
    MANAGE_MODEL = "MANAGE_MODEL"
    MANAGE_USER = "MANAGE_USER"


_RESEARCHER_EXTRA = frozenset({
    Permission.RUN_ANALYSIS,
    Permission.GENERATE_REPORT,
    Permission.EDIT_STRATEGY,
    Permission.RUN_BACKTEST,
    Permission.MANAGE_MODEL,
})

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset({Permission.VIEW_MARKET, Permission.RUN_REPLAY}),
    Role.RESEARCHER: frozenset({Permission.VIEW_MARKET, Permission.RUN_REPLAY})
    | _RESEARCHER_EXTRA,
    Role.TRADER: frozenset({Permission.VIEW_MARKET, Permission.RUN_REPLAY})
    | _RESEARCHER_EXTRA | {Permission.SUBMIT_ORDER},
    Role.ADMIN: frozenset(Permission),
}


def _coerce_role(role: str) -> Role | None:
    try:
        return Role(str(role).strip().upper())
    except ValueError:
        return None


def role_has(role: str, perm: Permission) -> bool:
    """True when ``role`` (raw string from the DB) grants ``perm``."""
    resolved = _coerce_role(role)
    if resolved is None:
        return False
    return perm in ROLE_PERMISSIONS[resolved]


def permissions_of(role: str) -> list[str]:
    """Permission names granted by ``role`` (empty for unknown roles)."""
    resolved = _coerce_role(role)
    if resolved is None:
        return []
    return sorted(p.value for p in ROLE_PERMISSIONS[resolved])
