"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { ArrowRight, Building2, HardHat, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/form";
import { useSession } from "@/lib/session";
import { API, errorCode } from "@/lib/backend";
import { roleLabel } from "@/lib/labels";
import { cn } from "@/lib/utils";

interface DemoUser {
  username: string;
  name: string;
  role: string;
  plant_ids: string[];
  technician_id?: string | null;
  open_jobs: { incident_id: string; asset_id: string; status: string }[];
  signoffs?: { incident_id: string; step: string }[];
}

/** Used when the backend has no demo-user list (scripted story / mock server). */
const FALLBACK: DemoUser[] = [
  { username: "op", name: "Operator", role: "operator", plant_ids: [], open_jobs: [] },
  { username: "lead", name: "Maintenance Lead", role: "maintenance_lead", plant_ids: [], open_jobs: [] },
  { username: "manager", name: "Plant Manager", role: "plant_manager", plant_ids: [], open_jobs: [] },
  { username: "admin", name: "Admin", role: "admin", plant_ids: [], open_jobs: [] },
];

/** GET /auth/demo-users (no auth, simulated plant only): every demo user, with each technician's open jobs. */
function useDemoUsers(enabled: boolean) {
  const [users, setUsers] = useState<DemoUser[]>(FALLBACK);
  useEffect(() => {
    if (!enabled) return;
    let stop = false;
    const load = async () => {
      try {
        const r = await fetch(`${API}/auth/demo-users`);
        if (!r.ok) return;
        const list = (await r.json()) as DemoUser[];
        if (!stop && list.length) setUsers(list);
      } catch {
        /* keep the fallback */
      }
    };
    void load();
    const t = setInterval(load, 10_000); // jobs move while the login page is open
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [enabled]);
  return users;
}

const shortName = (name: string) => name.replace(/\s*\(.*\)$/, "");
const plantText = (ids: string[]) => ids.map((p) => p.replace("PLANT-", "")).join("+");

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <Login />
    </Suspense>
  );
}

function Login() {
  const router = useRouter();
  const params = useSearchParams();
  const { status, mode, me, configError, loginLocal, loginSso } = useSession();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("demo");
  const [error, setError] = useState<string | null>(params.get("expired") ? "You were signed out. Please sign in again." : null);
  const [busy, setBusy] = useState(false);
  const demoUsers = useDemoUsers(mode === "local");

  useEffect(() => {
    if (status === "signed-in") router.replace(me?.role === "technician" ? "/jobs" : "/"); // technicians land on their jobs
  }, [status, me, router]);

  const signIn = async (user: string, pass: string) => {
    setBusy(true);
    setError(null);
    try {
      await loginLocal(user.trim(), pass); // the effect above moves on once signed in
    } catch (err) {
      setError(errorCode(err) === "NETWORK" ? "Can't reach the PlantGuard server." : "That username or password didn't work.");
    } finally {
      setBusy(false);
    }
  };
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    void signIn(username, password);
  };

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-5 bg-page px-4 py-12">
      <div className="flex w-full max-w-[440px] flex-col gap-6 rounded-[14px] border border-line bg-surface p-8 shadow-[0_8px_24px_rgba(16,24,40,0.06)]">
        <div className="flex items-center gap-3.5">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-brand-border bg-brand-soft">
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#1D5FD1" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M12 3l7 3v5c0 4.5-3 8.2-7 10-4-1.8-7-5.5-7-10V6l7-3z" />
              <circle cx="12" cy="11.5" r="2.6" />
            </svg>
          </div>
          <div className="flex flex-col gap-0.5">
            <h1 className="m-0 text-[22px] font-semibold tracking-tight">PlantGuard AI</h1>
            <p className="m-0 text-sm text-ink-4">Keeps your machines running and your orders on time</p>
          </div>
        </div>

        {configError && <p className="m-0 rounded-md border border-warn-border bg-warn-soft px-3 py-2 text-sm text-warn-deep">{configError}</p>}

        {mode === "keycloak" ? (
          <Button size="lg" onClick={() => void loginSso()}>
            <Building2 /> Sign in with your company account
          </Button>
        ) : (
          <form onSubmit={submit} className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="u">Username</Label>
              <Input id="u" value={username} onChange={(e) => setUsername(e.target.value)} placeholder="e.g. lead, or a technician like meera" autoComplete="username" required />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="p">Password</Label>
              <Input id="p" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
            </div>
            {error && <p role="alert" className="m-0 text-sm text-crit-text">{error}</p>}
            <Button type="submit" size="lg" disabled={busy || mode === null}>
              {busy ? <Loader2 className="animate-spin" /> : null}
              Sign in <ArrowRight />
            </Button>
          </form>
        )}
      </div>

      {mode !== "keycloak" && (
        <DemoUsers users={demoUsers} busy={busy} onPick={(u) => void signIn(u, "demo")} onFill={setUsername} />
      )}
      <p className="m-0 text-[13px] text-ink-5">You’ll be signed out after 15 minutes · connection encrypted</p>
    </div>
  );
}

