/**
 * Timezone-aware date math with no dependencies.
 *
 * Everything is stored as UTC epoch milliseconds. Business hours are written
 * in the site's local wall-clock time, so every conversion has to respect the
 * zone's DST rules — `Intl.DateTimeFormat` is the source of truth for those.
 */

const partsCache = new Map();

function formatter(timeZone) {
  let f = partsCache.get(timeZone);
  if (!f) {
    f = new Intl.DateTimeFormat('en-US', {
      timeZone,
      hourCycle: 'h23',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
    partsCache.set(timeZone, f);
  }
  return f;
}

/** Wall-clock fields for an instant, as seen in `timeZone`. */
export function zonedParts(utcMs, timeZone) {
  const parts = formatter(timeZone).formatToParts(new Date(utcMs));
  const out = {};
  for (const p of parts) if (p.type !== 'literal') out[p.type] = Number(p.value);
  const minutes = out.hour * 60 + out.minute;
  const weekday = new Date(
    Date.UTC(out.year, out.month - 1, out.day),
  ).getUTCDay();
  return {
    year: out.year,
    month: out.month,
    day: out.day,
    hour: out.hour,
    minute: out.minute,
    minutes,
    weekday,
  };
}

/** Offset of `timeZone` at a given instant, in milliseconds (local - UTC). */
export function zoneOffset(utcMs, timeZone) {
  const p = zonedParts(utcMs, timeZone);
  const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, 0);
  // Round to the second to absorb the dropped milliseconds.
  return asUtc - Math.floor(utcMs / 1000) * 1000;
}

/**
 * Convert a local wall-clock time into a UTC instant.
 *
 * Returns `null` when that wall-clock time does not exist in the zone — the
 * hour skipped by a spring-forward transition. Callers drop those slots rather
 * than silently booking an hour that never happens.
 */
export function zonedToUtc(year, month, day, minutes, timeZone) {
  const naive = Date.UTC(year, month - 1, day, 0, 0, 0) + minutes * 60_000;
  // Two passes converge for every real-world zone: the first guess lands close
  // enough that the second offset lookup is the correct one.
  let utc = naive - zoneOffset(naive, timeZone);
  utc = naive - zoneOffset(utc, timeZone);

  const back = zonedParts(utc, timeZone);
  const wanted = normalizeWallClock(year, month, day, minutes);
  if (
    back.year !== wanted.year ||
    back.month !== wanted.month ||
    back.day !== wanted.day ||
    back.minutes !== wanted.minutes
  ) {
    return null;
  }
  return utc;
}

function normalizeWallClock(year, month, day, minutes) {
  const base = new Date(Date.UTC(year, month - 1, day, 0, 0, 0) + minutes * 60_000);
  return {
    year: base.getUTCFullYear(),
    month: base.getUTCMonth() + 1,
    day: base.getUTCDate(),
    minutes: base.getUTCHours() * 60 + base.getUTCMinutes(),
  };
}

/** `YYYY-MM-DD` for an instant, in the given zone. */
export function dateKey(utcMs, timeZone) {
  const p = zonedParts(utcMs, timeZone);
  return `${p.year}-${pad(p.month)}-${pad(p.day)}`;
}

export function parseDateKey(key) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(key || '').trim());
  if (!m) return null;
  const [year, month, day] = [Number(m[1]), Number(m[2]), Number(m[3])];
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  // Reject calendar-invalid dates such as 2026-02-30.
  const probe = new Date(Date.UTC(year, month - 1, day));
  if (probe.getUTCMonth() + 1 !== month || probe.getUTCDate() !== day) return null;
  return { year, month, day };
}

/** Advance a `YYYY-MM-DD` key by whole days, staying in calendar space. */
export function addDays(key, days) {
  const d = parseDateKey(key);
  if (!d) return null;
  const next = new Date(Date.UTC(d.year, d.month - 1, d.day) + days * 86_400_000);
  return `${next.getUTCFullYear()}-${pad(next.getUTCMonth() + 1)}-${pad(next.getUTCDate())}`;
}

export function weekdayOf(key) {
  const d = parseDateKey(key);
  if (!d) return null;
  return new Date(Date.UTC(d.year, d.month - 1, d.day)).getUTCDay();
}

const pad = (n) => String(n).padStart(2, '0');

export function minutesToLabel(minutes) {
  const h = Math.floor(minutes / 60) % 24;
  const m = minutes % 60;
  const suffix = h < 12 ? 'AM' : 'PM';
  const hour12 = h % 12 === 0 ? 12 : h % 12;
  return `${hour12}:${pad(m)} ${suffix}`;
}

export function labelToMinutes(label) {
  const m = /^\s*(\d{1,2}):(\d{2})\s*$/.exec(String(label || ''));
  if (!m) return null;
  const h = Number(m[1]);
  const min = Number(m[2]);
  if (h > 23 || min > 59) return null;
  return h * 60 + min;
}

/** Human-readable instant in the site's zone, e.g. "Thu, Mar 5 at 2:30 PM". */
export function formatInZone(utcMs, timeZone, { withYear = false } = {}) {
  return new Intl.DateTimeFormat('en-US', {
    timeZone,
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    ...(withYear ? { year: 'numeric' } : {}),
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(utcMs));
}

export const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
