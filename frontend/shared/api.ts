/* Every request goes through the API gateway with the session cookie
   (HttpOnly, set by auth-svc at login). The gateway takes the tenant and
   user from the token; X-Tenant-Id is only read before login. */

export type ApiResult<T = any> = { ok: boolean; status: number; data: T; ms: number };

let tenant = localStorageGet("voyqi.tenant") || "voyqi-demo";
let onSessionExpired: () => void = () => {};

function localStorageGet(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}

export function setTenant(t: string) {
  tenant = t;
  try { localStorage.setItem("voyqi.tenant", t); } catch { /* private mode */ }
}
export const getTenant = () => tenant;
export function onExpired(fn: () => void) { onSessionExpired = fn; }

async function send(method: string, path: string, body?: unknown): Promise<ApiResult> {
  const started = performance.now();
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Tenant-Id": tenant },
    body: body === undefined ? undefined : typeof body === "string" ? body : JSON.stringify(body),
  });
  const text = await res.text();
  let data: any = text;
  try { data = text ? JSON.parse(text) : null; } catch { /* plain-text error from the gateway */ }
  return { ok: res.ok, status: res.status, data, ms: Math.round(performance.now() - started) };
}

let refreshing: Promise<boolean> | null = null;
async function refreshSession(): Promise<boolean> {
  refreshing ??= send("POST", "/api/v1/auth/refresh", {}).then((r) => r.ok).finally(() => { refreshing = null; });
  return refreshing;
}

/** A request that renews an expired access token once, transparently. */
export async function api<T = any>(method: string, path: string, body?: unknown): Promise<ApiResult<T>> {
  let r = await send(method, path, body);
  if (r.status === 401 && !path.startsWith("/api/v1/auth/")) {
    if (await refreshSession()) r = await send(method, path, body);
    else onSessionExpired();
  }
  return r;
}

export const get = <T = any>(path: string) => api<T>("GET", path);
export const post = <T = any>(path: string, body: unknown = {}) => api<T>("POST", path, body);
export const put = <T = any>(path: string, body: unknown = {}) => api<T>("PUT", path, body);
export const del = <T = any>(path: string) => api<T>("DELETE", path);

export function errorText(r: ApiResult): string {
  const d = r.data;
  if (d && typeof d === "object" && "detail" in d) {
    return typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail);
  }
  return typeof d === "string" && d ? d : `HTTP ${r.status}`;
}

/** Throws the gateway/service error as a readable message. */
export async function must<T = any>(p: Promise<ApiResult<T>>): Promise<T> {
  const r = await p;
  if (!r.ok) throw new Error(`${r.status}: ${errorText(r)}`);
  return r.data;
}
