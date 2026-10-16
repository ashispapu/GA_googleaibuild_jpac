// Run with: node --test tests/keycloak-network.test.cjs (after npm ci).
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const ts = require("typescript");

const publicIssuer = "http://localhost:8080/realms/plantguard";
const internalIssuer = "http://keycloak:8080/realms/plantguard";
const source = fs.readFileSync(path.join(__dirname, "../src/lib/keycloak-network.ts"), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
} }).outputText;

function load(env = {}, fetchImpl = async () => new Response("OK")) {
  const context = { exports: {}, URL, Request, process: { env }, fetch: fetchImpl };
  vm.runInNewContext(compiled, context);
  return context.exports;
}

const settings = { AUTH_KEYCLOAK_ISSUER: publicIssuer, AUTH_KEYCLOAK_INTERNAL_ISSUER: internalIssuer };

test("host-based development keeps the public URL without an internal issuer", () => {
  const network = load({ AUTH_KEYCLOAK_ISSUER: publicIssuer });
  assert.equal(network.keycloakServerUrl(publicIssuer + "/protocol/openid-connect/token"),
    publicIssuer + "/protocol/openid-connect/token");
});

test("server discovery uses the internal URL without changing the public issuer", () => {
  const env = { ...settings };
  const network = load(env);
  assert.equal(network.keycloakServerUrl(publicIssuer + "/.well-known/openid-configuration"),
    internalIssuer + "/.well-known/openid-configuration");
  assert.equal(env.AUTH_KEYCLOAK_ISSUER, publicIssuer);
});

test("token refresh and query parameters use the internal address", () => {
  const network = load(settings);
  assert.equal(network.keycloakServerUrl(new URL(publicIssuer + "/protocol/openid-connect/token?check=1")),
    internalIssuer + "/protocol/openid-connect/token?check=1");
});

test("other hosts and other realms are not redirected", () => {
  const network = load(settings);
  for (const url of ["http://localhost:8080/realms/master", "https://accounts.google.com/token",
    "http://localhost:8080/realms/plantguard-other/token", internalIssuer + "/token"]) {
    assert.equal(network.keycloakServerUrl(url), url);
  }
});

test("trailing slashes and internal base paths work", () => {
  const network = load({ AUTH_KEYCLOAK_ISSUER: publicIssuer + "/",
    AUTH_KEYCLOAK_INTERNAL_ISSUER: "http://keycloak:8080/auth/realms/plantguard/" });
  assert.equal(network.keycloakServerUrl(publicIssuer + "/protocol/openid-connect/token"),
    "http://keycloak:8080/auth/realms/plantguard/protocol/openid-connect/token");
});

test("fetch preserves POST options, headers and body", async () => {
  let called;
  const network = load(settings, async (input, init) => {
    called = { input, init };
    return new Response("OK");
  });
  const options = { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "refresh_token", refresh_token: "test-token" }) };
  await network.keycloakFetch(publicIssuer + "/protocol/openid-connect/token", options);
  assert.equal(called.input, internalIssuer + "/protocol/openid-connect/token");
  assert.equal(called.init, options);
});

test("fetch accepts Request objects and preserves their body and headers", async () => {
  let called;
  const network = load(settings, async (input) => { called = input; return new Response("OK"); });
  const input = new Request(publicIssuer + "/protocol/openid-connect/token", {
    method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: "grant_type=refresh_token",
  });
  await network.keycloakFetch(input);
  assert.equal(called.url, internalIssuer + "/protocol/openid-connect/token");
  assert.equal(called.method, "POST");
  assert.equal(called.headers.get("Content-Type"), "application/x-www-form-urlencoded");
  assert.equal(await called.text(), "grant_type=refresh_token");
  assert.equal(input.url, publicIssuer + "/protocol/openid-connect/token");
});
