"use client";
import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, ApiError } from "@/lib/api";
import { useApiQuery, useOnline } from "@/lib/hooks";
import { LangProvider, useT } from "@/lib/i18n";
import { clearOfflineData, saveSessionSnapshot, setSnapshotOwner } from "@/lib/offline/snapshots";
import { useConfirm } from "./ui/overlay";
import { flushQueue, listQueue, subscribeQueue } from "@/lib/offline/queue";
import { kv } from "@/lib/offline/store";
import type { Project, User } from "@/lib/types";
import { ConfirmProvider, ToastProvider, useToast } from "./ui/overlay";
import { PwaRegister } from "./PwaRegister";

interface SessionValue { user: User | null; loading: boolean; error: unknown; logout: () => Promise<void>; reload: () => void }
const SessionCtx = createContext<SessionValue>({ user: null, loading: true, error: null, logout: async () => undefined, reload: () => undefined });
export const useSession = () => useContext(SessionCtx);
const PUBLIC = ["/login", "/register", "/offline"];

function SessionProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient(); const router = useRouter(); const path = usePathname(); const toast = useToast(); const { t } = useT(); const confirm = useConfirm();
  const isPublic = PUBLIC.includes(path);
  const [user, setUser] = useState<User | null>(null);
  const q = useApiQuery<User>(["session"], "auth/me", { enabled: !isPublic, staleTime: 5 * 60_000 });
  const [cached, setCached] = useState<User | null>(null);

  useEffect(() => {
    if (q.data) { setUser(q.data); setSnapshotOwner(q.data.id); void saveSessionSnapshot(q.data); }
  }, [q.data]);
  // Offline start: no network to verify the session, but we can still open the app from the last saved profile (server still authoritative on every call).
  useEffect(() => {
    if (q.error instanceof ApiError && ["network", "offline", "timeout"].includes(q.error.kind) && !user) {
      kv().get<User>("snap", "session").then((u) => { if (u) { setCached(u); setSnapshotOwner(u.id); } }).catch(() => undefined);
    }
  }, [q.error, user]);

  const endSession = useCallback(async (expired: boolean) => {
    qc.clear(); await clearOfflineData(expired); navigator.serviceWorker?.controller?.postMessage("CLEAR_PAGES"); setUser(null); setCached(null); localStorage.removeItem("bg.project");
    if (expired) toast({ kind: "warning", title: t("err.auth") });
    router.replace(expired ? `/login?next=${encodeURIComponent(path)}&reason=expired` : "/login");
  }, [qc, router, path, toast, t]);

  useEffect(() => {
    let busy = false;
    const h = () => { if (busy || PUBLIC.includes(window.location.pathname)) return; busy = true; void endSession(true).finally(() => { busy = false; }); };
    window.addEventListener("bg:session-expired", h); return () => window.removeEventListener("bg:session-expired", h);
  }, [endSession]);

  const logout = useCallback(async () => {
    const uid = user?.id ?? cached?.id;
    if (uid) {
      const pending = (await listQueue(uid).catch(() => [])).length;
      if (pending > 0 && !(await confirm({ title: "Sign out and discard changes?", message: `${pending} change(s) made offline have not reached the server yet. Signing out deletes them from this device. Reconnect and sync first if you need them.`, confirmLabel: "Discard and sign out", danger: true }))) return;
    }
    try { const r = await fetch("/auth/logout", { method: "POST", headers: { "x-bg-csrf": "1", "content-type": "application/json" }, body: "{}", credentials: "same-origin" }); if (!r.ok) throw new Error(); }
    catch { toast({ kind: "error", title: t("err.network"), message: "You are still signed in." }); return; }
    await endSession(false);
  }, [endSession, toast, t, user, cached, confirm]);

  const value = useMemo<SessionValue>(() => ({ user: user ?? cached, loading: !isPublic && q.isLoading && !cached, error: user || cached ? null : q.error, logout, reload: () => void q.refetch() }), [user, cached, isPublic, logout, q]);
  return <SessionCtx.Provider value={value}>{children}</SessionCtx.Provider>;
}

interface ProjectValue { projects: Project[]; current: Project | null; setCurrent: (id: string) => void; loading: boolean }
const ProjectCtx = createContext<ProjectValue>({ projects: [], current: null, setCurrent: () => undefined, loading: true });
export const useProject = () => useContext(ProjectCtx);
export function ProjectProvider({ children }: { children: ReactNode }) {
  const { user } = useSession();
  const q = useApiQuery<Project[]>(["projects"], "projects", { enabled: !!user, offline: true });
  const [id, setId] = useState<string | null>(null);
  useEffect(() => { setId(localStorage.getItem("bg.project")); }, []);
  const projects = useMemo(() => q.data ?? [], [q.data]);
  const current = projects.find((p) => p.id === id) ?? projects[0] ?? null;
  const setCurrent = useCallback((v: string) => { setId(v); localStorage.setItem("bg.project", v); }, []);   // only an id: not sensitive
  return <ProjectCtx.Provider value={{ projects, current, setCurrent, loading: q.isLoading }}>{children}</ProjectCtx.Provider>;
}

export function useQueueCount() {
  const { user } = useSession(); const [n, setN] = useState({ pending: 0, failed: 0 });
  useEffect(() => {
    if (!user) return; let alive = true;
    const load = () => listQueue(user.id).then((l) => alive && setN({ pending: l.filter((o) => o.status === "pending").length, failed: l.filter((o) => o.status === "failed").length })).catch(() => undefined);
    void load(); const off = subscribeQueue(load); return () => { alive = false; off(); };
  }, [user]);
  return n;
}

/** Sends queued offline changes when the connection returns (and every minute while pending). Reports what the SERVER confirmed. */
export function SyncManager() {
  const { user } = useSession(); const online = useOnline(); const qc = useQueryClient(); const toast = useToast(); const router = useRouter();
  useEffect(() => {
    if (!user || !online) return;
    const run = async () => {
      try {
        const s = await flushQueue(user.id);
        if (s.applied + s.duplicates > 0) { toast({ kind: "success", title: `${s.applied + s.duplicates} offline change(s) confirmed by the server` }); void qc.invalidateQueries(); }
        if (s.failed > 0) toast({ kind: "warning", title: `${s.failed} change(s) were not accepted`, action: { label: "Review", onClick: () => router.push("/sync") } });
      } catch { /* try again on the next tick */ }
    };
    void run(); const iv = setInterval(run, 60_000); return () => clearInterval(iv);
  }, [user, online, qc, toast, router]);
  return null;
}

export function Providers({ children }: { children: ReactNode }) {
  useEffect(() => { document.documentElement.dataset.hydrated = "1"; }, []);   // lets tests (and monitoring) know React is interactive
  const [qc] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: true, networkMode: "always", staleTime: 30_000 }, mutations: { networkMode: "always", retry: false } } }));
  return (
    <QueryClientProvider client={qc}><LangProvider><ToastProvider><ConfirmProvider><SessionProvider>{children}<PwaRegister /></SessionProvider></ConfirmProvider></ToastProvider></LangProvider></QueryClientProvider>
  );
}
export { api };
