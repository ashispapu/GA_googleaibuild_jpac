"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { backend, setLlmUnavailableHandler } from "./backend";
import { useSession } from "./session";

/** C9 demo incident. Used to discover the open incident because C1 has no "list incidents" call. */
const DEMO_INCIDENT_ID = process.env.NEXT_PUBLIC_DEMO_INCIDENT_ID ?? "INC-0042";

interface AppState {
  plantId: string;
  setPlantId: (id: string) => void;
  /** set when any call returns LLM_UNAVAILABLE (C1) */
  llmUnavailable: boolean;
  setLlmUnavailable: (v: boolean) => void;
  /** the incident still open (not resolved), if any — drives badges and banners */
  activeIncidentId: string | null;
  /** the latest known incident, open or resolved — drives links and the presenter */
  currentIncidentId: string | null;
  announceIncident: (id: string) => void;
}

const Ctx = createContext<AppState | null>(null);

export function AppStateProvider({ children }: { children: ReactNode }) {
  const { me, status } = useSession();
  const [plantId, setPlantIdState] = useState<string>("PLANT-A");
  const [llmUnavailable, setLlmUnavailable] = useState(false);
  const [announced, setAnnounced] = useState<string | null>(null);

  useEffect(() => {
    if (me?.plant_ids?.length && !me.plant_ids.includes(plantId)) setPlantIdState(me.plant_ids[0]!);
  }, [me, plantId]);

  useEffect(() => {
    setLlmUnavailableHandler(() => setLlmUnavailable(true));
    return () => setLlmUnavailableHandler(null);
  }, []);

  // Poll the demo incident: null until the simulator opens it, and null again after a reset.
  const incidentId = announced ?? DEMO_INCIDENT_ID;
  const incident = useQuery({
    queryKey: ["incident", incidentId],
    queryFn: () => backend.incidentOrNull(incidentId),
    enabled: status === "signed-in",
    refetchInterval: 5000,
    retry: (n) => n < 2,
  });

  const currentIncidentId = incident.data ? incident.data.id : null;
  // Still needs attention until it is executed, resolved or rejected.
  const activeIncidentId = incident.data && ["open", "analysing", "awaiting_approval", "approved"].includes(incident.data.status) ? incident.data.id : null;

  const setPlantId = useCallback((id: string) => setPlantIdState(id), []);
  const announceIncident = useCallback((id: string) => setAnnounced(id), []);

  const value = useMemo(
    () => ({ plantId, setPlantId, llmUnavailable, setLlmUnavailable, activeIncidentId, currentIncidentId, announceIncident }),
    [plantId, setPlantId, llmUnavailable, activeIncidentId, currentIncidentId, announceIncident],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAppState() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAppState must be used inside <AppStateProvider>");
  return ctx;
}
