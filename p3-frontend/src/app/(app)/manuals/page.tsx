"use client";

import { useState } from "react";
import Link from "next/link";
import { BookOpen, FileText, Search } from "lucide-react";
import { EmptyState, Mono, PageTitle, QueryError, Skeleton } from "@/components/common";
import { Card, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/form";
import { useDocs, useDocSearch } from "@/lib/queries";
import { cn } from "@/lib/utils";

/** Manuals and past fixes, stored in the FAISS vector database the agents search. */
export default function ManualsPage() {
  const [kind, setKind] = useState<"manual" | "work_order">("manual");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const docs = useDocs(kind);
  const hits = useDocSearch(query);

  return (
    <>
      <PageTitle
        title="Manuals"
        sub="The same manuals and past fixes the AI agents search. Every closed repair is added here automatically."
      />
      <Card className="flex flex-col gap-3">
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            setQuery(q.trim());
          }}
        >
          <label htmlFor="doc-q" className="sr-only">Search the manuals</label>
          <Input id="doc-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. spindle vibration high after bearing change" className="flex-1" />
          <button type="submit" className="flex min-h-[44px] items-center gap-2 rounded-md bg-brand px-4 text-sm font-semibold text-white">
            <Search className="h-4 w-4" aria-hidden /> Search
          </button>
        </form>
        {query.length >= 3 &&
          (hits.error ? (
            <QueryError error={hits.error} what="the search" />
          ) : !hits.data ? (
            <Skeleton className="h-24" />
          ) : hits.data.chunks.length === 0 ? (
            <p className="m-0 text-sm text-ink-4">Nothing matches “{query}”.</p>
          ) : (
            <ul className="m-0 flex list-none flex-col divide-y divide-line-soft p-0">
              {hits.data.chunks.map((c, i) => (
                <li key={`${c.doc_id}-${c.section}-${i}`}>
                  <Link href={`/manuals/${encodeURIComponent(c.doc_id)}#s-${c.section}`} className="flex flex-col gap-1 px-1 py-3 text-ink no-underline hover:bg-subtle">
                    <span className="flex flex-wrap items-center gap-2 text-sm">
                      <strong className="font-semibold">{c.title}</strong>
                      <span className="text-ink-4">§{c.section} · page {c.page}</span>
                      <Mono className="ml-auto text-xs text-ink-5">match {Math.round(c.score * 100)}%</Mono>
                    </span>
                    <span className="line-clamp-2 text-[13px] text-ink-3">{c.text}</span>
                  </Link>
                </li>
              ))}
            </ul>
          ))}
      </Card>

      <div role="tablist" aria-label="Document kind" className="flex gap-1 border-b border-line">
        {(
          [
            ["manual", "Equipment manuals"],
            ["work_order", "Past fixes"],
          ] as const
        ).map(([k, label]) => (
          <button
            key={k}
            role="tab"
            type="button"
            aria-selected={kind === k}
            onClick={() => setKind(k)}
            className={cn("-mb-px border-b-2 px-3 py-2.5 text-sm", kind === k ? "border-brand font-semibold text-ink" : "border-transparent text-ink-4 hover:text-ink")}
          >
            {label}
          </button>
        ))}
      </div>
      <Card className="py-2">
        {docs.error && !docs.data ? (
          <QueryError error={docs.error} onRetry={() => void docs.refetch()} what="the documents" />
        ) : !docs.data ? (
          <Skeleton className="m-3 h-32" />
        ) : docs.data.length === 0 ? (
          <EmptyState title={kind === "manual" ? "No manuals loaded." : "No closed repairs yet."}>
            {kind === "work_order" && "When a technician closes a job, the fix is stored here so the agents can learn from it."}
          </EmptyState>
        ) : (
          <ul className="m-0 flex list-none flex-col divide-y divide-line-soft p-0">
            {docs.data.map((d) => (
              <li key={d.id}>
                <Link href={`/manuals/${encodeURIComponent(d.id)}`} className="flex items-center gap-3 px-1 py-3 text-ink no-underline hover:bg-subtle">
                  {d.kind === "manual" ? <BookOpen className="h-5 w-5 shrink-0 text-brand" aria-hidden /> : <FileText className="h-5 w-5 shrink-0 text-ink-4" aria-hidden />}
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="text-sm font-semibold">{d.title}</span>
                    <span className="text-xs text-ink-5">
                      {[d.equipment_model, d.asset_type, d.asset_id].filter(Boolean).join(" · ")}
                      {` · ${d.sections} section${d.sections === 1 ? "" : "s"}`}
                    </span>
                  </span>
                  <Mono className="text-xs text-ink-5">{d.id}</Mono>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}
