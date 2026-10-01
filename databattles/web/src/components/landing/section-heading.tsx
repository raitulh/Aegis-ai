import type { ReactNode } from "react";

import { Reveal } from "@/components/motion/reveal";
import { cn } from "@/lib/cn";

/** Landing section heading: numbered eyebrow, expressive title, supporting copy and an optional action. */
export function SectionHeading({
  index,
  eyebrow,
  title,
  description,
  action,
  align = "left",
  className,
}: {
  index?: string;
  eyebrow: string;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  align?: "left" | "center";
  className?: string;
}) {
  return (
    <Reveal
      className={cn(
        "flex flex-col gap-5",
        align === "center" ? "items-center text-center" : "md:flex-row md:items-end md:justify-between",
        className,
      )}
    >
      <div className={cn("max-w-2xl", align === "center" && "mx-auto")}>
        <p className={cn("flex items-center gap-2 text-eyebrow text-accent-strong", align === "center" && "justify-center")}>
          {index ? <span className="text-subtle">{index}</span> : null}
          {index ? <span aria-hidden className="h-px w-6 bg-border-strong" /> : null}
          {eyebrow}
        </p>
        <h2 className="mt-3 text-headline text-fg">{title}</h2>
        {description ? <p className="mt-4 text-base leading-relaxed text-muted sm:text-[17px]">{description}</p> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </Reveal>
  );
}
