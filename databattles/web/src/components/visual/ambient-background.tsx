import { cn } from "@/lib/cn";

/**
 * The global atmosphere behind every page: two soft energy fields, a faint grid that fades out below the
 * fold and a whisper of grain. Pure CSS (no blur filters, no JS) so it costs one composited layer.
 * `intense` is used by focused flows (auth) and the landing hero for a little more depth.
 */
export function AmbientBackground({ intense = false, className }: { intense?: boolean; className?: string }) {
  return (
    <div aria-hidden className={cn("pointer-events-none fixed inset-0 -z-10 overflow-hidden", className)}>
      <div
        className="absolute left-[-20%] top-[-35%] h-[85vh] w-[80vw] motion-safe:animate-[aurora-drift_38s_ease-in-out_infinite]"
        style={{ background: "radial-gradient(closest-side, var(--ambient-a), transparent)" }}
      />
      <div
        className="absolute right-[-25%] top-[-30%] h-[75vh] w-[70vw] motion-safe:animate-[aurora-drift_46s_ease-in-out_infinite_reverse]"
        style={{ background: "radial-gradient(closest-side, var(--ambient-b), transparent)" }}
      />
      {intense ? (
        <div
          className="absolute bottom-[-40%] left-[20%] h-[80vh] w-[70vw]"
          style={{ background: "radial-gradient(closest-side, var(--ambient-c), transparent)" }}
        />
      ) : null}
      <div
        className="absolute inset-0"
        style={{
          backgroundImage: "linear-gradient(var(--grid-line) 1px, transparent 1px), linear-gradient(90deg, var(--grid-line) 1px, transparent 1px)",
          backgroundSize: "56px 56px",
          maskImage: intense
            ? "radial-gradient(ellipse 90% 70% at 50% 30%, black 20%, transparent 75%)"
            : "radial-gradient(ellipse 80% 45% at 50% 0%, black 15%, transparent 72%)",
        }}
      />
      <div className="noise absolute inset-0" />
    </div>
  );
}
