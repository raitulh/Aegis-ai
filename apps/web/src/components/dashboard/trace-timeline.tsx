"use client";
import { Bot, FileSearch, Flag, MessageSquare, ShieldCheck, Sparkles, Wrench } from "lucide-react";
import type { TraceEvent } from "@/lib/types";
import { titleCase } from "@/lib/utils";

const KIND_META: Record<string, { icon: typeof Bot; color: string }> = {
  user_input: { icon: MessageSquare, color: "var(--color-text-muted)" },
  agent: { icon: Bot, color: "var(--color-accent-bright)" },
  llm: { icon: Sparkles, color: "var(--color-accent-bright)" },
  retrieval: { icon: FileSearch, color: "var(--color-low)" },
  tool_call: { icon: Wrench, color: "var(--color-medium)" },
  tool_result: { icon: Wrench, color: "var(--color-text-subtle)" },
  guardrail: { icon: ShieldCheck, color: "var(--color-success)" },
  action: { icon: Flag, color: "var(--color-high)" },
  final_output: { icon: MessageSquare, color: "var(--color-text)" },
};

export function TraceTimeline({ events }: { events: TraceEvent[] }) {
  return (
    <div className="space-y-0">
      {events.map((e, i) => {
        const meta = KIND_META[e.kind] ?? KIND_META.agent;
        const Icon = meta.icon;
        return (
          <div key={e.id} className="flex gap-3">
            <div className="flex flex-col items-center">
              <div className="grid h-7 w-7 shrink-0 place-items-center rounded-full border border-[var(--color-border)] bg-[var(--color-surface-2)]" style={{ color: meta.color }}>
                <Icon className="h-3.5 w-3.5" />
              </div>
              {i < events.length - 1 ? <div className="w-px flex-1 bg-[var(--color-border)]" /> : null}
            </div>
            <div className="pb-4">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">{titleCase(e.name)}</span>
                <span className="text-[10px] uppercase tracking-wide text-[var(--color-text-subtle)]">{titleCase(e.kind)}</span>
                {e.duration_ms ? <span className="text-[10px] text-[var(--color-text-subtle)]">{e.duration_ms}ms</span> : null}
              </div>
              {e.input_preview ? <p className="mt-0.5 line-clamp-2 font-mono text-[11px] text-[var(--color-text-subtle)]">{e.input_preview}</p> : null}
              {e.output_preview ? <p className="mt-0.5 line-clamp-2 font-mono text-[11px] text-[var(--color-text-muted)]">{e.output_preview}</p> : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}
