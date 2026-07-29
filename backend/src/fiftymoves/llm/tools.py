from __future__ import annotations

from typing import Any

import chess

from fiftymoves.domain.explanations import Evidence, EvidenceKind
from fiftymoves.engine.protocol import EngineProvider
from fiftymoves.llm.facts import pawns

EVALUATE_LINE = "evaluate_line"
BEST_REPLIES = "best_replies"

TOOLS: list[dict[str, Any]] = [
    {
        "name": EVALUATE_LINE,
        "description": (
            "Play a line from the position under discussion and report what the engine "
            "makes of the result. Use this to check a continuation before claiming "
            "anything about it. Never assert the outcome of a line you have not evaluated."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "moves_san": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Moves in SAN from the current position, White first.",
                }
            },
            "required": ["moves_san"],
            "additionalProperties": False,
        },
    },
    {
        "name": BEST_REPLIES,
        "description": (
            "List the engine's preferred moves in the position reached after an optional "
            "line, with their evaluations. Use this to find what the opponent gets to do."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "moves_san": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Moves in SAN leading to the position. Empty for this one.",
                },
                "count": {"type": "integer", "description": "How many moves to return, 1 to 5."},
            },
            "required": ["moves_san", "count"],
            "additionalProperties": False,
        },
    },
]


class ProbeLimit(RuntimeError):
    pass


class EngineProbe:
    """Bounded engine access for the model.

    The model never calculates; it asks and the engine answers. Every answer is
    recorded as evidence so a claim resting on a probe can still be checked.
    """

    def __init__(
        self,
        engine: EngineProvider,
        board: chess.Board,
        *,
        depth: int = 14,
        max_moves: int = 6,
        max_calls: int = 6,
    ) -> None:
        self._engine = engine
        self._board = board
        self._depth = depth
        self._max_moves = max_moves
        self._max_calls = max_calls
        self._calls = 0
        self.evidence: list[Evidence] = []

    @property
    def calls(self) -> int:
        return self._calls

    def _walk(self, moves_san: list[str]) -> tuple[chess.Board, str]:
        if len(moves_san) > self._max_moves:
            raise ProbeLimit(f"at most {self._max_moves} moves per probe")
        probe = self._board.copy(stack=False)
        played: list[str] = []
        for san in moves_san:
            try:
                move = probe.parse_san(san)
            except (chess.IllegalMoveError, chess.InvalidMoveError, chess.AmbiguousMoveError):
                raise ProbeLimit(
                    f"{san} is not legal after {' '.join(played) or 'the start'}"
                ) from None
            played.append(san)
            probe.push(move)
        return probe, " ".join(played)

    def _record(self, statement: str) -> str:
        identifier = f"probe{len(self.evidence) + 1}"
        self.evidence.append(
            Evidence(id=identifier, kind=EvidenceKind.ENGINE_LINE, statement=statement)
        )
        return identifier

    def _mover_cp(self, score_cp: int, board: chess.Board) -> int:
        return score_cp if board.turn == chess.WHITE else -score_cp

    def evaluate_line(self, moves_san: list[str]) -> dict[str, Any]:
        self._spend()
        probe, line = self._walk(moves_san)
        if probe.is_game_over(claim_draw=False):
            statement = f"After {line} the game is over: {probe.result()}."
            return {"evidence_id": self._record(statement), "summary": statement}

        report = self._engine.analyse(probe, depth=self._depth, multipv=1)
        best = report.best
        cp = self._mover_cp(report.score_cp, probe)
        mover = "White" if probe.turn == chess.WHITE else "Black"
        if best.mate_in is not None:
            detail = f"mate in {abs(best.mate_in)}"
        else:
            detail = f"{pawns(cp)} for {mover}"
        statement = (
            f"After {line or 'no moves'} the engine gives {detail} at depth {self._depth}, "
            f"with {best.move_san} to follow."
        )
        return {
            "evidence_id": self._record(statement),
            "summary": statement,
            "score_cp_for_mover": cp,
            "best_move_san": best.move_san,
            "mate_in": best.mate_in,
        }

    def best_replies(self, moves_san: list[str], count: int) -> dict[str, Any]:
        self._spend()
        bounded = max(1, min(int(count), 5))
        probe, line = self._walk(moves_san)
        if probe.is_game_over(claim_draw=False):
            statement = f"After {line} there are no moves: {probe.result()}."
            return {"evidence_id": self._record(statement), "summary": statement}

        report = self._engine.analyse(probe, depth=self._depth, multipv=bounded)
        mover = "White" if probe.turn == chess.WHITE else "Black"
        parts = [
            f"{item.move_san} ({pawns(self._mover_cp(item.score_cp, probe))})"
            for item in report.lines
        ]
        where = f"after {line}" if line else "here"
        statement = (
            f"The engine's choices for {mover} {where} at depth {self._depth}: {', '.join(parts)}."
        )
        return {
            "evidence_id": self._record(statement),
            "summary": statement,
            "moves": [
                {"san": item.move_san, "score_cp_for_mover": self._mover_cp(item.score_cp, probe)}
                for item in report.lines
            ],
        }

    def _spend(self) -> None:
        if self._calls >= self._max_calls:
            raise ProbeLimit(f"the engine has already answered {self._max_calls} times")
        self._calls += 1

    def dispatch(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        moves = list(payload.get("moves_san") or [])
        if name == EVALUATE_LINE:
            return self.evaluate_line(moves)
        if name == BEST_REPLIES:
            return self.best_replies(moves, int(payload.get("count", 3)))
        raise ProbeLimit(f"no such tool: {name}")
