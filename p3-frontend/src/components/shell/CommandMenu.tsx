"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Crosshair, Gauge, LayoutGrid, ListChecks, LogOut, MonitorPlay, Search, Shield } from "lucide-react";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { useAppState } from "@/lib/app-state";
import { usePlants } from "@/lib/queries";
import { usePresenter } from "@/lib/presenter";
import { useSession } from "@/lib/session";
import { PresenterSignIn } from "./PresenterBar";

export function CommandMenu({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const router = useRouter();
  const { plantId, currentIncidentId: activeIncidentId } = useAppState();
  const { logout } = useSession();
  const presenter = usePresenter();
  const plants = usePlants();
  const machines = plants.data?.find((p) => p.id === plantId)?.lines.flatMap((l) => l.stations.flatMap((s) => s.machines)) ?? [];
  const [presenterSignIn, setPresenterSignIn] = useState(false);

  const go = (href: string) => {
    onOpenChange(false);
    router.push(href);
  };

  return (
    <>
      <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
        <DialogPrimitive.Portal>
          <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-[rgba(22,32,44,0.35)]" />
          <DialogPrimitive.Content className="fixed left-1/2 top-[12vh] z-50 w-[calc(100%-32px)] max-w-[560px] -translate-x-1/2 overflow-hidden rounded-xl border border-line bg-surface shadow-pop">
            <DialogPrimitive.Title className="sr-only">Search and jump</DialogPrimitive.Title>
            <Command>
              <CommandInput placeholder="Type a machine, a page or an action…" />
              <CommandList>
                <CommandEmpty>Nothing found.</CommandEmpty>
                <CommandGroup heading="Go to">
                  <CommandItem onSelect={() => go("/")}><LayoutGrid /> Overview</CommandItem>
                  {activeIncidentId && (
                    <>
                      <CommandItem onSelect={() => go(`/investigation/${activeIncidentId}`)}><Search /> Investigation {activeIncidentId}</CommandItem>
                      <CommandItem onSelect={() => go(`/options/${activeIncidentId}`)}><ListChecks /> Options for {activeIncidentId}</CommandItem>
                    </>
                  )}
                  <CommandItem onSelect={() => go("/security")}><Shield /> Security</CommandItem>
                  <CommandItem onSelect={() => go("/accuracy")}><Crosshair /> Warning accuracy</CommandItem>
                </CommandGroup>
                {machines.length > 0 && (
                  <CommandGroup heading="Machines">
                    {machines.map((m) => (
                      <CommandItem key={m.id} value={`${m.id} ${m.name}`} onSelect={() => go(`/machines/${m.id}`)}>
                        <Gauge /> <span className="font-mono">{m.id}</span> {m.name}
                      </CommandItem>
                    ))}
                  </CommandGroup>
                )}
                <CommandGroup heading="Actions">
                  {presenter.enabled ? (
                    <CommandItem onSelect={() => { presenter.disable(); onOpenChange(false); }}><MonitorPlay /> Turn off demo controls</CommandItem>
                  ) : (
                    <CommandItem onSelect={() => { onOpenChange(false); setPresenterSignIn(true); }}><MonitorPlay /> Turn on demo controls (admin)</CommandItem>
                  )}
                  <CommandItem onSelect={() => { onOpenChange(false); void logout(); }}><LogOut /> Sign out</CommandItem>
                </CommandGroup>
              </CommandList>
            </Command>
          </DialogPrimitive.Content>
        </DialogPrimitive.Portal>
      </DialogPrimitive.Root>
      <PresenterSignIn open={presenterSignIn} onOpenChange={setPresenterSignIn} />
    </>
  );
}
