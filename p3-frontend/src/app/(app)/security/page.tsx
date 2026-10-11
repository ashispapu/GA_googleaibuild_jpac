"use client";

import { Check, Database, KeyRound, Lock, RefreshCw, ShieldCheck } from "lucide-react";
import { EmptyState, LockBadge, PageTitle, QueryError } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDayTime, formatTime } from "@/lib/format";
import { auditLabel, PERMISSION_LABEL, redactionLabel, roleLabel } from "@/lib/labels";
import { can, PERMISSIONS, ROLE_PERMISSIONS, ROLES } from "@/lib/permissions";
import { useAudit, useAuditVerify, useRedactions } from "@/lib/queries";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

export default function SecurityPage() {
  const { me } = useSession();
  const canAudit = can(me, "view_audit");
  const redactions = useRedactions();
  const audit = useAudit(canAudit);
  const verify = useAuditVerify(canAudit);

  // Group every replaced value by kind (C7: <KIND_n>)
  const byKind = new Map<string, { count: number; example: string }>();
  for (const report of redactions.data ?? []) {
    for (const item of report.findings ?? []) {
      const prev = byKind.get(item.kind);
      byKind.set(item.kind, { count: (prev?.count ?? 0) + 1, example: prev?.example ?? item.placeholder });
    }
  }
  const guardEntries = (audit.data?.items ?? []).filter((a) => /guard|injection/i.test(a.action));

  return (
    <>
      <PageTitle title="Security" sub="Who can do what, what we hide from the AI, and proof that nobody changed the record." />

      <Card className="flex flex-col gap-3">
        <CardHeader>
          <CardTitle>Who can do what</CardTitle>
          <span className="text-[13px] text-ink-5">Checked by the server every time someone clicks</span>
        </CardHeader>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px] border-collapse text-sm">
            <thead>
              <tr className="text-center text-[13px] text-ink-4">
                <th scope="col" className="py-2.5 pr-3 text-left font-semibold">Role</th>
                {PERMISSIONS.map((p) => (
                  <th key={p} scope="col" className="px-1.5 py-2.5 font-semibold">
                    {PERMISSION_LABEL[p]}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="text-center">
              {ROLES.map((r) => {
                const mine = me?.role === r;
                return (
                  <tr key={r} className={cn("border-t border-line-soft", mine && "bg-brand-tint")}>
                    <th scope="row" className={cn("py-3 pr-3 text-left font-medium", mine && "pl-2")}>
                      {roleLabel(r)}
                      {mine && <Badge tone="brand" className="ml-2 px-2 py-0.5 text-[11px]">you</Badge>}
                    </th>
                    {PERMISSIONS.map((p) =>
                      ROLE_PERMISSIONS[r].includes(p) ? (
                        <td key={p} className="px-1.5 py-3">
                          <Check className="mx-auto h-[18px] w-[18px] text-ok" strokeWidth={2.6} aria-label="Yes" />
                        </td>
                      ) : (
                        <td key={p} className="px-1.5 py-3 text-[#9AA8BA]" aria-label="No">
                          —
                        </td>
                      ),
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="flex flex-wrap items-start gap-4">
        <Card className="flex min-w-0 flex-[1_1_420px] flex-col gap-3">
          <CardTitle>Hidden before anything goes to the AI</CardTitle>
          <p className="m-0 text-sm leading-relaxed text-ink-4">
            Private details are swapped for placeholders. The real values never leave the plant — only a fingerprint is kept to prove what was hidden.
          </p>
          {redactions.error ? (
            <QueryError error={redactions.error} what="the hidden details" />
          ) : byKind.size === 0 ? (
            <EmptyState title="Nothing has been sent to the AI yet." />
          ) : (
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="text-left text-[13px] text-ink-4">
                  <th scope="col" className="py-2 font-semibold">What</th>
                  <th scope="col" className="py-2 font-semibold">Replaced with</th>
                  <th scope="col" className="py-2 text-right font-semibold">Times</th>
                </tr>
              </thead>
              <tbody>
                {[...byKind.entries()].map(([kind, v]) => (
                  <tr key={kind} className="border-t border-line-soft">
                    <td className="py-2.5">{redactionLabel(kind)}</td>
                    <td className="py-2.5 font-mono text-warn-text">{v.example}</td>
                    <td className="py-2.5 text-right font-mono">{v.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <div className="flex min-w-0 flex-[1_1_380px] flex-col gap-4">
          <Card className="flex flex-col gap-3">
            <CardHeader>
              <CardTitle>Hidden instructions blocked</CardTitle>
              {canAudit && <Badge tone={guardEntries.length ? "crit" : "ok"}>{guardEntries.length} blocked</Badge>}
            </CardHeader>
            {!canAudit ? (
              <LockBadge />
            ) : guardEntries.length === 0 ? (
              <p className="m-0 text-sm text-ink-4">No hidden instructions found in documents so far.</p>
            ) : (
              <ul className="m-0 flex list-none flex-col gap-2 p-0 text-sm">
                {guardEntries.slice(-3).map((g) => (
                  <li key={g.seq} className="rounded-md border border-dashed border-[#E59A93] bg-[#FFF8F7] px-3 py-2">
                    <div className="text-crit-deep">{auditLabel(g.action)}{g.resource ? ` · ${g.resource}` : ""}</div>
                    <div className="text-xs text-ink-5">{formatDayTime(g.ts)}</div>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card className="flex flex-col gap-3.5">
            <CardHeader>
              <CardTitle>Activity record</CardTitle>
              {canAudit && verify.data && <Badge tone={verify.data.valid ? "ok" : "crit"}>{verify.data.valid ? "Untouched" : "Changed!"}</Badge>}
            </CardHeader>
            {!canAudit ? (
              <div className="flex flex-col items-start gap-2">
                <LockBadge />
                <p className="m-0 text-sm text-ink-4">Plant Managers and Admins can see the activity record.</p>
              </div>
            ) : (
              <>
                <p className="m-0 text-sm leading-relaxed text-ink-4">
                  Each entry is locked to the one before it, so any change would show up right away.
                  {verify.data && (
                    <>
                      {" "}
                      {verify.data.entries_checked} entries checked{verify.dataUpdatedAt ? ` at ${formatTime(verify.dataUpdatedAt)}` : ""}.
                    </>
                  )}
                  {verify.data && !verify.data.valid && verify.data.broken_at_seq != null && (
                    <> The first changed entry is number {verify.data.broken_at_seq}.</>
                  )}
                </p>
                {verify.error ? <QueryError error={verify.error} what="the activity record" /> : null}
                <Button variant="soft" className="self-start" onClick={() => void verify.refetch()} disabled={verify.isFetching}>
                  <RefreshCw className={cn(verify.isFetching && "animate-spin")} /> Check now
                </Button>
              </>
            )}
          </Card>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2.5">
        <span className="mr-1 text-[13px] text-ink-5">Always on</span>
        <Chip icon={<Lock className="h-3.5 w-3.5 text-brand" />}>Encrypted connection</Chip>
        <Chip icon={<Database className="h-3.5 w-3.5 text-brand" />}>Encrypted storage</Chip>
        <Chip icon={<KeyRound className="h-3.5 w-3.5 text-brand" />}>Passwords kept in a vault</Chip>
        <Chip icon={<ShieldCheck className="h-3.5 w-3.5 text-brand" />}>Follows IEC 62443 and GDPR</Chip>
      </div>
    </>
  );
}

function Chip({ icon, children }: { icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1.5 text-[13px]">
      {icon}
      {children}
    </span>
  );
}
