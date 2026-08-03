from __future__ import annotations

from typing import Any

from celery import Task

from fiftymoves.config import get_settings
from fiftymoves.ingest.lichess import LichessError
from fiftymoves.ingest.pipeline import IngestProgress, ingest_player
from fiftymoves.jobs.app import app
from fiftymoves.jobs.notify import deliver

IMPORT_TASK = "fiftymoves.import_player"
ANNOTATE_TASK = "fiftymoves.annotate_player"
LEARN_TASK = "fiftymoves.learn_positions"
MEASURE_TASK = "fiftymoves.measure_losses"


def announce(event: str, body: dict[str, Any]) -> None:
    settings = get_settings()
    if not settings.job_webhook_url:
        return
    deliver(
        settings.job_webhook_url,
        event,
        body,
        secret=settings.job_webhook_secret,
        timeout_s=settings.job_webhook_timeout_s,
    )


@app.task(bind=True, name=IMPORT_TASK, max_retries=0)
def import_player(self: Task, username: str, max_games: int | None = None) -> dict[str, Any]:
    settings = get_settings()

    def publish(progress: IngestProgress) -> None:
        self.update_state(state="PROGRESS", meta=progress.model_dump())

    try:
        result = ingest_player(
            username,
            settings=settings,
            max_games=max_games,
            out_dir=settings.data_dir,
            on_progress=publish,
            report_every=settings.job_report_every,
        )
    except LichessError as exc:
        # Carried as a plain result so the API can report it without a traceback.
        failure = {"username": username, "failed": str(exc), "job_id": self.request.id}
        announce("import.failed", failure)
        return failure

    body = result.model_dump()
    announce("import.finished", {**body, "job_id": self.request.id})
    return body


@app.task(bind=True, name=LEARN_TASK, max_retries=0)
def learn_positions(
    self: Task,
    username: str,
    side: str = "white",
    max_ply: int = 10,
    min_volume: int = 12,
    max_children: int = 4,
) -> dict[str, Any]:
    """Study the positions a player actually reaches and remember what was found."""
    import chess

    from fiftymoves.analysis.knowledge import learn_position
    from fiftymoves.config import EngineNotProvisioned
    from fiftymoves.domain.games import Side
    from fiftymoves.engine.stockfish import StockfishEngine
    from fiftymoves.ingest.graph import build_graph
    from fiftymoves.ingest.knowledge_store import KnowledgeStore
    from fiftymoves.ingest.pipeline import load_player_games
    from fiftymoves.llm.explain import study_position

    settings = get_settings()
    try:
        engine_path = settings.resolve_engine_path()
    except EngineNotProvisioned as exc:
        return {"username": username, "failed": str(exc).splitlines()[0]}

    games, _ = load_player_games(username, settings.data_dir)
    if not games:
        return {"username": username, "failed": f"no games imported for {username!r} yet"}

    graph = build_graph(
        games,
        side=Side(side),
        max_ply=max_ply,
        min_volume=min_volume,
        max_children=max_children,
        family_window_ply=settings.family_window_ply,
        family_min_games=settings.family_min_games,
        family_prior_games=settings.family_prior_games,
        family_slots=settings.family_slots,
    )
    store = KnowledgeStore(settings.data_dir)

    # Busiest first, so a budget cut keeps the positions the player lives in.
    wanted = sorted(graph.nodes, key=lambda n: -n.games)[: settings.knowledge_budget]
    todo = [n for n in wanted if store.get(n.digest) is None]

    engine = StockfishEngine(
        str(engine_path), threads=settings.engine_threads, hash_mb=settings.engine_hash_mb
    )
    learned = []
    try:
        for index, node in enumerate(todo, start=1):
            board = chess.Board(f"{node.epd} 0 1")
            study = study_position(
                board,
                engine=engine,
                depth=settings.knowledge_depth,
                ablation_depth=settings.ablation_depth,
                multipv=settings.multipv,
            )
            learned.append(
                learn_position(board, node.digest, study.report, study.sensitivity, study.landscape)
            )
            if index % 10 == 0:
                self.update_state(
                    state="PROGRESS",
                    meta={"username": username, "studied": index, "total": len(todo)},
                )
    finally:
        engine.close()

    added = store.extend(learned)
    plans = store.by_plan()
    body = {
        "username": username,
        "studied": len(learned),
        "added": added,
        "known_positions": len(store),
        "distinct_plans": len(plans),
        "job_id": self.request.id,
    }
    announce("knowledge.finished", body)
    return body


