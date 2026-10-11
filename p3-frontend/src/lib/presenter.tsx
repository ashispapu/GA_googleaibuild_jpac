"use client";

/**
 * Presenter mode (README FE-8). Only admin may change scenes, so presenter mode
 * keeps a SECOND token, signed in as admin, used only for POST /simulator/scenario/{name}.
 * The main session can stay `op` or `lead`.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { backend, errorCode } from "./backend";
import { useAppState } from "./app-state";
import type { Scenario } from "./contract";

export interface Scene {
  n: number;
  label: string;
  /** simulator scenario to switch to (README: calm → warning → … → resolved) */
  scenario?: Scenario;
  /** where the screen should go; {incident} is replaced with the active incident */
  route: string;
}

export const SCENES: Scene[] = [
  { n: 1, label: "Calm", scenario: "calm", route: "/" },
  { n: 2, label: "Warning", scenario: "warning", route: "/" },
  { n: 3, label: "Investigation", route: "/investigation/{incident}" },
  { n: 4, label: "Crisis", scenario: "crisis", route: "/investigation/{incident}" },
  { n: 5, label: "Options", route: "/options/{incident}" },
  { n: 6, label: "Human approves", route: "/options/{incident}?approve=1" },
  { n: 7, label: "Fixed", scenario: "resolved", route: "/fixed/{incident}" },
  { n: 8, label: "Learning", route: "/fixed/{incident}#feedback" },
];

interface PresenterValue {
  enabled: boolean;
  step: number; // index into SCENES
  busy: boolean;
  error: string | null;
  enable: (username: string, password: string) => Promise<void>;
  disable: () => void;
  goTo: (index: number) => Promise<void>;
  next: () => Promise<void>;
}

const Ctx = createContext<PresenterValue | null>(null);
const KEY = "pg.presenter";

export function PresenterProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { currentIncidentId: activeIncidentId } = useAppState();
  const [adminToken, setAdminToken] = useState<string | null>(null);
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(KEY) ?? "null") as { token: string; step: number } | null;
      if (saved?.token) {
        setAdminToken(saved.token);
        setStep(saved.step ?? 0);
      }
    } catch {
      /* ignore */
    }
  }, []);

  const persist = (token: string | null, s: number) => {
    try {
      if (token) sessionStorage.setItem(KEY, JSON.stringify({ token, step: s }));
      else sessionStorage.removeItem(KEY);
    } catch {
      /* ignore */
    }
  };

  const enable = useCallback(async (username: string, password: string) => {
    setError(null);
    const res = await backend.adminLogin(username, password);
    if (res.user.role !== "admin" && !res.user.permissions?.includes("run_simulator")) throw new Error("Only an admin can run the demo.");
    const token = res.access_token;
    setAdminToken(token);
    persist(token, 0);
  }, []);

  const disable = useCallback(() => {
    setAdminToken(null);
    persist(null, 0);
  }, []);

  const goTo = useCallback(
    async (index: number) => {
      const scene = SCENES[index];
      if (!scene || !adminToken) return;
      setBusy(true);
      setError(null);
      try {
        if (scene.scenario) await backend.setScene(scene.scenario, adminToken);
        setStep(index);
        persist(adminToken, index);
        const incident = activeIncidentId ?? process.env.NEXT_PUBLIC_DEMO_INCIDENT_ID ?? "INC-0042";
        router.push(scene.route.replace("{incident}", encodeURIComponent(incident)));
      } catch (e) {
        if (errorCode(e) === "UNAUTHENTICATED" || errorCode(e) === "FORBIDDEN") {
          setError("The admin sign-in for the demo has expired. Turn demo controls on again.");
          setAdminToken(null);
          persist(null, 0);
        } else {
          setError(e instanceof Error ? e.message : "Could not change the scene.");
        }
      } finally {
        setBusy(false);
      }
    },
    [adminToken, activeIncidentId, router],
  );

  const next = useCallback(() => goTo(Math.min(step + 1, SCENES.length - 1)), [goTo, step]);

  const value = useMemo(
    () => ({ enabled: !!adminToken, step, busy, error, enable, disable, goTo, next }),
    [adminToken, step, busy, error, enable, disable, goTo, next],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function usePresenter() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("usePresenter must be used inside <PresenterProvider>");
  return ctx;
}
