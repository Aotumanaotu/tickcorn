"""Real-time web dashboard (no external dependencies, no CDN).

Modes:
    * embedded  -- started inside `app collect` (live view only)
    * control   -- `app serve`: adds the settings/control API so SimNow
      credentials, front addresses, instruments and start/stop can be
      managed from the web page (operational settings live in
      data/settings.local.json, 0600, gitignored).
    * standalone-- `app dashboard`: tails the live JSONL feed file.

Optional access token (recommended for cloud deployments): all requests
must then carry ?token=... or the X-Auth-Token header.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

from app.collector.live import JsonlTailSource, LiveState
from app.common.exceptions import AppError
from app.common.logging import get_logger

logger = get_logger("dashboard.server")

_STATIC_DIR = Path(__file__).resolve().parent / "static"


class DashboardServer:
    def __init__(self, state: LiveState, host: str = "127.0.0.1",
                 port: int = 8800, refresh_ms: int = 1000,
                 tail_source: Optional[JsonlTailSource] = None,
                 manager=None, auth_token: Optional[str] = None):
        self.state = state
        self.host = host
        self.port = port
        self.refresh_ms = refresh_ms
        self.tail_source = tail_source
        self.manager = manager
        self.auth_token = auth_token
        self._stop = threading.Event()
        self._httpd: Optional[ThreadingHTTPServer] = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # silence default logging
                pass

            # ---------------- helpers ----------------
            def _authorized(self) -> bool:
                token = outer.auth_token
                if not token:
                    return True
                supplied = None
                if self.path.startswith("/"):
                    from urllib.parse import urlparse, parse_qs
                    q = parse_qs(urlparse(self.path).query)
                    supplied = (q.get("token") or [None])[0]
                if not supplied:
                    supplied = self.headers.get("X-Auth-Token")
                return supplied == token

            def _deny(self):
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "unauthorized"}')

            def _json(self, obj: Any, status: int = 200):
                payload = json.dumps(obj, ensure_ascii=False,
                                     default=str).encode()
                self.send_response(status)
                self.send_header("Content-Type",
                                 "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)

            def _read_json(self) -> dict:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0:
                    return {}          # empty body (e.g. start/stop buttons)
                if length > 1 << 20:
                    raise AppError("invalid request body")
                return json.loads(self.rfile.read(length).decode("utf-8"))

            # ---------------- routing ----------------
            def do_GET(self):  # noqa: N802
                path = self.path.split("?")[0]
                if not self._authorized():
                    return self._deny()
                if path == "/api/state":
                    outer._tail_once()
                    return self._json(outer._state_payload())
                if path == "/api/settings" and outer.manager is not None:
                    resp = outer.manager.settings.masked()
                    resp["yaml_fronts"] = list(outer.config_fronts())
                    resp["running"] = outer.manager.running
                    return self._json(resp)
                if path in ("/", "/index.html"):
                    html = (outer._static_dir / "index.html").read_text(
                        encoding="utf-8").replace("__REFRESH_MS__",
                                                  str(outer.refresh_ms))
                    self.send_response(200)
                    self.send_header("Content-Type",
                                     "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(html.encode())
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):  # noqa: N802
                path = self.path.split("?")[0]
                if not self._authorized():
                    return self._deny()
                if outer.manager is None:
                    return self._json({"error": "control API not enabled "
                                       "(use `python -m app serve`)"}, 400)
                try:
                    body = self._read_json()
                    if path == "/api/settings":
                        return self._json(outer.manager.update_settings(body))
                    if path == "/api/collect/start":
                        return self._json(outer.manager.start())
                    if path == "/api/collect/stop":
                        return self._json(outer.manager.stop())
                    return self._json({"error": "unknown endpoint"}, 404)
                except AppError as e:
                    return self._json({"error": str(e)}, 400)
                except Exception as e:  # noqa: BLE001
                    logger.exception("control API error")  # body NOT logged
                    return self._json(
                        {"error": f"{type(e).__name__}: {e}"}, 500)

        self._handler = Handler
        self._static_dir = _STATIC_DIR
        # Bind eagerly so port=0 (ephemeral) works and server_address is
        # available before serve_forever() is called.
        self._httpd = ThreadingHTTPServer((self.host, self.port), self._handler)
        self.port = self._httpd.server_address[1]

    # ------------------------------------------------------------------
    def config_fronts(self) -> list[str]:
        try:
            return list(self.manager.config.ctp.fronts)
        except Exception:
            return []

    def _state_payload(self) -> dict:
        if self.manager is not None:
            live = self.manager.get_live_state()
            payload = (live.to_json() if live is not None
                       else self.state.to_json())
            payload["manager"] = self.manager.status()
            return payload
        return self.state.to_json()

    def _tail_once(self) -> None:
        if self.tail_source is not None:
            try:
                self.tail_source.poll()
            except Exception:
                logger.exception("live feed tail failed")

    def serve_forever(self) -> None:
        logger.info("dashboard listening on http://%s:%d", self.host, self.port)
        try:
            while not self._stop.is_set():
                self._httpd.timeout = 0.5
                self._httpd.handle_request()
        finally:
            self._httpd.server_close()

    def shutdown(self) -> None:
        self._stop.set()
