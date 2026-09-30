import { kv } from "./store";
/** Read-only copies of recently viewed data so lists still open offline. Scoped to the signed-in user and wiped on sign-out/expiry.
 *  Never used for AI conversations, audit logs, user lists, or credentials. */
let owner = "";
export const setSnapshotOwner = (id: string) => { owner = id; };
export const SNAP_ALLOW = ["projects", "batches", "batch", "tests", "test", "dashboard", "tree", "investigations", "alerts", "risk"];
export const snapshotAllowed = (key: readonly unknown[]) => SNAP_ALLOW.includes(String(key[0]));
const k = (key: readonly unknown[]) => `${owner}|${JSON.stringify(key)}`;
export async function saveSnapshot(key: readonly unknown[], data: unknown) { if (!owner || !snapshotAllowed(key)) return; try { await kv().put("snap", k(key), { data, at: Date.now() }); } catch { /* storage full or blocked: offline copy is best-effort */ } }
export async function loadSnapshot<T>(key: readonly unknown[]): Promise<{ data: T; at: number } | undefined> { if (!owner || !snapshotAllowed(key)) return undefined; try { return await kv().get<{ data: T; at: number }>("snap", k(key)); } catch { return undefined; } }
/** Wipe local copies. `keepQueue` preserves unsynced offline changes (used when a session merely expires, so the same user's work isn't lost).
 *  Also revokes the snapshot owner so late-arriving responses cannot re-save data after sign-out. */
export async function clearOfflineData(keepQueue = false) { owner = ""; try { if (keepQueue) await kv().clear("snap"); else await kv().clear(); } catch { /* ignore */ } }
export async function saveSessionSnapshot(user: unknown) { if (!owner) return; try { await kv().put("snap", "session", user); } catch { /* best effort */ } }
