from __future__ import annotations

from collections.abc import Sequence

import chess
import pytest

from fiftymoves.analysis.landscape import compute_landscape
from fiftymoves.analysis.sensitivity import compute_sensitivity
from fiftymoves.domain.explanations import Claim, Evidence, EvidenceKind, Explanation
from fiftymoves.domain.games import Side
from fiftymoves.domain.graph import GraphEdge, GraphNode
from fiftymoves.domain.identity import position_key
from fiftymoves.domain.models import Variant
from fiftymoves.engine.reference import ReferenceEngine
from fiftymoves.llm.explain import (
    ExplanationCache,
    build_provider,
    cache_key,
    explain_position,
    ground,
    study_key,
    study_position,
)
from fiftymoves.llm.facts import build_evidence, mover_cp
from fiftymoves.llm.provider import (
    DeterministicProvider,
    Draft,
    PositionBrief,
    ProviderError,
    effort_level,
    render_request,
)

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

    @property
    def name(self) -> str:
        return "stub"

    @property
    def model(self) -> str | None:
        return "stub-1"

    def explain(self, brief: PositionBrief, evidence: Sequence[Evidence]) -> Draft:
        self.calls += 1
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

    def test_the_key_separates_colours(self) -> None:
        provider = DeterministicProvider()
        white = cache_key("d", "v1", provider, Side.WHITE)
        black = cache_key("d", "v1", provider, Side.BLACK)
        assert white != black
        assert white != cache_key("d", "v1", provider, Side.BOTH)

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
