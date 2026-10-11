// Auth.js route for Keycloak SSO (C7, auth mode "keycloak").
// Assumes src/lib/auth.ts exports `handlers` from NextAuth({...}) — the Auth.js v5 pattern.
import { handlers } from "@/lib/auth";

export const { GET, POST } = handlers;
