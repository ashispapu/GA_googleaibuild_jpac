"use client";

import * as React from "react";
import * as LabelPrimitive from "@radix-ui/react-label";
import * as RadioGroupPrimitive from "@radix-ui/react-radio-group";
import { cn } from "@/lib/utils";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        "h-[46px] w-full rounded-md border border-line-strong bg-surface px-3 text-[15px] text-ink placeholder:text-ink-5 focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/20",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea
      ref={ref}
      className={cn(
        "w-full resize-y rounded-md border border-line-strong bg-surface px-3 py-2.5 text-sm leading-relaxed text-ink focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/20",
        className,
      )}
      {...props}
    />
  ),
);
Textarea.displayName = "Textarea";

export const Label = React.forwardRef<
  React.ElementRef<typeof LabelPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof LabelPrimitive.Root>
>(({ className, ...props }, ref) => (
  <LabelPrimitive.Root ref={ref} className={cn("text-sm font-semibold text-ink-2", className)} {...props} />
));
Label.displayName = "Label";

export const RadioGroup = React.forwardRef<
  React.ElementRef<typeof RadioGroupPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof RadioGroupPrimitive.Root>
>(({ className, ...props }, ref) => <RadioGroupPrimitive.Root ref={ref} className={cn("flex flex-wrap gap-2.5", className)} {...props} />);
RadioGroup.displayName = "RadioGroup";

/** A big, tappable choice (≥ 44 px) used for Yes / No answers. */
export function ChoiceCard({ value, children, tone = "brand" }: { value: string; children: React.ReactNode; tone?: "ok" | "brand" }) {
  return (
    <RadioGroupPrimitive.Item
      value={value}
      className={cn(
        "flex h-12 min-w-[120px] flex-1 items-center gap-2.5 rounded-md border border-line-strong bg-surface px-3.5 text-[15px] text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand",
        tone === "ok"
          ? "data-[state=checked]:border-2 data-[state=checked]:border-ok data-[state=checked]:bg-ok-soft data-[state=checked]:font-semibold"
          : "data-[state=checked]:border-2 data-[state=checked]:border-brand data-[state=checked]:bg-brand-soft data-[state=checked]:font-semibold",
      )}
    >
      <span className="flex h-[18px] w-[18px] items-center justify-center rounded-full border-2 border-line-strong">
        <RadioGroupPrimitive.Indicator className="h-2.5 w-2.5 rounded-full bg-current" />
      </span>
      {children}
    </RadioGroupPrimitive.Item>
  );
}
