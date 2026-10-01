"use client";
/**
 * Resilient audit progress stream.
 *
 * - `EventSource` reconnects by itself and sends `Last-Event-ID`; the API resumes exactly after the last seen
 *   event, so reconnects never duplicate or skip (events are also de-duplicated by `seq` here).
 * - If the stream cannot be (re)established — e.g. a proxy that buffers SSE or a permanent error — it falls back
 *   to polling the resumable `/events?after=` endpoint until the audit finishes.
 * - Pauses while the tab is hidden and resumes (from the last seq) when it becomes visible again.
 */
import { useEffect, useRef, useState } from "react";
import { api, API_BASE, path } from "./api";

export type AuditStreamEvent = {
  seq: number;
  type: string;
  stage?: string | null;
  level: "info" | "success" | "warning" | "error";
  message: string;
  progress: number;
  data: Record<string, unknown>;
  created_at?: string | null;
};

export type StreamState = "connecting" | "live" | "reconnecting" | "polling" | "done";

const TERMINAL_TYPES = new Set(["audit.completed", "audit.failed", "audit.cancelled"]);

export function useAuditStream(auditId: string, enabled: boolean, onTerminal?: () => void) {
  const [events, setEvents] = useState<AuditStreamEvent[]>([]);
  const [state, setState] = useState<StreamState>("connecting");
  const lastSeq = useRef(0);
  const terminalRef = useRef(onTerminal);
  useEffect(() => {
    terminalRef.current = onTerminal;
  });

  useEffect(() => {
    if (!auditId || !enabled) return;
    let source: EventSource | null = null;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;
    let failures = 0;
    let finished = false;
    const controller = new AbortController();

    const accept = (incoming: AuditStreamEvent[]) => {
      const fresh = incoming.filter((e) => e.seq > lastSeq.current);
      if (!fresh.length) return;
      lastSeq.current = Math.max(...fresh.map((e) => e.seq));
      setEvents((prev) => [...prev, ...fresh]);
      if (fresh.some((e) => TERMINAL_TYPES.has(e.type))) finish();
    };

    const finish = () => {
      if (finished) return;
      finished = true;
      source?.close();
      if (pollTimer) clearTimeout(pollTimer);
      setState("done");
      terminalRef.current?.();
    };

    const poll = async () => {
      if (finished || controller.signal.aborted) return;
      setState("polling");
      try {
        const batch = await api.get<AuditStreamEvent[]>(path`/audits/${auditId}/events`, { after: lastSeq.current }, controller.signal);
        accept(batch);
        const audit = await api.get<{ status: string }>(path`/audits/${auditId}`, undefined, controller.signal);
        if (["completed", "partially_completed", "failed", "cancelled"].includes(audit.status)) {
          const rest = await api.get<AuditStreamEvent[]>(path`/audits/${auditId}/events`, { after: lastSeq.current }, controller.signal);
          accept(rest);
          finish();
          return;
        }
      } catch {
        /* transient: keep polling */
      }
      pollTimer = setTimeout(poll, 2000);
    };

    const connect = () => {
      if (finished || document.hidden) return;
      setState(lastSeq.current ? "reconnecting" : "connecting");
      source = new EventSource(`${API_BASE}${path`/audits/${auditId}/stream`}?after=${lastSeq.current}`, { withCredentials: true });
      source.onopen = () => {
        failures = 0;
        setState("live");
      };
      source.onmessage = (e) => handle(e);
      source.addEventListener("done", () => finish());
      source.onerror = () => {
        failures += 1;
        // EventSource retries on its own; after repeated failures (or a permanent close) fall back to polling.
        if (source && (source.readyState === EventSource.CLOSED || failures >= 3)) {
          source.close();
          source = null;
          void poll();
        } else {
          setState("reconnecting");
        }
      };
      // Named events (audit.*) are dispatched by type; listen generically via a wildcard-style set.
      for (const type of KNOWN_TYPES) source.addEventListener(type, handle as EventListener);
    };

    const handle = (e: MessageEvent) => {
      try {
        accept([JSON.parse(e.data) as AuditStreamEvent]);
      } catch {
        /* heartbeat comments are not delivered as messages */
      }
    };

    const onVisibility = () => {
      if (document.hidden) {
        source?.close();
        source = null;
      } else if (!finished && !source && !pollTimer) {
        connect();
      }
    };

    connect();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      controller.abort();
      source?.close();
      if (pollTimer) clearTimeout(pollTimer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [auditId, enabled]);

  return { events, state };
}

// Event types emitted by the orchestrator. Unknown future types still arrive via the poll fallback.
const KNOWN_TYPES = [
  "audit.started",
  "audit.setup",
  "audit.tests_generated",
  "audit.probe",
  "audit.stage",
  "audit.inference_progress",
  "audit.eval_progress",
  "audit.finding_signal",
  "audit.evidence",
  "audit.policy_mapping",
  "audit.finding_created",
  "audit.completed",
  "audit.failed",
  "audit.cancelled",
  "audit.recovered",
  "audit.warning",
];