@app.task(bind=True, name=MEASURE_TASK, max_retries=0)
def measure_player_losses(
    self: Task,
    username: str,
    side: str = "white",
    max_ply: int = 28,
    max_depth: int = 28,
    workers: int = 0,
    threads: int = 1,
) -> dict[str, Any]:
    # The pool forks, so this queue has to run on a worker that allows children:
    # celery -Q measure --pool=solo. A prefork worker is daemonic and cannot.
    from fiftymoves.analysis.book import BOOK_MAX_CHILDREN, MIN_VOLUME
    from fiftymoves.analysis.sweep import SweepProgress, plan_sweep, run_sweep
    from fiftymoves.config import EngineNotProvisioned
    from fiftymoves.domain.games import Side
    from fiftymoves.ingest.graph import build_graph
    from fiftymoves.ingest.loss_store import LossStore
    from fiftymoves.ingest.pipeline import load_player_games

    settings = get_settings()
    try:
        engine_path = settings.resolve_engine_path()
    except EngineNotProvisioned as exc:
        return {"username": username, "failed": str(exc).splitlines()[0]}

    games, _ = load_player_games(username, settings.data_dir)
    if not games:
        return {"username": username, "failed": f"no games imported for {username!r} yet"}

    graph = build_graph(
        games,
        side=Side(side),
        max_ply=max_ply,
        min_volume=MIN_VOLUME,
        max_children=BOOK_MAX_CHILDREN,
    )
    store = LossStore(settings.data_dir)
    items = plan_sweep(graph, store, max_depth=max_depth)

    def publish(progress: SweepProgress) -> None:
        self.update_state(state="PROGRESS", meta={"username": username, **progress.model_dump()})

    result = run_sweep(
        items,
        store,
        engine_path=str(engine_path),
        workers=workers,
        threads=threads,
        hash_mb=settings.engine_hash_mb,
        on_progress=publish,
    )
    body = {"username": username, **result.model_dump()}
    announce("measure.finished", {**body, "job_id": self.request.id})
    return body


@app.task(bind=True, name=ANNOTATE_TASK, max_retries=0)
def annotate_player(
    self: Task,
    username: str,
    side: str = "white",
    max_ply: int = 10,
    min_volume: int = 2,
    max_children: int = 4,
) -> dict[str, Any]:
    from fiftymoves.analysis.annotations import annotate_graph
    from fiftymoves.config import EngineNotProvisioned
    from fiftymoves.domain.games import Side
    from fiftymoves.engine.stockfish import StockfishEngine
    from fiftymoves.ingest.annotations_store import AnnotationStore, shape_key
    from fiftymoves.ingest.graph import build_graph
    from fiftymoves.ingest.pipeline import load_player_games

    settings = get_settings()
    try:
        engine_path = settings.resolve_engine_path()
    except EngineNotProvisioned as exc:
        return {"username": username, "failed": str(exc).splitlines()[0]}

    games, stamp = load_player_games(username, settings.data_dir)
    if not games:
        return {"username": username, "failed": f"no games imported for {username!r} yet"}

    chosen = Side(side)
    graph = build_graph(
        games,
        side=chosen,
        max_ply=max_ply,
        min_volume=min_volume,
        max_children=max_children,
        family_window_ply=settings.family_window_ply,
        family_min_games=settings.family_min_games,
        family_prior_games=settings.family_prior_games,
        family_slots=settings.family_slots,
    )
    shape = shape_key(username, chosen, max_ply, min_volume, max_children, stamp)

    def publish(done: int, total: int) -> None:
        self.update_state(
            state="PROGRESS",
            meta={"username": username, "positions": done, "total": total, "shape": shape},
        )

    engine = StockfishEngine(
        str(engine_path), threads=settings.engine_threads, hash_mb=settings.engine_hash_mb
    )
    try:
        result = annotate_graph(
            engine,
            graph,
            username=username,
            shape=shape,
            depth=settings.annotation_depth,
            dubious_cp=settings.annotation_dubious_cp,
            mistake_cp=settings.annotation_mistake_cp,
            blunder_cp=settings.annotation_blunder_cp,
            min_games=settings.annotation_min_games,
            budget=settings.annotation_budget,
            on_progress=publish,
        )
    finally:
        engine.close()

    AnnotationStore(settings.data_dir).write(result)
    body = {
        "username": username,
        "shape": shape,
        "annotated": len(result.annotations),
        "flawed": len(result.flawed),
        "positions_searched": result.positions_searched,
        "truncated": result.truncated,
        "job_id": self.request.id,
    }
    announce("annotations.finished", body)
    return body
