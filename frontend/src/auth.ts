// Authorization code flow with PKCE against the Cognito hosted UI. The client
// holds no secret, so the code verifier is what proves the callback belongs to
// the browser that started the sign in.

export interface AuthConfig {
  required: boolean;
  domain: string | null;
  client_id: string | null;
  region: string;
}

export interface Session {
  accessToken: string;
  refreshToken: string | null;
  expiresAt: number;
  email: string | null;
}

const SESSION_KEY = "gtochess.session";
const VERIFIER_KEY = "gtochess.verifier";
const STATE_KEY = "gtochess.state";
// Refresh this far before expiry, so a request never carries a token that dies
// in flight.
const RENEW_MARGIN_MS = 120_000;

export const CALLBACK_PATH = "/auth/callback";

let config: AuthConfig | null = null;
let session: Session | null = null;
let renewal: Promise<string | null> | null = null;

export async function loadConfig(): Promise<AuthConfig> {
  if (config) return config;
  const response = await fetch("/api/auth/config");
  if (!response.ok) throw new Error("could not read the sign in settings");
  config = (await response.json()) as AuthConfig;
  return config;
}

function base64url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function random(length = 64): string {
  return base64url(crypto.getRandomValues(new Uint8Array(length)));
}

async function challenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(verifier),
  );
  return base64url(new Uint8Array(digest));
}

function redirectUri(): string {
  return `${window.location.origin}${CALLBACK_PATH}`;
}

function endpoint(path: string): string {
  if (!config?.domain) throw new Error("no user pool is configured");
  const host = config.domain.includes(".")
    ? config.domain
    : `${config.domain}.auth.${config.region}.amazoncognito.com`;
  return `https://${host}${path}`;
}

// sessionStorage rather than localStorage: a token dies with the tab instead of
// outliving it on a shared machine. The cost is signing in again per tab.
function remember(next: Session | null): void {
  session = next;
  if (next) sessionStorage.setItem(SESSION_KEY, JSON.stringify(next));
  else sessionStorage.removeItem(SESSION_KEY);
}

function recall(): Session | null {
  if (session) return session;
  const raw = sessionStorage.getItem(SESSION_KEY);
  if (!raw) return null;
  try {
    session = JSON.parse(raw) as Session;
  } catch {
    sessionStorage.removeItem(SESSION_KEY);
    return null;
  }
  return session;
}

function emailFrom(idToken: string | undefined): string | null {
  if (!idToken) return null;
  try {
    const body = idToken.split(".")[1];
    const json = atob(body.replace(/-/g, "+").replace(/_/g, "/"));
    const claims = JSON.parse(json) as { email?: string; "cognito:username"?: string };
    return claims.email ?? claims["cognito:username"] ?? null;
  } catch {
    return null;
  }
}

interface TokenResponse {
  access_token: string;
  id_token?: string;
  refresh_token?: string;
  expires_in: number;
}

async function exchange(body: Record<string, string>): Promise<TokenResponse> {
  const response = await fetch(endpoint("/oauth2/token"), {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(body),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`the sign in could not be completed: ${detail}`);
  }
  return (await response.json()) as TokenResponse;
}

function store(tokens: TokenResponse, fallbackRefresh: string | null): void {
  remember({
    accessToken: tokens.access_token,
    refreshToken: tokens.refresh_token ?? fallbackRefresh,
    expiresAt: Date.now() + tokens.expires_in * 1000,
    email: emailFrom(tokens.id_token) ?? recall()?.email ?? null,
  });
}

export async function signIn(): Promise<void> {
  await loadConfig();
  const verifier = random();
  const state = random(16);
  sessionStorage.setItem(VERIFIER_KEY, verifier);
  sessionStorage.setItem(STATE_KEY, state);

  const params = new URLSearchParams({
    response_type: "code",
    client_id: config!.client_id ?? "",
    redirect_uri: redirectUri(),
    scope: "openid email profile",
    state,
    code_challenge: await challenge(verifier),
    code_challenge_method: "S256",
  });
  window.location.assign(endpoint(`/oauth2/authorize?${params}`));
}

// Returns true when this load was a callback, so the caller knows to clean the
// URL rather than leaving a spent code in the address bar.
export async function completeSignIn(): Promise<boolean> {
  if (window.location.pathname !== CALLBACK_PATH) return false;
  const params = new URLSearchParams(window.location.search);
  const code = params.get("code");
  const returned = params.get("state");
  const verifier = sessionStorage.getItem(VERIFIER_KEY);
  const expected = sessionStorage.getItem(STATE_KEY);
  sessionStorage.removeItem(VERIFIER_KEY);
  sessionStorage.removeItem(STATE_KEY);

  if (!code || !verifier) return true;
  if (!returned || returned !== expected) {
    throw new Error("that sign in did not start here");
  }

  await loadConfig();
  const tokens = await exchange({
    grant_type: "authorization_code",
    client_id: config!.client_id ?? "",
    code,
    redirect_uri: redirectUri(),
    code_verifier: verifier,
  });
  store(tokens, null);
  return true;
}

async function renew(held: Session): Promise<string | null> {
  if (!held.refreshToken) {
    remember(null);
    return null;
  }
  try {
    await loadConfig();
    const tokens = await exchange({
      grant_type: "refresh_token",
      client_id: config!.client_id ?? "",
      refresh_token: held.refreshToken,
    });
    store(tokens, held.refreshToken);
    return tokens.access_token;
  } catch {
    remember(null);
    return null;
  }
}

// Concurrent callers share one refresh, so a burst of requests does not spend
// the refresh token several times over.
export async function accessToken(): Promise<string | null> {
  const held = recall();
  if (!held) return null;
  if (Date.now() < held.expiresAt - RENEW_MARGIN_MS) return held.accessToken;
  if (!renewal) {
    renewal = renew(held).finally(() => {
      renewal = null;
    });
  }
  return renewal;
}

export function currentEmail(): string | null {
  return recall()?.email ?? null;
}

export function signedIn(): boolean {
  return recall() !== null;
}

export function forget(): void {
  remember(null);
}

export async function signOut(): Promise<void> {
  remember(null);
  await loadConfig();
  if (!config?.domain) {
    window.location.assign("/");
    return;
  }
  const params = new URLSearchParams({
    client_id: config.client_id ?? "",
    logout_uri: `${window.location.origin}/`,
  });
  window.location.assign(endpoint(`/logout?${params}`));
}
