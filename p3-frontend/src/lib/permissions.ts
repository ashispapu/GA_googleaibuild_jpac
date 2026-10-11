// C7 ROLE_PERMISSIONS, mirrored for the UI.
// This is only a hint for what to show (lock badges, hidden costs).
// The backend still decides every call; a 403 always wins over this table.

export type Permission =
  | "view_plant"
  | "view_costs"
  | "run_agents"
  | "approve"
  | "submit_feedback"
  | "view_audit"
  | "run_simulator";

export type Role = "operator" | "maintenance_lead" | "plant_manager" | "admin";

export const PERMISSIONS: Permission[] = [
  "view_plant",
  "view_costs",
  "run_agents",
  "approve",
  "submit_feedback",
  "view_audit",
  "run_simulator",
];

export const ROLES: Role[] = ["operator", "maintenance_lead", "plant_manager", "admin"];

export const ROLE_PERMISSIONS: Record<Role, Permission[]> = {
  operator: ["view_plant", "run_agents", "submit_feedback"],
  maintenance_lead: ["view_plant", "view_costs", "run_agents", "approve", "submit_feedback"],
  plant_manager: ["view_plant", "view_costs", "run_agents", "approve", "view_audit"],
  admin: [...PERMISSIONS],
};

/**
 * Pass the signed-in user (GET /auth/me returns `permissions`) or just a role.
 * The user's own permission list wins; the role table is the fallback.
 */
export function can(who: { role: string; permissions?: string[] } | string | null | undefined, permission: Permission): boolean {
  if (!who) return false;
  if (typeof who !== "string" && who.permissions?.length) return who.permissions.includes(permission);
  const role = typeof who === "string" ? who : who.role;
  return ROLE_PERMISSIONS[role as Role]?.includes(permission) ?? false;
}
