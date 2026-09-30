import { api, ApiError, toApiError } from "./api";

export async function postAuth(path: "login" | "register", body: unknown): Promise<void> {
  let res: Response;
  try { res = await fetch(`/auth/${path}`, { method: "POST", credentials: "same-origin", headers: { "content-type": "application/json", "x-bg-csrf": "1" }, body: JSON.stringify(body) }); }
  catch { throw new ApiError(0, "NETWORK", "Network error", navigator.onLine ? "network" : "offline"); }
  if (!res.ok) {
    const e = await toApiError(res);
    // A 401 here means wrong credentials, not an expired session: show the server's own wording, don't trigger session-expiry handling.
    if (e.status === 401) throw new ApiError(401, "INVALID_CREDENTIALS", e.message || "Incorrect email or password", "validation", [], e.requestId);
    throw e;
  }
}
/** Only same-site relative paths are accepted after login: never an off-site or protocol-relative URL. */
export const safeNext = (n: string | null): string => (n && n.startsWith("/") && !n.startsWith("//") && !n.startsWith("/\\") ? n : "/");

/** After sign-in, prove the browser actually kept the HttpOnly session cookie (some browsers silently drop Secure cookies on http://localhost). */
export async function confirmSession(): Promise<void> {
  try { await api("auth/me", { silent401: true, retries: 0 }); }
  catch (e) { if (e instanceof ApiError && e.kind === "auth") throw new ApiError(0, "COOKIE_REJECTED", "You signed in, but your browser did not keep the session cookie. Safari refuses Secure cookies on plain http://localhost: use Chrome, Edge or Firefox, or set COOKIE_SECURE=false in .env.local and restart.", "validation"); }
}
