from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import chess
import pytest

from gtochess.analysis.landscape import compute_landscape
from gtochess.analysis.sensitivity import compute_sensitivity
from gtochess.domain.explanations import Claim, Evidence, EvidenceKind, Explanation
from gtochess.domain.graph import GraphEdge, GraphNode
from gtochess.domain.identity import position_key
from gtochess.domain.knowledge import PositionKnowledge
from gtochess.domain.models import Variant
from gtochess.engine.reference import ReferenceEngine
from gtochess.ingest.knowledge_store import KnowledgeStore
from gtochess.llm.explain import (
    ExplanationCache,
    PersonalContext,
    build_provider,
    cache_key,
    explain_position,
    ground,
    study_key,
    study_position,
    transferable,
)
from gtochess.llm.facts import build_evidence, mover_cp, plan_principles
from gtochess.llm.provider import (
    DeterministicProvider,
    Draft,
    Persona,
    PositionBrief,
    ProviderError,
    effort_level,
    move_cache_breakpoint,
    render_request,
)
from gtochess.llm.tools import EngineProbe

MATE_IN_ONE = "7k/6pp/8/8/8/8/1p6/R5K1 w - - 0 1"


def node_for(board: chess.Board, **overrides: object) -> GraphNode:
    base: dict[str, object] = {
        "digest": position_key(board).digest,
        "epd": position_key(board).epd,
        "variant": Variant.STANDARD,
        "depth_ply": 6,
        "games": 12,
        "player_to_move": True,
        "san_path": ("e4", "e6", "d4", "d5", "Nc3", "Nf6"),
        "pruned_children": 2,
        "pruned_child_games": 3,
        "family": "french-defense",
        "family_share": 0.9,
        "score": 0.625,
    }
    base.update(overrides)
    return GraphNode(**base)  # type: ignore[arg-type]


class StubProvider:
    def __init__(self, draft: Draft) -> None:
        self._draft = draft
        self.calls = 0
        self.saw_probe = False

    @property
    def name(self) -> str:
        return "stub"

    @property
    def model(self) -> str | None:
        return "stub-1"

    def explain(
        self,
        brief: PositionBrief,
        evidence: Sequence[Evidence],
        probe: EngineProbe | None = None,
        persona: Persona = Persona.POSITION,
        ask: str | None = None,
    ) -> Draft:
        self.calls += 1
        self.saw_probe = probe is not None
        return self._draft


class TestMoverPerspective:
    def test_white_reads_the_score_directly(self) -> None:
        assert mover_cp(-30, chess.Board()) == -30

    def test_black_reads_the_score_inverted(self) -> None:
        board = chess.Board()
        board.push_san("e4")
        assert mover_cp(-30, board) == 30


class TestEvidence:
    def test_the_mate_is_stated_not_buried(self) -> None:
        board = chess.Board(MATE_IN_ONE)
        engine = ReferenceEngine()
        report = engine.analyse(board, depth=3, multipv=3)
        sensitivity = compute_sensitivity(engine, board, baseline=report, depth=2)
        landscape = compute_landscape(board, report)
        evidence = build_evidence(board, report, sensitivity, landscape)
        assert any(e.kind is EvidenceKind.EVAL for e in evidence)
        assert any(e.kind is EvidenceKind.ENGINE_LINE for e in evidence)

    def test_repertoire_facts_appear_only_with_a_node(self) -> None:
        board = chess.Board()
        engine = ReferenceEngine()
        report = engine.analyse(board, depth=2, multipv=2)
        sensitivity = compute_sensitivity(engine, board, baseline=report, depth=1)
        landscape = compute_landscape(board, report)

        without = build_evidence(board, report, sensitivity, landscape)
        assert not any(e.kind is EvidenceKind.REPERTOIRE for e in without)

        with_node = build_evidence(board, report, sensitivity, landscape, node=node_for(board))
        assert any(e.id == "rep" for e in with_node)

    def test_continuations_are_quoted_with_their_counts(self) -> None:
        board = chess.Board()
        engine = ReferenceEngine()
        report = engine.analyse(board, depth=2, multipv=2)
        sensitivity = compute_sensitivity(engine, board, baseline=report, depth=1)
        landscape = compute_landscape(board, report)
        node = node_for(board)
        edges = (
            GraphEdge(parent=node.digest, child="x", uci="e2e4", san="e4", games=9, by_player=True),
        )
        evidence = build_evidence(
            board, report, sensitivity, landscape, node=node, continuations=edges
        )
        statement = next(e.statement for e in evidence if e.id == "cont")
        assert "e4 in 9" in statement

    def test_every_fact_carries_an_id(self) -> None:
        board = chess.Board()
        engine = ReferenceEngine()
        report = engine.analyse(board, depth=2, multipv=3)
        sensitivity = compute_sensitivity(engine, board, baseline=report, depth=1)
        landscape = compute_landscape(board, report)
        evidence = build_evidence(board, report, sensitivity, landscape)
        ids = [e.id for e in evidence]
        assert len(ids) == len(set(ids))
        assert all(ids)


