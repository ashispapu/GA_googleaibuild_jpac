"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { ArrowRight, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Input, Label } from "@/components/ui/form";
import { SCENES, usePresenter } from "@/lib/presenter";
import { useAppState } from "@/lib/app-state";
import { usePlants } from "@/lib/queries";
import { FAILURE_MODES, failureModeLabel } from "@/lib/labels";
import { cn } from "@/lib/utils";

/** Demo controls: visible only after an admin turned them on (Ctrl K or ?presenter=1). */
export function PresenterBar() {
  return (
    <Suspense fallback={null}>
      <PresenterBarInner />
    </Suspense>
  );
}

function PresenterBarInner() {
  const p = usePresenter();
  const params = useSearchParams();
  const [askSignIn, setAskSignIn] = useState(false);

  useEffect(() => {
    if (params.get("presenter") === "1" && !p.enabled) setAskSignIn(true);
  }, [params, p.enabled]);

  if (!p.enabled) return <PresenterSignIn open={askSignIn} onOpenChange={setAskSignIn} />;
  return <ScenesOrPlant />;
}

function ScenesOrPlant() {
  const { simMode } = useAppState();
  return simMode ? <PlantControls /> : <SceneControls />;
}

/** Simulated plant: faults also start by themselves at random times; these buttons start one now. */
function PlantControls() {
  const p = usePresenter();
  const { plantScale } = useAppState();
  const plants = usePlants();
  const [asset, setAsset] = useState("");
  const [mode, setMode] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const machines = (plants.data ?? []).flatMap((pl) => pl.lines.flatMap((l) => l.stations.flatMap((s) => s.machines.map((m) => ({ id: m.id, label: `${m.id} · ${m.name} · ${pl.name}` })))));
  const select = "min-h-[36px] rounded-md border border-line bg-surface px-2 text-[13px]";
  return (
    <section aria-label="Plant controls" className="flex flex-wrap items-center gap-3 rounded-lg border border-brand-border bg-[#EEF3FC] px-[18px] py-3.5">
      <span className="text-xs font-semibold uppercase tracking-[0.06em] text-brand-text">Plant controls · admin</span>
      <select aria-label="Machine" value={asset} onChange={(e) => setAsset(e.target.value)} className={select}>
        <option value="">Random machine</option>
        {machines.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
      </select>
      <select aria-label="Fault" value={mode} onChange={(e) => setMode(e.target.value)} className={select}>
        <option value="">Typical fault for it</option>
        {FAILURE_MODES.map((k) => <option key={k} value={k}>{failureModeLabel(k)}</option>)}
      </select>
      <Button
        disabled={p.busy}
        onClick={() => void p.injectFault(asset || null, mode || null).then(setMsg, () => undefined)}
      >
        {p.busy && <Loader2 className="animate-spin" />} Start a fault
      </Button>
      <span className="flex items-center gap-1.5 text-[13px] text-ink-4">
        Plant time
        {[0, 30, 60, 120, 300].map((s) => (
          <button
            key={s}
            type="button"
            aria-pressed={plantScale === s}
            onClick={() => void p.setSpeed(s).catch(() => undefined)}
            className={cn("min-h-[32px] rounded-md px-2", plantScale === s ? "bg-ink font-semibold text-white" : "border border-line bg-surface")}
          >
            {s === 0 ? "Pause" : `${s}×`}
          </button>
        ))}
      </span>
      {(p.error || msg) && <span className={cn("text-sm", p.error ? "text-crit-text" : "text-ink-3")}>{p.error ?? msg}</span>}
      <Button variant="outline" className="ml-auto" onClick={p.disable}>Hide</Button>
    </section>
  );
}

function SceneControls() {
  const p = usePresenter();
  const next = SCENES[p.step + 1];
  return (
    <section aria-label="Demo controls" className="flex flex-wrap items-center gap-4 rounded-lg border border-brand-border bg-[#EEF3FC] px-[18px] py-3.5">
      <span className="text-xs font-semibold uppercase tracking-[0.06em] text-brand-text">Demo controls · admin</span>
      <ol className="m-0 flex flex-[1_1_520px] list-none flex-wrap gap-3.5 p-0 text-[13px]">
        {SCENES.map((s, i) => (
          <li key={s.n}>
            <button
              type="button"
              onClick={() => void p.goTo(i)}
              className={cn("flex items-center gap-1.5", i === p.step ? "font-semibold text-ink" : "text-ink-4 hover:text-ink")}
            >
              <span
                className={cn(
                  "box-border h-2.5 w-2.5 rounded-full",
                  i === p.step ? "bg-brand" : i < p.step ? "bg-brand-light" : "border-[1.5px] border-[#9AA8BA]",
                )}
              />
              {s.n} {s.label}
            </button>
          </li>
        ))}
      </ol>
      {p.error && <span className="text-sm text-crit-text">{p.error}</span>}
      {next ? (
        <Button onClick={() => void p.next()} disabled={p.busy}>
          {p.busy ? <Loader2 className="animate-spin" /> : null}
          Next scene: {next.label}
          <ArrowRight />
        </Button>
      ) : (
        <Button variant="outline" onClick={() => void p.goTo(0)} disabled={p.busy}>
          Start again
        </Button>
      )}
    </section>
  );
}

export function PresenterSignIn({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const p = usePresenter();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await p.enable(username, password);
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[440px]">
        <DialogTitle>Turn on demo controls</DialogTitle>
        <DialogDescription>
          Only an admin can change scenes. This admin sign-in is kept separately, so you can stay signed in as an operator or lead.
        </DialogDescription>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="pr-user">Admin username</Label>
            <Input id="pr-user" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="off" />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="pr-pass">Password</Label>
            <Input id="pr-pass" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          {error && <p className="m-0 text-sm text-crit-text">{error}</p>}
          <Button type="submit" disabled={busy}>
            {busy && <Loader2 className="animate-spin" />} Turn on
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
