"use client";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, Clock, Info, Inbox, Loader2, RefreshCw, ShieldAlert, XCircle } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { friendly } from "@/lib/errors";
import { useT } from "@/lib/i18n";
import type { Status } from "@/lib/types";

export function cx(...a: (string | false | null | undefined)[]) { return a.filter(Boolean).join(" "); }

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "danger" | "ghost" | "default"; size?: "sm" | "md"; loading?: boolean; icon?: boolean };
export function Button({ variant = "default", size = "md", loading, icon, className, children, disabled, type = "button", ...p }: BtnProps) {
  return (
    <button type={type} className={cx("btn", variant !== "default" && variant, size === "sm" && "sm", icon && "icon", className)} disabled={disabled || loading} aria-busy={loading || undefined} {...p}>
      {loading && <span className="spinner" aria-hidden />}{children}
    </button>
  );
}
export function LinkButton({ href, variant = "default", size = "md", children, className }: { href: string; variant?: "primary" | "ghost" | "default"; size?: "sm" | "md"; children: ReactNode; className?: string }) {
  return <Link href={href} className={cx("btn", variant !== "default" && variant, size === "sm" && "sm", className)}>{children}</Link>;
}
export function Card({ children, className, title, actions }: { children: ReactNode; className?: string; title?: ReactNode; actions?: ReactNode }) {
  return (
    <section className={cx("card", className)}>
      {(title || actions) && <div className="row between" style={{ marginBottom: ".75rem" }}>{title && <h2 style={{ margin: 0 }}>{title}</h2>}{actions}</div>}
      {children}
    </section>
  );
}
export type Tone = "success" | "warning" | "danger" | "info" | "neutral";
export function Badge({ tone = "neutral", icon, children }: { tone?: Tone; icon?: ReactNode; children: ReactNode }) { return <span className={cx("badge", tone)}>{icon}{children}</span>; }

