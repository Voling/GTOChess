from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence

import chess

from fiftymoves.domain.flaws import OpeningRecord
from fiftymoves.domain.games import GameRecord
from fiftymoves.domain.identity import position_key
from fiftymoves.domain.models import PositionKey, Variant
from fiftymoves.domain.repertoire import RepertoireNode

DEFAULT_POPULATION_SCORE = 0.5


class _Accumulator:
    def __init__(self, key: PositionKey, depth_ply: int) -> None:
        self.key = key
        self.depth_ply = depth_ply
        self.game_ids: set[str] = set()
        self.replies: Counter[str] = Counter()


def starting_board(game: GameRecord) -> chess.Board:
    chess960 = game.variant is Variant.CHESS960
    if game.initial_fen:
        return chess.Board(game.initial_fen, chess960=chess960)
    return chess.Board(chess960=chess960)


def build_decision_nodes(games: Sequence[GameRecord], *, max_ply: int = 24) -> list[RepertoireNode]:
    accumulators: dict[str, _Accumulator] = {}

    for game in games:
        board = starting_board(game)
        for ply, san in enumerate(game.moves_san):
            if ply >= max_ply:
                break
            try:
                move = board.parse_san(san)
            except (chess.IllegalMoveError, chess.InvalidMoveError, chess.AmbiguousMoveError):
                break

            our_turn = (board.turn == chess.WHITE) == game.player_is_white
            if our_turn:
                key = position_key(board)
                accumulator = accumulators.get(key.digest)
                if accumulator is None:
                    accumulator = _Accumulator(key, ply)
                    accumulators[key.digest] = accumulator
                accumulator.game_ids.add(game.game_id)
                accumulator.replies[move.uci()] += 1

            board.push(move)

    return [
        RepertoireNode(
            key=a.key,
            depth_ply=a.depth_ply,
            game_count=len(a.game_ids),
            replies=dict(a.replies),
        )
        for a in accumulators.values()
    ]


def build_opening_records(
    games: Sequence[GameRecord],
    *,
    population_scores: Mapping[str, float] | None = None,
) -> list[OpeningRecord]:
    population_scores = population_scores or {}
    grouped: dict[str, list[float]] = defaultdict(list)
    for game in games:
        opening_id = game.opening_id
        if opening_id is not None:
            grouped[opening_id].append(game.score)

    return [
        OpeningRecord(
            opening_id=opening_id,
            games=len(scores),
            score=statistics.fmean(scores),
            population_score=population_scores.get(opening_id, DEFAULT_POPULATION_SCORE),
        )
        for opening_id, scores in sorted(grouped.items())
    ]