function DemoUsers({ users, busy, onPick, onFill }: { users: DemoUser[]; busy: boolean; onPick: (u: string) => void; onFill: (u: string) => void }) {
  const staff = users.filter((u) => u.role !== "technician");
  const techs = users.filter((u) => u.role === "technician");
  return (
    <div className="flex w-full max-w-[560px] flex-col gap-3 rounded-xl border border-dashed border-line-strong bg-surface px-4 py-3.5">
      <div className="text-[13px] text-ink-4">
        Demo users · password <span className="font-mono text-ink">demo</span> · click to sign in
      </div>
      <div className="flex flex-wrap gap-2 text-[13px]">
        {staff.map((d) => (
          <button
            key={d.username}
            type="button"
            disabled={busy}
            onClick={() => onPick(d.username)}
            onContextMenu={(e) => { e.preventDefault(); onFill(d.username); }}
            className="rounded-sm border border-line bg-page px-2.5 py-1.5 text-left text-ink hover:border-brand-border disabled:opacity-60"
          >
            <span className="font-mono">{d.username}</span> · {roleLabel(d.role)}
            {d.plant_ids.length > 0 && <span className="text-ink-5"> · Plant {plantText(d.plant_ids)}</span>}
            {!!d.signoffs?.length && (
              <span className="ml-1 rounded-full bg-warn-soft px-1.5 font-semibold text-warn-text">
                sign: {Array.from(new Set(d.signoffs.map((x) => x.incident_id))).join(", ")}
              </span>
            )}
          </button>
        ))}
      </div>
      {techs.length > 0 && (
        <>
          <div className="flex items-center gap-1.5 border-t border-line-soft pt-2.5 text-[13px] font-semibold text-ink-3">
            <HardHat className="h-4 w-4" aria-hidden /> Technicians — sign in as the one a job is assigned to
          </div>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] gap-2 text-[13px]">
            {techs.map((d) => {
              const busyTech = d.open_jobs.length > 0;
              return (
                <button
                  key={d.username}
                  type="button"
                  disabled={busy}
                  onClick={() => onPick(d.username)}
                  className={cn(
                    "flex flex-col gap-0.5 rounded-md border px-2.5 py-2 text-left hover:border-brand-border disabled:opacity-60",
                    busyTech ? "border-brand-border bg-brand-soft" : "border-line bg-page",
                  )}
                >
                  <span className="text-ink">
                    <span className="font-semibold">{shortName(d.name)}</span> <span className="font-mono text-ink-4">{d.username}</span>
                  </span>
                  <span className="text-xs text-ink-5">Plant {plantText(d.plant_ids)} · {d.technician_id}</span>
                  {busyTech ? (
                    <span className="text-xs font-semibold text-brand-text">
                      Job{d.open_jobs.length > 1 ? "s" : ""}: {d.open_jobs.map((j) => j.incident_id).join(", ")}
                    </span>
                  ) : (
                    <span className="text-xs text-ink-5">no open jobs</span>
                  )}
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
