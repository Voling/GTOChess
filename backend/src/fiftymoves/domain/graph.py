from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from fiftymoves.domain.games import Side
from fiftymoves.domain.models import Variant
from fiftymoves.domain.openings import OpeningFamily


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


class RepertoireGraph(BaseModel):
    root: str
    side: Side
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    families: tuple[OpeningFamily, ...]
    max_games: int
    pruned_edges: int
    considered_edges: int

    @property
    def node_count(self) -> int:
        return len(self.nodes)
