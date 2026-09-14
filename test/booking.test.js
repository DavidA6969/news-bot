import test from 'node:test';
import assert from 'node:assert/strict';
import { createMemoryDb } from '../src/db/index.js';
import { id } from '../src/lib/ids.js';
import {
  slotsForDay, checkSlot, createAppointment, availabilityCalendar,
  expireStaleHolds, confirmAppointment, HOLD_TTL_MS, MIN_LEAD_MIN,
} from '../src/services/booking.js';
import { zonedToUtc } from '../src/lib/time.js';

const TZ = 'America/New_York';
const SITE = 'site_test';

function setup({ duration = 60, buffer = 0, capacity = 1, deposit = 0, weekdays = [0, 1, 2, 3, 4, 5, 6], start = 540, end = 1020 } = {}) {
  const db = createMemoryDb();
  const now = Date.now();
  db.prepare('INSERT INTO users (id,email,password_hash,name,created_at) VALUES (?,?,?,?,?)')
    .run('u1', 'a@b.co', 'x', '', now);
  db.prepare('INSERT INTO sites (id,user_id,name,slug,prompt,spec,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)')
    .run(SITE, 'u1', 'T', 't', '', '{}', now, now);

  const serviceId = id('svc');
  db.prepare(`INSERT INTO services (id,site_id,name,description,duration_min,price_cents,deposit_cents,buffer_min,capacity,active,position,created_at)
              VALUES (?,?,?,?,?,?,?,?,?,1,0,?)`)
    .run(serviceId, SITE, 'Test', '', duration, 10000, deposit, buffer, capacity, now);
  for (const w of weekdays) {
    db.prepare('INSERT INTO availability (id,site_id,service_id,weekday,start_min,end_min) VALUES (?,?,NULL,?,?,?)')
      .run(id('av'), SITE, w, start, end);
  }
  return { db, service: db.prepare('SELECT * FROM services WHERE id = ?').get(serviceId) };
}

// A fixed reference point well clear of any DST boundary.
const DAY = '2026-06-15';
const at = (minutes) => zonedToUtc(2026, 6, 15, minutes, TZ);
const NOW = at(0);

const book = (db, service, startsAt, over = {}) =>
  createAppointment(db, {
    siteId: SITE, service, startsAt, timezone: TZ, now: NOW,
    customer: { name: 'A', email: 'a@b.co' }, requiresDeposit: false, ...over,
  });

test('generates slots across the open window only', () => {
  const { db, service } = setup({ duration: 60, start: 540, end: 720 }); // 09:00-12:00
  const slots = slotsForDay(db, { siteId: SITE, service, day: DAY, timezone: TZ, now: NOW });
  assert.deepEqual(slots.map((s) => s.time), ['09:00', '09:30', '10:00', '10:30', '11:00']);
});

test('a service that cannot finish before closing is not offered', () => {
  const { db, service } = setup({ duration: 120, start: 540, end: 660 }); // 2h service, 2h window
  const slots = slotsForDay(db, { siteId: SITE, service, day: DAY, timezone: TZ, now: NOW });
  assert.deepEqual(slots.map((s) => s.time), ['09:00']);
});

test('closed days produce no slots', () => {
  const { db, service } = setup({ weekdays: [0] }); // Sundays only
  assert.equal(weekdayName(DAY), 'Monday');
  assert.equal(slotsForDay(db, { siteId: SITE, service, day: DAY, timezone: TZ, now: NOW }).length, 0);
});

test('booking removes that slot and everything it overlaps', () => {
  const { db, service } = setup({ duration: 60, start: 540, end: 720 });
  book(db, service, at(600)); // 10:00-11:00
  const times = slotsForDay(db, { siteId: SITE, service, day: DAY, timezone: TZ, now: NOW }).map((s) => s.time);
  assert.ok(!times.includes('10:00'));
  assert.ok(!times.includes('09:30'), '09:30 would run into the 10:00 booking');
  assert.ok(!times.includes('10:30'), '10:30 starts inside the 10:00 booking');
  assert.ok(times.includes('09:00'));
  assert.ok(times.includes('11:00'));
});

test('turnaround buffer blocks the following slot', () => {
  const { db, service } = setup({ duration: 60, buffer: 30, start: 540, end: 780 });
  book(db, service, at(540)); // 09:00-10:00 plus 30m cleanup
  const times = slotsForDay(db, { siteId: SITE, service, day: DAY, timezone: TZ, now: NOW }).map((s) => s.time);
  assert.ok(!times.includes('10:00'), 'still in cleanup');
  assert.ok(times.includes('10:30'));
});

test('double booking the same slot is refused', () => {
  const { db, service } = setup();
  book(db, service, at(600));
  assert.throws(() => book(db, service, at(600)), (err) => err.status === 409);
});

test('group services sell seats until the session is full', () => {
  const { db, service } = setup({ capacity: 3, duration: 60, start: 540, end: 660 });
  book(db, service, at(540));
  book(db, service, at(540));
  assert.equal(checkSlot(db, { siteId: SITE, service, startsAt: at(540), now: NOW }).remaining, 1);
  book(db, service, at(540));
  const check = checkSlot(db, { siteId: SITE, service, startsAt: at(540), now: NOW });
  assert.equal(check.ok, false);
  assert.equal(check.reason, 'full');
  assert.throws(() => book(db, service, at(540)), (err) => err.status === 409);
});

