"use client";
import { cloneElement, isValidElement, useId, type ComponentProps, type ReactElement } from "react";
import { useT } from "@/lib/i18n";

export function Field({ label, hint, error, required, children, className, plain }: { label: string; hint?: string; error?: string; required?: boolean; children: ReactElement<Record<string, unknown>>; className?: string; plain?: boolean }) {
  const id = useId(); const { t } = useT();
  const describedBy = [hint && `${id}-h`, error && `${id}-e`].filter(Boolean).join(" ") || undefined;
  // A wrapper (e.g. several inputs in a row) can't be a label target: expose it as a labelled group instead of pointing <label for> at a <div>.
  const isGroup = isValidElement(children) && typeof children.type === "string" && !["input", "select", "textarea"].includes(children.type);
  const text = (<>{label}{required ? <span className="req" aria-hidden> *</span> : plain ? null : <span className="hint"> ({t("common.optional")})</span>}{required && <span className="sr-only"> ({t("common.required")})</span>}</>);
  const control = isValidElement(children) && !isGroup ? cloneElement(children, { id, "aria-describedby": describedBy, "aria-invalid": error ? true : undefined, required: required || undefined }) : children;
  return (
    <div className={`field ${className ?? ""}`} {...(isGroup ? { role: "group", "aria-labelledby": `${id}-l`, "aria-describedby": describedBy } : {})}>
      {isGroup ? <span id={`${id}-l`} className="label">{text}</span> : <label htmlFor={id}>{text}</label>}
      {control}
      {hint && <span id={`${id}-h`} className="hint">{hint}</span>}
      {error && <span id={`${id}-e`} className="err-text" role="alert">✕ {error}</span>}
    </div>
  );
}
export const Input = (p: ComponentProps<"input">) => <input className="input" {...p} />;
export const Select = (p: ComponentProps<"select">) => <select className="select" {...p} />;
export const Textarea = (p: ComponentProps<"textarea">) => <textarea className="textarea" {...p} />;
