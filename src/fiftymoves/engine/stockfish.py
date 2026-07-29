"""Stockfish over UCI.

Kept out of the request path entirely: analysis is CPU-bound and blocking, so
these live in enrichment workers that scale independently of the API.
"""

from __future__ import annotations

import chess
import chess.engine

from fiftymoves.domain.identity import position_key
from fiftymoves.domain.models import MATE_SCORE_CP, EngineLine, EngineReport
from fiftymoves.engine.protocol import EngineError


def fold_mate(score: chess.engine.PovScore) -> tuple[int, int | None]:
    """Collapse a PovScore to (white-POV centipawns, mate_in).

    Mates are folded to a magnitude far above any real evaluation so that
    ranking is total and a forced mate can never sort below material.
    """
    white = score.white()
    mate = white.mate()
    if mate is not None:
        sign = 1 if mate > 0 else -1
        # Shorter mates score higher, so a mate in 1 outranks a mate in 7.
        return sign * (MATE_SCORE_CP - abs(mate)), mate
    cp = white.score()
    if cp is None:  # pragma: no cover - defensive
        raise EngineError("engine returned neither centipawns nor mate")
    return cp, None


class StockfishEngine:
    """UCI-backed :class:`~fiftymoves.engine.protocol.EngineProvider`."""

    def __init__(self, binary_path: str, *, threads: int = 1, hash_mb: int = 128) -> None:
        try:
            self._engine = chess.engine.SimpleEngine.popen_uci(binary_path)
        except (OSError, chess.engine.EngineError) as exc:
            raise EngineError(f"could not start engine at {binary_path!r}: {exc}") from exc
        self._engine.configure({"Threads": threads, "Hash": hash_mb})
        self._id = self._engine.id.get("name", "stockfish")

    @property
    def engine_id(self) -> str:
        return self._id

    def analyse(self, board: chess.Board, *, depth: int, multipv: int = 3) -> EngineReport:
        # Chess960 needs no handling here: python-chess owns UCI_Chess960 and
        # derives it from ``board.chess960`` on every command. Setting it by hand
        # raises "cannot set UCI_Chess960 which is automatically managed".
        infos = self._engine.analyse(
            board, chess.engine.Limit(depth=depth), multipv=max(1, multipv)
        )
        if isinstance(infos, dict):  # multipv=1 returns a bare InfoDict
            infos = [infos]

        lines: list[EngineLine] = []
        for rank, info in enumerate(infos, start=1):
            pv = info.get("pv") or []
            if not pv:
                continue
            score_cp, mate_in = fold_mate(info["score"])
            lines.append(
                EngineLine(
                    rank=rank,
                    move_san=board.san(pv[0]),
                    move_uci=pv[0].uci(),
                    pv_san=list(board.variation_san(pv).split()),
                    pv_uci=[m.uci() for m in pv],
                    score_cp=score_cp,
                    mate_in=mate_in,
                    depth=info.get("depth", depth),
                )
            )
        if not lines:
            raise EngineError("engine returned no principal variation")
        return EngineReport(
            key=position_key(board), lines=lines, depth=depth, engine_id=self.engine_id
        )

    def close(self) -> None:
        self._engine.quit()

    def __enter__(self) -> StockfishEngine:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
