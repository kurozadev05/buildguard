"use client";
import { Field, Input, Select } from "./ui/form";
import { useApiQuery } from "@/lib/hooks";

export const TEST_TYPES = [
  ["cube_compressive_strength", "Cube compressive strength"], ["slump", "Slump"], ["fresh_temperature", "Fresh concrete temperature"],
  ["core_strength", "Core strength"], ["upv", "Ultrasonic pulse velocity (UPV)"], ["rebound_hammer", "Rebound hammer"],
] as const;
export type TState = Record<string, string>;
const nums = (s: string) => s.split(/[\s,;]+/).filter(Boolean).map(Number);
const bad = (n: number) => !Number.isFinite(n) || n <= 0;

/** Turns form text into the exact `values` object the rules engine expects, with plain-language validation. Backend validation stays authoritative. */
export function buildValues(type: string, s: TState): { values: Record<string, unknown>; age_days?: number; errors: Record<string, string> } {
  const errors: Record<string, string> = {}; const values: Record<string, unknown> = {}; let age_days: number | undefined;
  if (type === "cube_compressive_strength") {
    age_days = Number(s.age_days || 28);
    const sp = [s.s1, s.s2, s.s3].map((x) => Number(x));
    if (sp.some((x, i) => ![s.s1, s.s2, s.s3][i] || bad(x))) errors.specimens = "Enter all three specimen strengths in N/mm² (greater than 0)"; else if (sp.some((x) => x > 200)) errors.specimens = "These values look too high for N/mm²";
    values.specimens_mpa = sp;
  } else if (type === "slump") {
    const v = Number(s.slump_mm); if (s.slump_mm === "" || !Number.isFinite(v) || v < 0 || v > 300) errors.slump_mm = "Enter slump in mm (0–300)"; values.slump_mm = v; values.placing_condition = s.placing_condition || "lightly_reinforced";
  } else if (type === "fresh_temperature") {
    const v = Number(s.temperature_c); if (s.temperature_c === "" || !Number.isFinite(v) || v < -10 || v > 70) errors.temperature_c = "Enter the temperature in °C"; values.temperature_c = v;
  } else if (type === "core_strength") {
    const c = nums(s.cores ?? ""); if (c.length < 3 || c.some(bad)) errors.cores = "Enter at least three equivalent cube strengths in N/mm², separated by commas"; values.cores_equiv_cube_mpa = c;
  } else if (type === "upv") {
    const v = Number(s.velocity_km_s); if (s.velocity_km_s === "" || bad(v) || v > 10) errors.velocity_km_s = "Enter the pulse velocity in km/s"; values.velocity_km_s = v;
  } else if (type === "rebound_hammer") {
    const v = Number(s.rebound_number_avg); if (s.rebound_number_avg === "" || bad(v) || v > 100) errors.rebound_number_avg = "Enter the average rebound number"; values.rebound_number_avg = v;
    if (s.estimated_strength_mpa) { const e = Number(s.estimated_strength_mpa); if (bad(e)) errors.estimated_strength_mpa = "Enter a positive strength"; values.estimated_strength_mpa = e; }
  }
  return { values, age_days, errors };
}

export function TestFields({ type, s, set, errors }: { type: string; s: TState; set: (k: string, v: string) => void; errors: Record<string, string> }) {
  const rules = useApiQuery<{ slump: { ranges: Record<string, unknown> } }>(["rules"], type === "slump" ? "rules" : null, { staleTime: Infinity, enabled: type === "slump" });
  const on = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => set(k, e.target.value);
  if (type === "cube_compressive_strength") return (<>
    <Field label="Age at test" required><Select value={s.age_days || "28"} onChange={on("age_days")}>{[3, 7, 14, 28, 56, 90].map((d) => <option key={d} value={d}>{d} days</option>)}</Select></Field>
    <Field label="Specimen strengths (N/mm²)" required error={errors.specimens} hint="Three cubes from the same sample" className="span2">
      <div className="row" style={{ flexWrap: "nowrap" }}>{(["s1", "s2", "s3"] as const).map((k, i) => <Input key={k} type="number" inputMode="decimal" step="any" min="0" aria-label={`Specimen ${i + 1}`} placeholder={`Cube ${i + 1}`} value={s[k] ?? ""} onChange={on(k)} />)}</div></Field></>);
  if (type === "slump") { const opts = Object.keys(rules.data?.slump.ranges ?? { lightly_reinforced: 1 }); return (<>
    <Field label="Slump (mm)" required error={errors.slump_mm}><Input type="number" inputMode="decimal" step="any" min="0" value={s.slump_mm ?? ""} onChange={on("slump_mm")} /></Field>
    <Field label="Placing condition" hint="Sets the acceptable slump range (IS 456 Cl. 7.1)"><Select value={s.placing_condition || "lightly_reinforced"} onChange={on("placing_condition")}>{opts.map((o) => <option key={o} value={o}>{o.replaceAll("_", " ")}</option>)}</Select></Field></>); }
  if (type === "fresh_temperature") return <Field label="Concrete temperature (°C)" required error={errors.temperature_c}><Input type="number" inputMode="decimal" step="any" value={s.temperature_c ?? ""} onChange={on("temperature_c")} /></Field>;
  if (type === "core_strength") return <Field label="Equivalent cube strength of each core (N/mm²)" required error={errors.cores} hint="e.g. 24.5, 26.1, 25.3" className="span2"><Input inputMode="decimal" value={s.cores ?? ""} onChange={on("cores")} /></Field>;
  if (type === "upv") return <Field label="Pulse velocity (km/s)" required error={errors.velocity_km_s}><Input type="number" inputMode="decimal" step="any" min="0" value={s.velocity_km_s ?? ""} onChange={on("velocity_km_s")} /></Field>;
  return (<>
    <Field label="Average rebound number" required error={errors.rebound_number_avg}><Input type="number" inputMode="decimal" step="any" min="0" value={s.rebound_number_avg ?? ""} onChange={on("rebound_number_avg")} /></Field>
    <Field label="Estimated strength (N/mm²)" error={errors.estimated_strength_mpa}><Input type="number" inputMode="decimal" step="any" min="0" value={s.estimated_strength_mpa ?? ""} onChange={on("estimated_strength_mpa")} /></Field></>);
}
