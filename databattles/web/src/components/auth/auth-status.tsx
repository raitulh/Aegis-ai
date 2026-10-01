import type { ReactNode } from "react";

import { cn } from "@/lib/cn";
import { CheckGlyph, OrbitMotif } from "./orbit-motif";

/**
 * Centred status block for auth flows (email sent, verified, password updated, link failed, working).
 * The orbit motif carries the icon; `celebrate` adds the success pulse + self-drawing check and must only be
 * used for a real, completed success. The title and body are announced politely (or assertively for errors);
 * the action slot sits outside the live region.
 */
export function AuthStatus({
  tone,
  icon,
  title,
  children,
  action,
  celebrate = false,
  busy = false,
  className,
}: {
  tone: "accent" | "success" | "danger" | "cyan";
  icon?: ReactNode;
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  celebrate?: boolean;
  busy?: boolean;
  className?: string;
}) {
  return (
    <div className={cn("flex animate-scale-in flex-col items-center text-center", className)}>
      <OrbitMotif tone={tone} pulse={celebrate} busy={busy}>
        {celebrate && !icon ? <CheckGlyph /> : icon}
      </OrbitMotif>
      {/* Only the message is the live region; actions stay outside so their labels aren't read as part of it. */}
      {title || children ? (
        <div role={tone === "danger" ? "alert" : "status"} className="flex flex-col items-center">
          {title ? <p className="mt-6 text-base font-semibold tracking-[-0.015em] text-fg">{title}</p> : null}
          {children ? <div className={cn("max-w-sm text-sm leading-relaxed text-muted", title ? "mt-1.5" : "mt-6")}>{children}</div> : null}
        </div>
      ) : null}
      {action ? <div className="mt-6 flex w-full flex-wrap justify-center gap-2">{action}</div> : null}
    </div>
  );
}
