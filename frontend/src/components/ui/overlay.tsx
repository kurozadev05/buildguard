"use client";
import { X } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Button } from "./ui";
import { useT } from "@/lib/i18n";

/** Native <dialog>: focus trap, Esc to close and focus restore come from the browser. */
export function Modal({ open, onClose, title, children, footer }: { open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null); const { t } = useT();
  useEffect(() => { const d = ref.current; if (!d) return; if (open && !d.open) d.showModal(); if (!open && d.open) d.close(); }, [open]);
  return (
    <dialog ref={ref} onClose={onClose} onClick={(e) => { if (e.target === ref.current) onClose(); }} aria-labelledby="dlg-title">
      {open && <>
        <div className="dlg-head"><h2 id="dlg-title">{title}</h2><Button variant="ghost" size="sm" icon onClick={onClose} aria-label={t("common.close")}><X size={18} aria-hidden /></Button></div>
        <div className="dlg-body">{children}</div>{footer && <div className="dlg-foot">{footer}</div>}
      </>}
    </dialog>
  );
}

interface ConfirmOpts { title: string; message: string; confirmLabel?: string; danger?: boolean }
const ConfirmCtx = createContext<(o: ConfirmOpts) => Promise<boolean>>(async () => false);
export const useConfirm = () => useContext(ConfirmCtx);
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<(ConfirmOpts & { resolve: (v: boolean) => void }) | null>(null); const { t } = useT();
  const confirm = useCallback((o: ConfirmOpts) => new Promise<boolean>((resolve) => setState({ ...o, resolve })), []);
  const done = (v: boolean) => { state?.resolve(v); setState(null); };
  return (
    <ConfirmCtx.Provider value={confirm}>
      {children}
      <Modal open={!!state} onClose={() => done(false)} title={state?.title ?? ""} footer={<><Button onClick={() => done(false)}>{t("common.cancel")}</Button><Button variant={state?.danger ? "danger" : "primary"} onClick={() => done(true)}>{state?.confirmLabel ?? "OK"}</Button></>}>
        <p>{state?.message}</p>
      </Modal>
    </ConfirmCtx.Provider>
  );
}

export interface ToastIn { kind: "success" | "error" | "warning" | "info"; title: string; message?: string; action?: { label: string; onClick: () => void } }
interface ToastItem extends ToastIn { id: number }
const ToastCtx = createContext<(t: ToastIn) => void>(() => undefined);
export const useToast = () => useContext(ToastCtx);
const ICON = { success: "✓", error: "✕", warning: "⚠", info: "ℹ" } as const;
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]); const n = useRef(0);
  const push = useCallback((t: ToastIn) => {
    const id = ++n.current; setItems((x) => [...x.slice(-3), { ...t, id }]);
    setTimeout(() => setItems((x) => x.filter((i) => i.id !== id)), t.kind === "error" || t.action ? 9000 : 5000);
  }, []);
  const value = useMemo(() => push, [push]);
  return (
    <ToastCtx.Provider value={value}>
      {children}
      <div className="toasts" role="region" aria-label="Notifications" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`} role={t.kind === "error" ? "alert" : "status"}>
            <span aria-hidden style={{ fontWeight: 700 }}>{ICON[t.kind]}</span>
            <div className="grow"><strong>{t.title}</strong>{t.message && <div className="small muted">{t.message}</div>}{t.action && <button className="btn sm" style={{ marginTop: 6 }} onClick={t.action.onClick}>{t.action.label}</button>}</div>
            <button className="btn ghost sm icon" aria-label="Dismiss" onClick={() => setItems((x) => x.filter((i) => i.id !== t.id))}><X size={16} aria-hidden /></button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
