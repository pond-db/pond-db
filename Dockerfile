FROM python:3.12-slim AS builder

WORKDIR /build

COPY pyproject.toml README.md ./
COPY src/ src/

RUN pip install --no-cache-dir --prefix=/install .

# --- runtime stage ---
FROM python:3.12-slim AS runtime

WORKDIR /app

COPY --from=builder /install /usr/local
COPY src/ src/

RUN useradd --no-create-home --shell /bin/false appuser
USER appuser

EXPOSE 8432

CMD ["uvicorn", "ponddb.app:app", "--host", "0.0.0.0", "--port", "8432"]
