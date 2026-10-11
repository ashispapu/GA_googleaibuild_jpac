"use client";

import { ActiveIncidentRedirect } from "@/components/active-incident";

const to = (id: string) => `/investigation/${id}`;

export default function InvestigationIndex() {
  return <ActiveIncidentRedirect to={to} title="Investigation" />;
}
