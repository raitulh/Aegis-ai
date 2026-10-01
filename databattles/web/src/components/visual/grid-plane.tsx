import { cn } from "@/lib/cn";

/**
 * A perspective grid floor that recedes toward a horizon — the "arena" the platform's data lives on.
 * The lines drift slowly forward (CSS only; frozen under reduced motion).
 */
export function GridPlane({ className, animated = true }: { className?: string; animated?: boolean }) {
  return (
    <div aria-hidden className={cn("pointer-events-none absolute inset-x-0 bottom-0 h-1/2 overflow-hidden [perspective:520px]", className)}>
      <div
        className={cn(
          "absolute inset-x-[-50%] bottom-[-10%] h-[160%] origin-bottom [transform:rotateX(64deg)]",
          animated && "motion-safe:animate-[grid-scroll_9s_linear_infinite]",
        )}
        style={{
          backgroundImage:
            "linear-gradient(color-mix(in oklab, var(--accent) 22%, transparent) 1px, transparent 1px), linear-gradient(90deg, color-mix(in oklab, var(--accent) 22%, transparent) 1px, transparent 1px)",
          backgroundSize: "64px 64px",
          maskImage: "linear-gradient(to top, black 10%, transparent 85%)",
        }}
      />
    </div>
  );
}
