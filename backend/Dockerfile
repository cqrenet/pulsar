FROM python:3.11-slim

# Bake the version into the image at build time
ARG VERSION=unknown
ENV VERSION=${VERSION}

# Security: run as non-root
RUN groupadd -r pulsar && useradd -r -g pulsar pulsar

WORKDIR /app

# Install dependencies first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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
