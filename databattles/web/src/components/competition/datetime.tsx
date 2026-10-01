"use client";

import { useEffect, useState } from "react";

import { cn } from "@/lib/cn";
import { formatDateTime, localTimeZone, relativeTime } from "@/lib/format";

/**
 * A UTC timestamp shown in the viewer's local time zone (with zone name), optionally with relative time and —
 * when the event runs in a different zone — the event's local time as well.
 */
export function DateTime({
  value,
  eventTimeZone,
  relative,
  className,
}: {
  value: string | null | undefined;
  eventTimeZone?: string | null;
  relative?: boolean;
  className?: string;
}) {
  // Time zone and "now" are only known on the client; render the zone-dependent parts after mount.
  const [local, setLocal] = useState<string | null>(null);
  useEffect(() => setLocal(localTimeZone()), []);
  if (!value) return <span className={cn("text-subtle", className)}>—</span>;
  const showEvent = Boolean(local && eventTimeZone && eventTimeZone !== local);
  return (
    <span className={cn("inline-flex flex-col", className)}>
      <time dateTime={value} title={eventTimeZone ? formatDateTime(value, eventTimeZone) : undefined}>
        {formatDateTime(value)}
      </time>
      {relative && local ? <span className="text-xs text-subtle">{relativeTime(value)}</span> : null}
      {showEvent ? <span className="text-xs text-subtle">Event time: {formatDateTime(value, eventTimeZone ?? undefined)}</span> : null}
    </span>
  );
}
