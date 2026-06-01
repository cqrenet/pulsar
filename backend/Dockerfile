FROM python:3.11-slim

# Bake the version into the image at build time
ARG VERSION=unknown
ENV VERSION=${VERSION}

# Security: run as non-root
RUN groupadd -r aoc && useradd -r -g aoc aoc

WORKDIR /app

# Install dependencies first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create directories for potential volume mounts and fix permissions
RUN mkdir -p /app/data && chown -R aoc:aoc /app

USER aoc

# Production: use gunicorn with uvicorn workers
# Workers = 2-4 x $NUM_CORES; keep it conservative for containerised workloads
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

CMD ["gunicorn", "main:app", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-"]
