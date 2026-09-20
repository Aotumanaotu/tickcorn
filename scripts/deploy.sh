#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
case "${1:-}" in ""|--check) ;; *) echo 'Usage: bash scripts/deploy.sh [--check]' >&2; exit 2;; esac
[[ "$(uname -m)" == x86_64 ]] || { echo 'Requires Linux x86_64 (CTP SDK architecture).' >&2; exit 1; }
for sdk_file in include/ThostFtdcMdApi.h include/ThostFtdcUserApiDataType.h include/ThostFtdcUserApiStruct.h lib/thostmduserapi_se.so; do
  [[ -f "third_party/ctp/v6.7.13_linux64/$sdk_file" ]] || {
    echo "Missing official SDK file: $sdk_file; see third_party/README.md" >&2; exit 1;
  }
done
docker version >/dev/null
docker compose version
docker compose config --quiet
if [[ "${1:-}" == --check ]]; then
  echo 'Preflight passed. This does not validate a market account or SDK usage rights.'
  exit 0
fi
docker compose up -d --build --wait --wait-timeout 180
echo 'Deployed. Configure .env (admin bootstrap), then use the SSH tunnel steps in docs/deployment.md and log in at http://127.0.0.1:8800.'
