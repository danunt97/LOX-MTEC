# LOX-MTEC - M-TEC Energybutler -> Loxone Miniserver
#
# Build (from the repository root):
#   docker build -f docker/Dockerfile -t loxmtec .
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --upgrade pip wheel \
 && python -m pip wheel --wheel-dir /wheels .


FROM python:3.12-slim

LABEL org.opencontainers.image.title="LOX-MTEC" \
      org.opencontainers.image.description="M-TEC Energybutler (Modbus) to Loxone Miniserver bridge with web GUI and watchdog" \
      org.opencontainers.image.source="https://github.com/danunt97/LOX-MTEC" \
      org.opencontainers.image.licenses="GPL-3.0"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    LOXMTEC_CONFIG=/config/config.yaml \
    LOXMTEC_WEB_PORT=8080

RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin loxmtec \
 && mkdir -p /config \
 && chown loxmtec:loxmtec /config

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels MTECmqtt \
 && rm -rf /wheels

COPY docker/healthcheck.py /usr/local/bin/healthcheck.py

USER loxmtec
WORKDIR /config
VOLUME ["/config"]
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
    CMD ["python", "/usr/local/bin/healthcheck.py"]

ENTRYPOINT ["python", "-m", "loxmtec"]