class TestGrounding:
    def test_a_claim_citing_unknown_evidence_is_dropped(self) -> None:
        evidence = [Evidence(id="eval", kind=EvidenceKind.EVAL, statement="fact")]
        draft = Draft(
            headline="head",
            claims=(
                Claim(text="grounded", evidence_id="eval"),
                Claim(text="invented", evidence_id="nowhere"),
            ),
        )
        result = ground("abc", draft, evidence, DeterministicProvider())
        assert [c.text for c in result.claims] == ["grounded"]
        assert result.dropped_claims == 1
        assert result.grounded

    def test_a_fully_cited_draft_survives_intact(self) -> None:
        evidence = [Evidence(id="eval", kind=EvidenceKind.EVAL, statement="fact")]
        draft = Draft(headline="head", claims=(Claim(text="a", evidence_id="eval"),))
        result = ground("abc", draft, evidence, DeterministicProvider())
        assert result.dropped_claims == 0
        assert result.source == "deterministic"

    def test_an_explanation_with_no_evidence_is_not_grounded(self) -> None:
        explanation = Explanation(
            digest="abc",
            headline="head",
            claims=(Claim(text="a", evidence_id="eval"),),
            evidence=(),
            source="stub",
        )
        assert explanation.grounded is False


class TestDeterministicProvider:
    def test_it_cites_the_facts_it_reports(self) -> None:
        evidence = [
            Evidence(id="eval", kind=EvidenceKind.EVAL, statement="one"),
            Evidence(id="shape", kind=EvidenceKind.LANDSCAPE, statement="two"),
        ]
        brief = PositionBrief(line="e4 e5", side_to_move="White", variant="standard", depth_ply=2)
        draft = DeterministicProvider().explain(brief, evidence)
        assert [c.evidence_id for c in draft.claims] == ["eval", "shape"]

    def test_it_refuses_to_invent_from_nothing(self) -> None:
        brief = PositionBrief(line="", side_to_move="White", variant="standard", depth_ply=0)
        with pytest.raises(ProviderError):
            DeterministicProvider().explain(brief, [])


class TestRequestRendering:
    def test_facts_are_labelled_by_id(self) -> None:
        evidence = [Evidence(id="eval", kind=EvidenceKind.EVAL, statement="the eval")]
        brief = PositionBrief(line="e4", side_to_move="Black", variant="standard", depth_ply=1)
        rendered = render_request(brief, evidence)
        assert "[eval] the eval" in rendered
        assert "Black to move" in rendered

    def test_the_root_is_named_rather_than_left_blank(self) -> None:
        brief = PositionBrief(line="", side_to_move="White", variant="standard", depth_ply=0)
        assert "the starting position" in render_request(brief, [])


def turn(text: str) -> list[Any]:
    return [{"type": "tool_result", "tool_use_id": text, "content": text}]


