/** Connectivity as the app experiences it. `navigator.onLine` can say "online" while nothing is reachable (captive portal, weak signal),
 *  so a failed network request marks us offline, and any HTTP response (even an error) marks us reachable again. */
let down = false;
const subs = new Set<() => void>();
export const netDown = () => down;
export function setNetDown(v: boolean) { if (down !== v) { down = v; subs.forEach((f) => f()); } }
export const subscribeNet = (f: () => void) => { subs.add(f); return () => { subs.delete(f); }; };
if (typeof window !== "undefined") window.addEventListener("online", () => setNetDown(false));
