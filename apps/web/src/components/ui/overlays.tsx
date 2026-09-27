"use client";
import { Dialog } from "@base-ui-components/react/dialog";
import { Tabs as BaseTabs } from "@base-ui-components/react/tabs";
import { Tooltip as BaseTooltip } from "@base-ui-components/react/tooltip";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/* --------------------------------- Sheet ------------------------------------ */
export function Sheet({ open, onOpenChange, children }: { open: boolean; onOpenChange: (o: boolean) => void; children: ReactNode }) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      {children}
    </Dialog.Root>
  );
}

export function SheetContent({ children, title, description, side = "right", className }: { children: ReactNode; title?: string; description?: string; side?: "right" | "center"; className?: string }) {
  return (
    <Dialog.Portal>
      <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm transition-opacity data-[starting-style]:opacity-0 data-[ending-style]:opacity-0" />
      <Dialog.Popup
        className={cn(
          "fixed z-50 flex flex-col border-[var(--color-border-strong)] bg-[var(--color-bg-elevated)] shadow-[var(--shadow-lg)] transition-transform",
          side === "right"
            ? "right-0 top-0 h-full w-full max-w-xl border-l data-[starting-style]:translate-x-full data-[ending-style]:translate-x-full"
            : "left-1/2 top-1/2 max-h-[85vh] w-full max-w-2xl -translate-x-1/2 -translate-y-1/2 rounded-[var(--radius-lg)] border data-[starting-style]:scale-95 data-[starting-style]:opacity-0",
          className,
        )}
      >
        {(title || description) && (
          <div className="flex items-start justify-between gap-4 border-b border-[var(--color-border)] px-6 py-4">
            <div>
              {title ? <Dialog.Title className="text-base font-semibold">{title}</Dialog.Title> : null}
              {description ? <Dialog.Description className="mt-0.5 text-sm text-[var(--color-text-muted)]">{description}</Dialog.Description> : null}
            </div>
            <Dialog.Close className="rounded-md p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)] focus-ring">
              <X className="h-4 w-4" />
            </Dialog.Close>
          </div>
        )}
        <div className="flex-1 overflow-y-auto">{children}</div>
      </Dialog.Popup>
    </Dialog.Portal>
  );
}

export const SheetTrigger = Dialog.Trigger;
export const SheetClose = Dialog.Close;

/* --------------------------------- Tabs ------------------------------------- */
export function Tabs({ value, onValueChange, children, className }: { value: string; onValueChange: (v: string) => void; children: ReactNode; className?: string }) {
  return (
    <BaseTabs.Root value={value} onValueChange={(v) => onValueChange(String(v))} className={className}>
      {children}
    </BaseTabs.Root>
  );
}
export function TabsList({ children, className }: { children: ReactNode; className?: string }) {
  return <BaseTabs.List className={cn("flex items-center gap-1 border-b border-[var(--color-border)]", className)}>{children}</BaseTabs.List>;
}
export function TabTrigger({ value, children }: { value: string; children: ReactNode }) {
  return (
    <BaseTabs.Tab
      value={value}
      className="relative -mb-px px-3.5 py-2.5 text-sm font-medium text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)] data-[selected]:text-[var(--color-text)] focus-ring data-[selected]:after:absolute data-[selected]:after:inset-x-2 data-[selected]:after:-bottom-px data-[selected]:after:h-0.5 data-[selected]:after:rounded-full data-[selected]:after:bg-[var(--color-accent)]"
    >
      {children}
    </BaseTabs.Tab>
  );
}
export function TabPanel({ value, children, className }: { value: string; children: ReactNode; className?: string }) {
  return (
    <BaseTabs.Panel value={value} className={cn("focus-ring outline-none", className)}>
      {children}
    </BaseTabs.Panel>
  );
}

/* -------------------------------- Tooltip ----------------------------------- */
export function Tooltip({ content, children }: { content: ReactNode; children: ReactNode }) {
  return (
    <BaseTooltip.Provider delay={200}>
      <BaseTooltip.Root>
        <BaseTooltip.Trigger render={<span className="inline-flex" />}>{children}</BaseTooltip.Trigger>
        <BaseTooltip.Portal>
          <BaseTooltip.Positioner sideOffset={6}>
            <BaseTooltip.Popup className="z-50 max-w-xs rounded-md border border-[var(--color-border-strong)] bg-[var(--color-surface-3)] px-2.5 py-1.5 text-xs text-[var(--color-text)] shadow-[var(--shadow)]">
              {content}
            </BaseTooltip.Popup>
          </BaseTooltip.Positioner>
        </BaseTooltip.Portal>
      </BaseTooltip.Root>
    </BaseTooltip.Provider>
  );
}
