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

  const logout = useCallback(async () => {
    clear();
    if (modeRef.current === "keycloak") await authJsSignOut({ callbackUrl: "/login" });
    else router.replace("/login");
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
