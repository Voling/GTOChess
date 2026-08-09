from __future__ import annotations

import chess
import pytest

from gtochess.domain.storyboard import ArrowRole, Glyph
from gtochess.engine.reference import ReferenceEngine
from gtochess.llm.board import MAX_LOSS_CP, BoardLimit, BoardSession, glyph_for


def session(fen: str = chess.STARTING_FEN, **kwargs: int) -> BoardSession:
    return BoardSession(ReferenceEngine(), chess.Board(fen), depth=4, **kwargs)


class TestGlyphs:
    def test_a_move_the_engine_likes_gets_no_mark(self) -> None:
        assert glyph_for(0) is Glyph.PLAIN
        assert glyph_for(49) is Glyph.PLAIN

    def test_the_bands_climb_with_the_loss(self) -> None:
        assert glyph_for(50) is Glyph.DUBIOUS
        assert glyph_for(150) is Glyph.MISTAKE
        assert glyph_for(300) is Glyph.BLUNDER


class TestWalking:
    def test_every_move_in_the_line_is_priced(self) -> None:
        result = session().walk_line(["e4", "e5", "Nf3"])
        assert [m["san"] for m in result["moves"]] == ["e4", "e5", "Nf3"]
        assert all(m["gave_up_cp"] >= 0 for m in result["moves"])
        assert all(m["engine_preferred"] for m in result["moves"])

    def test_an_illegal_move_is_refused_with_the_line_so_far(self) -> None:
        with pytest.raises(BoardLimit, match="after e4"):
            session().walk_line(["e4", "Qh8"])

    def test_the_engine_budget_is_finite(self) -> None:
        held = session(max_calls=1)
        held.walk_line(["e4"])
        with pytest.raises(BoardLimit):
            held.walk_line(["d4"])


class TestShowing:
    def test_a_line_must_be_walked_before_it_can_be_shown(self) -> None:
        with pytest.raises(BoardLimit, match="walk it first"):
            session().show_line({"line_id": "line1", "title": "t", "notes": []})

    def test_the_scene_opens_on_the_position_before_any_move(self) -> None:
        held = session()
        walked = held.walk_line(["e4", "e5"])
        held.show_line({"line_id": walked["line_id"], "title": "Centre", "notes": ["a", "b"]})
        scene = held.storyboard().scenes[0]
        assert scene.beats[0].is_root
        assert [b.note for b in scene.beats[1:]] == ["a", "b"]
        assert scene.moves == 2

    def test_the_move_played_is_always_drawn(self) -> None:
        held = session()
        walked = held.walk_line(["e4"])
        held.show_line({"line_id": walked["line_id"], "title": "t", "notes": [""]})
        arrows = held.storyboard().scenes[0].beats[1].arrows
        assert arrows[0].origin == "e2"
        assert arrows[0].target == "e4"
        assert arrows[0].role is ArrowRole.PLAYED

    def test_an_arrow_to_nowhere_is_dropped_rather_than_drawn(self) -> None:
        held = session()
        walked = held.walk_line(["e4"])
        held.show_line(
            {
                "line_id": walked["line_id"],
                "title": "t",
                "notes": [""],
                "arrows": [{"move_index": 0, "origin": "e4", "target": "z9", "role": "idea"}],
            }
        )
        assert len(held.storyboard().scenes[0].beats[1].arrows) == 1

    def test_the_model_cannot_award_a_question_mark(self) -> None:
        held = session()
        walked = held.walk_line(["e4"])
        assert walked["moves"][0]["glyph"] == ""
        held.show_line(
            {
                "line_id": walked["line_id"],
                "title": "t",
                "notes": [""],
                "praise": [{"move_index": 0, "glyph": "??"}],
            }
        )
        assert held.storyboard().scenes[0].beats[1].glyph is Glyph.PLAIN

    def test_praise_lands_only_where_the_engine_found_no_fault(self) -> None:
        held = session()
        walked = held.walk_line(["e4", "e5", "Qh5", "Ke7"])
        assert [m["glyph"] for m in walked["moves"]] == ["", "?!", "?", "??"]
        held.show_line(
            {
                "line_id": walked["line_id"],
                "title": "t",
                "notes": ["", "", "", ""],
                "praise": [
                    {"move_index": 0, "glyph": "!!"},
                    {"move_index": 3, "glyph": "!!"},
                ],
            }
        )
        beats = held.storyboard().scenes[0].beats
        assert beats[1].glyph is Glyph.BRILLIANT
        assert beats[4].glyph is Glyph.BLUNDER

    def test_a_walked_line_nobody_showed_is_reported_as_unshown(self) -> None:
        held = session()
        held.walk_line(["e4", "e5"])
        assert held.unshown == ["line1"]
        held.show_line({"line_id": "line1", "title": "t", "notes": []})
        assert held.unshown == []

    def test_the_longest_walk_goes_up_when_the_model_showed_nothing(self) -> None:
        held = session()
        held.walk_line(["e4"])
        held.walk_line(["d4", "d5", "c4"])
        assert held.show_longest_walk() is True
        scene = held.storyboard().scenes[0]
        assert [b.move_san for b in scene.beats[1:]] == ["d4", "d5", "c4"]

    def test_there_is_nothing_to_fall_back_on_when_no_line_was_walked(self) -> None:
        assert session().show_longest_walk() is False

    def test_an_idea_arrow_repeating_the_move_is_not_drawn_twice(self) -> None:
        held = session()
        held.walk_line(["e4"])
        held.show_line(
            {
                "line_id": "line1",
                "title": "t",
                "notes": [""],
                "arrows": [{"move_index": 0, "origin": "e2", "target": "e4", "role": "idea"}],
            }
        )
        assert len(held.storyboard().scenes[0].beats[1].arrows) == 1

    def test_two_scenes_agree_about_the_position_they_start_from(self) -> None:
        held = session()
        held.walk_line(["e4", "e5"])
        held.walk_line(["d4", "d5"])
        held.show_line({"line_id": "line1", "title": "a", "notes": []})
        held.show_line({"line_id": "line2", "title": "b", "notes": []})
        scenes = held.storyboard().scenes
        assert scenes[0].beats[0].score_cp == scenes[1].beats[0].score_cp

    def test_a_mate_score_cannot_blow_up_the_loss(self) -> None:
        held = session()
        walked = held.walk_line(["e4", "e5", "Qh5", "Ke7"])
        assert walked["moves"][3]["gave_up_cp"] <= MAX_LOSS_CP