const STATUS_META: Record<Status, { tone: Tone; Icon: typeof CheckCircle2 }> = {
  VERIFIED: { tone: "success", Icon: CheckCircle2 }, REVIEW_REQUIRED: { tone: "warning", Icon: AlertTriangle }, FLAGGED: { tone: "danger", Icon: XCircle }, PENDING: { tone: "neutral", Icon: Clock },
};
/** Status is always icon + text + colour, never colour alone. */
export function StatusBadge({ status }: { status: string }) {
  const { t } = useT();
  const m = STATUS_META[status as Status];
  if (!m) return <Badge>{status}</Badge>;
  return <Badge tone={m.tone} icon={<m.Icon size={14} aria-hidden />}>{t(`status.${status}`)}</Badge>;
}
export function Alert({ tone = "info", title, children, action }: { tone?: "info" | "warning" | "danger" | "success"; title?: string; children?: ReactNode; action?: ReactNode }) {
  const Icon = { info: Info, warning: AlertTriangle, danger: ShieldAlert, success: CheckCircle2 }[tone];
  return (
    <div className={cx("alert", tone)} role={tone === "danger" ? "alert" : "status"}>
      <Icon size={18} aria-hidden style={{ flex: "none", marginTop: 2 }} />
      <div className="grow">{title && <strong>{title}</strong>}{title && children ? <br /> : null}{children}</div>{action}
    </div>
  );
}
export function Spinner({ label }: { label?: string }) { return <span role="status" className="row" style={{ gap: ".4rem" }}><Loader2 size={16} className="spinner-icon" aria-hidden /><span className={label ? "" : "sr-only"}>{label ?? "Loading"}</span></span>; }
export function Skeleton({ h = 16, w = "100%", style }: { h?: number; w?: number | string; style?: React.CSSProperties }) { return <div className="skeleton" style={{ height: h, width: w, ...style }} aria-hidden />; }
export function SkeletonCards({ n = 4, h = 78 }: { n?: number; h?: number }) { return <div className="grid cols-4" role="status" aria-label="Loading">{Array.from({ length: n }, (_, i) => <div className="card" key={i}><Skeleton h={12} w="50%" /><Skeleton h={h - 40} w="40%" style={{ marginTop: 12 }} /></div>)}</div>; }
export function SkeletonRows({ rows = 6 }: { rows?: number }) { return <div className="card stack-sm" role="status" aria-label="Loading">{Array.from({ length: rows }, (_, i) => <Skeleton key={i} h={38} />)}</div>; }
export function EmptyState({ title, hint, action, icon, bare }: { title: string; hint?: string; action?: ReactNode; icon?: ReactNode; bare?: boolean }) {
  return <div className={bare ? "state" : "state card"}>{icon ?? <Inbox size={36} aria-hidden />}<h2>{title}</h2>{hint && <p>{hint}</p>}{action}</div>;
}
export function ErrorState({ error, onRetry, compact }: { error: unknown; onRetry?: () => void; compact?: boolean }) {
  const { t } = useT();
  const f = friendly(error, t);
  return (
    <div className={cx("state", !compact && "card")} role="alert">
      <ShieldAlert size={36} aria-hidden style={{ color: "var(--danger)" }} /><h2>{f.title}</h2><p>{f.message}</p>
      {onRetry && f.retryable && <Button onClick={onRetry}><RefreshCw size={16} aria-hidden />{t("common.retry")}</Button>}
      {f.requestId && <span className="hint">Reference: <span className="mono">{f.requestId}</span></span>}
    </div>
  );
}
export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return <div className="page-header"><div><h1>{title}</h1>{subtitle && <p>{subtitle}</p>}</div>{actions && <div className="row">{actions}</div>}</div>;
}
export function Stat({ label, value, tone, href }: { label: string; value: ReactNode; tone?: Tone; href?: string }) {
  const body = <div className="stat"><span className="num" style={tone === "danger" ? { color: "var(--danger-ink)" } : undefined}>{value}</span><span className="lbl">{label}</span></div>;
  return <div className="card">{href ? <Link href={href} style={{ textDecoration: "none", color: "inherit" }}>{body}</Link> : body}</div>;
}
export function Tabs({ tabs, value, onChange, label }: { tabs: { id: string; label: string }[]; value: string; onChange: (id: string) => void; label: string }) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs.map((tb) => <button key={tb.id} role="tab" id={`tab-${tb.id}`} aria-selected={value === tb.id} aria-controls={value === tb.id ? `panel-${tb.id}` : undefined} tabIndex={value === tb.id ? 0 : -1} onClick={() => onChange(tb.id)}
        onKeyDown={(e) => { const i = tabs.findIndex((x) => x.id === value); if (e.key === "ArrowRight") onChange(tabs[(i + 1) % tabs.length].id); if (e.key === "ArrowLeft") onChange(tabs[(i - 1 + tabs.length) % tabs.length].id); }}>{tb.label}</button>)}
    </div>
  );
}
export const TabPanel = ({ id, value, children }: { id: string; value: string; children: ReactNode }) => (value === id ? <div role="tabpanel" id={`panel-${id}`} aria-labelledby={`tab-${id}`}>{children}</div> : null);
export function Progress({ value, max, label }: { value: number; max: number; label: string }) {
  const pct = max ? Math.min(100, Math.round((value / max) * 100)) : 0;
  return <div role="progressbar" aria-valuenow={value} aria-valuemin={0} aria-valuemax={max} aria-label={label} className="progress"><span style={{ width: `${pct}%` }} /></div>;
}
export function Kv({ items }: { items: [string, ReactNode][] }) { return <dl className="kv">{items.map(([k, v]) => <div key={k} style={{ display: "contents" }}><dt>{k}</dt><dd>{v ?? "—"}</dd></div>)}</dl>; }
export function StaleNote({ at }: { at?: number }) { const { t } = useT(); return at ? <Alert tone="warning">{t("offline.saved")} {new Date(at).toLocaleString()}.</Alert> : null; }

export function FormError({ error }: { error: unknown }) {
  const { t } = useT();
  if (!error) return null;
  const f = friendly(error, t);
  const extra = Object.entries(f.fields);
  return (
    <div className="alert danger" role="alert"><ShieldAlert size={18} aria-hidden style={{ flex: "none", marginTop: 2 }} />
      <div><strong>{f.message}</strong>{extra.length > 0 && <ul style={{ margin: ".25rem 0 0", paddingLeft: "1.1rem" }}>{extra.map(([k, v]) => <li key={k}>{k}: {v}</li>)}</ul>}{f.requestId && <div className="small">Reference: <span className="mono">{f.requestId}</span></div>}</div>
    </div>
  );
}
