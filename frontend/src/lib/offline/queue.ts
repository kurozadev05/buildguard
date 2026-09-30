import { api, ApiError } from "../api";
import { kv } from "./store";

export type QueueType = "batch.create" | "sample.create" | "test.create" | "usage.create" | "observation.create";
export interface QueuedOp { op_id: string; user_id: string; type: QueueType; data: Record<string, unknown>; label: string; client_ts: string; status: "pending" | "failed"; error?: string; attempts: number }
interface PushResult { results: { op_id: string; status: "applied" | "duplicate" | "rejected" | "conflict"; error?: string }[]; applied: number; duplicates: number; rejected: number }

const listeners = new Set<() => void>();
export const subscribeQueue = (fn: () => void) => { listeners.add(fn); return () => { listeners.delete(fn); }; };
const emit = () => listeners.forEach((f) => f());

export async function listQueue(userId: string): Promise<QueuedOp[]> {
  return (await kv().all<QueuedOp>("queue")).filter((o) => o.user_id === userId).sort((a, b) => a.client_ts.localeCompare(b.client_ts));
}
export async function enqueue(userId: string, type: QueueType, data: Record<string, unknown>, label: string): Promise<QueuedOp> {
  const op: QueuedOp = { op_id: `op-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`, user_id: userId, type, data, label, client_ts: new Date().toISOString(), status: "pending", attempts: 0 };
  await kv().put("queue", op.op_id, op); emit(); return op;
}
export async function discard(opId: string) { await kv().del("queue", opId); emit(); }
export async function retryFailed(userId: string) { for (const o of await listQueue(userId)) if (o.status === "failed") await kv().put("queue", o.op_id, { ...o, status: "pending", error: undefined }); emit(); }

let flushing: Promise<FlushSummary> | null = null;
export interface FlushSummary { applied: number; duplicates: number; failed: number; remaining: number }
/** Sends pending operations to the server's idempotent sync endpoint. An operation leaves the queue ONLY after the server confirms it. */
export function flushQueue(userId: string): Promise<FlushSummary> {
  flushing ??= (async () => {
    const pending = (await listQueue(userId)).filter((o) => o.status === "pending").slice(0, 100);
    if (!pending.length) return { applied: 0, duplicates: 0, failed: 0, remaining: 0 };
    let res: PushResult;
    try { res = await api<PushResult>("sync/push", { method: "POST", retries: 0, body: { device_id: "web", ops: pending.map((o) => ({ op_id: o.op_id, type: o.type, client_ts: o.client_ts, data: o.data })) } }); }
    catch (e) {
      if (e instanceof ApiError && ["network", "offline", "timeout", "server", "rate_limit"].includes(e.kind)) { for (const o of pending) await kv().put("queue", o.op_id, { ...o, attempts: o.attempts + 1 }); emit(); return { applied: 0, duplicates: 0, failed: 0, remaining: pending.length }; }
      throw e;
    }
    const byId = new Map(pending.map((o) => [o.op_id, o]));
    let failed = 0;
    for (const r of res.results) {
      const o = byId.get(r.op_id); if (!o) continue;
      if (r.status === "applied" || r.status === "duplicate") await kv().del("queue", o.op_id);
      else { failed++; await kv().put("queue", o.op_id, { ...o, status: "failed", error: r.error ?? "Rejected by the server", attempts: o.attempts + 1 }); }
    }
    emit();
    return { applied: res.applied, duplicates: res.duplicates, failed, remaining: (await listQueue(userId)).filter((o) => o.status === "pending").length };
  })().finally(() => { flushing = null; });
  return flushing;
}

export type SubmitResult<T> = { queued: false; result: T } | { queued: true; op: QueuedOp };
/** Online: call the API and only report success on server confirmation. Offline/unreachable: keep it on this device as "pending". Timeouts are NOT queued (outcome unknown). */
export async function submitOrQueue<T>(o: { userId: string; type: QueueType; data: Record<string, unknown>; label: string; online: () => Promise<T> }): Promise<SubmitResult<T>> {
  if (typeof navigator !== "undefined" && navigator.onLine === false) return { queued: true, op: await enqueue(o.userId, o.type, o.data, o.label) };
  try { return { queued: false, result: await o.online() }; } catch (e) {
    if (e instanceof ApiError && (e.kind === "network" || e.kind === "offline")) return { queued: true, op: await enqueue(o.userId, o.type, o.data, o.label) };
    throw e;
  }
}
