"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { Sheet, SheetContent } from "@/components/ui/overlays";
import { Button } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { useInvalidate } from "@/lib/queries";
import type { Policy } from "@/lib/types";

export function NewPolicyDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const router = useRouter();
  const invalidate = useInvalidate();
  const [loading, setLoading] = useState(false);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setLoading(true);
    const f = new FormData(e.currentTarget);
    try {
      const policy = await api.post<Policy>("/policies", { name: f.get("name"), key: (f.get("key") as string).toUpperCase(), source_text: f.get("source_text"), category: "custom" });
      invalidate("policies");
      onOpenChange(false);
      router.push(`/dashboard/policies/${policy.id}`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to create policy");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent title="New Policy" description="Write your policy text — Aegis will compile it into executable controls, each traced to its source.">
        <form onSubmit={submit} className="space-y-4 p-6">
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-[var(--color-text-muted)]">Name</span>
            <input name="name" required placeholder="Hiring AI Policy" className="w-full rounded-[var(--radius)] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 py-2 text-sm outline-none focus:border-[var(--color-accent)]" />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-[var(--color-text-muted)]">Key (short code)</span>
            <input name="key" required pattern="[A-Za-z][A-Za-z0-9]*" placeholder="HR" className="w-full rounded-[var(--radius)] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 py-2 font-mono text-sm outline-none focus:border-[var(--color-accent)]" />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-[var(--color-text-muted)]">Policy text</span>
            <textarea name="source_text" required rows={10} placeholder={"Section 3.1 Protected attributes such as gender and age must not materially alter candidate scores.\nSection 3.2 Final hiring decisions require human review before they are sent."} className="w-full rounded-[var(--radius)] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 py-2 text-sm outline-none focus:border-[var(--color-accent)]" />
          </label>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" loading={loading}>Create & compile</Button>
          </div>
        </form>
      </SheetContent>
    </Sheet>
  );
}
