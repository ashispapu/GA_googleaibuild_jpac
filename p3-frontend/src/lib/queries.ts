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
export function useOrders(plantId?: string) {
  const on = useSignedIn();
  return useQuery({ queryKey: ["orders", plantId ?? "all"], queryFn: () => backend.orders(undefined, plantId), enabled: on, refetchInterval: 15_000, retry });
}

export function useOee(lineId: string | null, plantId?: string) {
  const on = useSignedIn();
  return useQuery({
    queryKey: ["oee", lineId, plantId ?? "all"],
    queryFn: () => backend.oee(lineId!, plantId),
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
    refetchInterval: 5000, // other approvers sign, decline or add options from the chat
    retry,
  });
}

export function useApprove(incidentId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { optionId: string; decision: "approve" | "reject"; reason: string }) =>
      backend.approve(incidentId, { option_id: v.optionId, decision: v.decision, reason: v.reason }),
    onSuccess: (approval: Approval) => {
      // v1.2: only the final signature of the approval chain carries a token; earlier signatures are not "approved" yet
      if (approval.decision === "approve" && approval.approval_token) {
        qc.setQueryData(["approval", incidentId], approval);
        try {
          sessionStorage.setItem(`pg.approval.${incidentId}`, JSON.stringify(approval));
        } catch {
          /* ignore */
        }
      }
      void qc.invalidateQueries({ queryKey: ["incident", incidentId] });
      void qc.invalidateQueries({ queryKey: ["options", incidentId] });
      void qc.invalidateQueries({ queryKey: ["overview", incidentId] });
      void qc.invalidateQueries({ queryKey: ["signoffs"] });
      void qc.invalidateQueries({ queryKey: ["incidents"] });
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

// ---- v1.2: incident lists, jobs, log, manuals, sessions, simulator ------------------------------
type ListQuery = Parameters<typeof backend.incidents>[0];

/** GET /incidents — polled, so stage tags move by themselves. */
export function useIncidents(q: ListQuery = {}, refetchMs = 4000) {
  const on = useSignedIn();
  return useQuery({
    queryKey: ["incidents", q],
    queryFn: async () => (await backend.incidents(q)).items,
    enabled: on,
    refetchInterval: refetchMs,
    retry,
  });
}

export function useOverview(id: string | null) {
  const on = useSignedIn();
  return useQuery({ queryKey: ["overview", id], queryFn: () => backend.overview(id!), enabled: on && !!id, refetchInterval: 4000, retry });
}

export function useTimeline(id: string | null) {
  const on = useSignedIn();
  return useQuery({ queryKey: ["timeline", id], queryFn: () => backend.timeline(id!), enabled: on && !!id, refetchInterval: 5000, retry });
}

export function useJob(id: string | null) {
  const on = useSignedIn();
  return useQuery({ queryKey: ["job", id], queryFn: () => backend.job(id!), enabled: on && !!id, refetchInterval: 3000, retry });
}

/** Every job action refreshes the job, the overview and the lists. */
export function useJobAction<A>(id: string, fn: (a: A) => Promise<unknown>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSettled: () => {
      for (const k of ["job", "overview", "timeline", "incident"]) void qc.invalidateQueries({ queryKey: [k, id] });
      void qc.invalidateQueries({ queryKey: ["incidents"] });
    },
  });
}

export function useMaintenanceLog() {
  const on = useSignedIn();
  return useQuery({ queryKey: ["maintenance-log"], queryFn: backend.maintenanceLog, enabled: on, refetchInterval: 15_000, retry });
}

export function useDocs(kind?: string) {
  const on = useSignedIn();
  return useQuery({ queryKey: ["docs", kind ?? "all"], queryFn: () => backend.docs(kind), enabled: on, staleTime: 30_000, retry });
}

export function useDoc(id: string | null) {
  const on = useSignedIn();
  return useQuery({ queryKey: ["doc", id], queryFn: () => backend.doc(id!), enabled: on && !!id, staleTime: 60_000, retry });
}

export function useDocSearch(query: string, assetId?: string | null) {
  const on = useSignedIn();
  return useQuery({
    queryKey: ["doc-search", query, assetId ?? null],
    queryFn: () => backend.docSearch(query, assetId),
    enabled: on && query.trim().length >= 3,
    staleTime: 30_000,
    retry,
  });
}

export function useSessions(allUsers: boolean) {
  const on = useSignedIn();
  return useQuery({ queryKey: ["sessions", allUsers], queryFn: () => backend.sessions(allUsers), enabled: on, refetchInterval: 15_000, retry });
}

export function useSimStatus() {
  const on = useSignedIn();
  return useQuery({ queryKey: ["sim-status"], queryFn: backend.simStatus, enabled: on, refetchInterval: 10_000, retry });
}

// ---- v1.2: approval chain and options chat ------------------------------------------------------
/** Approval steps waiting for me (release a part for another plant, co-sign as plant manager). */
export function useSignoffs() {
  const on = useSignedIn();
  return useQuery({ queryKey: ["signoffs"], queryFn: backend.signoffs, enabled: on, refetchInterval: 5000, retry });
}

export function useOptionChat(id: string | null) {
  const on = useSignedIn();
  return useQuery({ queryKey: ["option-chat", id], queryFn: () => backend.optionChat(id!), enabled: on && !!id,
                    refetchInterval: 5000, retry });
}

export function usePostOptionChat(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (message: string) => backend.postOptionChat(id, message),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["option-chat", id] });
      void qc.invalidateQueries({ queryKey: ["options", id] });
    },
  });
}
