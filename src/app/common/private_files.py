"""Owner-only runtime files; never put operational secrets in source config."""

import json
import os
import secrets
import tempfile
from pathlib import Path


def write_private_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".settings-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def dashboard_token(data_dir: Path) -> str:
    path = data_dir / "dashboard-token.local.json"
    if not path.exists():
        write_private_json(path, {"token": secrets.token_urlsafe(32)})
    os.chmod(path, 0o600)
    token = json.loads(path.read_text(encoding="utf-8"))["token"]
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError("Invalid dashboard token file; restore or rotate it locally")
    return token


class ProcessLock:
    """Prevent two processes from controlling the same runtime directory."""

    def __init__(self, path: Path):
        import fcntl
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = path.open("a")
        try:
            fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise RuntimeError("该数据目录已被另一个进程使用") from None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.stream.close()
