import { cn } from "@/lib/cn";
import { initials } from "@/lib/format";

const palette = ["#8b6dff", "#22d3ee", "#34d399", "#f59e0b", "#f472b6", "#60a5fa", "#a3e635", "#fb7185"];

function hash(s: string) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

export function Avatar({ name, src, size = 32, className }: { name: string; src?: string | null; size?: number; className?: string }) {
  const style = { width: size, height: size, fontSize: Math.max(10, size * 0.38) };
  if (src) {
     
    return <img src={src} alt="" width={size} height={size} style={style} className={cn("shrink-0 rounded-full object-cover ring-1 ring-border", className)} />;
  }
  return (
    <span
      aria-hidden
      style={{ ...style, background: `${palette[hash(name) % palette.length]}33`, color: palette[hash(name) % palette.length] }}
      className={cn("inline-flex shrink-0 items-center justify-center rounded-full font-semibold ring-1 ring-border", className)}
    >
      {initials(name)}
    </span>
  );
}

export function AvatarStack({ people, max = 4, size = 24 }: { people: { display_name: string; avatar_url?: string | null }[]; max?: number; size?: number }) {
  const shown = people.slice(0, max);
  return (
    <span className="flex -space-x-2">
      {shown.map((p, i) => (
        <Avatar key={i} name={p.display_name} src={p.avatar_url} size={size} className="ring-2 ring-surface" />
      ))}
      {people.length > max ? (
        <span className="inline-flex items-center justify-center rounded-full bg-surface-3 text-[10px] font-medium text-muted ring-2 ring-surface" style={{ width: size, height: size }}>
          +{people.length - max}
        </span>
      ) : null}
    </span>
  );
}
