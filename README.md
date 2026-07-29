# FiftyMoves

Knowledge graph and assessment tool for chess openings, built from lichess.org and
chess.com games. Design notes are in [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md).

## Stack

| Part | Choice |
|---|---|
| API | FastAPI, served by uvicorn |
| Analysis | Stockfish over UCI, driven by python-chess |
| Store | Postgres with pgvector |
| Cache | Redis |
| Models | Pydantic v2 |

Requires Python 3.12 or newer.

## Running with Docker

Stockfish is fetched during the image build and checked against a digest in
`engine.lock.json`. It is not committed to the repository.

```
docker compose up --build
curl localhost:8010/health
```

Compose brings up the API on 8010, Postgres on 5432 and Redis on 6379. The API port
defaults to 8010 because 8000 is often taken, and `FIFTYMOVES_API_PORT` overrides it.

## Running locally

```
python -m venv .venv
.venv/Scripts/activate
pip install -e ".[dev]"
python -m fiftymoves.tools.fetch_stockfish
uvicorn fiftymoves.api.main:app --reload
```

On macOS and Linux, activate with `source .venv/bin/activate`.

The fetch step downloads the pinned engine into `vendor/stockfish/<platform>/`. It is
required. A Stockfish already on `PATH` is never used, because evaluations feed cache
keys and test assertions, and an engine of unknown provenance would change them.

## Tests

```
pytest
pytest -m "not needs_engine"
```

The second form skips the tests that need a provisioned engine.

## Configuration

Settings are read from environment variables prefixed `FIFTYMOVES_`, or from a `.env`
file in the working directory.

| Variable | Purpose |
|---|---|
| `FIFTYMOVES_ENGINE_PATH` | Path to the engine binary. Falls back to `/opt/stockfish` then `vendor/stockfish/<platform>` |
| `FIFTYMOVES_PIPELINE_VERSION` | Version segment in cache keys. Bumping it rotates every cached explanation |

## Layout

```
engine.lock.json          pinned engine version, assets, digests
Dockerfile                engine, python-build, runtime and dev stages
docker-compose.yml        api, postgres with pgvector, redis
src/fiftymoves/
  layout.py               paths and platform slug, stdlib only
  config.py               settings and engine resolution
  api/                    FastAPI app
  domain/                 position identity for standard and 960, models
  engine/                 EngineProvider protocol, Stockfish over UCI, ReferenceEngine
  analysis/               ablation sensitivity, plan fingerprint, eval landscape
  tools/                  fetch_stockfish
tests/
```

`ReferenceEngine` is a material search at fixed depth. It lets the test suite run with
no engine binary present.
