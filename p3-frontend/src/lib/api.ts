// Typed API client. Types come from ../../../generated/api.d.ts — never write API types by hand.
import type { components } from "../../../generated/api";
export type S = components["schemas"];

export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"; // mock now, real later — same URL

let token = "";
export async function login(username: string, password = "demo"): Promise<S["TokenResponse"]> {
  const r = await call<S["TokenResponse"]>("POST", "/auth/token", { username, password });
  token = r.access_token;
  return r;
}

export class ApiError extends Error {
  constructor(public status: number, public body: S["ErrorResponse"]) { super(body.message); }
}

export async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(API + path, {
    method,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new ApiError(r.status, await r.json()); // switch on body.code, never on message
  return r.json();
}

export const getToken = () => token;
export const setToken = (t: string) => { token = t; }; // Keycloak mode: token from the Auth.js session
export const getAuthConfig = () => call<S["AuthConfig"]>("GET", "/auth/config");

// Examples
export const getPlants = () => call<S["Plant"][]>("GET", "/plants");
export const getHealth = (id: string) => call<S["AssetHealth"]>("GET", `/ml/assets/${id}/health`);
export const getOptions = (inc: string) => call<S["OptionsResponse"]>("GET", `/incidents/${inc}/options`);
export const approve = (inc: string, b: S["ApprovalRequest"]) => call<S["Approval"]>("POST", `/incidents/${inc}/approve`, b);
export const setScene = (name: string) => call<S["ScenarioResponse"]>("POST", `/simulator/scenario/${name}`);
