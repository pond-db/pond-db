FROM python:3.12-slim AS builder

WORKDIR /build

COPY pyproject.toml README.md ./
COPY src/ src/

RUN pip install --no-cache-dir --prefix=/install .

# --- runtime stage ---
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="PondDB" \
      org.opencontainers.image.description="Lightweight self-hosted DuckDB compute platform"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=builder /install /usr/local

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --no-create-home --shell /bin/false appuser \
    && mkdir -p /app/data \
    && chown appuser /app/data

USER appuser

EXPOSE 8432

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8432/health || exit 1

CMD ["uvicorn", "ponddb.app:app", "--host", "0.0.0.0", "--port", "8432", "--log-level", "warning"]
