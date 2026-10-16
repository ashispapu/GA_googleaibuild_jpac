// Server-side Keycloak requests use Docker's internal address. Browser redirects and token issuer checks
// must keep the public address (localhost:8080 in the local stack).
export function keycloakServerUrl(input: string | URL): string {
  const publicIssuer = process.env.AUTH_KEYCLOAK_ISSUER;
  const internalIssuer = process.env.AUTH_KEYCLOAK_INTERNAL_ISSUER;
  if (!publicIssuer || !internalIssuer) return String(input);

  const url = new URL(input);
  const issuer = new URL(publicIssuer);
  const prefix = issuer.pathname.replace(/\/+$/, "");
  if (url.origin !== issuer.origin || (url.pathname !== prefix && !url.pathname.startsWith(`${prefix}/`))) {
    return String(input);
  }

  const internal = new URL(internalIssuer);
  internal.pathname = internal.pathname.replace(/\/+$/, "") + url.pathname.slice(prefix.length);
  internal.search = url.search;
  internal.hash = url.hash;
  return internal.href;
}

export const keycloakFetch: typeof fetch = (input, init) => {
  const url = input instanceof Request ? input.url : input;
  const target = keycloakServerUrl(url);
  return fetch(input instanceof Request ? new Request(target, input) : target, init);
};
