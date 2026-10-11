"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { EmptyState, PageTitle } from "./common";
import { useAppState } from "@/lib/app-state";

/** /investigation and /options without an id: jump to the open issue, or say there is none. */
export function ActiveIncidentRedirect({ to, title }: { to: (id: string) => string; title: string }) {
  const { currentIncidentId: activeIncidentId } = useAppState();
  const router = useRouter();
  useEffect(() => {
    if (activeIncidentId) router.replace(to(activeIncidentId));
  }, [activeIncidentId, router, to]);
  return (
    <>
      <PageTitle title={title} />
      <EmptyState title="No open issues right now.">
        When the AI spots a machine that may break down, it opens an issue and the AI team starts looking into it. You’ll see it here.
      </EmptyState>
    </>
  );
}
