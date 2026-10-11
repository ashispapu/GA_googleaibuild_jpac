"use client";

import { ActiveIncidentRedirect } from "@/components/active-incident";

const to = (id: string) => `/options/${id}`;

export default function OptionsIndex() {
  return <ActiveIncidentRedirect to={to} title="Options" />;
}
