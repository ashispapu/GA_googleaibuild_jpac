// Demo controls sign-in when the backend runs in Keycloak mode (POST /auth/token is off there).
// The browser cannot call Keycloak's password grant directly (CORS), so this server route does it for the
// presenter, with the "plantguard-dev" client from the realm. Only admins may run the demo.
// Disabled in production builds unless ALLOW_PRESENTER_LOGIN=true.
import { NextResponse } from "next/server";
import { keycloakFetch } from "@/lib/keycloak-network";

const ROLE_ORDER = ["admin", "plant_manager", "maintenance_lead", "operator"];

function fail(status: number, message: string) {
  return NextResponse.json({ code: status === 401 ? "UNAUTHENTICATED" : "FORBIDDEN", message, trace_id: "" }, { status });
}

export async function POST(req: Request) {
  if (process.env.NODE_ENV === "production" && process.env.ALLOW_PRESENTER_LOGIN !== "true") {
    return fail(403, "Demo sign-in is switched off.");
  }
  const issuer = process.env.AUTH_KEYCLOAK_ISSUER;
  if (!issuer) return fail(403, "Keycloak is not configured for the frontend.");

  const { username, password } = (await req.json().catch(() => ({}))) as { username?: string; password?: string };
  if (!username || !password) return fail(401, "Enter the admin username and password.");

  let res: Response;
  try {
    res = await keycloakFetch(`${issuer}/protocol/openid-connect/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ grant_type: "password", client_id: "plantguard-dev", username, password }),
    });
  } catch {
    return fail(403, "Can't reach Keycloak.");
  }
  const t = (await res.json().catch(() => ({}))) as { access_token?: string; expires_in?: number };
  if (!res.ok || !t.access_token) return fail(401, "Wrong username or password.");

  // The backend verifies the signature on every call; here we only read the role to decide what the UI shows.
  const claims = JSON.parse(Buffer.from(t.access_token.split(".")[1], "base64url").toString()) as {
    realm_access?: { roles?: string[] };
  };
  const role = ROLE_ORDER.find((r) => claims.realm_access?.roles?.includes(r)) ?? "operator";
  return NextResponse.json({
    access_token: t.access_token,
    token_type: "bearer",
    expires_in: t.expires_in ?? 900,
    user: { role, permissions: role === "admin" ? ["run_simulator"] : [] },
  });
}
