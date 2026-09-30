import { ApiError } from "./api";

export interface Friendly { title: string; message: string; retryable: boolean; requestId?: string; fields: Record<string, string> }
/** Turns any failure into calm, user-readable text. Raw backend messages are shown only for validation/conflict, which are written for users. */
export function friendly(e: unknown, t: (k: string) => string): Friendly {
  if (!(e instanceof ApiError)) return { title: t("err.title"), message: t("err.unknown"), retryable: true, fields: {} };
  const k = e.kind;
  const fields: Record<string, string> = {};
  for (const d of e.details) fields[d.field.replace(/^body\./, "")] = d.message;
  const base: Record<string, string> = { offline: "err.offline", network: "err.network", timeout: "err.timeout", auth: "err.auth", forbidden: "err.forbidden", notfound: "err.notfound", validation: "err.validation",
    conflict: "err.conflict", rate_limit: "err.rate", server: "err.server", unknown: "err.unknown" };
  let message = t(base[k] ?? "err.unknown");
  if (e.code.startsWith("AI_") && e.kind !== "rate_limit") message = e.kind === "validation" ? message : t("err.ai");
  if ((k === "validation" || k === "conflict") && e.message && e.message !== "Invalid request") message = e.message;
  if (e.code === "AI_BUDGET_EXCEEDED" || e.code === "AI_RATE_LIMITED") message = e.message;
  if (k === "rate_limit" && e.retryAfter) message += ` (${e.retryAfter}s)`;
  return { title: t("err.title"), message, retryable: !["auth", "forbidden", "validation", "notfound", "conflict"].includes(k), requestId: e.requestId, fields };
}
