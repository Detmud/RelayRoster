# RelayRoster: local fleet management for Shelly Gen2+ devices (read-only, write functions only with a switch in Settings)

# Stage 1: fetch Tabler CSS from the official npm package, version and SHA-256 are pinned in tools/fetch_vendor.py
FROM python:3.15-slim AS vendor
WORKDIR /build
COPY tools/fetch_vendor.py .
RUN python fetch_vendor.py /build/vendor

# Stage 2: runtime
FROM python:3.15-slim

ARG VERSION=dev
LABEL org.opencontainers.image.title="RelayRoster" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.description="Local fleet management for Shelly Gen2+ devices: drift, peer comparison, firmware, config backups" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data \
    TZ=UTC

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && useradd --system --uid 10003 --home-dir /srv --shell /usr/sbin/nologin relayroster \
 && mkdir -p /data && chown relayroster:relayroster /data

COPY app ./app
COPY LICENSE THIRD_PARTY_NOTICES.md ./
COPY --from=vendor /build/vendor ./app/static/vendor

USER relayroster
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--no-server-header"]
