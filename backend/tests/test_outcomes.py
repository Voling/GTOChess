from __future__ import annotations

from typing import Any

import chess

from fiftymoves.analysis.outcomes import measure_outcomes
from fiftymoves.analysis.sensitivity import compute_sensitivity
from fiftymoves.domain.annotations import MoveQuality
from fiftymoves.domain.book import PositionLosses
from fiftymoves.domain.games import GameRecord, GameSource
from fiftymoves.domain.graph import GraphEdge
from fiftymoves.domain.identity import position_key
from fiftymoves.domain.models import EvalLandscape, PlayableMove, Variant
from fiftymoves.engine.reference import ReferenceEngine
from fiftymoves.ingest.graph import build_graph
from fiftymoves.llm.facts import build_evidence

ROOT = position_key(chess.Board()).digest
SANS = {"e2e4": "e4", "d2d4": "d4", "f2f3": "f3", "g1f3": "Nf3"}


def game(game_id: str, moves: str, score: float, **overrides: Any) -> GameRecord:
    base: dict[str, Any] = {
        "source": GameSource.LICHESS,
        "game_id": game_id,
        "played_at_ms": 0,
        "variant": Variant.STANDARD,
        "speed": "blitz",
        "rated": True,
        "player_is_white": True,
        "player_rating": 1800,
        "opponent_rating": 1800,
        "score": score,
        "eco": "C50",
        "opening_name": "Italian Game",
        "opening_ply": 6,
        "initial_fen": None,
        "moves_san": tuple(moves.split()),
        "clocks_cs": (),
        "evals_cp": (),
        "initial_seconds": 180,
        "increment_seconds": 0,
    }
    base.update(overrides)
    return GameRecord(**base)


def held(mapping: dict[str, int]) -> dict[str, PositionLosses]:
    return {
        ROOT: PositionLosses(
            digest=ROOT,
            epd=chess.Board().epd(),
            depth=20,
            best_uci="e2e4",
            best_san="e4",
            best_cp=20,
            losses=mapping,
            sans=SANS,
        )
    }


class TestOutcomes:
    def test_the_flagged_moves_are_scored_apart_from_the_sound_ones(self) -> None:
        games = [game(f"w{i}", "e4 e5", 1.0) for i in range(3)]
        games += [game(f"l{i}", "f3 e5", 0.0) for i in range(2)]
        report = measure_outcomes(build_graph(games), held({"e2e4": 0, "f2f3": 400}))

        sound = next(q for q in report.by_quality if q.quality is MoveQuality.SOUND)
        blunder = next(q for q in report.by_quality if q.quality is MoveQuality.BLUNDER)
        assert sound.wins == 3
        assert blunder.losses == 2
        assert report.sound_score == 1.0
        assert report.flawed_score == 0.0
        assert report.score_gap == 1.0

    def test_a_draw_counts_as_half_a_point(self) -> None:
        games = [game("a", "e4 e5", 1.0), game("b", "e4 e5", 0.0), game("c", "e4 e5", 0.5)]
        report = measure_outcomes(build_graph(games), held({"e2e4": 0}))
        sound = next(q for q in report.by_quality if q.quality is MoveQuality.SOUND)
        assert (sound.wins, sound.draws, sound.losses) == (1, 1, 1)
        assert sound.score == 0.5

    def test_a_move_the_engine_never_measured_is_counted_not_guessed(self) -> None:
        games = [game("a", "e4 e5", 1.0), game("b", "d4 d5", 0.0)]
        report = measure_outcomes(build_graph(games), held({"e2e4": 0}))
        assert report.moves_measured == 1
        assert report.moves_unmeasured == 1

    def test_the_worst_move_is_the_one_costing_points_not_centipawns(self) -> None:
        games = [game(f"s{i}", "e4 e5", 1.0) for i in range(10)]
        games += [game(f"m{i}", "d4 d5", 0.0) for i in range(8)]
        games += [game("b", "f3 e5", 0.0)]
        report = measure_outcomes(build_graph(games), held({"e2e4": 0, "d2d4": 200, "f2f3": 900}))

        assert [m.san for m in report.worst] == ["d4", "f3"]
        assert report.worst[0].quality is MoveQuality.MISTAKE
        assert report.worst[0].points_lost == 8.0
        assert report.worst[1].loss_cp == 900

    def test_a_move_is_labelled_with_the_line_that_reaches_it(self) -> None:
        games = [game("a", "e4 e5 Qh5", 0.0)]
        digest = next(n.digest for n in build_graph(games).nodes if n.san_path == ("e4", "e5"))
        losses = {
            digest: PositionLosses(
                digest=digest,
                epd="x",
                depth=20,
                best_uci="g1f3",
                best_san="Nf3",
                best_cp=20,
                losses={"d1h5": 500},
                sans={"d1h5": "Qh5"},
            )
        }
        report = measure_outcomes(build_graph(games), losses)
        assert report.worst[0].line == "e4 e5"
        assert report.worst[0].best_san == "Nf3"

    def test_nothing_measured_yields_an_empty_report_rather_than_a_divide_by_zero(self) -> None:
        report = measure_outcomes(build_graph([game("a", "e4 e5", 1.0)]), {})
        assert report.by_quality == ()
        assert report.score_gap == 0.0
        assert report.moves_unmeasured == 1


