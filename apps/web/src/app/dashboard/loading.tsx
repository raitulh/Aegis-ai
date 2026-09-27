export default function Loading() {
  return (
    <div className="space-y-4">
      <div className="h-8 w-48 skeleton rounded-[var(--radius)]" />
      <div className="grid gap-3 sm:grid-cols-4">{Array.from({ length: 4 }).map((_, i) => <div key={i} className="h-24 skeleton rounded-[var(--radius-lg)]" />)}</div>
      <div className="h-64 skeleton rounded-[var(--radius-lg)]" />
    </div>
  );
}
