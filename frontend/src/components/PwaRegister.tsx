"use client";
import { useEffect } from "react";
import { useSession } from "./Providers";
import { useToast } from "./ui/overlay";

/** Core screens that must work with no network (forms that queue writes + lists that read saved snapshots). */
const WARM = ["/", "/batches", "/batches/new", "/tests", "/tests/new", "/projects", "/investigations", "/alerts", "/risk", "/evidence", "/advisor", "/sync", "/account"];

export function PwaRegister() {
  const toast = useToast(); const { user } = useSession();
  useEffect(() => {
    if (process.env.NODE_ENV !== "production" || !user || !("serviceWorker" in navigator)) return;
    void navigator.serviceWorker.ready.then((reg) => reg.active?.postMessage({ type: "WARM", urls: WARM }));
  }, [user]);
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    if (process.env.NODE_ENV !== "production") {
      // Development: a service worker left over from a production build on this same address would serve stale code and hide your edits. Remove it.
      void navigator.serviceWorker.getRegistrations().then(async (rs) => { for (const r of rs) await r.unregister(); for (const k of await caches.keys()) if (k.startsWith("bg-")) await caches.delete(k); });
      return;
    }
    let reloading = false; const hadController = !!navigator.serviceWorker.controller;
    // Reload only when an EXISTING worker is replaced by an update the user accepted. The first-ever claim (no previous controller) must not reload the page.
    navigator.serviceWorker.addEventListener("controllerchange", () => { if (hadController && !reloading) { reloading = true; window.location.reload(); } });
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).then((reg) => {
      const offer = (w: ServiceWorker) => toast({ kind: "info", title: "Update available", message: "A new version of BUILDGUARD is ready.", action: { label: "Reload", onClick: () => w.postMessage("SKIP_WAITING") } });
      if (reg.waiting && navigator.serviceWorker.controller) offer(reg.waiting);
      reg.addEventListener("updatefound", () => { const w = reg.installing; w?.addEventListener("statechange", () => { if (w.state === "installed" && navigator.serviceWorker.controller) offer(w); }); });
      const iv = setInterval(() => void reg.update().catch(() => undefined), 60 * 60 * 1000); return () => clearInterval(iv);
    }).catch(() => undefined);
  }, [toast]);
  return null;
}
