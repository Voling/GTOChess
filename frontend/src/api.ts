import { accessToken } from "./auth";

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
  rating: number | null;
  opening: number | null;
}

export interface OpeningName {
  eco: string;
  name: string;
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
  return post<{ authorize_url: string; state: string }>(
    "/api/auth/lichess/start",
  );
}

export function completeAuth(code: string, state: string): Promise<AuthStatus> {
  const params = new URLSearchParams({ code, state });
  return post<AuthStatus>(`/api/auth/lichess/callback?${params}`);
}

export async function disconnectAuth(): Promise<void> {
  await send<void>("/api/auth/lichess", "DELETE");
}

export function startImport(
  username: string,
  maxGames?: number,
): Promise<ImportJob> {
  const params = new URLSearchParams(
    maxGames ? { max_games: String(maxGames) } : {},
  );
  return post<ImportJob>(
    `/api/players/${encodeURIComponent(username)}/import?${params}`,
  );
}

export function fetchImportJob(
  jobId: string,
  username: string,
): Promise<ImportJob> {
  const params = new URLSearchParams({ username });
  return get<ImportJob>(`/api/imports/${jobId}?${params}`);
}

export type MoveQuality = "??" | "?" | "?!" | "sound";

export interface MoveAnnotation {
  parent: string;
  child: string;
  uci: string;
  san: string;
  quality: MoveQuality;
  loss_cp: number;
  best_san: string;
  games: number;
  depth: number;
}

export interface LossResponse {
  measured_moves: number;
  flagged: number;
  marks: MoveAnnotation[];
}

export interface MoveAccuracy {
  full_move: number;
  positions: number;
  played: number;
  mean_loss_cp: number;
  clean_share: number;
}

export interface FamilyBook {
  key: string;
  name: string;
  games: number;
  positions: number;
  by_move: MoveAccuracy[];
  raw_depth: number;
  book_depth: number;
  clean_share: number;
  mean_loss_cp: number;
}

export interface OpeningPhase {
  families: FamilyBook[];
  book_depth: number;
  clean_share: number;
  mean_loss_cp: number;
  positions_scored: number;
  moves_scored: number;
  band_cp: number;
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

export interface Arrow {
  origin: string;
  target: string;
  role: "played" | "idea" | "threat";
}

export interface Beat {
  index: number;
  epd: string;
  move_uci: string | null;
  move_san: string | null;
  glyph: string;
  arrows: Arrow[];
  highlights: string[];
  note: string;
  score_cp: number | null;
  evidence_id: string | null;
}

export interface Scene {
  title: string;
  beats: Beat[];
}

export interface Storyboard {
  root_epd: string;
  orientation: "white" | "black";
  scenes: Scene[];
}

export interface Analysis {
  explanation: Explanation;
  storyboard: Storyboard;
}

export interface StoredAnalysis {
  state: "ready" | "missing";
  analysis?: Analysis;
  model?: string | null;
}

export function fetchAnalysis(
  query: GraphQuery,
  digest: string,
): Promise<StoredAnalysis> {
  const user = encodeURIComponent(query.username);
  return get<StoredAnalysis>(
    `/api/players/${user}/positions/${digest}/analysis`,
  );
}

export function buildAnalysis(
  query: GraphQuery,
  digest: string,
): Promise<Analysis> {
  const user = encodeURIComponent(query.username);
  const path = `/api/players/${user}/positions/${digest}/analysis`;
  return post<Analysis>(`${path}?${shapeParams(query)}`);
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
  openings: OpeningName[];
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
  // Every /api route needs an account, so the token goes on here rather than at
  // each of the twenty call sites.
  const token = await accessToken();
  const headers = token ? { Authorization: `Bearer ${token}` } : undefined;
  const response = await fetch(path, { method, headers });
  if (!response.ok) {
    const detail = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    throw new GraphError(
      detail.detail ?? `The server answered ${response.status}.`,
      response.status,
    );
  }
  if (response.status === 204) return undefined as T;
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
  return get<RepertoireGraph>(
    `/api/players/${user}/graph?${shapeParams(query)}`,
  );
}

export function fetchMoveLosses(query: GraphQuery): Promise<LossResponse> {
  const user = encodeURIComponent(query.username);
  return get<LossResponse>(`/api/players/${user}/move-losses?${shapeParams(query)}`);
}

export function fetchOpeningPhase(query: GraphQuery): Promise<OpeningPhase> {
  const user = encodeURIComponent(query.username);
  return get<OpeningPhase>(
    `/api/players/${user}/opening-phase?${shapeParams(query)}`,
  );
}

export function explainMove(
  query: GraphQuery,
  digest: string,
  uci: string,
): Promise<Explanation> {
  const user = encodeURIComponent(query.username);
  return post<Explanation>(
    `/api/players/${user}/positions/${digest}/moves/${uci}/explanation`,
  );
}

export interface StoredExplanation {
  state: "ready" | "missing";
  explanation?: Explanation;
  model?: string | null;
}

/** Free. Returns only what has already been written for this position. */
export function fetchExplanation(
  query: GraphQuery,
  digest: string,
): Promise<StoredExplanation> {
  const user = encodeURIComponent(query.username);
  return get<StoredExplanation>(
    `/api/players/${user}/positions/${digest}/explanation`,
  );
}

/** Spends a model call, once per position, then shared by everyone. */
export function requestExplanation(
  query: GraphQuery,
  digest: string,
): Promise<Explanation> {
  const user = encodeURIComponent(query.username);
  const path = `/api/players/${user}/positions/${digest}/explanation`;
  return post<Explanation>(`${path}?${shapeParams(query)}`);
}
