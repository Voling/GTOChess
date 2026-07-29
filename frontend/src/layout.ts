import type { GraphEdge, GraphNode, RepertoireGraph } from "./api";

export interface PlacedNode {
  node: GraphNode;
  x: number;
  y: number;
  angle: number;
  radius: number;
  depth: number;
  intensity: number;
  span: number;
}

export interface PlacedEdge {
  edge: GraphEdge;
  key: string;
  path: string;
  weight: number;
  source: PlacedNode;
  target: PlacedNode;
  transposition: boolean;
}

export interface DepthRing {
  depth: number;
  radius: number;
}

export interface Placement {
  nodes: PlacedNode[];
  edges: PlacedEdge[];
  rings: DepthRing[];
  byDigest: Map<string, PlacedNode>;
  outgoing: Map<string, PlacedEdge[]>;
  parent: Map<string, string>;
  children: Map<string, string[]>;
  root: string;
  radius: number;
  ringGap: number;
}

export interface Trail {
  active: boolean;
  nodes: Set<string>;
  edges: Set<string>;
}

interface TreeDatum {
  node: GraphNode;
  children: TreeDatum[];
}

const EMPTY_TRAIL: Trail = {
  active: false,
  nodes: new Set(),
  edges: new Set(),
};
const INNER_RING = 0.24;
const VOLUME_WEIGHT = 0.72;

function edgeKey(edge: GraphEdge): string {
  return `${edge.parent}>${edge.child}`;
}

function radialPath(source: PlacedNode, target: PlacedNode): string {
  const mid = (source.radius + target.radius) / 2;
  const c1x = Math.cos(source.angle) * mid;
  const c1y = Math.sin(source.angle) * mid;
  const c2x = Math.cos(target.angle) * mid;
  const c2y = Math.sin(target.angle) * mid;
  return `M${source.x},${source.y}C${c1x},${c1y} ${c2x},${c2y} ${target.x},${target.y}`;
}

function spanningTree(graph: RepertoireGraph): {
  root: TreeDatum | null;
  extra: GraphEdge[];
  parent: Map<string, string>;
  children: Map<string, string[]>;
} {
  const byDigest = new Map(graph.nodes.map((n) => [n.digest, n]));
  const childrenOf = new Map<string, GraphEdge[]>();
  for (const edge of graph.edges) {
    const bucket = childrenOf.get(edge.parent);
    if (bucket) bucket.push(edge);
    else childrenOf.set(edge.parent, [edge]);
  }

  const parent = new Map<string, string>();
  const children = new Map<string, string[]>();
  const rootNode = byDigest.get(graph.root);
  if (!rootNode) return { root: null, extra: [], parent, children };

  const claimed = new Set<string>([graph.root]);
  const extra: GraphEdge[] = [];

  const build = (digest: string): TreeDatum => {
    const datum: TreeDatum = { node: byDigest.get(digest)!, children: [] };
    const outgoing = (childrenOf.get(digest) ?? [])
      .slice()
      .sort((a, b) => b.games - a.games);
    const kids: string[] = [];
    for (const edge of outgoing) {
      if (claimed.has(edge.child)) {
        extra.push(edge);
        continue;
      }
      claimed.add(edge.child);
      parent.set(edge.child, digest);
      kids.push(edge.child);
      datum.children.push(build(edge.child));
    }
    children.set(digest, kids);
    return datum;
  };

  return { root: build(graph.root), extra, parent, children };
}

function maxDepth(datum: TreeDatum, depth = 0): number {
  return datum.children.reduce(
    (deepest, child) => Math.max(deepest, maxDepth(child, depth + 1)),
    depth,
  );
}