def landscape_of(*moves: tuple[str, str]) -> EvalLandscape:
    return EvalLandscape(
        best_cp=20,
        legal_move_count=20,
        playable_move_count=len(moves),
        playable=tuple(PlayableMove(uci=u, san=s, score_cp=20) for u, s in moves),
        delta_to_second_cp=10,
        top_move_entropy=1.0,
        eval_volatility_cp=0.0,
        best_move_changes=0,
    )


def edge(uci: str, san: str, *, games: int, wins: int, draws: int, losses: int) -> GraphEdge:
    return GraphEdge(
        parent=ROOT,
        child="c",
        uci=uci,
        san=san,
        games=games,
        by_player=True,
        wins=wins,
        draws=draws,
        losses=losses,
    )


def coverage(landscape: EvalLandscape, continuations: tuple[GraphEdge, ...]) -> str | None:
    board = chess.Board()
    engine = ReferenceEngine()
    report = engine.analyse(board, depth=4, multipv=3)
    evidence = build_evidence(
        board,
        report,
        compute_sensitivity(engine, board, baseline=report, depth=2),
        landscape,
        continuations=continuations,
    )
    return next((e.statement for e in evidence if e.id == "covers"), None)


class TestCoverage:
    def test_it_counts_the_allowed_moves_the_player_actually_plays(self) -> None:
        statement = coverage(
            landscape_of(("e2e4", "e4"), ("d2d4", "d4"), ("g1f3", "Nf3")),
            (edge("e2e4", "e4", games=10, wins=6, draws=1, losses=3),),
        )
        assert statement is not None
        assert statement.startswith("You play 1 of the 3 moves the engine allows here.")
        assert "e4 in 10 games scoring 65%, 6 wins 1 draws 3 losses." in statement
        assert "You have never played d4 and Nf3." in statement

    def test_a_move_outside_the_allowed_set_is_reported_separately(self) -> None:
        statement = coverage(
            landscape_of(("e2e4", "e4")),
            (
                edge("e2e4", "e4", games=4, wins=4, draws=0, losses=0),
                edge("f2f3", "f3", games=6, wins=0, draws=0, losses=6),
            ),
        )
        assert statement is not None
        assert "You play 1 of the 1 moves the engine allows here." in statement
        assert "Outside that set you play f3 in 6 games scoring 0%" in statement

    def test_without_the_player_it_names_the_moves_and_no_record(self) -> None:
        statement = coverage(landscape_of(("e2e4", "e4"), ("d2d4", "d4")), ())
        assert statement == "The engine allows 2 moves here: e4 and d4."

    def test_the_band_count_sentence_is_gone(self) -> None:
        board = chess.Board()
        engine = ReferenceEngine()
        report = engine.analyse(board, depth=4, multipv=3)
        evidence = build_evidence(
            board,
            report,
            compute_sensitivity(engine, board, baseline=report, depth=2),
            landscape_of(("e2e4", "e4")),
        )
        assert not any("playable band" in e.statement for e in evidence)
