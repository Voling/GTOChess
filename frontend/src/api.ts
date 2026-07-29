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

export interface AuthStatus {
  connected: boolean;
  source: "env" | "oauth" | null;
  username: string | null;
  export_rate: number;
}

export interface ImportProgress {
  username: string;
  exported: number;
  usable: number;
  skipped: number;
  limit: number | null;
  rate: number;
  eta_seconds: number | null;
}

export interface ImportResult {
  username: string;
  exported: number;
  usable: number;
  seconds: number;
  authenticated: boolean;
}

export interface ImportJob {
  job_id: string;
  username: string;
  state: "queued" | "running" | "done" | "failed";
  progress: ImportProgress | null;
  result: ImportResult | null;
  error: string | null;
}

export function fetchAuthStatus(): Promise<AuthStatus> {
  return get<AuthStatus>("/api/auth/lichess");
}

export function startAuth(): Promise<{ authorize_url: string; state: string }> {
  return post<{ authorize_url: string; state: string }>("/api/auth/lichess/start");
}

export function completeAuth(code: string, state: string): Promise<AuthStatus> {
  const params = new URLSearchParams({ code, state });
  return post<AuthStatus>(`/api/auth/lichess/callback?${params}`);
}

export async function disconnectAuth(): Promise<void> {
  await fetch("/api/auth/lichess", { method: "DELETE" });
}

export function startImport(username: string, maxGames?: number): Promise<ImportJob> {
  const params = new URLSearchParams(maxGames ? { max_games: String(maxGames) } : {});
  return post<ImportJob>(`/api/players/${encodeURIComponent(username)}/import?${params}`);
}

export function fetchImportJob(jobId: string, username: string): Promise<ImportJob> {
  const params = new URLSearchParams({ username });
  return get<ImportJob>(`/api/imports/${jobId}?${params}`);
}

export type MoveQuality = "??" | "?" | "?!" | "sound";

export interface MoveAnnotation {
  parent: string;
  child: string;
  san: string;
  quality: MoveQuality;
  loss_cp: number;
  best_san: string;
  games: number;
  by_player: boolean;
}

export interface AnnotationSet {
  username: string;
  shape: string;
  annotations: MoveAnnotation[];
  positions_searched: number;
  edges_considered: number;
  depth: number;
  truncated: boolean;
}

export interface AnnotationResponse {
  state: "ready" | "missing";
  shape: string;
  annotations?: AnnotationSet;
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
  fallback_reason: string | null;
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
  side: Side;
  nodes: GraphNode[];
  edges: GraphEdge[];
  families: OpeningFamily[];
  max_games: number;
  pruned_edges: number;
  considered_edges: number;
}

export type Side = "white" | "black" | "both";

export interface GraphQuery {
  username: string;
  side: Side;
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
    side: query.side,
    max_ply: String(query.maxPly),
    min_volume: String(query.minVolume),
    max_children: String(query.maxChildren),
  });
}

async function send<T>(path: string, method: string): Promise<T> {
  const response = await fetch(path, { method });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ detail: response.statusText }));
    throw new GraphError(
      detail.detail ?? `The server answered ${response.status}.`,
      response.status,
    );
  }
  return response.json() as Promise<T>;
}

function get<T>(path: string): Promise<T> {
  return send<T>(path, "GET");
}

function post<T>(path: string): Promise<T> {
  return send<T>(path, "POST");
}

export function fetchGraph(query: GraphQuery): Promise<RepertoireGraph> {
  const user = encodeURIComponent(query.username);
  return get<RepertoireGraph>(`/api/players/${user}/graph?${shapeParams(query)}`);
}

export function fetchAnnotations(query: GraphQuery): Promise<AnnotationResponse> {
  const user = encodeURIComponent(query.username);
  return get<AnnotationResponse>(`/api/players/${user}/annotations?${shapeParams(query)}`);
}

export async function startAnnotation(query: GraphQuery): Promise<{ job_id: string }> {
  const user = encodeURIComponent(query.username);
  const response = await fetch(
    `/api/players/${user}/annotations?${shapeParams(query)}`,
    { method: "POST" },
  );
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ detail: response.statusText }));
    throw new GraphError(detail.detail ?? "could not start the analysis", response.status);
  }
  return response.json();
}

export function fetchExplanation(query: GraphQuery, digest: string): Promise<Explanation> {
  const user = encodeURIComponent(query.username);
  const path = `/api/players/${user}/positions/${digest}/explanation`;
  return get<Explanation>(`${path}?${shapeParams(query)}`);
}
