"use client";

/**
 * Sign-in for both auth modes (C7). On start the UI asks GET /auth/config:
 *  - local    → our own login form → POST /auth/token
 *  - keycloak → Auth.js signIn("keycloak"), then setToken(session.accessToken)
 * Permissions are always decided by the backend; the UI only reads the role.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { getSession, signIn as authJsSignIn, signOut as authJsSignOut } from "next-auth/react";
import { backend, setToken, setUnauthenticatedHandler } from "./backend";
import type { User } from "./contract";

type Mode = "local" | "keycloak";
type Status = "loading" | "signed-out" | "signed-in";

interface SessionContextValue {
  status: Status;
  mode: Mode | null;
  me: User | null;
  /** set when /auth/config could not be reached */
  configError: string | null;
  loginLocal: (username: string, password: string) => Promise<void>;
  loginSso: () => Promise<void>;
  logout: () => Promise<void>;
}

const SessionContext = createContext<SessionContextValue | null>(null);
const TOKEN_KEY = "pg.token";

function store(key: string, value: string | null) {
  try {
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, value);
  } catch {
    /* private mode: token stays in memory only */
  }
}
function load(key: string): string | null {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [status, setStatus] = useState<Status>("loading");
  const [mode, setMode] = useState<Mode | null>(null);
  const [me, setMe] = useState<User | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const modeRef = useRef<Mode | null>(null);
  const logoutUrlRef = useRef<string | null>(null); // Keycloak end-session URL from GET /auth/config

  const clear = useCallback(() => {
    setToken("");
    store(TOKEN_KEY, null);
    setMe(null);
    setStatus("signed-out");
  }, []);

  const adopt = useCallback(async (token: string, user?: User) => {
    setToken(token);
    setMe(user ?? (await backend.me()));
    setStatus("signed-in");
  }, []);

  // C1: 401 anywhere → back to the login page
  useEffect(() => {
    setUnauthenticatedHandler(() => {
      clear();
      router.replace("/login?expired=1");
    });
    return () => setUnauthenticatedHandler(null);
  }, [clear, router]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      let m: Mode = "local";
      try {
        const cfg = await backend.authConfig();
        m = cfg.mode;
        logoutUrlRef.current = cfg.logout_url ?? null;
      } catch {
        setConfigError("Can't reach the PlantGuard server.");
      }
      if (cancelled) return;
      modeRef.current = m;
      setMode(m);
      try {
        if (m === "keycloak") {
          const s = (await getSession()) as { accessToken?: string } | null;
          if (s?.accessToken) {
            await adopt(s.accessToken);
            return;
          }
        } else {
          const saved = load(TOKEN_KEY);
          if (saved) {
            await adopt(saved);
            return;
          }
        }
      } catch {
        /* expired or invalid token */
      }
      if (!cancelled) clear();
    })();
    return () => {
      cancelled = true;
    };
  }, [adopt, clear]);

  // Keycloak: auth.ts refreshes the 15-min access token; pick up the new one every minute.
  useEffect(() => {
    if (mode !== "keycloak" || status !== "signed-in") return;
    const t = setInterval(async () => {
      const s = (await getSession()) as { accessToken?: string; error?: string } | null;
      if (!s?.accessToken || s.error === "RefreshFailed") {
        clear();
        router.replace("/login?expired=1");
      } else {
        setToken(s.accessToken);
      }
    }, 60_000);
    return () => clearInterval(t);
  }, [mode, status, clear, router]);

  const loginLocal = useCallback(
    async (username: string, password: string) => {
      const res = await backend.login(username, password); // TokenResponse carries the user
      store(TOKEN_KEY, res.access_token);
      await adopt(res.access_token, res.user);
    },
    [adopt],
  );

  const loginSso = useCallback(async () => {
    await authJsSignIn("keycloak", { callbackUrl: "/" });
  }, []);

  // Local mode: the backend keeps a server-side session; swap the 15-min token for a fresh one every 10 minutes.
  useEffect(() => {
    if (mode !== "local" || status !== "signed-in") return;
    const t = setInterval(async () => {
      try {
        const res = await backend.refresh();
        setToken(res.access_token);
        store(TOKEN_KEY, res.access_token);
      } catch {
        /* a 401 sends us to the login page through the handler above */
      }
    }, 10 * 60_000);
    return () => clearInterval(t);
  }, [mode, status]);

  const logout = useCallback(async () => {
    const idToken = modeRef.current === "keycloak" ? ((await getSession()) as { idToken?: string } | null)?.idToken : undefined;
    if (modeRef.current !== "keycloak") {
      try {
        await backend.logoutServer(); // ends the server-side session: this token stops working everywhere
      } catch {
        /* already ended */
      }
    }
    clear();
    if (modeRef.current !== "keycloak") {
      router.replace("/login");
      return;
    }
    // signOut only clears this app's cookie. Keycloak keeps its own SSO cookie, so without the
    // end-session redirect the next "Login with Keycloak" signs the old user straight back in.
    await authJsSignOut({ redirect: false });
    const logoutUrl = logoutUrlRef.current;
    if (!logoutUrl) {
      window.location.assign("/login");
      return;
    }
    const url = new URL(logoutUrl);
    url.searchParams.set("post_logout_redirect_uri", `${window.location.origin}/login`);
    if (idToken) url.searchParams.set("id_token_hint", idToken);
    else url.searchParams.set("client_id", "plantguard-web");
    window.location.assign(url.toString());
  }, [clear, router]);

  const value = useMemo(
    () => ({ status, mode, me, configError, loginLocal, loginSso, logout }),
    [status, mode, me, configError, loginLocal, loginSso, logout],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession() {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used inside <SessionProvider>");
  return ctx;
}
