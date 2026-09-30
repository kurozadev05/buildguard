import type { ReactNode } from "react";
export function AuthCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <main style={{ minHeight: "100dvh", display: "grid", placeItems: "center", padding: "1rem" }}>
      <div style={{ width: "min(420px, 100%)" }} className="stack">
        <div className="row" style={{ justifyContent: "center" }}><span className="brand-mark" style={{ width: 40, height: 40, fontSize: "1.2rem" }} aria-hidden>B</span><strong style={{ fontSize: "1.3rem", letterSpacing: ".04em" }}>BUILDGUARD</strong></div>
        <div className="card stack"><h1>{title}</h1>{children}</div>
      </div>
    </main>
  );
}
