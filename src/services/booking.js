/**
 * The booking engine.
 *
 * Two kinds of service exist, and they contend for time differently:
 *
 *   • Exclusive (capacity === 1) — a one-to-one appointment. It blocks the
 *     calendar against every other exclusive appointment that overlaps it,
 *     because one provider cannot be in two places.
 *   • Group (capacity > 1) — a class or shared session. Bookings of the same
 *     service starting at the same moment share the capacity; they neither
 *     block nor are blocked by exclusive appointments, since a class runs in
 *     its own room.
 *
 * Times are stored as UTC epoch milliseconds. Opening hours are written in the
 * site's local wall clock, so every comparison goes through src/lib/time.js.
 */
import { id } from '../lib/ids.js';
import { conflict, badRequest, notFound } from '../lib/http.js';
import { dateKey, addDays, weekdayOf, zonedToUtc, zonedParts, formatInZone } from '../lib/time.js';

/** How soon from now a slot may be booked. */
export const MIN_LEAD_MIN = 60;
/** How far ahead the calendar opens. */
export const BOOKING_HORIZON_DAYS = 60;
/** Unpaid deposit holds expire after this long, releasing the slot. */
export const HOLD_TTL_MS = 20 * 60 * 1000;

const stepFor = (durationMin) => (durationMin <= 30 ? 15 : 30);

export function getService(db, siteId, serviceId) {
  const row = db
    .prepare('SELECT * FROM services WHERE id = ? AND site_id = ? AND active = 1')
    .get(serviceId, siteId);
  if (!row) throw notFound('That service is not available');
  return row;
}

/** Release holds whose payment window has passed, so their slots free up. */
export function expireStaleHolds(db, siteId, now = Date.now()) {
  db.prepare(
    `UPDATE appointments SET status = 'cancelled'
      WHERE site_id = ? AND status = 'hold' AND hold_expires_at IS NOT NULL AND hold_expires_at < ?`,
  ).run(siteId, now);
}

function openWindows(db, siteId, serviceId, weekday) {
  // A rule tied to a service wins over the site-wide default for that day.
  const specific = db
    .prepare('SELECT start_min, end_min FROM availability WHERE site_id = ? AND service_id = ? AND weekday = ? ORDER BY start_min')
    .all(siteId, serviceId, weekday);
  if (specific.length) return specific;
  return db
    .prepare('SELECT start_min, end_min FROM availability WHERE site_id = ? AND service_id IS NULL AND weekday = ? ORDER BY start_min')
    .all(siteId, weekday);
}

const overlaps = (aStart, aEnd, bStart, bEnd) => aStart < bEnd && bStart < aEnd;

function busyIntervals(db, siteId, fromMs, toMs) {
  return db
    .prepare(
      `SELECT id, service_id, starts_at, ends_at, status
         FROM appointments
        WHERE site_id = ? AND status IN ('confirmed','hold')
          AND ends_at > ? AND starts_at < ?`,
    )
    .all(siteId, fromMs, toMs);
}

function blackoutIntervals(db, siteId, fromMs, toMs) {
  return db
    .prepare('SELECT starts_at, ends_at FROM blackouts WHERE site_id = ? AND ends_at > ? AND starts_at < ?')
    .all(siteId, fromMs, toMs);
}

/**
 * Is `[startsAt, blockEnd)` bookable for this service?
 * Returns `{ ok, reason, remaining }` — `remaining` counts free seats for
 * group services.
 */
export function checkSlot(db, { siteId, service, startsAt, now = Date.now() }) {
  const durationMs = service.duration_min * 60_000;
  const serviceEnd = startsAt + durationMs;
  const blockEnd = serviceEnd + service.buffer_min * 60_000;

  if (startsAt < now + MIN_LEAD_MIN * 60_000) {
    return { ok: false, reason: 'too_soon', remaining: 0 };
  }

  for (const b of blackoutIntervals(db, siteId, startsAt, blockEnd)) {
    if (overlaps(startsAt, blockEnd, b.starts_at, b.ends_at)) {
      return { ok: false, reason: 'closed', remaining: 0 };
    }
  }

  const busy = busyIntervals(db, siteId, startsAt, blockEnd);

  if (service.capacity > 1) {
    // Group session: only identical sittings of this same class compete.
    const taken = busy.filter((a) => a.service_id === service.id && a.starts_at === startsAt).length;
    const remaining = service.capacity - taken;
    return remaining > 0
      ? { ok: true, reason: null, remaining }
      : { ok: false, reason: 'full', remaining: 0 };
  }

  for (const a of busy) {
    if (a.service_id === service.id && a.starts_at === startsAt) {
      return { ok: false, reason: 'taken', remaining: 0 };
    }
    const other = db.prepare('SELECT capacity FROM services WHERE id = ?').get(a.service_id);
    if (other && other.capacity > 1) continue; // a class in its own room
    if (overlaps(startsAt, blockEnd, a.starts_at, a.ends_at)) {
      return { ok: false, reason: 'taken', remaining: 0 };
    }
  }
  return { ok: true, reason: null, remaining: 1 };
}

