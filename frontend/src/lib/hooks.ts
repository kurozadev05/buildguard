"use client";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useEffect, useState, useSyncExternalStore } from "react";
import { api, ApiError, type Query } from "./api";
import { netDown, subscribeNet } from "./net";
import { loadSnapshot, saveSnapshot, snapshotAllowed } from "./offline/snapshots";

export function useDebounced<T>(value: T, ms = 350): T {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}
const subscribeOnline = (cb: () => void) => { window.addEventListener("online", cb); window.addEventListener("offline", cb); const off = subscribeNet(cb); return () => { window.removeEventListener("online", cb); window.removeEventListener("offline", cb); off(); }; };
/** Online = the browser says so AND our last request reached the server. */
export const useOnline = () => useSyncExternalStore(subscribeOnline, () => navigator.onLine && !netDown(), () => true);

const staleAt = new Map<string, number>();
const skey = (k: readonly unknown[]) => JSON.stringify(k);
const offlineKinds = ["network", "offline", "timeout"];

export interface QOpts { query?: Query; enabled?: boolean; offline?: boolean; staleTime?: number; refetchInterval?: number }
/** GET with dedupe/cancellation (TanStack Query). With `offline`, falls back to the last saved copy when the network is down (and says so). */
export function useApiQuery<T>(key: readonly unknown[], path: string | null, o: QOpts = {}) {
  const fullKey = [...key, o.query ?? {}] as const;
  const q = useQuery<T, ApiError>({
    queryKey: fullKey as QueryKey, enabled: !!path && o.enabled !== false, staleTime: o.staleTime ?? 30_000, refetchInterval: o.refetchInterval, retry: false,
    queryFn: async ({ signal }) => {
      try {
        const data = await api<T>(path!, { query: o.query, signal, retries: o.offline ? 0 : undefined });   // a saved copy is the fallback: don't stall on retries
        staleAt.delete(skey(fullKey));
        if (o.offline && snapshotAllowed(key)) void saveSnapshot(fullKey, data);
        return data;
      } catch (e) {
        if (o.offline && e instanceof ApiError && offlineKinds.includes(e.kind)) {
          const snap = await loadSnapshot<T>(fullKey);
          if (snap) { staleAt.set(skey(fullKey), snap.at); return snap.data; }
        }
        throw e;
      }
    },
  });
  return Object.assign(q, { savedAt: staleAt.get(skey(fullKey)) as number | undefined });
}

/** Offset pagination ("Load more"): the backend caps page size, so we never render unbounded lists. */
export function useInfiniteApi<T>(key: readonly unknown[], path: string, query: Query = {}, pageSize = 20, o: { offline?: boolean; enabled?: boolean } = {}) {
  const q = useInfiniteQuery<T[], ApiError, { pages: T[][] }, QueryKey, number>({
    queryKey: [...key, query, pageSize], initialPageParam: 0, enabled: o.enabled !== false, staleTime: 30_000, retry: false,
    queryFn: async ({ pageParam, signal }) => {
      const k = [...key, query, pageSize, pageParam];
      try {
        const data = await api<T[]>(path, { query: { ...query, limit: pageSize, offset: pageParam }, signal, retries: o.offline ? 0 : undefined });
        staleAt.delete(skey(k)); if (o.offline && pageParam === 0 && snapshotAllowed(key)) void saveSnapshot(k, data);
        return data;
      } catch (e) {
        if (o.offline && pageParam === 0 && e instanceof ApiError && offlineKinds.includes(e.kind)) { const s = await loadSnapshot<T[]>(k); if (s) { staleAt.set(skey(k), s.at); return s.data; } }
        throw e;
      }
    },
    getNextPageParam: (last, all) => (last.length === pageSize ? all.length * pageSize : undefined),
  });
  const items = q.data?.pages.flat() ?? [];
  return Object.assign(q, { items, savedAt: staleAt.get(skey([...key, query, pageSize, 0])) as number | undefined });
}

/** Writes never auto-retry. On success, related queries are refetched so the UI shows server truth. */
export function useApiMutation<V, R>(fn: (v: V) => Promise<R>, o: { invalidate?: QueryKey[]; onSuccess?: (r: R, v: V) => void } = {}) {
  const qc = useQueryClient();
  return useMutation<R, ApiError, V>({
    mutationFn: fn, retry: false, networkMode: "always",
    onSuccess: async (r, v) => { await Promise.all((o.invalidate ?? []).map((k) => qc.invalidateQueries({ queryKey: k }))); o.onSuccess?.(r, v); },
  });
}
