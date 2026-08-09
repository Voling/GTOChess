# GTO Chess — Implementation Plan

## 1. Scope

An **assessment and visualisation** tool for chess opening knowledge, backed by a
position knowledge graph.

**Not** a coach. Chess.com owns interactive teaching and user attention spans are
short. The LLM's entire job is: *the user expanded or moved to a node — say what
matters here, briefly, and correctly*. Everything else is graph navigation and
mastery measurement.

Non-goals: chatbot interface, lesson sequencing, puzzle rush, engine-vs-human play.

---

## 2. Layered architecture

| Layer | Produced by | Available in Chess960? |
|---|---|---|
| Position identity (Zobrist-equivalent, 960-safe) | deterministic | ✅ |
| Engine report (multi-PV, evals) | Stockfish | ✅ |
| Ablation sensitivity | Stockfish perturbations | ✅ |
| Plan fingerprint | PV abstraction | ✅ |
| Structural embedding | feature vector → pgvector | ✅ |
| Empirical stats | Lichess explorer / masters | partial (960 games exist) |
| Opening names / ECO | `lichess-org/chess-openings` (CC0) | ❌ |
| Literature | user-supplied, public domain | ❌ |
| Precedent (validated neighbours) | concept propagation | ✅ |

The tiers above the line are **intrinsic** — computed from the position itself.
This is why Chess960 works without special-casing: theory tiers are simply
absent, and because every LLM claim must cite an evidence ID, a missing tier
produces *fewer claims* rather than invented ones. 960 is our honest test that
the engine-reasoning layer isn't leaning on memorised opening names.

---

## 3. Storage and caching

Three tiers, each doing what it is actually good at.

