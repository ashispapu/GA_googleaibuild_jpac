// Typed WebSocket helpers for the frontend. Frames are JSON, one object per message.
import type { components } from "./api";
export type TelemetryMessage = components["schemas"]["TelemetryMessage"]; // WS /ot/stream?token=&line_id=
export type AgentEvent = components["schemas"]["AgentEvent"];             // WS /incidents/{id}/stream?token=&speed=

export function openStream<T>(base: string, path: string, token: string, onMsg: (m: T) => void,
                              params: Record<string, string> = {}): WebSocket {
  const q = new URLSearchParams({ token, ...params });
  const ws = new WebSocket(`${base.replace(/^http/, "ws")}${path}?${q}`);
  ws.onmessage = (e) => onMsg(JSON.parse(e.data) as T);
  return ws;
}
