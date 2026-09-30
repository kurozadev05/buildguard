"use client";
import { Activity, Bell, Bot, Boxes, Building2, ClipboardList, FlaskConical, LayoutDashboard, Languages, LineChart, LogOut, Menu, RefreshCw, ScrollText, ShieldCheck, ScanSearch, User as UserIcon, Camera, WifiOff, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Fragment, useEffect, useState, type ReactNode } from "react";
import { useOnline } from "@/lib/hooks";
import { setNetDown } from "@/lib/net";
import { useT } from "@/lib/i18n";
import { can, type Cap } from "@/lib/perm";
import { ProjectProvider, SyncManager, useProject, useQueueCount, useSession } from "./Providers";
import { Button, ErrorState, Skeleton } from "./ui/ui";
import { Select } from "./ui/form";

interface Item { href: string; key: string; icon: typeof Bell; cap?: Cap }
const GROUPS: { key: string; items: Item[] }[] = [
  { key: "nav.quality", items: [
    { href: "/", key: "nav.dashboard", icon: LayoutDashboard }, { href: "/batches", key: "nav.batches", icon: Boxes }, { href: "/tests", key: "nav.tests", icon: FlaskConical },
    { href: "/investigations", key: "nav.investigations", icon: ScanSearch }, { href: "/alerts", key: "nav.alerts", icon: Bell }, { href: "/insights", key: "nav.insights", icon: LineChart, cap: "screen" } ] },
  { key: "nav.site", items: [
    { href: "/projects", key: "nav.projects", icon: Building2 }, { href: "/risk", key: "nav.risk", icon: Activity }, { href: "/evidence", key: "nav.evidence", icon: Camera }, { href: "/advisor", key: "nav.advisor", icon: ClipboardList } ] },
  { key: "nav.ai", items: [{ href: "/assistant", key: "nav.assistant", icon: Bot }] },
  { key: "nav.manage", items: [
    { href: "/audit", key: "nav.audit", icon: ScrollText, cap: "audit" }, { href: "/admin", key: "nav.admin", icon: ShieldCheck, cap: "admin" }, { href: "/sync", key: "nav.sync", icon: RefreshCw }, { href: "/account", key: "nav.account", icon: UserIcon } ] },
];
const TABS: Item[] = [GROUPS[0].items[0], GROUPS[0].items[1], GROUPS[2].items[0], GROUPS[0].items[4]];

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  const { user } = useSession(); const { t } = useT(); const path = usePathname(); const q = useQueueCount();
  const current = (h: string) => (h === "/" ? path === "/" : path === h || path.startsWith(h + "/"));
  return (
    <nav aria-label="Main">
      <ul className="nav">
        {GROUPS.map((g) => {
          const items = g.items.filter((i) => !i.cap || can(user, i.cap));               // hiding is a courtesy: the backend re-checks every call
          if (!items.length) return null;
          return (
            <Fragment key={g.key}>
              <li className="group" aria-hidden>{t(g.key)}</li>
              {items.map((i) => (
                <li key={i.href}>
                  <Link href={i.href} aria-current={current(i.href) ? "page" : undefined} onClick={onNavigate}>
                    <i.icon size={18} aria-hidden />{t(i.key)}
                    {i.href === "/sync" && q.pending + q.failed > 0 && <span className="badge warning" style={{ marginLeft: "auto" }}>{q.pending + q.failed}</span>}
                  </Link>
                </li>
              ))}
            </Fragment>
          );
        })}
      </ul>
    </nav>
  );
}

