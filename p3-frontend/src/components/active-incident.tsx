"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { EmptyState, PageTitle } from "./common";
import { IncidentRows } from "./incidents";
import { Card } from "./ui/card";
import { useAppState } from "@/lib/app-state";

/**
 * /investigation and /options without an id.
 * Simulated plant (v1.2): several issues can be open at once, so list them all.
 * Scripted story: jump to the one open issue, or say there is none.
 */
export function ActiveIncidentRedirect({ to, title }: { to: (id: string) => string; title: string }) {
  const { currentIncidentId, simMode, openIncidents } = useAppState();
  const router = useRouter();
  useEffect(() => {
    if (!simMode && currentIncidentId) router.replace(to(currentIncidentId));
  }, [simMode, currentIncidentId, router, to]);

  if (simMode) {
    const items = title === "Options" ? openIncidents.filter((o) => o.stage !== "ai_analysing") : openIncidents;
    return (
      <>
        <PageTitle
          title={title}
          sub={title === "Options" ? "Pick an issue to see the repair options the AI prepared." : "Pick an issue to watch the AI team work on it."}
        />
        <Card className="py-2">
          <IncidentRows items={items} hrefFor={(o) => to(o.incident.id)} empty="No open issues right now." />
        </Card>
      </>
    );
  }
  return (
    <>
      <PageTitle title={title} />
      <EmptyState title="No open issues right now.">
        When the AI spots a machine that may break down, it opens an issue and the AI team starts looking into it. You’ll see it here.
      </EmptyState>
    </>
  );
}