class TestCacheBreakpoint:
    def test_the_tail_of_the_conversation_is_marked(self) -> None:
        messages: list[Any] = [
            {"role": "user", "content": "ask"},
            {"role": "user", "content": turn("a")},
        ]
        move_cache_breakpoint(messages)
        assert messages[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}

    def test_only_one_breakpoint_survives_a_second_turn(self) -> None:
        first = turn("a")
        messages: list[Any] = [
            {"role": "user", "content": "ask"},
            {"role": "user", "content": first},
        ]
        move_cache_breakpoint(messages)
        messages.append({"role": "user", "content": turn("b")})
        move_cache_breakpoint(messages)
        assert "cache_control" not in first[0]
        assert "cache_control" in messages[-1]["content"][-1]

    def test_a_plain_string_message_is_left_alone(self) -> None:
        messages: list[Any] = [{"role": "user", "content": "ask"}]
        move_cache_breakpoint(messages)
        assert messages[0]["content"] == "ask"

    def test_model_blocks_are_not_treated_as_dicts(self) -> None:
        block = SimpleNamespace(type="text", text="thinking")
        messages: list[Any] = [
            {"role": "assistant", "content": [block]},
            {"role": "user", "content": turn("a")},
        ]
        move_cache_breakpoint(messages)
        assert not hasattr(block, "cache_control")


class TestCache:
    def test_a_stored_explanation_comes_back(self) -> None:
        cache = ExplanationCache(max_entries=2)
        explanation = Explanation(digest="a", headline="h", claims=(), evidence=(), source="stub")
        cache.put("k", explanation)
        assert cache.get("k") is explanation

    def test_the_oldest_entry_is_evicted_first(self) -> None:
        cache = ExplanationCache(max_entries=2)
        for name in ("a", "b", "c"):
            cache.put(
                name,
                Explanation(digest=name, headline="h", claims=(), evidence=(), source="stub"),
            )
        assert cache.get("a") is None
        assert len(cache) == 2

    def test_reading_an_entry_keeps_it_alive(self) -> None:
        cache = ExplanationCache(max_entries=2)
        for name in ("a", "b"):
            cache.put(
                name,
                Explanation(digest=name, headline="h", claims=(), evidence=(), source="stub"),
            )
        cache.get("a")
        cache.put("c", Explanation(digest="c", headline="h", claims=(), evidence=(), source="s"))
        assert cache.get("a") is not None
        assert cache.get("b") is None

    def test_the_key_separates_pipeline_versions(self) -> None:
        provider = DeterministicProvider()
        assert cache_key("d", "v1", provider) != cache_key("d", "v2", provider)

    def test_the_key_separates_providers(self) -> None:
        stub = StubProvider(Draft(headline="h", claims=()))
        assert cache_key("d", "v1", stub) != cache_key("d", "v1", DeterministicProvider())

    def test_one_position_has_one_explanation(self) -> None:
        provider = DeterministicProvider()
        assert cache_key("d", "v1", provider) == cache_key("d", "v1", provider)

    def test_different_positions_do_not_share(self) -> None:
        provider = DeterministicProvider()
        assert cache_key("a", "v1", provider) != cache_key("b", "v1", provider)

    def test_the_engine_study_is_shared_across_colours(self) -> None:
        assert study_key("d", "v1", 18, 12) == study_key("d", "v1", 18, 12)

    def test_the_engine_study_separates_search_depths(self) -> None:
        assert study_key("d", "v1", 18, 12) != study_key("d", "v1", 20, 12)
        assert study_key("d", "v1", 18, 12) != study_key("d", "v1", 18, 14)


class TestExplainPosition:
    def test_it_grounds_the_draft_against_the_facts_it_gathered(self) -> None:
        board = chess.Board(MATE_IN_ONE)
        provider = StubProvider(
            Draft(
                headline="Mate is available",
                claims=(
                    Claim(text="real", evidence_id="eval"),
                    Claim(text="fabricated", evidence_id="ghost"),
                ),
            )
        )
        explanation = explain_position(
            board,
            engine=ReferenceEngine(),
            provider=provider,
            digest="abc",
            depth=3,
            ablation_depth=2,
            multipv=3,
        )
        assert provider.calls == 1
        assert [c.text for c in explanation.claims] == ["real"]
        assert explanation.dropped_claims == 1
        assert explanation.model == "stub-1"

    def test_a_prepared_study_means_no_engine_is_needed(self) -> None:
        board = chess.Board(MATE_IN_ONE)
        study = study_position(board, engine=ReferenceEngine(), depth=3, ablation_depth=2)
        provider = StubProvider(Draft(headline="h", claims=(Claim(text="a", evidence_id="eval"),)))
        explanation = explain_position(board, provider=provider, digest="abc", study=study)
        assert explanation.claims[0].text == "a"

    def test_without_an_engine_or_a_study_it_refuses(self) -> None:
        provider = StubProvider(Draft(headline="h", claims=()))
        with pytest.raises(ValueError):
            explain_position(chess.Board(), provider=provider, digest="abc")


