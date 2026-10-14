"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { Mono, PageTitle, QueryError, Skeleton } from "@/components/common";
import { Card } from "@/components/ui/card";
import { useDoc } from "@/lib/queries";
import { cn } from "@/lib/utils";

/** One manual (or past fix), section by section. `#s-7.4` scrolls to and highlights §7.4. */
export default function ManualPage() {
  const { docId } = useParams<{ docId: string }>();
  const id = decodeURIComponent(docId);
  const doc = useDoc(id);
  const [target, setTarget] = useState<string | null>(null);

  useEffect(() => {
    const read = () => setTarget(window.location.hash.startsWith("#s-") ? decodeURIComponent(window.location.hash.slice(3)) : null);
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);
  useEffect(() => {
    if (!doc.data || !target) return;
    document.getElementById(`s-${target}`)?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [doc.data, target]);

  if (doc.error && !doc.data) return <QueryError error={doc.error} onRetry={() => void doc.refetch()} what="this manual" />;
  if (!doc.data) return <Skeleton className="h-96" />;
  const d = doc.data;

  return (
    <>
      <Link href="/manuals" className="flex items-center gap-1.5 text-[13px]">
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden /> All manuals
      </Link>
      <PageTitle
        title={d.title}
        sub={
          <>
            <Mono>{d.id}</Mono>
            {[d.equipment_model, d.asset_type, d.asset_id].filter(Boolean).map((x) => ` · ${x}`)}
          </>
        }
      />
      <div className="flex flex-wrap items-start gap-4">
        <Card className="sticky top-4 flex max-h-[80vh] min-w-0 flex-[1_1_220px] flex-col gap-1 overflow-y-auto py-3">
          <span className="px-1 pb-1 text-[13px] font-semibold text-ink-4">Contents</span>
          {d.sections.map((s) => (
            <a
              key={s.section}
              href={`#s-${s.section}`}
              className={cn("rounded px-2 py-1 text-[13px] no-underline", target === s.section ? "bg-brand-soft font-semibold text-brand-text" : "text-ink-3 hover:bg-subtle")}
            >
              {s.section} {s.heading}
            </a>
          ))}
          {d.used_by.length > 0 && (
            <div className="mt-2 border-t border-line px-1 pt-2 text-[13px] text-ink-4">
              Used on issues:{" "}
              {d.used_by.map((u, i) => (
                <span key={u}>
                  {i > 0 && ", "}
                  <Link href={`/investigation/${u}`}><Mono>{u}</Mono></Link>
                </span>
              ))}
            </div>
          )}
        </Card>
        <Card className="flex min-w-0 flex-[3_1_520px] flex-col gap-5">
          {d.sections.map((s) => (
            <section
              key={s.section}
              id={`s-${s.section}`}
              className={cn("scroll-mt-6 rounded-md p-2 transition-colors", target === s.section && "bg-warn-soft ring-1 ring-warn-border")}
            >
              <h2 className="m-0 flex items-baseline gap-2 text-base font-semibold">
                <Mono className="text-ink-4">§{s.section}</Mono> {s.heading}
                <span className="ml-auto text-xs font-normal text-ink-5">page {s.page}</span>
              </h2>
              <p className="m-0 mt-2 whitespace-pre-line text-[15px] leading-relaxed text-ink-2">{s.text}</p>
            </section>
          ))}
        </Card>
      </div>
    </>
  );
}