**Postgres + pgvector — system of record. Never expires.**
Position DAG, concept nodes, negative (failed-transfer) edges, eval summaries,
plan fingerprints, structural embeddings, user mastery state, ingest cursors.
Node expansion is a *query* ("children of this position with mastery, eval and
concept tags"), and similarity search is a query — neither has an S3 equivalent.

**Redis — hot cache. TTL minutes to hours.**
Serialised node-expansion payloads keyed by `(position, user, depth)`. This is
what actually serves the click.

**S3 + CloudFront — cold blobs, aggressive lifecycle.**
Rendered explanation bundles, full multi-PV dumps, raw Lichess PGN, Batch API
input/output. Lifecycle rules apply **only to regenerable artifacts**.

### Cache key strategy

Explanations are identical across all users for a given position, which makes
them ideal CDN content. Keys are content-addressed with the pipeline version
baked in:

```
explanations/{variant}/{position_digest}/{pipeline_version}.json
```

Immutable by construction — we never invalidate. A prompt change, model change,
or threshold change bumps `pipeline_version` (see `config.Settings`), new objects
are written, and old ones age out on lifecycle. No CloudFront purge, ever.

---

## 4. Graph UI — the Obsidian model

Obsidian's graph view is the right interaction model here, and it is worth
copying closely rather than reinventing. What it does:

### 4.1 What Obsidian actually provides

**Two views.** A *global graph* (every note, force-directed) and a *local graph*
(the neighbourhood of the note you are currently in). The local graph is the one
people actually use for navigation; the global graph is for orientation and for
spotting structure.

**Filters panel.** A search query using the app's full search syntax, plus
toggles: show tags, show attachments, existing files only, show orphans. Anything
not matching is removed from the graph entirely.

**Groups panel.** Add a group → give it a search query → pick a colour. Multiple
groups stack, and nodes matching a query take that group's colour. This is the
mechanism that turns a monochrome hairball into something readable.

**Display panel.** Arrows (show link direction), *text fade threshold* (zoom
level at which labels appear — the graph is dots when zoomed out and labelled
when zoomed in), node size scaling, link thickness, and an animate/timelapse
control that replays the vault's growth over time.

**Forces panel.** Center force, repel force, link force, link distance — all
exposed as live sliders. Users tune the layout to their own taste rather than
accepting one hardcoded physics config.

**Local graph extras.** A *depth* slider (1–5 hops), and toggles for incoming
links, outgoing links, and neighbour links.

**Interactions.** Scroll to zoom, drag to pan, drag a node to reposition it,
hover a node for a preview popover, click to open. Node size scales with inbound
link count, so important notes are visibly bigger.

### 4.2 How it maps onto GTO Chess

| Obsidian | GTO Chess |
|---|---|
| Local graph, depth 1–5 | **Repertoire DAG around the current position**, depth = plies. Default 2–3. This *is* the primary navigation surface. |
| Global graph | **Concept graph** — structures, plans, motifs. Force-directed, opt-in. |
| Node size = inbound links | Node size = frequency in the user's own games |
| Groups (query → colour) | Saved queries → colour: `mastery < 40` red, `eval_swing > 150` amber, ECO family tints, "in my repertoire" accent |
| Filters: orphans | Positions with zero games played by this user |
| Filters: attachments | Positions with literature/annotation attached |
| Text fade threshold | SAN labels fade in as you zoom; dots when zoomed out |
| Arrows | Move direction; separate tint for side-to-move |
| Force sliders | Concept graph only — the repertoire DAG uses a layered (Sugiyama) layout because ply depth is semantically meaningful and physics would destroy it |
| Hover preview popover | **Mini chessboard popover.** The single highest-value borrowing: hover a node, see the position, without navigating |
| Animate / timelapse | Replay repertoire growth over the user's rating history |

### 4.3 Deviations, and why

- **The repertoire DAG is not force-directed.** Obsidian's physics is right for
  an unordered link graph and wrong for an opening tree — ply is an axis, and
  the layout must respect it. Layered layout, computed server-side, cached per
  user. Force sliders are exposed only on the concept graph.
- **Transposition edges get a distinct style.** Obsidian has no equivalent
  concept; merges into an already-visited node must be visually obvious or the
  DAG reads as a tree and misleads.
- **Local-first, hard.** Given the attention-span constraint, the global view is
  never the landing screen. You land on your current position at depth 2.
- **The explanation panel is part of the graph view**, not a separate page.
  Select a node → an explanation ranked by sensitivity appears beside the board. Cached
  and CDN-served, so it is effectively instant.

### 4.4 Rendering

- SVG / Cytoscape.js up to ~2k nodes.
- graphology + sigma.js (WebGL) above that.
- Vue 3 + TypeScript + Pinia; chessground for boards (lichess's own, and it is
  what makes the hover popover cheap).

---

## 5. Game import

### 5.1 Lichess — OAuth is worth it

Lichess supports **OAuth 2.0 with PKCE**, no client secret and no app
registration: you choose your own `client_id` (a URL identifying the app) and go.
Authorisation happens at `/oauth`, the token exchange at `/api/token`.

What authenticating actually buys us:

- **Higher rate limits** on the export endpoints, which is the real speed win.
- **Verified identity** — we know the account belongs to this user, which is
  what makes a mastery score meaningful rather than a claim about a stranger.
- Access to the user's own ongoing and hidden games.

Export uses `GET /api/games/user/{username}`, which **streams NDJSON or PGN**, so
ingest is incremental and memory-flat. Useful parameters: `since` (our sync
cursor), `max`, `perfType`, `rated`, `opening`, `clocks`, `evals`.

Rate-limit discipline is mandatory: issue requests **serially**, and on `429`
back off for a full minute rather than retrying tightly. Getting this wrong gets
the app blocked, not just throttled.

For positions rather than users, the CC0 Lichess database dumps and the opening
explorer API cover empirical statistics without touching per-user quota.

### 5.2 Chess.com — no OAuth exists for this

Correcting a common assumption: **Chess.com's Published-Data API is
unauthenticated, and there is no public OAuth flow for game data.** Authorising
is not an option, so it cannot be the speed lever.

Flow: `GET /pub/player/{username}/games/archives` returns a list of monthly
archive URLs; each `GET /pub/player/{username}/games/{YYYY}/{MM}` returns that
month's games with embedded PGN.

The speed levers that *do* exist:

- **`ETag` / `If-None-Match` per monthly archive.** Historical months never
  change, so after the first sync every back-month is a `304` costing nothing.
  Only the current month is re-fetched.
- **Serial requests.** Parallel fetching earns `429`s; the API is explicitly
  designed around sequential access.
- Persist an archive-URL → ETag map as the sync cursor.

**Identity verification**, since there is no OAuth: have the user place a
short-lived token in their Chess.com profile (e.g. the location field), then read
it back via `GET /pub/player/{username}`. Without this, a Chess.com-sourced
mastery profile is an unverified claim and should be labelled as such in the UI.

### 5.3 Ingest pipeline

```
authorise/verify → stream games → parse PGN → replay to positions
   → upsert DAG edges → attach per-user move+clock evidence
   → enqueue unenriched positions by empirical popularity
```

Clock-per-move is load-bearing for the assessment (fast reply = recall, long
think = calculation). Lichess provides it reliably with `clocks=true`;
Chess.com's PGN clock annotations are less consistent, so **implicit evidence
from Chess.com games is weighted lower**. Surface that honestly rather than
pretending the two sources are equivalent.

---

## 6. LLM layer

**Topic selection is computed, never chosen by the model.** Ablation ranking decides
what the dossier contains; the model writes prose about what it is given.

**Engine as a bounded tool.** The model may call `evaluate_line(moves, depth)`,
`compare_candidates(moves)` and `probe_plan(moves)`. Every call replays from the
canonical position server-side, so an invented line cannot be smuggled in. Each
probe result gets an evidence ID and becomes citable like any other evidence.
Iterations capped at ~6.

**Structured output only** (`Explanation` / `Claim` in `domain/models.py`). A
claim whose `evidence_id` does not resolve is dropped before storage — which is
also what kills generic filler, since a platitude has nothing to cite.

**Validators**: legality replay, eval consistency, sensitivity coverage (must lead
with a top-*k* ablation item), platitude similarity, rating-band calibration.
Reject → escalate one model tier → reject again → quarantine.

**Model routing by landscape, for cost not content:**

| Landscape | Handler |
|---|---|
| `is_single_answer` (mate, only-move) | deterministic template, **no model call** |
| forcing line, facts fully supplied | `claude-haiku-4-5` |
| strategic / critical theory / concept synthesis / grading | `claude-opus-5` |

Enrichment runs offline via the Batch API (50% cost) with prompt caching on the
stable system + rubric prefix. Each position is enriched once and shared by all
users, which is what makes Opus-tier affordable for the hard cases.

---

## 7. Build, containers and reproducibility

**The engine is a build input, never a repository artifact.** `vendor/` is
git-ignored and excluded from the Docker context; the wheel is pure Python.

**Docker stages** (`Dockerfile`):

| Stage | Job |
|---|---|
| `engine` | Fetches + checksum-verifies Stockfish from `engine.lock.json` into `/opt/stockfish`, then smoke-tests it with a `uci` handshake. Stdlib-only — runs before any dependency resolution, so its cache key is the lockfile alone and source edits never trigger a re-download. |
| `python-build` | Installs deps then the app into a relocatable `/opt/venv`. Dependency layer is invalidated only by `pyproject.toml`. |
| `runtime` | Copies `/opt/stockfish` + `/opt/venv`. Non-root (uid 10001), no compilers, no source tree, no pip cache. Installs `libstdc++6` explicitly — the release builds link it dynamically, and a missing shared object surfaces as a silent engine-start failure at request time. |
| `dev` | Same verified engine, editable install, `--reload`. Used by compose with a read-only bind mount over `src/` only, so a mount can never shadow the engine or the venv. |

**Platform.** Pinned assets are AVX2 x86-64 builds, so images target
`linux/amd64`; compose pins `platform: linux/amd64` so Apple Silicon emulates
rather than failing at engine start. An arm64 Linux entry would need adding to
the lockfile.

**Reproducibility rules:**

- Development happens in `.venv`; the system Python is never used.
- `config.resolve_engine_path()` **refuses to fall back to a Stockfish on
  `PATH`** — it checks `/opt/stockfish` (container), then `vendor/stockfish/<platform>/`
  (local), then raises. Evaluations feed cache keys and golden-set assertions, so
  an unpinned build silently destroys reproducibility. Covered by
  `tests/test_engine_resolution.py`.
- Checksums are trust-on-first-use with explicit `--record`, then committed.
  Neither CI nor the Docker build passes `--record`, so a lockfile entry without
  a digest **fails the build** rather than trusting the network.
- `engine_binary_name()` derives from the *target* slug, not the host —
  cross-provisioning (a Windows machine fetching the linux asset for an image)
  must not produce `stockfish.exe`.

**CI jobs:** `check` (ruff/format/mypy/fast tests) → `engine` (provision + real
Stockfish tests) → `wheel` (asserts no engine binary leaked into the artifact)
and `image` (builds `runtime`, resolves the engine inside it, boots the API and
asserts `/health` reports the engine available).

---

## 8. Status

**Done**
- Position identity, 960-safe, en-passant and move-counter normalised
- Engine abstraction + Stockfish UCI adapter + deterministic `ReferenceEngine`
- Ablation sensitivity (piece removal, move-space, tempo)
- Plan fingerprinting with mover-relative zones
- Eval-landscape descriptors
- Domain models incl. grounded `Claim`/`Explanation` and `KnowledgeAvailability`
- 34 tests green: golden set, engine-resolution rules, and real-Stockfish
  integration (incl. a Chess960-over-UCI regression)
- venv, pinned-engine provisioning, Docker (4 stages) + compose, CI

**Verified against real Stockfish 17.1 inside the runtime image**

```
best       : Ra8# | mate_in 1
sensitivity: [(None,'tempo',-100650), ('a1','piece_removal',-100614),
              ('h7','piece_removal',-99463), ('g7','piece_removal',-99371)]
b2 in top3 : False       # the loud passed pawn never reaches the dossier
chess960   : c3 chess960 # SP 356, no theory tier involved
```

**Next**
1. Postgres schema + Alembic (positions, edges, concepts, negative edges, embeddings)
2. Structural embedding + pgvector similarity, concept propagation with verification
3. Lichess OAuth + streaming ingest; Chess.com ETag sync + profile-token verification
4. LLM tool loop, validator chain, Batch enrichment worker
5. FastAPI node-expansion endpoint with Redis + S3/CloudFront caching
6. Vue graph shell: local repertoire DAG, depth slider, hover board popover
