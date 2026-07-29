# FiftyMoves

Knowledge graph and assessment tool for chess openings, built from lichess.org and
chess.com games. Design notes are in [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md).

## Repository layout

```
backend/          Python service: ingest, analysis, engine, API
frontend/         Vue client
docs/             design notes
docker-compose.yml
```

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
`backend/engine.lock.json`. It is not committed to the repository.

```
docker compose up --build
curl localhost:8010/health
```

Compose brings up the API on 8010, Postgres on 5432 and Redis on 6379. The API port
defaults to 8010 because 8000 is often taken, and `FIFTYMOVES_API_PORT` overrides it.

## Running the backend locally

```
cd backend
python -m venv .venv
.venv/Scripts/activate
pip install -e ".[dev]"
python -m fiftymoves.tools.fetch_stockfish
cp .env.example .env
uvicorn fiftymoves.api.main:app --reload
```

On macOS and Linux, activate with `source .venv/bin/activate`.

The fetch step downloads the pinned engine into `backend/vendor/stockfish/<platform>/`.
It is required. A Stockfish already on `PATH` is never used, because evaluations feed
cache keys and test assertions, and an engine of unknown provenance would change them.

## Importing a player

```
cd backend
python -m fiftymoves.tools.ingest_lichess <username> --max 400 --out data
```

Prints the repertoire summary and writes parsed games and decision positions to
`data/`. Export is unauthenticated by default at 20 games per second. Setting
`FIFTYMOVES_LICHESS_TOKEN` raises that to 60 for the token holder's own games.

## Tests

```
cd backend
pytest
pytest -m "not needs_engine"
```

The second form skips the tests that need a provisioned engine.

## Configuration

Settings are read from environment variables prefixed `FIFTYMOVES_`, or from
`backend/.env`. Every setting and its default is listed in
[`backend/.env.example`](backend/.env.example), covering engine depth, the thresholds
that decide what counts as a real choice, position selection budgets, trait sample
floors, and the lichess client.

## Backend layout

```
backend/
  engine.lock.json        pinned engine version, assets, digests
  Dockerfile              engine, python-build, runtime and dev stages
  src/fiftymoves/
    layout.py             paths and platform slug, stdlib only
    config.py             settings and engine resolution
    api/                  FastAPI app
    domain/               position identity, games, repertoire, profile, flaws
    engine/               EngineProvider protocol, Stockfish over UCI, ReferenceEngine
    analysis/             ablation sensitivity, plan fingerprint, eval landscape,
                          decisions, profile, selection, flaws
    ingest/               lichess client, game parsing, repertoire building
    tools/                fetch_stockfish, ingest_lichess
  tests/
```

`ReferenceEngine` is a material search at fixed depth. It lets the test suite run with
no engine binary present.
