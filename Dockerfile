# Corn futures 1-tick microstructure system -- Phase 1
# Build:  docker compose build
# Run:    docker compose up -d
# Panel:  http://<host>:8800/?token=<DASHBOARD_TOKEN>
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

# g++ needed to compile the CTP ctypes shim against the bundled CTP headers
RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY config ./config
COPY src ./src
COPY third_party ./third_party
COPY scripts ./scripts

RUN pip install --no-cache-dir -e . \
    && bash scripts/build_ctp_shim.sh \
    && python -m app selftest

VOLUME ["/app/data"]
EXPOSE 8800

HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os,sys; \
t=os.environ.get('DASHBOARD_TOKEN',''); \
u='http://127.0.0.1:8800/api/state'+('?token='+t if t else ''); \
sys.exit(0 if urllib.request.urlopen(u,timeout=5).status==200 else 1)" \
    || exit 1

CMD ["python", "-m", "app", "serve", "--host", "0.0.0.0", "--port", "8800"]
