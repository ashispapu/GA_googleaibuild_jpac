import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-1 text-[13px] font-semibold",
  {
    variants: {
      tone: {
        ok: "border-ok-border bg-ok-soft text-ok-text",
        warn: "border-warn-border bg-warn-soft text-warn-text",
        crit: "border-crit-border bg-crit-soft text-crit-text",
        brand: "border-brand-border bg-brand-soft text-brand-text",
        neutral: "border-line bg-subtle text-ink-3 font-medium",
        solid: "border-brand bg-brand text-white",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}