export function placeRadial(graph: RepertoireGraph, radius: number): Placement {
  const { root, extra, parent, children } = spanningTree(graph);
  const byDigest = new Map<string, PlacedNode>();
  const outgoing = new Map<string, PlacedEdge[]>();
  const base: Placement = {
    nodes: [],
    edges: [],
    rings: [],
    byDigest,
    outgoing,
    parent,
    children,
    root: graph.root,
    radius,
    ringGap: radius,
  };
  if (!root) return base;

  const maxGames = Math.max(graph.max_games, 1);
  const ceiling = Math.log1p(maxGames);
  const spread = Math.max(maxDepth(root) - 1, 1);
  const ringRadius = new Map<number, number>();

  base.ringGap = (radius * (1 - INNER_RING)) / spread;

  const ringAt = (depth: number) =>
    depth === 0
      ? 0
      : radius * (INNER_RING + (1 - INNER_RING) * ((depth - 1) / spread));

  const place = (datum: TreeDatum, depth: number, from: number, to: number) => {
    const node = datum.node;
    const angle = (from + to) / 2 - Math.PI / 2;
    const distance = ringAt(depth);
    byDigest.set(node.digest, {
      node,
      x: Math.cos(angle) * distance,
      y: Math.sin(angle) * distance,
      angle,
      radius: distance,
      depth,
      intensity: Math.log1p(node.games) / ceiling,
      span: to - from,
    });
    ringRadius.set(depth, distance);

    const kids = datum.children;
    if (kids.length === 0) return;

    const played = kids.reduce((sum, kid) => sum + kid.node.games, 0) || 1;
    const even = 1 / kids.length;
    const width = to - from;
    let cursor = from;
    for (const kid of kids) {
      const share =
        VOLUME_WEIGHT * (kid.node.games / played) + (1 - VOLUME_WEIGHT) * even;
      const next = cursor + width * share;
      place(kid, depth + 1, cursor, next);
      cursor = next;
    }
  };

  place(root, 0, 0, 2 * Math.PI);

  const seen = new Set<string>();
  const collect = (edge: GraphEdge, transposition: boolean) => {
    const source = byDigest.get(edge.parent);
    const target = byDigest.get(edge.child);
    if (!source || !target) return;
    const key = edgeKey(edge);
    if (seen.has(key)) return;
    seen.add(key);
    const placed: PlacedEdge = {
      edge,
      key,
      path: radialPath(source, target),
      weight: Math.log1p(edge.games) / ceiling,
      source,
      target,
      transposition,
    };
    base.edges.push(placed);
    const bucket = outgoing.get(edge.parent);
    if (bucket) bucket.push(placed);
    else outgoing.set(edge.parent, [placed]);
  };
  for (const edge of graph.edges) collect(edge, false);
  for (const edge of extra) collect(edge, true);

  for (const bucket of outgoing.values())
    bucket.sort((a, b) => b.edge.games - a.edge.games);

  base.nodes = [...byDigest.values()];
  base.rings = [...ringRadius.entries()]
    .filter(([depth]) => depth > 0)
    .sort((a, b) => a[0] - b[0])
    .map(([depth, r]) => ({ depth, radius: r }));

  return base;
}

export function pathTo(
  placement: Placement | null,
  digest: string | null,
): Trail {
  if (!placement || !digest || !placement.byDigest.has(digest))
    return EMPTY_TRAIL;
  if (digest === placement.root) return EMPTY_TRAIL;
  const nodes = new Set<string>([digest]);
  const edges = new Set<string>();
  let current = digest;
  while (true) {
    const above = placement.parent.get(current);
    if (!above || nodes.has(above)) break;
    edges.add(`${above}>${current}`);
    nodes.add(above);
    current = above;
  }
  return { active: true, nodes, edges };
}

export function ancestry(
  placement: Placement | null,
  digest: string | null,
): string[] {
  if (!placement || !digest || !placement.byDigest.has(digest)) return [];
  const path = [digest];
  const seen = new Set(path);
  let current = digest;
  while (true) {
    const above = placement.parent.get(current);
    if (!above || seen.has(above)) break;
    path.unshift(above);
    seen.add(above);
    current = above;
  }
  return path;
}

export function walk(
  placement: Placement,
  digest: string,
  key: string,
): string | null {
  if (key === "ArrowLeft") return placement.parent.get(digest) ?? null;
  if (key === "ArrowRight") return placement.children.get(digest)?.[0] ?? null;

  const above = placement.parent.get(digest);
  if (!above) return null;
  const siblings = placement.children.get(above) ?? [];
  const index = siblings.indexOf(digest);
  if (index < 0) return null;
  const next = key === "ArrowUp" ? index - 1 : index + 1;
  return siblings[next] ?? null;
}
