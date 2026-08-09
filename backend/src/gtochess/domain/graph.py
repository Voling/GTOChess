from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from gtochess.domain.games import Side
from gtochess.domain.models import Variant
from gtochess.domain.openings import OpeningFamily


class GraphNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    digest: str
    epd: str
    variant: Variant
    depth_ply: int
    games: int
    player_to_move: bool
    san_path: tuple[str, ...]
    pruned_children: int
    pruned_child_games: int
    family: str | None
    family_share: float
    score: float
    rating: int | None = Field(
        default=None, description="Mean opponent rating across the games through here"
    )
    opening: int | None = Field(
        default=None,
        description="Index into RepertoireGraph.openings, interned to keep nodes small",
    )

    @property
    def label(self) -> str:
        return self.san_path[-1] if self.san_path else "start"


class GraphEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    parent: str
    child: str
    uci: str
    san: str
    games: int
    by_player: bool
    wins: int = 0
    draws: int = 0
    losses: int = 0

    @property
    def decided(self) -> int:
        return self.wins + self.draws + self.losses

    @property
    def score(self) -> float:
        return (self.wins + self.draws / 2) / self.decided if self.decided else 0.5


class OpeningName(BaseModel):
    model_config = ConfigDict(frozen=True)

    eco: str
    name: str


class RepertoireGraph(BaseModel):
    root: str
    side: Side
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    families: tuple[OpeningFamily, ...]
    openings: tuple[OpeningName, ...] = ()
    max_games: int
    pruned_edges: int
    considered_edges: int

    @property
    def node_count(self) -> int:
        return len(self.nodes)