/** Every bookable start time on one local calendar day. */
export function slotsForDay(db, { siteId, service, day, timezone, now = Date.now() }) {
  const weekday = weekdayOf(day);
  if (weekday === null) throw badRequest('Invalid date');

  const windows = openWindows(db, siteId, service.id, weekday);
  if (!windows.length) return [];

  const step = stepFor(service.duration_min);
  const slots = [];

  for (const w of windows) {
    for (let minute = w.start_min; minute + service.duration_min <= w.end_min; minute += step) {
      const parsed = day.split('-').map(Number);
      const startsAt = zonedToUtc(parsed[0], parsed[1], parsed[2], minute, timezone);
      if (startsAt === null) continue; // wall-clock time skipped by DST

      const check = checkSlot(db, { siteId, service, startsAt, now });
      if (!check.ok) continue;

      const local = zonedParts(startsAt, timezone);
      slots.push({
        startsAt,
        time: `${String(local.hour).padStart(2, '0')}:${String(local.minute).padStart(2, '0')}`,
        label: new Intl.DateTimeFormat('en-US', {
          timeZone: timezone, hour: 'numeric', minute: '2-digit',
        }).format(new Date(startsAt)),
        remaining: check.remaining,
      });
    }
  }
  return slots;
}

/** Day-by-day availability across a range, for the public calendar. */
export function availabilityCalendar(db, { siteId, service, timezone, days = 14, from, now = Date.now() }) {
  expireStaleHolds(db, siteId, now);
  const start = from || dateKey(now, timezone);
  const out = [];
  for (let i = 0; i < Math.min(days, BOOKING_HORIZON_DAYS); i++) {
    const day = addDays(start, i);
    if (!day) break;
    const slots = slotsForDay(db, { siteId, service, day, timezone, now });
    out.push({ date: day, weekday: weekdayOf(day), slots });
  }
  return out;
}

/**
 * Reserve a slot. Runs the availability check and the insert inside one
 * transaction so two people clicking the same time cannot both win.
 */
export function createAppointment(db, { siteId, service, startsAt, customer, timezone, requiresDeposit, now = Date.now() }) {
  const durationMs = service.duration_min * 60_000;
  const serviceEnd = startsAt + durationMs;
  const blockEnd = serviceEnd + service.buffer_min * 60_000;

  const reserve = db.transaction(() => {
    expireStaleHolds(db, siteId, now);
    const check = checkSlot(db, { siteId, service, startsAt, now });
    if (!check.ok) {
      const messages = {
        too_soon: 'That time is too close to now — please pick a later slot',
        closed: 'We are closed at that time',
        full: 'That session just filled up',
        taken: 'That slot was just booked by someone else',
      };
      throw conflict(messages[check.reason] || 'That slot is no longer available', { reason: check.reason });
    }

    const appointmentId = id('apt');
    db.prepare(
      `INSERT INTO appointments
         (id, site_id, service_id, name, email, phone, notes, starts_at, ends_at, service_end_at,
          status, hold_expires_at, order_id, created_at)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
    ).run(
      appointmentId,
      siteId,
      service.id,
      customer.name,
      customer.email,
      customer.phone || '',
      customer.notes || '',
      startsAt,
      blockEnd,
      serviceEnd,
      requiresDeposit ? 'hold' : 'confirmed',
      requiresDeposit ? now + HOLD_TTL_MS : null,
      null,
      now,
    );
    return appointmentId;
  });

  const appointmentId = reserve();
  return {
    id: appointmentId,
    startsAt,
    endsAt: serviceEnd,
    status: requiresDeposit ? 'hold' : 'confirmed',
    label: formatInZone(startsAt, timezone, { withYear: true }),
  };
}

export function confirmAppointment(db, appointmentId, orderId) {
  db.prepare(
    `UPDATE appointments SET status = 'confirmed', hold_expires_at = NULL, order_id = ?
      WHERE id = ? AND status = 'hold'`,
  ).run(orderId, appointmentId);
}

export function cancelAppointment(db, siteId, appointmentId) {
  const res = db
    .prepare(`UPDATE appointments SET status = 'cancelled' WHERE id = ? AND site_id = ?`)
    .run(appointmentId, siteId);
  if (!res.changes) throw notFound('Appointment not found');
}
