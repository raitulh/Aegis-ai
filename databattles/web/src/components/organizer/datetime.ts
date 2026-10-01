/**
 * Time-zone aware conversion between `<input type="datetime-local">` values and UTC ISO strings.
 *
 * Organizers enter wall-clock times in the competition's time zone (not necessarily the browser's);
 * the API always stores UTC. These helpers use Intl only — no date library.
 */

const FALLBACK_ZONES = [
  "UTC", "Europe/London", "Europe/Berlin", "Europe/Paris", "Europe/Istanbul", "Africa/Lagos", "Africa/Nairobi",
  "Asia/Dubai", "Asia/Karachi", "Asia/Kolkata", "Asia/Dhaka", "Asia/Singapore", "Asia/Shanghai", "Asia/Tokyo",
  "Australia/Sydney", "Pacific/Auckland", "America/Sao_Paulo", "America/New_York", "America/Chicago",
  "America/Denver", "America/Los_Angeles",
];

export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function isValidTimeZone(tz: string): boolean {
  if (!tz) return false;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

let zoneCache: string[] | null = null;

/** IANA zones supported by this browser (with UTC first). */
export function timeZoneOptions(): string[] {
  if (zoneCache) return zoneCache;
  let zones: string[] = [];
  try {
    const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] };
    zones = intl.supportedValuesOf ? intl.supportedValuesOf("timeZone") : [];
  } catch {
    zones = [];
  }
  if (!zones.length) zones = FALLBACK_ZONES;
  zoneCache = ["UTC", ...zones.filter((z) => z !== "UTC")];
  return zoneCache;
}

function partsIn(date: Date, timeZone: string) {
  const dtf = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const out: Record<string, number> = {};
  for (const p of dtf.formatToParts(date)) {
    if (p.type !== "literal") out[p.type] = Number(p.value);
  }
  return { year: out.year, month: out.month, day: out.day, hour: out.hour % 24, minute: out.minute, second: out.second };
}

/** Offset (ms) of `timeZone` from UTC at the given instant. */
function offsetAt(utcMs: number, timeZone: string): number {
  const p = partsIn(new Date(utcMs), timeZone);
  const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
  return asUtc - Math.floor(utcMs / 1000) * 1000;
}

const pad = (n: number) => String(n).padStart(2, "0");

/** "2026-10-01T09:00" in `timeZone` → "2026-10-01T07:00:00.000Z". Returns null for empty/invalid input. */
export function zonedInputToUtc(value: string | null | undefined, timeZone: string): string | null {
  if (!value) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value);
  if (!m) return null;
  const tz = isValidTimeZone(timeZone) ? timeZone : "UTC";
  const guess = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4]), Number(m[5]));
  const first = offsetAt(guess, tz);
  let utc = guess - first;
  const second = offsetAt(utc, tz);
  if (second !== first) utc = guess - second;
  const d = new Date(utc);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

/** UTC ISO → "YYYY-MM-DDTHH:mm" wall-clock value in `timeZone` for a datetime-local input. */
export function utcToZonedInput(iso: string | null | undefined, timeZone: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const p = partsIn(d, isValidTimeZone(timeZone) ? timeZone : "UTC");
  return `${p.year}-${pad(p.month)}-${pad(p.day)}T${pad(p.hour)}:${pad(p.minute)}`;
}

/** Short zone label such as "GMT+2" / "PDT" for the given zone at the given instant. */
export function zoneLabel(timeZone: string, at: Date = new Date()): string {
  try {
    const part = new Intl.DateTimeFormat("en-US", { timeZone, timeZoneName: "short" })
      .formatToParts(at)
      .find((p) => p.type === "timeZoneName");
    return part?.value ?? timeZone;
  } catch {
    return timeZone;
  }
}
