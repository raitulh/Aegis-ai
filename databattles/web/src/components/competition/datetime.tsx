"use client";

import { useEffect, useState } from "react";

import { cn } from "@/lib/cn";
import { formatDate, formatDateTime, localTimeZone, relativeTime } from "@/lib/format";

/**
 * A UTC timestamp shown in the viewer's local time zone (with zone name), optionally with relative time and —
 * when the event runs in a different zone — the event's local time as well. `stacked` puts the date on the
 * first line and the time (with zone) on a quieter second line, for narrow fact cells.
 */
export function DateTime({
  value,
  eventTimeZone,
  relative,
  stacked,
  className,
}: {
  value: string | null | undefined;
  eventTimeZone?: string | null;
  relative?: boolean;
  stacked?: boolean;
  className?: string;
}) {
  // Time zone and "now" are only known on the client; render the zone-dependent parts after mount.
  const [local, setLocal] = useState<string | null>(null);
  useEffect(() => setLocal(localTimeZone()), []);
  if (!value) return <span className={cn("text-subtle", className)}>—</span>;
  const showEvent = Boolean(local && eventTimeZone && eventTimeZone !== local);
  return (
    <span className={cn("inline-flex flex-col", className)}>
      {stacked ? (
        <time dateTime={value} title={eventTimeZone ? formatDateTime(value, eventTimeZone) : formatDateTime(value)} className="flex flex-col">
          <span className="tabular">{formatDate(value, { year: "numeric", month: "short", day: "numeric" })}</span>
          <span className="tabular text-xs font-normal text-subtle">{formatDate(value, { hour: "numeric", minute: "2-digit", timeZoneName: "short" })}</span>
        </time>
      ) : (
        <time dateTime={value} title={eventTimeZone ? formatDateTime(value, eventTimeZone) : undefined}>
          {formatDateTime(value)}
        </time>
      )}
      {relative && local ? <span className="text-xs text-subtle">{relativeTime(value)}</span> : null}
      {showEvent ? <span className="text-xs text-subtle">Event time: {formatDateTime(value, eventTimeZone ?? undefined)}</span> : null}
    </span>
  );
}
