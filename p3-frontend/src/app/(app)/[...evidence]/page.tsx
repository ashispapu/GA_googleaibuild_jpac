"use client";

/**
 * Evidence viewer. `evidence.link` is a UI route chosen by the backend (C2);
 * any link that has no screen of its own lands here and shows what the AI cited.
 */
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { ArrowLeft, FileText } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

export default function EvidencePage() {
  return (
    <Suspense fallback={null}>
      <Evidence />
    </Suspense>
  );
}

function Evidence() {
  const router = useRouter();
  const { evidence } = useParams<{ evidence: string[] }>();
  const params = useSearchParams();
  const title = params.get("title") ?? decodeURIComponent(evidence.join(" / "));
  const snippet = params.get("snippet");

  return (
    <Card className="flex max-w-3xl flex-col gap-4">
      <span className="flex items-center gap-2 text-[13px] font-semibold text-brand-text">
        <FileText className="h-4 w-4" aria-hidden /> Evidence the AI used
      </span>
      <h1 className="m-0 text-2xl font-semibold">{title}</h1>
      {snippet ? (
        <blockquote className="m-0 rounded-md border-l-0 border border-line bg-subtle px-4 py-3 text-[15px] leading-relaxed text-ink-2">{snippet}</blockquote>
      ) : (
        <p className="m-0 text-sm text-ink-4">The AI cited this source. The full document is kept in the plant’s document system.</p>
      )}
      <p className="m-0 font-mono text-xs text-ink-5">/{evidence.join("/")}</p>
      <Button variant="outline" className="self-start" onClick={() => router.back()}>
        <ArrowLeft /> Back
      </Button>
    </Card>
  );
}
