# The CTP MdApi SDK must be supplied locally under its own license.
FROM python:3.10-slim-bookworm AS build
RUN test "$(uname -m)" = x86_64 || (echo "CTP SDK requires Linux x86_64" >&2; exit 1)
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends g++ \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.lock ./
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir --require-hashes -r requirements.lock \
    && /opt/venv/bin/pip install --no-cache-dir setuptools==80.9.0 wheel==0.45.1
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY scripts/build_ctp_shim.sh ./scripts/build_ctp_shim.sh
COPY third_party/ctp/v6.7.13_linux64 ./third_party/ctp/v6.7.13_linux64
RUN bash scripts/build_ctp_shim.sh \
    && /opt/venv/bin/pip install --no-cache-dir --no-deps --no-build-isolation -e .

FROM python:3.10-slim-bookworm
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends libstdc++6 fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    MPLCONFIGDIR=/tmp/matplotlib TZ=Asia/Shanghai
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY --from=build /app/src /app/src
COPY --from=build /app/config /app/config
COPY --from=build /app/pyproject.toml /app/README.md ./
RUN mkdir -p /app/data && chown app:app /app/data && chmod 700 /app/data
USER app
RUN python -m app selftest
VOLUME ["/app/data"]
EXPOSE 8800
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8800/healthz', timeout=3).read()"
CMD ["python", "-m", "app", "serve", "--host", "0.0.0.0", "--port", "8800"]
