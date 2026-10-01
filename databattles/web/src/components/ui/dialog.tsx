"use client";

import * as RD from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useState, type ReactNode } from "react";

import { cn } from "@/lib/cn";
import { Button } from "./button";
import { Textarea } from "./form";

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  trigger,
  size = "md",
}: {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  trigger?: ReactNode;
  size?: "sm" | "md" | "lg";
}) {
  return (
    <RD.Root open={open} onOpenChange={onOpenChange}>
      {trigger ? <RD.Trigger asChild>{trigger}</RD.Trigger> : null}
      <RD.Portal>
        <RD.Overlay className="fixed inset-0 z-50 bg-[rgb(3_4_8/0.62)] backdrop-blur-[6px] data-[state=open]:animate-fade-in" />
        <RD.Content
          className={cn(
            "fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[calc(100vw-2rem)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto",
            "rounded-[var(--radius-xl)] border border-border-strong bg-surface surface-sheen p-6 shadow-elevated outline-none",
            "data-[state=open]:animate-[dialog-in_260ms_var(--ease-out)_both]",
            size === "sm" && "max-w-md",
            size === "md" && "max-w-lg",
            size === "lg" && "max-w-3xl",
          )}
        >
          <div className="flex items-start justify-between gap-4">
            <div>
              <RD.Title className="text-lg font-semibold tracking-[-0.02em] text-fg">{title}</RD.Title>
              {description ? <RD.Description className="mt-1 text-sm text-muted">{description}</RD.Description> : <RD.Description className="sr-only">{String(title)}</RD.Description>}
            </div>
            <RD.Close className="-mr-1 -mt-1 rounded-lg p-1.5 text-subtle transition-colors hover:bg-surface-2 hover:text-fg" aria-label="Close">
              <X className="h-4 w-4" />
            </RD.Close>
          </div>
          {children ? <div className="mt-5">{children}</div> : null}
          {footer ? <div className="-mx-6 -mb-6 mt-6 flex flex-wrap justify-end gap-2 border-t border-border bg-bg-elevated/50 px-6 py-4">{footer}</div> : null}
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  );
}

/**
 * Confirmation for destructive or irreversible actions. With `requireReason`, collects a reason
 * (sent to the audit log) before enabling the confirm button.
 */
export function ConfirmDialog({
  trigger,
  title,
  description,
  confirmLabel = "Confirm",
  tone = "danger",
  requireReason,
  reasonLabel = "Reason (recorded in the audit log)",
  onConfirm,
}: {
  trigger: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  confirmLabel?: string;
  tone?: "danger" | "primary";
  requireReason?: boolean;
  reasonLabel?: string;
  onConfirm: (reason: string) => Promise<unknown> | void;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) setReason("");
      }}
      trigger={trigger}
      title={title}
      description={description}
      size="sm"
      footer={
        <>
          <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
          <Button
            variant={tone === "danger" ? "danger" : "primary"}
            loading={busy}
            disabled={requireReason && reason.trim().length < 3}
            onClick={async () => {
              setBusy(true);
              try {
                await onConfirm(reason.trim());
                setOpen(false);
                setReason("");
              } finally {
                setBusy(false);
              }
            }}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      {requireReason ? (
        <label className="block text-sm">
          <span className="font-medium">{reasonLabel}</span>
          <Textarea className="mt-1.5" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} rows={3} />
        </label>
      ) : null}
    </Dialog>
  );
}
