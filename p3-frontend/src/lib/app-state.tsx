"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { backend, setLlmUnavailableHandler } from "./backend";
import type { IncidentOverview } from "./contract";
import { useSession } from "./session";

/** C9 demo incident: used only when the backend has no incident list (the mock server). */
const DEMO_INCIDENT_ID = process.env.NEXT_PUBLIC_DEMO_INCIDENT_ID ?? "INC-0042";
const OPEN_STATUSES = ["open", "analysing", "awaiting_approval", "approved", "executed", "in_repair", "verifying", "repair_failed"];

interface AppState {
  plantId: string;
  setPlantId: (id: string) => void;
  /** set when any call returns LLM_UNAVAILABLE (C1) */
  llmUnavailable: boolean;
  setLlmUnavailable: (v: boolean) => void;
  /** most recently active open incident in the selected plant — drives badges, banners and default links */
  activeIncidentId: string | null;
  /** the latest known incident, open or done — drives links and the presenter */
  currentIncidentId: string | null;
  announceIncident: (id: string) => void;
  /** every open incident you may see (all plants), newest activity first */
  openIncidents: IncidentOverview[];
  /** technician: jobs assigned to me that are not closed */
  myJobs: IncidentOverview[];
  /** simulated plant (v1.2) or the scripted story */
  simMode: boolean;
  /** plant clock: real time in story mode, simulated plant time otherwise */
  plantNow: () => Date;
  plantScale: number;
}

const Ctx = createContext<AppState | null>(null);

export function AppStateProvider({ children }: { children: ReactNode }) {
  const { me, status } = useSession();
  const [plantId, setPlantIdState] = useState<string>("PLANT-A");
  const [llmUnavailable, setLlmUnavailable] = useState(false);
  const [announced, setAnnounced] = useState<string | null>(null);
  const on = status === "signed-in";

  useEffect(() => {
    if (me?.plant_ids?.length && !me.plant_ids.includes(plantId)) setPlantIdState(me.plant_ids[0]!);
  }, [me, plantId]);

  useEffect(() => {
    setLlmUnavailableHandler(() => setLlmUnavailable(true));
    return () => setLlmUnavailableHandler(null);
  }, []);

  // ---- plant clock (GET /simulator/status) ----
  const sim = useQuery({ queryKey: ["sim-status"], queryFn: backend.simStatus, enabled: on, refetchInterval: 10_000, retry: 1 });
  const clockRef = useRef<{ plant: number; real: number; scale: number } | null>(null);
  useEffect(() => {
    if (sim.data) clockRef.current = { plant: new Date(sim.data.now).getTime(), real: Date.now(), scale: sim.data.scale };
  }, [sim.data]);
  const plantNow = useCallback(() => {
    const c = clockRef.current;
    return c ? new Date(c.plant + (Date.now() - c.real) * c.scale) : new Date();
  }, []);
  const simMode = sim.data?.mode === "sim";

  // ---- incidents: the real list (v1.2); the mock server has none, so fall back to the demo incident ----
  const list = useQuery({
    queryKey: ["incidents", { scope: "open", kind: "incident" }],
    queryFn: async () => (await backend.incidents({ scope: "open", kind: "incident" })).items,
    enabled: on,
    refetchInterval: 4000,
    retry: 1,
  });
  const isTech = me?.role === "technician";
  const jobs = useQuery({
    queryKey: ["incidents", { scope: "open", kind: "incident", mine: true }],
    queryFn: async () => (await backend.incidents({ scope: "open", kind: "incident", mine: true })).items,
    enabled: on && isTech,
    refetchInterval: 4000,
    retry: 1,
  });
  const hasList = !list.isError;
  const demo = useQuery({
    queryKey: ["incident", announced ?? DEMO_INCIDENT_ID],
    queryFn: () => backend.incidentOrNull(announced ?? DEMO_INCIDENT_ID),
    enabled: on && !hasList,
    refetchInterval: 5000,
    retry: (n) => n < 2,
  });

  const openIncidents = useMemo(() => list.data ?? [], [list.data]);
  let activeIncidentId: string | null = null;
  let currentIncidentId: string | null = null;
  if (hasList) {
    const inPlant = openIncidents.filter((o) => o.incident.plant_id === plantId);
    activeIncidentId = (announced && openIncidents.some((o) => o.incident.id === announced) ? announced : null) ?? inPlant[0]?.incident.id ?? null;
    currentIncidentId = activeIncidentId ?? announced ?? null;
  } else {
    currentIncidentId = demo.data ? demo.data.id : null;
    activeIncidentId = demo.data && OPEN_STATUSES.slice(0, 4).includes(demo.data.status) ? demo.data.id : null;
  }

  const setPlantId = useCallback((id: string) => setPlantIdState(id), []);
  const announceIncident = useCallback((id: string) => setAnnounced(id), []);

  const value = useMemo(
    () => ({
      plantId, setPlantId, llmUnavailable, setLlmUnavailable, activeIncidentId, currentIncidentId, announceIncident,
      openIncidents, myJobs: jobs.data ?? [], simMode, plantNow, plantScale: sim.data?.scale ?? 1,
    }),
    [plantId, setPlantId, llmUnavailable, activeIncidentId, currentIncidentId, announceIncident, openIncidents, jobs.data,
     simMode, plantNow, sim.data?.scale],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAppState() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAppState must be used inside <AppStateProvider>");
  return ctx;
}

/** Re-renders every `ms` with the plant clock (simulated plant time runs faster than real time). */
export function usePlantNow(ms = 1000): Date {
  const { plantNow } = useAppState();
  const [now, setNow] = useState<Date>(() => plantNow());
  useEffect(() => {
    setNow(plantNow());
    const t = setInterval(() => setNow(plantNow()), ms);
    return () => clearInterval(t);
  }, [plantNow, ms]);
  return now;
}
