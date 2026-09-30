/** Runs once when the web server starts: say plainly whether the backend is reachable, so "blank page" problems are diagnosable in seconds.
 *  The backend usually starts a moment after the web server, so this retries in the background for ~20 s instead of reporting a false alarm. */
export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const url = (process.env.BACKEND_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
  const secure = (process.env.COOKIE_SECURE ?? (process.env.NODE_ENV === "production" ? "true" : "false")) === "true";
  void (async () => {                                   // not awaited: never delays the web server's own start
    for (let attempt = 0; attempt < 20; attempt++) {
      try {
        const r = await fetch(`${url}/ready`, { signal: AbortSignal.timeout(2000) });
        const j = await r.json().catch(() => ({}));
        console.log(`[web] backend ${r.ok ? "reachable" : `answered HTTP ${r.status}`} at ${url}  checks=${JSON.stringify(j.checks ?? {})}  secureCookies=${secure}`);
        return;
      } catch { await new Promise((res) => setTimeout(res, 1000)); }
    }
    console.warn(`[web] backend NOT reachable at ${url} after 20 s. Start it (npm run dev) or fix BACKEND_URL in .env.local. Pages will show a friendly error until it is up.`);
  })();
}
