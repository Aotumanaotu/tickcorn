"""Fail on runtime files or recognizable secrets, printing paths only."""
import re
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
paths = subprocess.check_output(
    ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root
).decode().split("\0")
failures = set()
secret_patterns = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"LTAI[0-9A-Za-z]{12,}"),
    re.compile(r"gh[pousr]_\w{30,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"-----BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"https?://[^\s/]+:[^\s/]+@"),
]
for name in filter(None, paths):
    path = root / name
    if not path.is_file():
        continue
    parts = Path(name).parts
    if (parts[0] in ("data", ".venv", ".agents", ".codex") or
            Path(name).name.startswith(".env") and Path(name).name != ".env.example" or
            ".local." in name or name.endswith((".pem", ".key", ".so")) or
            name.startswith("third_party/ctp/")):
        failures.add(name)
    if path.stat().st_size > 2_000_000:
        continue
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
    if any(pattern.search(content) for pattern in secret_patterns):
        failures.add(name)
if failures:
    print("Review prohibited runtime files or potential secrets (values redacted):")
    print("\n".join(sorted(failures)))
    raise SystemExit(1)
print("Repository checks passed (path and pattern checks; not a complete secret audit).")
