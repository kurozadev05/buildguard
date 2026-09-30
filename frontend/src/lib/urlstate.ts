"use client";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";
/** Filter state lives in the URL: shareable, survives refresh, and back/forward works. */
export function useUrlParams<K extends string>(keys: readonly K[]): [Record<K, string>, (patch: Partial<Record<K, string>>) => void] {
  const sp = useSearchParams(); const router = useRouter(); const path = usePathname();
  const values = Object.fromEntries(keys.map((k) => [k, sp.get(k) ?? ""])) as Record<K, string>;
  const set = useCallback((patch: Partial<Record<K, string>>) => {
    const p = new URLSearchParams(sp.toString());
    for (const [k, v] of Object.entries(patch)) { if (v) p.set(k, v as string); else p.delete(k); }
    const s = p.toString(); router.replace(s ? `${path}?${s}` : path, { scroll: false });
  }, [sp, router, path]);
  return [values, set];
}
