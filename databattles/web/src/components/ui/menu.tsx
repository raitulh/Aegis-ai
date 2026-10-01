"use client";

import * as DM from "@radix-ui/react-dropdown-menu";
import * as TT from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export const Menu = DM.Root;
export const MenuTrigger = DM.Trigger;

export function MenuContent({ children, align = "end", className }: { children: ReactNode; align?: "start" | "end" | "center"; className?: string }) {
  return (
    <DM.Portal>
      <DM.Content
        align={align}
        sideOffset={6}
        className={cn(
          "z-50 min-w-48 origin-[var(--radix-dropdown-menu-content-transform-origin)] rounded-[var(--radius-lg)] border border-border-strong bg-surface/95 p-1.5 shadow-elevated backdrop-blur-xl",
          "data-[state=open]:animate-[menu-in_180ms_var(--ease-out)_both]",
          className,
        )}
      >
        {children}
      </DM.Content>
    </DM.Portal>
  );
}

export function MenuItem({ children, onSelect, destructive, disabled }: { children: ReactNode; onSelect?: () => void; destructive?: boolean; disabled?: boolean }) {
  return (
    <DM.Item
      disabled={disabled}
      onSelect={onSelect}
      className={cn(
        "flex cursor-pointer items-center gap-2.5 rounded-[8px] px-2.5 py-2 text-[13.5px] outline-none transition-colors data-[disabled]:pointer-events-none data-[disabled]:opacity-50 [&_svg]:text-subtle",
        destructive ? "text-danger data-[highlighted]:bg-danger-soft [&_svg]:text-danger" : "text-fg data-[highlighted]:bg-surface-2 data-[highlighted]:[&_svg]:text-accent-strong",
      )}
    >
      {children}
    </DM.Item>
  );
}

export function MenuLabel({ children }: { children: ReactNode }) {
  return <DM.Label className="px-2.5 py-2 text-xs text-subtle">{children}</DM.Label>;
}

export function MenuSeparator() {
  return <DM.Separator className="-mx-1.5 my-1.5 h-px bg-border" />;
}

export function Tooltip({ content, children }: { content: ReactNode; children: ReactNode }) {
  return (
    <TT.Provider delayDuration={250}>
      <TT.Root>
        <TT.Trigger asChild>{children}</TT.Trigger>
        <TT.Portal>
          <TT.Content sideOffset={6} className="z-50 max-w-xs rounded-lg border border-border-strong bg-surface-3/95 px-2.5 py-1.5 text-xs text-fg shadow-elevated backdrop-blur-md data-[state=delayed-open]:animate-[menu-in_160ms_var(--ease-out)_both]">
            {content}
          </TT.Content>
        </TT.Portal>
      </TT.Root>
    </TT.Provider>
  );
}
