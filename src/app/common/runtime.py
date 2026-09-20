"""Runtime settings for the web stack (api / gateway / ws / auth).

Deliberately separate from AppConfig: the data-plane configuration is
loaded from YAML (deterministic, hash-stamped for provenance) while the
service-plane configuration is environment-driven (12-factor, docker
friendly). Env always wins so containers need no mounted config to boot.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from app.common.config import AppConfig
from app.common.private_files import write_private_json


@dataclass(frozen=True)
class WebSettings:
    database_url: str
    jwt_secret: str
    access_token_ttl_s: int = 1800            # 30 minutes
    refresh_token_ttl_days: int = 14
    gateway_socket: Path = Path("gateway.sock")
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: tuple[str, ...] = field(default_factory=tuple)
    environment: str = "dev"                  # dev | production

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def cookie_secure(self) -> bool:
        return self.is_production


def load_web_settings(config: AppConfig) -> WebSettings:
    """Env-driven settings with safe dev defaults under the data dir."""
    data_dir = Path(config.paths.data_dir)

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        database_url = f"sqlite+aiosqlite:///{data_dir / 'app.db'}"

    jwt_secret = os.environ.get("JWT_SECRET", "").strip()
    if not jwt_secret:
        secret_file = data_dir / "jwt-secret.local.json"
        if secret_file.exists():
            try:
                import json
                jwt_secret = json.loads(secret_file.read_text())["secret"]
            except (OSError, ValueError, KeyError):
                jwt_secret = ""
        if not jwt_secret:
            jwt_secret = secrets.token_urlsafe(48)
            data_dir.mkdir(parents=True, exist_ok=True)
            write_private_json(secret_file, {"secret": jwt_secret})

    socket_env = os.environ.get("GATEWAY_SOCKET", "").strip()
    gateway_socket = Path(socket_env) if socket_env else data_dir / "gateway.sock"

    cors = tuple(x.strip() for x in os.environ.get("CORS_ORIGINS", "").split(",")
                 if x.strip())

    return WebSettings(
        database_url=database_url,
        jwt_secret=jwt_secret,
        access_token_ttl_s=int(os.environ.get("ACCESS_TOKEN_TTL_S", "1800")),
        refresh_token_ttl_days=int(os.environ.get("REFRESH_TOKEN_TTL_DAYS", "14")),
        gateway_socket=gateway_socket,
        api_host=os.environ.get("API_HOST", "127.0.0.1"),
        api_port=int(os.environ.get("API_PORT", "8000")),
        cors_origins=cors,
        environment=os.environ.get("APP_ENV", "dev"),
    )