def personal_context(games: int) -> PersonalContext:
    return PersonalContext(
        node=GraphNode(
            digest="abc",
            epd="8/8/8/8/8/8/8/8 w - -",
            variant=Variant.STANDARD,
            depth_ply=2,
            games=games,
            player_to_move=True,
            san_path=("e4",),
            pruned_children=0,
            pruned_child_games=0,
            family=None,
            family_share=1.0,
            score=0.5,
        )
    )


class TestPersonalEvidence:
    def test_a_shared_explanation_refuses_one_player_s_counts(self) -> None:
        board = chess.Board(MATE_IN_ONE)
        study = study_position(board, engine=ReferenceEngine(), depth=3, ablation_depth=2)
        provider = StubProvider(Draft(headline="h", claims=()))
        with pytest.raises(ValueError):
            explain_position(
                board,
                provider=provider,
                digest="abc",
                study=study,
                personal=personal_context(40),
            )

    def test_two_players_at_one_position_do_not_share_a_key(self) -> None:
        provider = DeterministicProvider()
        mine = cache_key("abc", "v1", provider, personal_context(40))
        theirs = cache_key("abc", "v1", provider, personal_context(9))
        assert mine != theirs
        assert mine != cache_key("abc", "v1", provider)

    def test_a_private_call_carries_the_player_s_own_record(self) -> None:
        board = chess.Board(MATE_IN_ONE)
        study = study_position(board, engine=ReferenceEngine(), depth=3, ablation_depth=2)
        provider = StubProvider(Draft(headline="h", claims=(Claim(text="a", evidence_id="rep"),)))
        explanation = explain_position(
            board,
            provider=provider,
            digest="abc",
            study=study,
            personal=personal_context(40),
            shared=False,
        )
        assert "40 times" in next(e.statement for e in explanation.evidence if e.id == "rep")


class TestProviderSelection:
    def test_no_credentials_falls_back_to_deterministic(self) -> None:
        provider = build_provider(
            kind="auto",
            model="claude-opus-5",
            effort="medium",
            max_tokens=100,
            timeout=1.0,
            api_key=None,
        )
        assert provider.name == "deterministic"

    def test_deterministic_can_be_forced_with_credentials_present(self) -> None:
        provider = build_provider(
            kind="deterministic",
            model="claude-opus-5",
            effort="medium",
            max_tokens=100,
            timeout=1.0,
            api_key="sk-test",
        )
        assert provider.name == "deterministic"

    def test_an_unknown_effort_is_rejected(self) -> None:
        with pytest.raises(ProviderError):
            effort_level("blazing")

    def test_known_efforts_pass_through(self) -> None:
        assert effort_level("xhigh") == "xhigh"


def knowledge_of(digest: str, **overrides: object) -> PositionKnowledge:
    base: dict[str, object] = {
        "digest": digest,
        "epd": "8/8/8/8/8/8/8/8 w - -",
        "variant": Variant.STANDARD,
        "depth": 18,
        "best_san": "Nf3",
        "best_cp": 30,
        "delta_to_second_cp": 20,
        "is_single_answer": False,
        "playable_moves": 4,
        "legal_moves": 30,
        "plan_digest": "p1",
        "plan_tokens": "develop centralise castle",
        "load_bearing": ("e4", "d5"),
    }
    base.update(overrides)
    return PositionKnowledge(**base)  # type: ignore[arg-type]


