"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { ArrowRight, Building2, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/form";
import { useSession } from "@/lib/session";
import { errorCode } from "@/lib/backend";

const DEMO_USERS = [
  { user: "op", role: "Operator" },
  { user: "lead", role: "Maintenance Lead" },
  { user: "manager", role: "Plant Manager" },
  { user: "admin", role: "Admin" },
];

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
  const { status, mode, configError, loginLocal, loginSso } = useSession();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("demo");
  const [error, setError] = useState<string | null>(params.get("expired") ? "You were signed out. Please sign in again." : null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (status === "signed-in") router.replace("/");
  }, [status, router]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await loginLocal(username.trim(), password);
      router.replace("/");
    } catch (err) {
      setError(errorCode(err) === "NETWORK" ? "Can't reach the PlantGuard server." : "That username or password didn't work.");
    } finally {
      setBusy(false);
    }
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
              <Input id="u" value={username} onChange={(e) => setUsername(e.target.value)} placeholder="op, lead, manager or admin" autoComplete="username" required />
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
        <div className="flex w-full max-w-[440px] flex-col gap-2.5 rounded-xl border border-dashed border-line-strong bg-surface px-4 py-3.5">
          <div className="text-[13px] text-ink-4">
            Demo users · password <span className="font-mono text-ink">demo</span>
          </div>
          <div className="flex flex-wrap gap-2 text-[13px]">
            {DEMO_USERS.map((d) => (
              <button
                key={d.user}
                type="button"
                onClick={() => setUsername(d.user)}
                className="rounded-sm border border-line bg-page px-2.5 py-1.5 text-ink hover:border-brand-border"
              >
                <span className="font-mono">{d.user}</span> · {d.role}
              </button>
            ))}
          </div>
        </div>
      )}
      <p className="m-0 text-[13px] text-ink-5">You’ll be signed out after 15 minutes · connection encrypted</p>
    </div>
  );
}
