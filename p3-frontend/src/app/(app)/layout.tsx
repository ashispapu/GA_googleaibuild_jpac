"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { AppShell } from "@/components/shell/AppShell";
import { useSession } from "@/lib/session";

export default function SignedInLayout({ children }: { children: ReactNode }) {
  const { status } = useSession();
  const router = useRouter();

  useEffect(() => {
    if (status === "signed-out") router.replace("/login");
  }, [status, router]);

  if (status !== "signed-in") {
    return (
      <div className="flex min-h-screen items-center justify-center gap-2 text-ink-4">
        <Loader2 className="h-5 w-5 animate-spin" aria-hidden /> Loading PlantGuard…
      </div>
    );
  }
  return <AppShell>{children}</AppShell>;
}
