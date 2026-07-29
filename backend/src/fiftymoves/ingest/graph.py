from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence

import chess

from fiftymoves.analysis.openings import build_families, dominant, family_key
from fiftymoves.domain.games import GameRecord, Side
from fiftymoves.domain.graph import GraphEdge, GraphNode, OpeningName, RepertoireGraph
from fiftymoves.domain.identity import position_key
from fiftymoves.domain.models import PositionKey, Variant


class _Node:
    def __init__(self, key: PositionKey, depth_ply: int, san_path: tuple[str, ...]) -> None:
        self.key = key
        self.depth_ply = depth_ply
        self.san_path = san_path
        self.games: set[str] = set()
        self.families: Counter[str] = Counter()
        self.openings: Counter[tuple[str, str]] = Counter()
        self.scores: list[float] = []
        self.as_white = 0

    def observe(self, game: GameRecord, family: str) -> None:
        if game.game_id in self.games:
            return
        self.games.add(game.game_id)
        self.families[family] += 1
        if game.opening_name:
            self.openings[(game.eco or "", game.opening_name)] += 1
        self.scores.append(game.score)
        if game.player_is_white:
            self.as_white += 1

    def opening(self, min_share: float = 0.6) -> tuple[str, str] | None:
        """The name only sticks once most games here agree on it.

        Near the root every game passes through, so the commonest name is just
        the commonest opening overall and says nothing about the position.
        """
        if not self.openings or not self.games:
            return None
        pair, count = max(self.openings.items(), key=lambda item: (item[1], item[0]))
        return pair if count / len(self.games) >= min_share else None

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


class GameWalk:
    """Every position the games reach. Independent of how the graph is pruned."""

    def __init__(
        self,
        side: Side,
        max_ply: int,
        root: str,
        nodes: dict[str, _Node],
        edges: dict[tuple[str, str], _Edge],
        games: list[GameRecord],
    ) -> None:
        self.side = side
        self.max_ply = max_ply
        self.root = root
        self.nodes = nodes
        self.edges = edges
        self.games = games


def walk_games(
    games: Sequence[GameRecord], *, side: Side = Side.BOTH, max_ply: int = 12
) -> GameWalk:
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
    # Generating a position's digest means rendering a FEN, which dominates the
    # walk. Games share prefixes, so the same move from the same position is
    # replayed constantly: remember where each one lands.
    lands_on: dict[tuple[str, str], _Node] = {}

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
            uci = move.uci()
            board.push(move)
            path = (*path, san)

            link = (parent_digest, uci)
            child = lands_on.get(link)
            if child is None:
                child_key = position_key(board)
                child = nodes.get(child_key.digest)
                if child is None:
                    child = _Node(child_key, ply + 1, path)
                    nodes[child_key.digest] = child
                lands_on[link] = child
            child.observe(game, family)
            child_digest = child.key.digest

            edge_id = (parent_digest, child_digest)
            edge = edges.get(edge_id)
            if edge is None:
                edge = _Edge(parent_digest, child_digest, uci, san, by_player)
                edges[edge_id] = edge
            edge.games.add(game.game_id)

            parent_digest = child_digest

    return GameWalk(side, max_ply, root_key.digest, nodes, edges, standard)


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
    return prune_walk(
        walk_games(games, side=side, max_ply=max_ply),
        min_volume=min_volume,
        max_children=max_children,
        family_window_ply=family_window_ply,
        family_min_games=family_min_games,
        family_prior_games=family_prior_games,
        family_slots=family_slots,
    )


def prune_walk(
    walk: GameWalk,
    *,
    min_volume: int = 1,
    max_children: int = 4,
    family_window_ply: int = 16,
    family_min_games: int = 4,
    family_prior_games: int = 12,
    family_slots: int = 3,
) -> RepertoireGraph:
    side = walk.side
    standard = walk.games
    nodes = walk.nodes
    edges = walk.edges
    root_digest = walk.root

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

    reachable = {root_digest}
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

    # One table of names for the whole graph; nodes carry an index. A full name
    # on every node would repeat the same forty characters hundreds of times.
    names: dict[tuple[str, str], int] = {}

    def intern(node: _Node) -> int | None:
        pair = node.opening()
        if pair is None:
            return None
        if pair not in names:
            names[pair] = len(names)
        return names[pair]

    def _node(digest: str, node: _Node) -> GraphNode:
        counts = {k: v for k, v in node.families.items() if k in ranked}
        family, share = dominant(counts)
        return GraphNode(
            opening=intern(node),
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
        root=root_digest,
        side=side,
        nodes=out_nodes,
        edges=out_edges,
        families=tuple(families),
        openings=tuple(
            OpeningName(eco=eco, name=name)
            for (eco, name), _ in sorted(names.items(), key=lambda pair: pair[1])
        ),
        max_games=max((n.games for n in out_nodes), default=0),
        pruned_edges=pruned_count,
        considered_edges=len(edges),
    )
