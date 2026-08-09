# syntax=docker/dockerfile:1.7
#
# Stockfish is fetched here, at build time, from the pinned engine.lock.json.
# It is never committed and never lands in the wheel -- the repository stays
# source-only and the image is the thing that carries a verified binary.
#
# Target platform is linux/amd64: the pinned release assets are AVX2 x86-64
# builds. On Apple Silicon, build with --platform=linux/amd64 (emulated) or
# add an arm64 asset to the lockfile.

ARG PYTHON_VERSION=3.13
ARG ENGINE_PLATFORM=linux-x86-64


# --------------------------------------------------------------------------
# Stage 1: engine. Stdlib-only, so it runs before any dependency resolution.
# Cache key is engine.lock.json alone -- application code changes never force
# a re-download.
# --------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS engine
ARG ENGINE_PLATFORM
WORKDIR /build

COPY engine.lock.json ./
COPY src/gtochess/__init__.py src/gtochess/layout.py ./src/gtochess/
COPY src/gtochess/tools/__init__.py src/gtochess/tools/fetch_stockfish.py \
     ./src/gtochess/tools/

ENV PYTHONPATH=/build/src
# No --record: a lockfile without a committed checksum must fail the build
# rather than silently trusting whatever the network returned.
RUN python -m gtochess.tools.fetch_stockfish \
        --platform "${ENGINE_PLATFORM}" \
        --lockfile /build/engine.lock.json \
        --dest /opt/stockfish \
 && printf 'uci\nquit\n' | /opt/stockfish/stockfish > /dev/null


# --------------------------------------------------------------------------
# Stage 2: python dependencies + application, installed into a relocatable venv.
# --------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS python-build
WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# Dependency layer: only invalidated by pyproject.toml, not by source edits.
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    mkdir -p src/gtochess && touch src/gtochess/__init__.py \
 && pip install --upgrade pip \
 && pip install .

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/pip pip install --no-deps .


# --------------------------------------------------------------------------
# Stage 3: runtime. No compilers, no pip cache, no source tree.
# --------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

# Stockfish release builds link libstdc++/libgcc dynamically. A missing shared
# object here surfaces as a silent engine start failure at request time, so it
# is installed explicitly rather than assumed present in the base image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libstdc++6 \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 gtochess

COPY --from=engine  /opt/stockfish /opt/stockfish
COPY --from=python-build /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GTOCHESS_ENGINE_PATH=/opt/stockfish/stockfish

USER gtochess
WORKDIR /home/gtochess

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

CMD ["uvicorn", "gtochess.api.main:app", "--host", "0.0.0.0", "--port", "8000"]


# --------------------------------------------------------------------------
# Stage 4: dev. Same verified engine, plus dev tooling and an editable install
# so the compose bind-mount gives live reload.
# --------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS dev

RUN apt-get update \
 && apt-get install -y --no-install-recommends libstdc++6 git \
 && rm -rf /var/lib/apt/lists/*

COPY --from=engine /opt/stockfish /opt/stockfish

WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    GTOCHESS_ENGINE_PATH=/opt/stockfish/stockfish

COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    mkdir -p src/gtochess && touch src/gtochess/__init__.py \
 && pip install --upgrade pip && pip install -e ".[dev]"

COPY . .
EXPOSE 8000
CMD ["uvicorn", "gtochess.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
