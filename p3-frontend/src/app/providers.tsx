"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as Tooltip from "@radix-ui/react-tooltip";
import { useState, type ReactNode } from "react";
import { SessionProvider } from "@/lib/session";
import { AppStateProvider } from "@/lib/app-state";
import { PresenterProvider } from "@/lib/presenter";

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { refetchOnWindowFocus: false, staleTime: 5_000 } },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <SessionProvider>
        <AppStateProvider>
          <PresenterProvider>
            <Tooltip.Provider delayDuration={200}>{children}</Tooltip.Provider>
          </PresenterProvider>
        </AppStateProvider>
      </SessionProvider>
    </QueryClientProvider>
  );
}
