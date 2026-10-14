"use client";

/**
 * C2 live streams.
 *
 * Sockets are opened with openStream() from the team's src/lib/ws.ts. On top of it
 * these hooks add the contract's rules:
 *  - de-duplicate agent events by `seq`
 *  - after a reconnect, GET /incidents/{id}/events?after_seq=<last> first, then reopen
 *  - the server closes the socket after the orchestrator sends `finished`
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { API, backend, getToken } from "./backend";
import { openStream } from "./ws";
import { eventView, isRoundFinished, telemetryPairs, type EventView, type SignalPoint, type TelemetryMessage, type AgentEvent } from "./contract";

const FLUSH_MS = 100;

/** prev is sorted by seq; new events almost always come after it, so append and only sort when needed. */
function mergeSorted(prev: EventView[], batch: EventView[]): EventView[] {
  batch.sort((a, b) => a.seq - b.seq);
  const last = prev.length ? prev[prev.length - 1]!.seq : -Infinity;
  if (batch[0]!.seq > last) return prev.concat(batch);
  return [...prev, ...batch].sort((a, b) => a.seq - b.seq);
}

export type StreamState = "idle" | "connecting" | "live" | "reconnecting" | "finished" | "error";

/** `round`: the incident's current analysis round (v1.2). A finished earlier round does not end the stream. */
export function useIncidentStream(incidentId: string | null, speed = 1, round = 1) {
  const [events, setEvents] = useState<EventView[]>([]);
  const [state, setState] = useState<StreamState>("idle");
  const seen = useRef(new Set<number>());
  const lastSeq = useRef<number | undefined>(undefined);
  const finished = useRef(false);
  const currentId = useRef<string | null>(null);
  const roundRef = useRef(round);
  const pending = useRef<EventView[]>([]);
  const flushTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const add = useCallback((list: AgentEvent[]) => {
    const fresh = list.filter((e) => typeof e.seq === "number" && !seen.current.has(e.seq));
    if (!fresh.length) return;
    for (const e of fresh) {
      seen.current.add(e.seq);
      lastSeq.current = Math.max(lastSeq.current ?? -1, e.seq);
    }
    const views = fresh.map(eventView);
    if (views.some((v) => isRoundFinished(v, roundRef.current))) finished.current = true;
    // A live answer arrives token by token. Re-rendering the feed for every token made the page sluggish, so events
    // are collected and applied at most every FLUSH_MS (the text still looks live).
    pending.current.push(...views);
    if (flushTimer.current === null) {
      flushTimer.current = setTimeout(() => {
        flushTimer.current = null;
        const batch = pending.current;
        pending.current = [];
        if (batch.length) setEvents((prev) => mergeSorted(prev, batch));
      }, FLUSH_MS);
    }
  }, []);
  useEffect(() => () => {
    if (flushTimer.current !== null) clearTimeout(flushTimer.current);
  }, []);

  useEffect(() => {
    if (!incidentId) return;
    if (currentId.current !== incidentId) {
      currentId.current = incidentId;
      seen.current = new Set();
      lastSeq.current = undefined;
      finished.current = false;
      pending.current = [];
      setEvents([]);
    }
    if (roundRef.current !== round) {
      roundRef.current = round; // a new analysis round: keep the history, listen again
      finished.current = false;
    }

    let ws: WebSocket | null = null;
    let closed = false;
    let retry = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const open = async () => {
      setState(retry ? "reconnecting" : "connecting");
      try {
        add(await backend.events(incidentId, lastSeq.current));
      } catch {
        /* the socket may still work */
      }
      if (closed) return;
      if (finished.current) {
        setState("finished");
        return;
      }
      const token = getToken() ?? "";
      ws = openStream<AgentEvent>(API, `/incidents/${encodeURIComponent(incidentId)}/stream`, token, (e) => add([e]), {
        speed: String(speed),
      });
      ws.onopen = () => {
        retry = 0;
        setState("live");
      };
      ws.onerror = () => ws?.close();
      ws.onclose = () => {
        if (closed) return;
        if (finished.current) {
          setState("finished");
          return;
        }
        retry += 1;
        if (retry > 6) {
          setState("error");
          return;
        }
        setState("reconnecting");
        timer = setTimeout(open, Math.min(10_000, 1000 * 2 ** (retry - 1)));
      };
    };

    void open();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      ws?.close();
    };
  }, [incidentId, speed, add, round]);

  return { events, state };
}

/** Latest value per asset+signal, plus a short live buffer for one asset. */
export interface TelemetrySnapshot {
  latest: Record<string, Record<string, { value: number; t: number; quality: string }>>;
  series: Record<string, SignalPoint[]>;
  connected: boolean;
}

/**
 * WS /ot/stream?line_id=… — one TelemetryMessage per asset per second.
 * State is published once a second to keep React work small.
 */
export function useTelemetry(lineId: string | null, bufferAssetId?: string, maxPoints = 900): TelemetrySnapshot {
  const [snap, setSnap] = useState<TelemetrySnapshot>({ latest: {}, series: {}, connected: false });
  const latest = useRef<TelemetrySnapshot["latest"]>({});
  const series = useRef<TelemetrySnapshot["series"]>({});
  const connected = useRef(false);
  const dirty = useRef(false);

  useEffect(() => {
    if (!lineId) return;
    latest.current = {};
    series.current = {};
    let ws: WebSocket | null = null;
    let closed = false;
    let retry = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const onTelemetry = (m: TelemetryMessage) => {
      for (const p of telemetryPairs(m)) {
        (latest.current[p.asset] ??= {})[p.signal] = { value: p.value, t: p.t, quality: p.quality };
        if (bufferAssetId && p.asset === bufferAssetId) {
          const arr = (series.current[p.signal] ??= []);
          arr.push({ t: p.t, value: p.value });
          if (arr.length > maxPoints) arr.splice(0, arr.length - maxPoints);
        }
      }
      dirty.current = true;
    };

    const open = () => {
      const token = getToken() ?? "";
      ws = openStream<TelemetryMessage>(API, "/ot/stream", token, onTelemetry, { line_id: lineId });
      ws.onopen = () => {
        retry = 0;
        connected.current = true;
        dirty.current = true;
      };
      ws.onerror = () => ws?.close();
      ws.onclose = () => {
        connected.current = false;
        dirty.current = true;
        if (closed) return;
        retry += 1;
        timer = setTimeout(open, Math.min(10_000, 1000 * 2 ** Math.min(retry, 4)));
      };
    };
    open();

    const tick = setInterval(() => {
      if (!dirty.current) return;
      dirty.current = false;
      setSnap({
        latest: { ...latest.current },
        series: Object.fromEntries(Object.entries(series.current).map(([k, v]) => [k, [...v]])),
        connected: connected.current,
      });
    }, 1000);

    return () => {
      closed = true;
      clearInterval(tick);
      if (timer) clearTimeout(timer);
      ws?.close();
    };
  }, [lineId, bufferAssetId, maxPoints]);

  return snap;
}
