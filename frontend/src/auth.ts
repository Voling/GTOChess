// Sign in against Cognito directly, so the form is ours rather than the hosted
// page. The password goes from this browser to Cognito over TLS and never
// reaches our own servers.

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
// Refresh this far before expiry, so a request never carries a token that dies
// in flight.
const RENEW_MARGIN_MS = 120_000;

let config: AuthConfig | null = null;
let loading: Promise<AuthConfig> | null = null;
let session: Session | null = null;
let renewal: Promise<string | null> | null = null;
let onLost: (() => void) | null = null;

// The app registers here so a refresh that fails puts the gate back rather than
// leaving a signed in shell firing unauthenticated requests at a closed API.
export function whenSessionLost(handler: () => void): void {
  onLost = handler;
}

export async function loadConfig(): Promise<AuthConfig> {
  if (config) return config;
  // The in-flight promise is cached, not only the result, or every concurrent
  // caller issues its own request.
  loading ??= fetch("/api/auth/config")
    .then((response) => {
      if (!response.ok) throw new Error("could not read the sign in settings");
      return response.json() as Promise<AuthConfig>;
    })
    .then((found) => (config = found))
    .finally(() => {
      loading = null;
    });
  return loading;
}

// sessionStorage rather than localStorage: a token dies with the tab instead of
// outliving it on a shared machine. The cost is signing in again per tab.
function remember(next: Session | null): void {
  const had = session !== null;
  session = next;
  if (next) sessionStorage.setItem(SESSION_KEY, JSON.stringify(next));
  else {
    sessionStorage.removeItem(SESSION_KEY);
    if (had) onLost?.();
  }
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
    const json = atob(idToken.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"));
    const claims = JSON.parse(json) as {
      email?: string;
      "cognito:username"?: string;
    };
    return claims.email ?? claims["cognito:username"] ?? null;
  } catch {
    return null;
  }
}

interface AuthResult {
  AccessToken: string;
  IdToken?: string;
  RefreshToken?: string;
  ExpiresIn: number;
}

// Cognito's unauthenticated API: a plain JSON POST, no signing and no SDK.
async function callCognito(target: string, body: unknown): Promise<Record<string, unknown>> {
  const settings = await loadConfig();
  const response = await fetch(`https://cognito-idp.${settings.region}.amazonaws.com/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-amz-json-1.1",
      "X-Amz-Target": `AWSCognitoIdentityProviderService.${target}`,
    },
    body: JSON.stringify(body),
  });
  const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>;
  if (!response.ok) {
    throw new Error(readable(payload));
  }
  return payload;
}

function readable(payload: Record<string, unknown>): string {
  const kind = String(payload.__type ?? "").split("#").pop() ?? "";
  switch (kind) {
    case "NotAuthorizedException":
    case "UserNotFoundException":
      return "That email and password do not match an account.";
    case "PasswordResetRequiredException":
      return "That account needs a new password. Ask for a reset.";
    case "UserNotConfirmedException":
      return "That account has not been confirmed yet.";
    case "TooManyRequestsException":
    case "LimitExceededException":
      return "Too many attempts. Wait a few minutes and try again.";
    default:
      return String(payload.message ?? "Could not sign in. Try again.");
  }
}

function store(result: AuthResult, fallbackRefresh: string | null): void {
  remember({
    accessToken: result.AccessToken,
    refreshToken: result.RefreshToken ?? fallbackRefresh,
    expiresAt: Date.now() + result.ExpiresIn * 1000,
    email: emailFrom(result.IdToken) ?? recall()?.email ?? null,
  });
}

export async function signIn(email: string, password: string): Promise<void> {
  const settings = await loadConfig();
  const payload = await callCognito("InitiateAuth", {
    AuthFlow: "USER_PASSWORD_AUTH",
    ClientId: settings.client_id,
    AuthParameters: { USERNAME: email.trim(), PASSWORD: password },
  });

  // An admin-created account can be issued needing a new password. Nothing
  // here can set one, so say so rather than failing on a missing token.
  if (payload.ChallengeName) {
    throw new Error("That account needs its password set before it can be used.");
  }
  const result = payload.AuthenticationResult as AuthResult | undefined;
  if (!result?.AccessToken) throw new Error("Cognito returned no token.");
  store(result, null);
}

async function renew(held: Session): Promise<string | null> {
  if (!held.refreshToken) {
    remember(null);
    return null;
  }
  try {
    const settings = await loadConfig();
    const payload = await callCognito("InitiateAuth", {
      AuthFlow: "REFRESH_TOKEN_AUTH",
      ClientId: settings.client_id,
      AuthParameters: { REFRESH_TOKEN: held.refreshToken },
    });
    const result = payload.AuthenticationResult as AuthResult | undefined;
    if (!result?.AccessToken) throw new Error("no token");
    store(result, held.refreshToken);
    return result.AccessToken;
  } catch {
    remember(null);
    return null;
  }
}

// Concurrent callers share one refresh so a burst of requests does not spend
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

export function signOut(): void {
  remember(null);
}
