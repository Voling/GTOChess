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

export async function fetchGraph(query: GraphQuery): Promise<RepertoireGraph> {
  const params = new URLSearchParams({
    max_ply: String(query.maxPly),
    min_volume: String(query.minVolume),
    max_children: String(query.maxChildren),
  });
  const response = await fetch(
    `/api/players/${encodeURIComponent(query.username)}/graph?${params}`,
  );
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ detail: response.statusText }));
    throw new GraphError(
      detail.detail ?? `The server answered ${response.status}.`,
      response.status,
    );
  }
  return response.json();
}