test('a class does not block a one-to-one at the same time', () => {
  const { db, service: klass } = setup({ capacity: 10, duration: 60, start: 540, end: 660 });
  const soloId = id('svc');
  db.prepare(`INSERT INTO services (id,site_id,name,description,duration_min,price_cents,deposit_cents,buffer_min,capacity,active,position,created_at)
              VALUES (?,?,'Solo','',60,0,0,0,1,1,1,?)`).run(soloId, SITE, Date.now());
  const solo = db.prepare('SELECT * FROM services WHERE id = ?').get(soloId);

  book(db, klass, at(540));
  assert.equal(checkSlot(db, { siteId: SITE, service: solo, startsAt: at(540), now: NOW }).ok, true);
});

test('slots inside the lead time are withheld', () => {
  const { db, service } = setup({ duration: 30, start: 540, end: 720 });
  const now = at(540); // 09:00 sharp
  const times = slotsForDay(db, { siteId: SITE, service, day: DAY, timezone: TZ, now }).map((s) => s.time);
  assert.ok(!times.includes('09:00'));
  assert.ok(!times.includes('09:30'), `inside the ${MIN_LEAD_MIN}-minute lead time`);
  assert.ok(times.includes('10:00'));
});

test('the hour skipped by spring forward is never offered', () => {
  const { db, service } = setup({ duration: 30, start: 0, end: 300 }); // midnight-05:00
  const day = '2026-03-08'; // New York jumps 02:00 -> 03:00
  const before = Date.UTC(2026, 2, 1);
  const times = slotsForDay(db, { siteId: SITE, service, day, timezone: TZ, now: before }).map((s) => s.time);
  assert.ok(times.includes('01:30'));
  assert.ok(!times.some((t) => t.startsWith('02:')), `02:xx does not exist that morning: ${times.join(',')}`);
  assert.ok(times.includes('03:00'));
});

test('blackouts close their window', () => {
  const { db, service } = setup({ duration: 60, start: 540, end: 780 });
  db.prepare('INSERT INTO blackouts (id,site_id,starts_at,ends_at,reason,created_at) VALUES (?,?,?,?,?,?)')
    .run(id('bo'), SITE, at(600), at(720), 'Staff meeting', Date.now());
  const times = slotsForDay(db, { siteId: SITE, service, day: DAY, timezone: TZ, now: NOW }).map((s) => s.time);
  assert.ok(!times.includes('10:00'));
  assert.ok(!times.includes('11:00'));
  assert.ok(times.includes('12:00'));
});

test('an unpaid hold blocks the slot, then releases it when it expires', () => {
  const { db, service } = setup({ duration: 60, deposit: 5000, start: 540, end: 720 });
  const appointment = createAppointment(db, {
    siteId: SITE, service, startsAt: at(600), timezone: TZ, now: NOW,
    customer: { name: 'A', email: 'a@b.co' }, requiresDeposit: true,
  });
  assert.equal(appointment.status, 'hold');
  assert.equal(checkSlot(db, { siteId: SITE, service, startsAt: at(600), now: NOW }).ok, false);

  expireStaleHolds(db, SITE, NOW + HOLD_TTL_MS + 1000);
  assert.equal(checkSlot(db, { siteId: SITE, service, startsAt: at(600), now: NOW }).ok, true);
});

test('a paid hold is confirmed and keeps its slot', () => {
  const { db, service } = setup({ duration: 60, deposit: 5000, start: 540, end: 720 });
  const appointment = createAppointment(db, {
    siteId: SITE, service, startsAt: at(600), timezone: TZ, now: NOW,
    customer: { name: 'A', email: 'a@b.co' }, requiresDeposit: true,
  });
  confirmAppointment(db, appointment.id, 'ord_1');

  expireStaleHolds(db, SITE, NOW + HOLD_TTL_MS + 1000);
  const row = db.prepare('SELECT status FROM appointments WHERE id = ?').get(appointment.id);
  assert.equal(row.status, 'confirmed');
  assert.equal(checkSlot(db, { siteId: SITE, service, startsAt: at(600), now: NOW }).ok, false);
});

test('a cancelled booking frees its slot again', () => {
  const { db, service } = setup({ duration: 60, start: 540, end: 720 });
  const appointment = book(db, service, at(600));
  db.prepare("UPDATE appointments SET status='cancelled' WHERE id=?").run(appointment.id);
  assert.equal(checkSlot(db, { siteId: SITE, service, startsAt: at(600), now: NOW }).ok, true);
});

test('the calendar spans the requested number of days', () => {
  const { db, service } = setup();
  const days = availabilityCalendar(db, { siteId: SITE, service, timezone: TZ, days: 10, from: DAY, now: NOW });
  assert.equal(days.length, 10);
  assert.equal(days[0].date, DAY);
  assert.ok(days.every((d) => d.slots.length > 0));
});

function weekdayName(key) {
  const [y, m, d] = key.split('-').map(Number);
  return ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][
    new Date(Date.UTC(y, m - 1, d)).getUTCDay()
  ];
}
