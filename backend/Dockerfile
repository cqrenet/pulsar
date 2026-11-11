# Pinned by digest; Dependabot proposes updates.
FROM python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535

# Bake the version into the image at build time
ARG VERSION=unknown
ENV VERSION=${VERSION}

# Security: run as non-root. Fixed UID/GID so mounted secrets can be owned by it (chown 10001:10001).
RUN groupadd -r -g 10001 pulsar && useradd -r -u 10001 -g pulsar pulsar

WORKDIR /app

# Debian security fixes released after the base image was built (CI's Trivy scan fails the build otherwise)
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

# Install dependencies first for layer caching
COPY requirements.txt .
# Hash-locked: every package must match requirements.txt exactly, or the build fails.
# setuptools ships with the base image but nothing needs it at runtime; it only adds vulnerable vendored code.
RUN pip install --no-cache-dir --require-hashes -r requirements.txt && pip uninstall -y setuptools

# Copy application code
COPY . .

# Create directories for potential volume mounts and fix permissions
RUN mkdir -p /app/data && chown -R pulsar:pulsar /app

USER pulsar

# Single worker — intentional.
#
# PULSAR is async (FastAPI + uvicorn): a single worker handles all concurrency
# via the event loop, making multiple workers unnecessary for an I/O-bound app.
#
# More importantly, the MCP SSE transport stores sessions in-memory per process.
# Multiple workers would round-robin POST /mcp/messages/ across processes,
# causing "session not found" 404s when a request lands on a different worker
# than the one that opened the SSE stream.  A single worker avoids this entirely
# without requiring Redis-backed session storage.
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

CMD ["gunicorn", "main:app", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "--workers", "1", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-"]
