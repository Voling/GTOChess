from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence

import chess

from fiftymoves.analysis.openings import build_families, dominant, family_key
from fiftymoves.domain.games import GameRecord, Side
from fiftymoves.domain.graph import GraphEdge, GraphNode, RepertoireGraph
from fiftymoves.domain.identity import position_key
from fiftymoves.domain.models import PositionKey, Variant


class _Node:
    def __init__(self, key: PositionKey, depth_ply: int, san_path: tuple[str, ...]) -> None:
        self.key = key
        self.depth_ply = depth_ply
        self.san_path = san_path
        self.games: set[str] = set()
        self.families: Counter[str] = Counter()
        self.scores: list[float] = []
        self.as_white = 0

    def observe(self, game: GameRecord, family: str) -> None:
        if game.game_id in self.games:
            return
        self.games.add(game.game_id)
        self.families[family] += 1
        self.scores.append(game.score)
        if game.player_is_white:
            self.as_white += 1

    def player_to_move(self, side: Side) -> bool:
        white_to_move = self.key.epd.split(" ")[1] == "w"
        if side is Side.WHITE:
            return white_to_move
        if side is Side.BLACK:
            return not white_to_move
        return white_to_move == (self.as_white * 2 >= len(self.games))


class _Edge:
    def __init__(self, parent: str, child: str, uci: str, san: str, by_player: bool) -> None:
        self.parent = parent
        self.child = child
        self.uci = uci
        self.san = san
        self.by_player = by_player
        self.games: set[str] = set()


def build_graph(
    games: Sequence[GameRecord],
    *,
    side: Side = Side.BOTH,
    max_ply: int = 12,
    min_volume: int = 1,
    max_children: int = 4,
    family_window_ply: int = 16,
    family_min_games: int = 4,
    family_prior_games: int = 12,
    family_slots: int = 3,
) -> RepertoireGraph:
    standard = [
        g
        for g in games
        if g.variant is Variant.STANDARD and not g.initial_fen and side.covers(g.player_is_white)
    ]
    root_key = position_key(chess.Board())

    nodes: dict[str, _Node] = {
        root_key.digest: _Node(root_key, 0, ()),
    }
    edges: dict[tuple[str, str], _Edge] = {}

    for game in standard:
        board = chess.Board()
        path: tuple[str, ...] = ()
        parent_digest = root_key.digest
        family = family_key(game.opening_name)
        nodes[parent_digest].observe(game, family)

        for ply, san in enumerate(game.moves_san):
            if ply >= max_ply:
                break
            try:
                move = board.parse_san(san)
            except (chess.IllegalMoveError, chess.InvalidMoveError, chess.AmbiguousMoveError):
                break

            by_player = (board.turn == chess.WHITE) == game.player_is_white

            board.push(move)
            path = (*path, san)
            child_key = position_key(board)
            child_digest = child_key.digest

            child = nodes.get(child_digest)
            if child is None:
                child = _Node(child_key, ply + 1, path)
                nodes[child_digest] = child
            child.observe(game, family)

            edge_id = (parent_digest, child_digest)
            edge = edges.get(edge_id)
            if edge is None:
                edge = _Edge(parent_digest, child_digest, move.uci(), san, by_player)
                edges[edge_id] = edge
            edge.games.add(game.game_id)

            parent_digest = child_digest

    by_parent: dict[str, list[_Edge]] = defaultdict(list)
    for edge in edges.values():
        by_parent[edge.parent].append(edge)

    kept: list[_Edge] = []
    pruned_count = 0
    pruned_games: dict[str, int] = defaultdict(int)
    pruned_children: dict[str, int] = defaultdict(int)

    for parent, children in by_parent.items():
        children.sort(key=lambda e: (-len(e.games), e.san))
        for rank, edge in enumerate(children):
            if rank < max_children and len(edge.games) >= min_volume:
                kept.append(edge)
            else:
                pruned_count += 1
                pruned_children[parent] += 1
                pruned_games[parent] += len(edge.games)

    reachable = {root_key.digest}
    for edge in sorted(kept, key=lambda e: len(nodes[e.parent].san_path)):
        if edge.parent in reachable:
            reachable.add(edge.child)
    kept = [e for e in kept if e.parent in reachable and e.child in reachable]

    families = build_families(
        standard,
        window_ply=family_window_ply,
        min_games=family_min_games,
        prior_games=family_prior_games,
        slots=family_slots,
    )
    ranked = {f.key for f in families}

    def _node(digest: str, node: _Node) -> GraphNode:
        counts = {k: v for k, v in node.families.items() if k in ranked}
        family, share = dominant(counts)
        return GraphNode(
            digest=node.key.digest,
            epd=node.key.epd,
            variant=node.key.variant,
            depth_ply=node.depth_ply,
            games=len(node.games),
            player_to_move=node.player_to_move(side),
            san_path=node.san_path,
            pruned_children=pruned_children.get(digest, 0),
            pruned_child_games=pruned_games.get(digest, 0),
            family=family,
            family_share=share,
            score=sum(node.scores) / len(node.scores) if node.scores else 0.5,
        )

    out_nodes = tuple(_node(digest, node) for digest, node in nodes.items() if digest in reachable)

    out_edges = tuple(
        GraphEdge(
            parent=edge.parent,
            child=edge.child,
            uci=edge.uci,
            san=edge.san,
            games=len(edge.games),
            by_player=edge.by_player,
        )
        for edge in kept
    )

    return RepertoireGraph(
        root=root_key.digest,
        side=side,
        nodes=out_nodes,
        edges=out_edges,
        families=tuple(families),
        max_games=max((n.games for n in out_nodes), default=0),
        pruned_edges=pruned_count,
        considered_edges=len(edges),
    )
