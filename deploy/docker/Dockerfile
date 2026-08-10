# syntax=docker/dockerfile:1.7

# ---------- Stage 1: builder ----------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# System deps needed to build wheels (numpy, scikit-learn, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy project, install into an isolated prefix we can copy out cleanly.
COPY pyproject.toml MANIFEST.in README.md ./
COPY claw_soul ./claw_soul

# Install with the optional extras (tweepy) so the image is batteries-
# included. A plain `pip install claw_soul` still gets the lean core.
RUN pip install --prefix=/install --no-warn-script-location ".[all]"

# ---------- Stage 2: runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CLAWSOUL_HOME=/data \
    HOME=/data \
    PORT=7788

# Runtime deps only (ffmpeg for voice transcription support, ca-certs for outbound TLS)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Bring in the installed package + entry point
COPY --from=builder /install /usr/local

# Non-root user for the daemon
RUN useradd -m -u 10001 -d /home/claw claw \
    && mkdir -p /data \
    && chown -R claw:claw /data

COPY --chown=claw:claw deploy/fly/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER claw
WORKDIR /data

# /data is a Fly volume mount point — user state lives here forever
VOLUME ["/data"]

EXPOSE 7788

# tini reaps zombie children (subprocesses) properly
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
