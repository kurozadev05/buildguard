# PWA (installable app + offline)

* **Manifest:** `frontend/public/manifest.webmanifest` (name, short name, standalone display, theme/background `#111827`, 192/512/maskable icons, shortcuts). Icons: `frontend/public/icons/`.
* **Service worker:** `frontend/public/sw.js`, registered only in the production-like run (`npm start`).
  * `/_next/static/*`, icons, manifest → cache-first (files are content-hashed)
  * page navigations → network-first, fall back to the cached page, then `/offline`
  * `/bff/*`, `/auth/*`, `/p/*` and every non-GET → **never cached**
  * after sign-in the app asks the worker to pre-cache the core screens and their scripts
* **Versioning / updates:** every `pnpm build` stamps the worker with the build id (`postbuild.mjs`). A new build ⇒ new worker bytes ⇒ the app shows **"Update available → Reload"** ⇒ on accept the new worker takes over and **all old caches are deleted**. The first install never reloads the page by itself.
* **Offline data:** IndexedDB (`bg-offline`): per-user read snapshots + a write queue. Both are wiped on sign-out (signing out with unsynced changes asks first) and on *Account → Clear offline data*. If a session merely expires, unsynced changes are kept for the same user.

## Developing without PWA surprises
* `npm run dev` **never registers** a service worker and, on load, removes any worker/caches left by an earlier `npm start` on the same address. Edits always show.
* If a browser still shows old code: DevTools → *Application* → *Service Workers* → **Unregister**, then *Storage* → **Clear site data**; or open `chrome://serviceworker-internals`.
* To test install/offline: `npm start` → open the app → Install. To test an update: change any code → `npm start -- --build` → in the installed app you'll see the update prompt.

## Requirements of the browser
Service workers need a secure context: `http://localhost` qualifies. Opening the app from another device via your LAN IP over plain http will *not* be installable (browser rule); use a tunnel or local HTTPS proxy for phone testing.
