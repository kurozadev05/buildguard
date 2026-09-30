import type { Role, User } from "./types";
/** Mirrors the backend role sets so the UI can hide what a user can't do. The backend stays the authority: every call is re-checked there. */
const SETS = {
  write: ["admin", "site_engineer", "qa"], test: ["admin", "site_engineer", "qa", "lab"], review: ["admin", "qa", "site_engineer"],
  audit: ["admin", "auditor", "qa"], screen: ["admin", "auditor", "qa", "site_engineer"], admin: ["admin"], any: ["admin", "site_engineer", "qa", "lab", "client", "auditor"],
} as const satisfies Record<string, readonly Role[]>;
export type Cap = keyof typeof SETS;
export const can = (u: User | null | undefined, cap: Cap): boolean => !!u && (SETS[cap] as readonly Role[]).includes(u.role);
