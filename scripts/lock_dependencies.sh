#!/usr/bin/env bash
# Run in a Python 3.10 / Linux x86_64 virtual environment after dependency review.
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
python -c 'import sys, platform; assert sys.version_info[:2] == (3, 10) and platform.machine() == "x86_64" and sys.platform == "linux"'
REPORT_PATH="$(mktemp)"
trap 'rm -f "$REPORT_PATH"' EXIT
python -m pip install --dry-run --ignore-installed --report "$REPORT_PATH" -r requirements.in
python - "$REPORT_PATH" <<'PY'
import json, sys
from pathlib import Path
report = json.loads(Path(sys.argv[1]).read_text())
rows = ['# Python 3.10 / Linux amd64. Generated from requirements.in; contains no registry URLs.']
for item in sorted(report['install'], key=lambda x: x['metadata']['name'].lower()):
    meta = item['metadata']
    digest = item['download_info']['archive_info']['hashes']['sha256']
    rows.append(f"{meta['name']}=={meta['version']} --hash=sha256:{digest}")
Path('requirements.lock').write_text('\n'.join(rows) + '\n')
PY