function Topbar({ onMenu }: { onMenu: () => void }) {
  const { user, logout } = useSession(); const { projects, current, setCurrent } = useProject(); const { t, lang, setLang } = useT(); const online = useOnline();
  return (
    <header className="topbar">
      <Button variant="ghost" icon className="only-mobile" onClick={onMenu} aria-label="Open menu" style={{ color: "#fff" }}><Menu size={22} aria-hidden /></Button>
      <Link href="/" className="brand" aria-label="BUILDGUARD home"><span className="brand-mark" aria-hidden>B</span><span className="only-desktop">BUILDGUARD</span></Link>
      <div className="grow" />
      {projects.length > 0 && (
        <label className="row" style={{ gap: ".4rem" }}><span className="sr-only">{t("common.project")}</span>
          <Select value={current?.id ?? ""} onChange={(e) => setCurrent(e.target.value)} style={{ minHeight: 36, maxWidth: 190 }} aria-label={t("common.project")}>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select>
        </label>
      )}
      {!online && <span className="badge warning" role="status"><WifiOff size={14} aria-hidden />Offline</span>}
      <Button variant="ghost" size="sm" onClick={() => setLang(lang === "en" ? "hi" : "en")} aria-label={`${t("common.language")}: ${lang === "en" ? "हिन्दी" : "English"}`} style={{ color: "#fff" }}><Languages size={18} aria-hidden /><span>{lang === "en" ? "हिन्दी" : "EN"}</span></Button>
      <span className="only-desktop small" style={{ color: "#E5E7EB" }}>{user?.full_name}</span>
      <Button variant="ghost" size="sm" onClick={() => void logout()} aria-label={t("common.signOut")} style={{ color: "#fff" }}><LogOut size={18} aria-hidden /><span className="only-desktop">{t("common.signOut")}</span></Button>
    </header>
  );
}

export default function AppShell({ children }: { children: ReactNode }) {
  const { user, loading, error, reload } = useSession(); const { t } = useT(); const path = usePathname(); const online = useOnline();
  const [drawer, setDrawer] = useState(false);
  useEffect(() => { setDrawer(false); }, [path]);
  // Browser says online but our requests fail ("lie-fi"): probe until the server answers again (any HTTP status means reachable).
  useEffect(() => {
    if (online || typeof navigator === "undefined" || !navigator.onLine) return;
    const iv = setInterval(() => { fetch("/bff/auth/me", { cache: "no-store", headers: { "x-bg-csrf": "1" } }).then(() => setNetDown(false)).catch(() => undefined); }, 8000);
    return () => clearInterval(iv);
  }, [online]);
  if (loading) return <div className="shell"><div className="topbar" /><main className="main" aria-busy><h1 className="sr-only">Loading BUILDGUARD</h1><Skeleton h={28} w={220} /><div style={{ height: 16 }} /><Skeleton h={120} /></main></div>;
  if (!user) return <main className="main"><ErrorState error={error} onRetry={reload} /></main>;
  return (
    <ProjectProvider>
      <SyncManager />
      <a href="#main" className="skip-link">Skip to content</a>
      <div className="shell">
        <Topbar onMenu={() => setDrawer(true)} />
        <aside className="sidebar"><NavList /></aside>
        <div style={{ minWidth: 0 }}>
          {!online && <div className="offline-bar" role="status">{t("offline.banner")}</div>}
          <main id="main" className="main" tabIndex={-1}>{children}</main>
        </div>
        <nav className="tabbar" aria-label="Quick">
          {TABS.map((i) => <Link key={i.href} href={i.href} aria-current={(i.href === "/" ? path === "/" : path.startsWith(i.href)) ? "page" : undefined}><i.icon size={22} aria-hidden />{t(i.key)}</Link>)}
          <button onClick={() => setDrawer(true)} aria-haspopup="dialog"><Menu size={22} aria-hidden />{t("nav.more")}</button>
        </nav>
      </div>
      {drawer && (
        <div className="drawer" onClick={(e) => { if (e.target === e.currentTarget) setDrawer(false); }} role="dialog" aria-modal="true" aria-label="Menu">
          <div className="drawer-panel"><Button variant="ghost" icon onClick={() => setDrawer(false)} aria-label={t("common.close")} style={{ color: "#fff", marginLeft: "auto", display: "flex" }}><X size={20} aria-hidden /></Button><NavList onNavigate={() => setDrawer(false)} /></div>
        </div>
      )}
    </ProjectProvider>
  );
}

export function RequireCap({ cap, children }: { cap: Cap; children: ReactNode }) {
  const { user } = useSession();
  if (!can(user, cap)) return <div className="state card" role="alert"><ShieldCheck size={36} aria-hidden /><h2>Not available for your role</h2><p>Ask an administrator if you need access to this area.</p></div>;
  return <>{children}</>;
}
