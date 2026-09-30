import "server-only";

export function backendUrl(): string {
  const u = process.env.BACKEND_URL || (process.env.NODE_ENV === "production" ? "" : "http://127.0.0.1:8000");
  if (!u) throw new Error("BACKEND_URL is not set");
  return u.replace(/\/$/, "");
}
export const cookieSecure = (): boolean => (process.env.COOKIE_SECURE ? process.env.COOKIE_SECURE === "true" : process.env.NODE_ENV === "production");
export const allowedOrigins = (): string[] => (process.env.ALLOWED_ORIGINS ?? "").split(",").map((s) => s.trim()).filter(Boolean);
