# ── Stage 1: build ──────────────────────────────────────────────────────────
# gcc + libpq-dev only needed to compile asyncpg/C extensions. Never ships to prod.
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt



# ── Stage 2: runtime ─────────────────────────────────────────────────────────
# No gcc, no libpq-dev, no build toolchain in the final image.
FROM python:3.11-slim

WORKDIR /tunescope

# libpq5 = asyncpg runtime dep (not the -dev headers)
# --no-install-recommends on ffmpeg skips Mesa/X11/Wayland/GPU stack
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

COPY . .

ENV PYTHONPATH=/tunescope/app
ENV NUMBA_CACHE_DIR=/tmp/numba_cache

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]