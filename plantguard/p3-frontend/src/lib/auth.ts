// SSO with on-prem Keycloak via Auth.js (next-auth v5). Used when GET /auth/config says mode = "keycloak".
// In mode = "local", show a simple login form and call login() from api.ts instead.
//
// .env.local
//   AUTH_SECRET=<random string>
//   AUTH_KEYCLOAK_ID=plantguard-web
//   AUTH_KEYCLOAK_SECRET=plantguard-web-dev-secret
//   AUTH_KEYCLOAK_ISSUER=http://localhost:8080/realms/plantguard
//
// app/api/auth/[...nextauth]/route.ts:  export { GET, POST } from "@/lib/auth-handlers"  (re-export handlers)
import NextAuth from "next-auth";
import Keycloak from "next-auth/providers/keycloak";

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [Keycloak], // reads AUTH_KEYCLOAK_ID / _SECRET / _ISSUER; Authorization Code + PKCE
  callbacks: {
    async jwt({ token, account }) {
      if (account) {
        token.accessToken = account.access_token; // the Keycloak access token the backend verifies
        token.expiresAt = account.expires_at;
        token.refreshToken = account.refresh_token; // TODO FE-1: refresh before expiresAt (15 min)
      }
      return token;
    },
    async session({ session, token }) {
      (session as any).accessToken = token.accessToken; // pass to setToken() in api.ts
      return session;
    },
  },
});
