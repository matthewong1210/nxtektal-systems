import { createContext, useContext, type ReactNode } from "react";

export type Tone = "ok" | "warn" | "bad" | "muted" | "info" | "sim";

/** True while the enclosing view shows a last-known snapshot after a failed
 * or expired read. Every Badge inside such a view downgrades its "ok" tone
 * and says so in text, so a stale view can never show a green status. */
export const StaleViewContext = createContext(false);

/** Text-first status chip: the label carries the meaning, color assists. */
export function Badge({ tone, children }: { tone: Tone; children: ReactNode }) {
  const staleView = useContext(StaleViewContext);
  const downgraded = staleView && tone === "ok";
  return (
    <span className={`badge badge-${downgraded ? "warn" : tone}`}>
      {children}
      {downgraded ? " · stale view" : null}
    </span>
  );
}

export function Section({
  title,
  aside,
  children,
  variant,
}: {
  title: string;
  aside?: ReactNode;
  children: ReactNode;
  variant?: "fixture";
}) {
  return (
    <section
      className={variant === "fixture" ? "panel panel-fixture" : "panel"}
      aria-label={title}
    >
      <header className="panel-head">
        <h2>{title}</h2>
        {aside ? <div className="panel-aside">{aside}</div> : null}
      </header>
      <div className="panel-body">{children}</div>
    </section>
  );
}

export function KeyValue({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="kv">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

export function EmptyNote({ children }: { children: ReactNode }) {
  return <p className="empty-note">{children}</p>;
}
