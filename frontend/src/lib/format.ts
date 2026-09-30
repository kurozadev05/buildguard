import type { Status } from "./types";
const loc = (lang: string) => (lang === "hi" ? "hi-IN" : "en-IN");
export const fmtDate = (iso?: string | null, lang = "en") => (iso ? new Intl.DateTimeFormat(loc(lang), { day: "2-digit", month: "short", year: "numeric" }).format(new Date(iso)) : "—");
export const fmtDateTime = (iso?: string | null, lang = "en") => (iso ? new Intl.DateTimeFormat(loc(lang), { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(iso)) : "—");
export const fmtNum = (n?: number | null, digits = 1) => (n === null || n === undefined || Number.isNaN(n) ? "—" : new Intl.NumberFormat("en-IN", { maximumFractionDigits: digits }).format(n));
export function timeAgo(iso: string, now = Date.now()): string {
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return "just now"; if (s < 3600) return `${Math.floor(s / 60)} min ago`; if (s < 86400) return `${Math.floor(s / 3600)} h ago`; return `${Math.floor(s / 86400)} d ago`;
}
export const fmtBytes = (n: number) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`);
export const TEST_LABEL: Record<string, string> = { slump: "Slump", fresh_temperature: "Fresh concrete temperature", cube_compressive_strength: "Cube compressive strength", core_strength: "Core strength", upv: "Ultrasonic pulse velocity", rebound_hammer: "Rebound hammer" };
export const OBS_LABEL: Record<string, string> = { crack_width_mm: "Crack width (mm)", moisture_pct: "Moisture (%)", upv_km_s: "UPV (km/s)", rebound_number: "Rebound number", carbonation_depth_mm: "Carbonation depth (mm)", half_cell_mv: "Half-cell potential (mV)", curing_days: "Curing (days)", temperature_c: "Temperature (°C)", humidity_pct: "Humidity (%)" };
export const isStatus = (s: string): s is Status => ["VERIFIED", "REVIEW_REQUIRED", "FLAGGED", "PENDING"].includes(s);
export const uuid = () => (typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : "00000000-0000-4000-8000-000000000000".replace(/[08]/g, () => Math.floor(Math.random() * 16).toString(16)));
