"use client";

import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { backend, errorCode } from "./backend";
import {
  accuracyView,
  eventView,
  healthView,
  optionsView,
  plantView,
  type Approval,
  type Feedback,
  type FeedbackRequest,
  type Signal,
} from "./contract";
import { useSession } from "./session";

const noRetryOn = ["FORBIDDEN", "PLANT_SCOPE", "NOT_FOUND", "UNAUTHENTICATED", "VALIDATION"];
const retry = (n: number, e: unknown) => !noRetryOn.includes(errorCode(e) ?? "") && n < 2;

function useSignedIn() {
  return useSession().status === "signed-in";
}

// ---- plant -----------------------------------------------------------------------
/** GET /plants returns plants → lines → stations → assets, each with health and risk. */
export function usePlants() {
  const on = useSignedIn();
  return useQuery({
    queryKey: ["plants"],
    queryFn: async () => (await backend.plants()).map(plantView),
    enabled: on,
    refetchInterval: 10_000,
    retry,
  });
}

/** The selected plant, and where a machine sits in it. */
export function usePlantLayout(plantId: string) {
  const plants = usePlants();
  const plant = plants.data?.find((p) => p.id === plantId);
  const lines = plant?.lines ?? [];
  const find = (assetId: string) => {
    for (const line of lines) {
      for (const station of line.stations) {
        const machine = station.machines.find((m) => m.id === assetId);
        if (machine) return { line, station, machine };
      }
    }
    return null;
  };
  return { plants, plant, lines, find, loading: plants.isLoading };
}

export function useHealthMany(assetIds: string[]) {
  const on = useSignedIn();
  return useQueries({
    queries: assetIds.map((id) => ({
      queryKey: ["health", id],
      queryFn: async () => healthView(await backend.health(id)),
      enabled: on,
      refetchInterval: 10_000,
      retry,
    })),
  });
}

export function useHealth(assetId: string | null) {
  const on = useSignedIn();
  return useQuery({
    queryKey: ["health", assetId],
    queryFn: async () => healthView(await backend.health(assetId!)),
    enabled: on && !!assetId,
    refetchInterval: 10_000,
    retry,
  });
}

/** One call for several signals: GET /ot/assets/{id}/signals?signals=…&minutes=… */
export function useSignals(assetId: string | null, signals: Signal[], minutes: number) {
  const on = useSignedIn();
  return useQuery({
    queryKey: ["signals", assetId, signals.join(","), minutes],
    queryFn: () => backend.signals(assetId!, signals, minutes),
    enabled: on && !!assetId,
    refetchInterval: 60_000,
    retry,
  });
}

export function useAlarms(plantId: string) {
  const on = useSignedIn();
  return useQuery({ queryKey: ["alarms", plantId], queryFn: () => backend.alarms(plantId), enabled: on, refetchInterval: 10_000, retry });
}

// ---- production --------------------------------------------------------------------
export function useOrders() {
  const on = useSignedIn();
  return useQuery({ queryKey: ["orders"], queryFn: () => backend.orders(), enabled: on, refetchInterval: 30_000, retry });
}

export function useOee(lineId: string | null) {
  const on = useSignedIn();
  return useQuery({
    queryKey: ["oee", lineId],
    queryFn: () => backend.oee(lineId!),
    enabled: on && !!lineId,
    refetchInterval: 30_000,
    retry,
  });
}

// ---- incidents ---------------------------------------------------------------------
export function useIncident(id: string | null) {
  const on = useSignedIn();
  return useQuery({
    queryKey: ["incident", id],
    queryFn: () => backend.incidentOrNull(id!),
    enabled: on && !!id,
    refetchInterval: 5000,
    retry,
  });
}

/** All agent events so far (no socket) — for evidence lists and timelines. */
export function useEvents(id: string | null) {
  const on = useSignedIn();
  return useQuery({
    queryKey: ["events", id],
    queryFn: async () => (await backend.allEvents(id!)).map(eventView),
    enabled: on && !!id,
    retry,
  });
}

export function useOptions(id: string | null) {
  const on = useSignedIn();
  return useQuery({
    queryKey: ["options", id],
    queryFn: async () => optionsView(await backend.options(id!)),
    enabled: on && !!id,
    retry,
  });
}

export function useApprove(incidentId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { optionId: string; decision: "approve" | "reject"; reason: string }) =>
      backend.approve(incidentId, { option_id: v.optionId, decision: v.decision, reason: v.reason }),
    onSuccess: (approval: Approval) => {
      if (approval.decision === "approve") {
        qc.setQueryData(["approval", incidentId], approval);
        try {
          sessionStorage.setItem(`pg.approval.${incidentId}`, JSON.stringify(approval));
        } catch {
          /* ignore */
        }
      }
      void qc.invalidateQueries({ queryKey: ["incident", incidentId] });
      void qc.invalidateQueries({ queryKey: ["options", incidentId] });
    },
    onError: (e) => {
      // C1: CONFLICT → refresh the incident
      if (errorCode(e) === "CONFLICT") {
        void qc.invalidateQueries({ queryKey: ["incident", incidentId] });
        void qc.invalidateQueries({ queryKey: ["options", incidentId] });
      }
    },
  });
}

/** The approval comes back from POST /approve; we keep it for the "Fixed" screen. */
export function useApproval(incidentId: string) {
  return useQuery<Approval | null>({
    queryKey: ["approval", incidentId],
    queryFn: () => {
      try {
        const saved = sessionStorage.getItem(`pg.approval.${incidentId}`);
        return saved ? (JSON.parse(saved) as Approval) : null;
      } catch {
        return null;
      }
    },
    staleTime: Infinity,
    gcTime: Infinity,
  });
}

export function useFeedback(incidentId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: FeedbackRequest) => backend.feedback(incidentId, body),
    onSuccess: (r: Feedback) => qc.setQueryData(["feedback", incidentId], r),
  });
}

export function useFeedbackResult(incidentId: string | null) {
  return useQuery<Feedback | null>({
    queryKey: ["feedback", incidentId],
    queryFn: () => null,
    enabled: !!incidentId,
    staleTime: Infinity,
    gcTime: Infinity,
  });
}

// ---- security + model ------------------------------------------------------------
export function useAudit(enabled: boolean, incidentId?: string) {
  const on = useSignedIn();
  return useQuery({
    queryKey: ["audit", incidentId ?? "all"],
    queryFn: () => backend.audit(incidentId),
    enabled: on && enabled,
    retry,
    refetchInterval: 15_000,
  });
}

export function useAuditVerify(enabled: boolean) {
  const on = useSignedIn();
  return useQuery({ queryKey: ["audit-verify"], queryFn: backend.auditVerify, enabled: on && enabled, retry });
}

export function useRedactions() {
  const on = useSignedIn();
  return useQuery({ queryKey: ["redactions"], queryFn: backend.redactions, enabled: on, retry, refetchInterval: 15_000 });
}

export function useAccuracy() {
  const on = useSignedIn();
  return useQuery({
    queryKey: ["models"],
    queryFn: async () => accuracyView(await backend.models()),
    enabled: on,
    staleTime: 60_000,
    retry,
  });
}