class TestPlanPrinciples:
    def test_nothing_shared_yields_nothing(self) -> None:
        assert plan_principles(knowledge_of("a"), 0, []) == []
        assert plan_principles(knowledge_of("a"), 2, []) == []

    def test_the_shared_idea_is_quoted_back(self) -> None:
        held = knowledge_of("a")
        others = [knowledge_of("b", best_san="Bb5"), knowledge_of("c", best_san="c4")]
        found = plan_principles(held, 2, others)
        assert found[0].id == "prin1"
        assert found[0].kind is EvidenceKind.PRINCIPLE
        assert "develop centralise" in found[0].statement
        assert "Bb5" in found[0].statement and "c4" in found[0].statement

    def test_a_principle_is_never_an_engine_reading(self) -> None:
        # The whole point of the separate kind: a reader can tell them apart.
        found = plan_principles(knowledge_of("a"), 2, [knowledge_of("b")])
        assert all(e.kind is EvidenceKind.PRINCIPLE for e in found)
        assert all(e.id.startswith("prin") for e in found)

    def test_squares_that_carry_every_position_are_named(self) -> None:
        held = knowledge_of("a", load_bearing=("e4", "d5"))
        others = [knowledge_of("b", load_bearing=("e4", "f7"))]
        statement = next(e for e in plan_principles(held, 2, others) if e.id == "prin2")
        assert "e4" in statement.statement
        assert "d5" not in statement.statement

    def test_no_common_square_means_no_such_claim(self) -> None:
        held = knowledge_of("a", load_bearing=("e4",))
        others = [knowledge_of("b", load_bearing=("h6",))]
        assert not any(e.id == "prin2" for e in plan_principles(held, 2, others))

    def test_forcing_neighbours_are_reported(self) -> None:
        others = [knowledge_of("b", is_single_answer=True)]
        statement = next(
            e for e in plan_principles(knowledge_of("a"), 2, others) if e.id == "prin3"
        )
        assert "only one move" in statement.statement

    def test_it_never_claims_more_than_it_measured(self) -> None:
        # Stating a figure drawn from nine positions beside moves drawn from two
        # is evidence that is false, and a claim citing it still passes ground().
        others = [knowledge_of(str(i), best_san=f"M{i}") for i in range(9)]
        found = plan_principles(knowledge_of("a"), 2, others, limit=2)
        assert "2 other studied positions" in found[0].statement
        assert "of 9 that do" in found[0].statement

    def test_the_forcing_count_is_over_the_same_few(self) -> None:
        others = [knowledge_of(str(i), is_single_answer=True) for i in range(9)]
        found = plan_principles(knowledge_of("a"), 2, others, limit=2)
        assert "In 2 of them" in next(e for e in found if e.id == "prin3").statement

    def test_a_full_set_says_nothing_about_a_wider_one(self) -> None:
        others = [knowledge_of(str(i), best_san=f"M{i}") for i in range(3)]
        found = plan_principles(knowledge_of("a"), 2, others, limit=4)
        assert "that do" not in found[0].statement

    def test_one_neighbour_reads_as_singular(self) -> None:
        found = plan_principles(knowledge_of("a"), 2, [knowledge_of("b")])
        assert "1 other studied position " in found[0].statement


class TestTransferable:
    def test_no_store_means_no_principles(self) -> None:
        assert transferable("abc", None) == []

    def test_a_position_the_store_never_saw_yields_nothing(self, tmp_path: Path) -> None:
        assert transferable("abc", KnowledgeStore(tmp_path)) == []

    def test_a_lone_position_has_nothing_to_transfer_from(self, tmp_path: Path) -> None:
        store = KnowledgeStore(tmp_path)
        store.extend([knowledge_of("abc")])
        assert transferable("abc", store) == []

    def test_company_produces_principles(self, tmp_path: Path) -> None:
        store = KnowledgeStore(tmp_path)
        store.extend([knowledge_of("abc"), knowledge_of("def", best_san="Bb5")])
        found = transferable("abc", store)
        assert found and found[0].kind is EvidenceKind.PRINCIPLE
