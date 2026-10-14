"use client";

import Link from "next/link";
import { AlertTriangle, CircleCheck, Lock, RefreshCw, WifiOff } from "lucide-react";
import type { ReactNode } from "react";
import { errorCode } from "@/lib/backend";
import type { Band } from "@/lib/labels";
import { cn } from "@/lib/utils";
import { Button } from "./ui/button";

export const BAND_DOT: Record<Band, string> = { ok: "bg-ok", warning: "bg-warn", critical: "bg-crit" };

export function StatusDot({ band, className, label }: { band: Band; className?: string; label?: string }) {
  return (
    <span
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      className={cn("inline-block h-2.5 w-2.5 shrink-0 rounded-full", BAND_DOT[band], className)}
    />
  );
}

/** C1: 403 FORBIDDEN / PLANT_SCOPE → lock badge "Not allowed for your role" */
export function LockBadge({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full border border-crit-border bg-crit-soft px-2.5 py-1 text-[13px] font-semibold text-crit-text", className)}>
      <Lock className="h-3.5 w-3.5" aria-hidden />
      Not allowed for your role
    </span>
  );
}

export function EmptyState({ title, children, icon }: { title: string; children?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-line px-4 py-8 text-center">
      {icon ?? <CircleCheck className="h-7 w-7 text-ok" aria-hidden />}
      <p className="m-0 text-[15px] font-semibold text-ink">{title}</p>
      {children && <div className="max-w-md text-sm text-ink-4">{children}</div>}
    </div>
  );
}

/** The issue was never opened, or the demo was reset (GET /incidents/{id} → NOT_FOUND). */
export function IncidentGone({ id }: { id: string }) {
  return (
    <EmptyState title={`Issue ${id} is not open.`}>
      It may have been closed or the demo was reset. <Link href="/">Back to overview</Link>
    </EmptyState>
  );
}

/**
 * Renders the right thing for a query error, switching on error.code (never the message).
 */
export function QueryError({ error, onRetry, what = "this" }: { error: unknown; onRetry?: () => void; what?: string }) {
  const code = errorCode(error);
  if (code === "FORBIDDEN" || code === "PLANT_SCOPE") {
    return (
      <div className="flex flex-col items-start gap-2 rounded-lg border border-line bg-subtle p-4">
        <LockBadge />
        <p className="m-0 text-sm text-ink-4">Ask a Maintenance Lead or Plant Manager if you need to see {what}.</p>
      </div>
    );
  }
  if (code === "NOT_FOUND") return <EmptyState title={`Nothing to show for ${what} yet.`} />;
  if (code === "NETWORK") {
    return (
      <EmptyState title="Can't reach the PlantGuard server." icon={<WifiOff className="h-7 w-7 text-ink-5" aria-hidden />}>
        {onRetry && (
          <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
            <RefreshCw /> Try again
          </Button>
        )}
      </EmptyState>
    );
  }
  return (
    <EmptyState title={`Couldn't load ${what}.`} icon={<AlertTriangle className="h-7 w-7 text-warn" aria-hidden />}>
      {onRetry && (
        <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
          <RefreshCw /> Try again
        </Button>
      )}
    </EmptyState>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-md bg-muted", className)} />;
}

/**
 * Every AI claim must be clickable (README). `evidence.link` is a UI route; we pass
 * the title and snippet along so the evidence page can show them.
 */
export function EvidenceLink({
  link,
  title,
  snippet,
  className,
  children,
}: {
  link: string;
  title: string;
  snippet?: string | null;
  className?: string;
  children?: ReactNode;
}) {
  const isExternal = /^https?:\/\//.test(link);
  const href = isExternal
    ? link
    : `${link}${link.includes("?") ? "&" : "?"}title=${encodeURIComponent(title)}${snippet ? `&snippet=${encodeURIComponent(snippet)}` : ""}`;
  return (
    <Link
      href={href}
      target={isExternal ? "_blank" : undefined}
      className={cn(
        "inline-flex items-center rounded-full border border-brand-border bg-surface px-2.5 py-1 text-[13px] text-brand-text no-underline hover:bg-brand-soft",
        className,
      )}
    >
      {children ?? title}
    </Link>
  );
}

export function PageTitle({ title, sub, right }: { title: ReactNode; sub?: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div className="flex flex-col gap-1.5">
        <h1 className="m-0 text-2xl font-semibold tracking-tight text-ink">{title}</h1>
        {sub && <div className="text-sm text-ink-4">{sub}</div>}
      </div>
      {right && <div className="flex flex-wrap items-center gap-2.5">{right}</div>}
    </div>
  );
}

export const Mono = ({ children, className }: { children: ReactNode; className?: string }) => (
  <span className={cn("font-mono", className)}>{children}</span>
);
