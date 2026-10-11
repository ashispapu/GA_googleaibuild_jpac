"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { Crosshair, Gauge, LayoutGrid, ListChecks, Lock, LogOut, Search, Shield, TriangleAlert } from "lucide-react";
import { useSession } from "@/lib/session";
import { useAppState } from "@/lib/app-state";
import { useOrders, usePlants } from "@/lib/queries";
import { count, formatDayTime, initials } from "@/lib/format";
import { roleLabel } from "@/lib/labels";
import { cn } from "@/lib/utils";
import { CommandMenu } from "./CommandMenu";
import { PresenterBar } from "./PresenterBar";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutGrid, match: (p: string) => p === "/" },
  { href: "/machines", label: "Machines", icon: Gauge, match: (p: string) => p.startsWith("/machines") },
  { href: "/investigation", label: "Investigation", icon: Search, match: (p: string) => p.startsWith("/investigation") },
  { href: "/options", label: "Options", icon: ListChecks, match: (p: string) => p.startsWith("/options") || p.startsWith("/fixed") },
  { href: "/security", label: "Security", icon: Shield, match: (p: string) => p.startsWith("/security") },
  { href: "/accuracy", label: "Accuracy", icon: Crosshair, match: (p: string) => p.startsWith("/accuracy") },
];

export function AppShell({ children }: { children: ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false);

  // Cmd+K / Ctrl+K opens the command menu (README FE-8)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setMenuOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="flex min-h-screen flex-wrap items-stretch bg-page text-ink">
      <SideNav />
      <div className="flex min-w-0 flex-[999_1_560px] flex-col">
        <TopBar onOpenMenu={() => setMenuOpen(true)} />
        <LlmBanner />
        <main className="flex flex-col gap-5 p-4 sm:p-6">
          {children}
          <PresenterBar />
        </main>
      </div>
      <CommandMenu open={menuOpen} onOpenChange={setMenuOpen} />
    </div>
  );
}

function SideNav() {
  const pathname = usePathname();
  const { activeIncidentId } = useAppState();
  return (
    <nav aria-label="Main" className="flex max-w-none flex-[1_1_200px] flex-col gap-1 border-b border-line bg-surface px-3 py-5 sm:max-w-[220px] sm:border-b-0 sm:border-r">
      <Link href="/" className="flex items-center gap-2.5 px-3 pb-5 text-ink no-underline">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#1D5FD1" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M12 3l7 3v5c0 4.5-3 8.2-7 10-4-1.8-7-5.5-7-10V6l7-3z" />
          <circle cx="12" cy="11.5" r="2.6" />
        </svg>
        <span className="text-[15px] font-semibold">PlantGuard AI</span>
      </Link>
      {NAV.map(({ href, label, icon: Icon, match }) => {
        const active = match(pathname);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex items-center gap-2.5 rounded-md p-3 text-sm no-underline",
              active ? "bg-brand-soft font-semibold text-brand-text" : "font-medium text-ink-3 hover:bg-subtle hover:text-ink",
            )}
          >
            <Icon className="h-[18px] w-[18px]" aria-hidden />
            {label}
            {label === "Machines" && activeIncidentId && <span className="ml-auto h-2.5 w-2.5 rounded-full bg-crit" aria-label="A machine needs attention" />}
            {label === "Investigation" && activeIncidentId && (
              <span className="ml-auto rounded-full bg-crit-soft px-2 font-mono text-[11px] font-semibold text-crit-text" aria-label="1 open issue">
                1
              </span>
            )}
          </Link>
        );
      })}
    </nav>
  );
}

function TopBar({ onOpenMenu }: { onOpenMenu: () => void }) {
  const { me, logout } = useSession();
  const { plantId, setPlantId } = useAppState();
  const plants = usePlants();
  const orders = useOrders();
  const [now, setNow] = useState<Date | null>(null);

  useEffect(() => {
    setNow(new Date());
    const t = setInterval(() => setNow(new Date()), 30_000);
    return () => clearInterval(t);
  }, []);

  const order = orders.data?.[0];
  const allowed = new Set(me?.plant_ids ?? []);

  return (
    <header className="flex flex-wrap items-center gap-3 border-b border-line bg-surface px-4 py-3 sm:px-6">
      <div role="group" aria-label="Plant" className="flex rounded-md border border-line bg-muted p-[3px]">
        {(plants.data ?? [{ id: plantId, name: plantId, location: "", lines: [] }]).map((p) => {
          const locked = allowed.size > 0 && !allowed.has(p.id);
          const active = p.id === plantId;
          return (
            <button
              key={p.id}
              type="button"
              aria-pressed={active}
              disabled={locked}
              title={locked ? "Not allowed for your role" : undefined}
              onClick={() => setPlantId(p.id)}
              className={cn(
                "flex min-h-[36px] items-center gap-1.5 rounded-[6px] px-3 text-[13px]",
                active ? "bg-surface font-semibold text-ink shadow-[0_1px_2px_rgba(16,24,40,0.12)]" : "text-ink-4",
                locked && "cursor-not-allowed opacity-70",
              )}
            >
              {locked && <Lock className="h-3 w-3" aria-hidden />}
              {p.name}
            </button>
          );
        })}
      </div>
      {now && <span className="whitespace-nowrap font-mono text-[13px] text-ink-4">{formatDayTime(now)}</span>}
      {order && (
        <span className="rounded-sm border border-line bg-subtle px-2.5 py-1.5 text-[13px] text-ink-3">
          Order <span className="font-mono">{order.id}</span> · {count(order.completed)} of {count(order.quantity)} · due {formatDayTime(order.due_at)}
        </span>
      )}
      <div className="flex-[1_1_20px]" />
      <button
        type="button"
        onClick={onOpenMenu}
        className="flex h-[38px] w-56 max-w-full items-center justify-between gap-2 whitespace-nowrap rounded-md border border-line-strong/70 bg-subtle px-3 text-[13px] text-ink-5"
      >
        Search or jump to…
        <kbd className="whitespace-nowrap rounded border border-line bg-surface px-1.5 font-mono text-[11px] text-ink-4">Ctrl K</kbd>
      </button>
      {me && (
        <div className="flex items-center gap-2 rounded-full border border-line bg-surface py-1 pl-1 pr-1">
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-brand-soft text-[11px] font-semibold text-brand-text">
            {initials(me.name)}
          </span>
          <span className="text-[13px]">
            {me.name} · {roleLabel(me.role)}
          </span>
          <button
            type="button"
            onClick={() => void logout()}
            aria-label="Sign out"
            title="Sign out"
            className="flex h-8 w-8 items-center justify-center rounded-full text-ink-4 hover:bg-muted"
          >
            <LogOut className="h-4 w-4" />
          </button>
        </div>
      )}
    </header>
  );
}

/** C1: 503 LLM_UNAVAILABLE → banner "Running from recorded analysis" */
function LlmBanner() {
  const { llmUnavailable } = useAppState();
  if (!llmUnavailable) return null;
  return (
    <div role="status" className="flex items-center gap-3 border-b border-warn-border bg-warn-soft px-6 py-2.5 text-sm text-warn-deep">
      <TriangleAlert className="h-4 w-4 shrink-0 text-warn-text" aria-hidden />
      <span>
        <strong>Running from recorded analysis.</strong> The AI service can’t be reached right now, so you’re seeing the saved analysis for this issue.
      </span>
    </div>
  );
}
