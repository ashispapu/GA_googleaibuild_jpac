// SSO with on-prem Keycloak via Auth.js (next-auth v5). Used when GET /auth/config says mode = "keycloak".
// In mode = "local", show a simple login form and call login() from api.ts instead.
//
// .env.local
//   AUTH_SECRET=<random string>
//   AUTH_KEYCLOAK_ID=plantguard-web
//   AUTH_KEYCLOAK_SECRET=plantguard-web-dev-secret
//   AUTH_KEYCLOAK_ISSUER=http://localhost:8080/realms/plantguard
//
// app/api/auth/[...nextauth]/route.ts:  import { handlers } from "@/lib/auth"; export const { GET, POST } = handlers;
import NextAuth, { customFetch } from "next-auth";
import Keycloak from "next-auth/providers/keycloak";
import { keycloakFetch } from "./keycloak-network";

export const { handlers, auth, signIn, signOut } = NextAuth({
  // Keep the public issuer for validation/redirects; route server-side discovery and token calls internally.
  providers: [Keycloak({ [customFetch]: keycloakFetch })],
  callbacks: {
    async jwt({ token, account }) {
      if (account) {
        token.accessToken = account.access_token; // the Keycloak access token the backend verifies
        token.expiresAt = account.expires_at;
        token.refreshToken = account.refresh_token;
        token.idToken = account.id_token; // lets logout end the Keycloak session too
        return token;
      }
      // FE-1: refresh the 15-min access token a minute before it expires.
      // The UI calls getSession() every minute, which runs this callback.
      const expiresAt = typeof token.expiresAt === "number" ? token.expiresAt : 0;
      if (Date.now() < (expiresAt - 60) * 1000 || !token.refreshToken) return token;
      try {
        const res = await keycloakFetch(`${process.env.AUTH_KEYCLOAK_ISSUER}/protocol/openid-connect/token`, {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({
            grant_type: "refresh_token",
            client_id: process.env.AUTH_KEYCLOAK_ID ?? "",
            client_secret: process.env.AUTH_KEYCLOAK_SECRET ?? "",
            refresh_token: String(token.refreshToken),
          }),
        });
        const t = (await res.json()) as { access_token?: string; expires_in?: number; refresh_token?: string };
        if (!res.ok || !t.access_token) throw new Error("refresh failed");
        return {
          ...token,
          accessToken: t.access_token,
          expiresAt: Math.floor(Date.now() / 1000) + (t.expires_in ?? 900),
          refreshToken: t.refresh_token ?? token.refreshToken,
          error: undefined,
        };
      } catch {
        return { ...token, error: "RefreshFailed" }; // the UI sends the user back to login
      }
    },
    async session({ session, token }) {
      (session as any).accessToken = token.accessToken; // pass to setToken() in api.ts
      (session as any).idToken = token.idToken;
      (session as any).error = token.error;
      return session;
    },
  },
});
