export interface GraphNode {
  digest: string;
  epd: string;
  variant: string;
  depth_ply: number;
  games: number;
  player_to_move: boolean;
  san_path: string[];
  pruned_children: number;
  pruned_child_games: number;
  family: string | null;
  family_share: number;
  score: number;
}

export interface OpeningFamily {
  key: string;
  name: string;
  eco_low: string | null;
  eco_high: string | null;
  games: number;
  as_white: number;
  score: number;
  forcing_rate: number;
  decisive_rate: number;
  sharpness: number;
  slot: number;
}

export interface Claim {
  text: string;
  evidence_id: string;
}

export interface Evidence {
  id: string;
  kind: string;
  statement: string;
}

export interface Explanation {
  digest: string;
  headline: string;
  claims: Claim[];
  evidence: Evidence[];
  source: string;
  model: string | null;
  dropped_claims: number;
}

export interface GraphEdge {
  parent: string;
  child: string;
  uci: string;
  san: string;
  games: number;
  by_player: boolean;
}

export interface RepertoireGraph {
  root: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  families: OpeningFamily[];
  max_games: number;
  pruned_edges: number;
  considered_edges: number;
}

export interface GraphQuery {
  username: string;
  maxPly: number;
  minVolume: number;
  maxChildren: number;
}

export class GraphError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "GraphError";
    this.status = status;
  }
}

function shapeParams(query: GraphQuery): URLSearchParams {
  return new URLSearchParams({
    max_ply: String(query.maxPly),
    min_volume: String(query.minVolume),
    max_children: String(query.maxChildren),
  });
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ detail: response.statusText }));
    throw new GraphError(
      detail.detail ?? `The server answered ${response.status}.`,
      response.status,
    );
  }
  return response.json() as Promise<T>;
}

export function fetchGraph(query: GraphQuery): Promise<RepertoireGraph> {
  const user = encodeURIComponent(query.username);
  return get<RepertoireGraph>(`/api/players/${user}/graph?${shapeParams(query)}`);
}

export function fetchExplanation(query: GraphQuery, digest: string): Promise<Explanation> {
  const user = encodeURIComponent(query.username);
  const path = `/api/players/${user}/positions/${digest}/explanation`;
  return get<Explanation>(`${path}?${shapeParams(query)}`);
}
