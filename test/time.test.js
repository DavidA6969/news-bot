import test from 'node:test';
import assert from 'node:assert/strict';
import {
  zonedToUtc, zonedParts, dateKey, addDays, weekdayOf, labelToMinutes, minutesToLabel,
} from '../src/lib/time.js';

test('converts local wall clock to UTC across DST', () => {
  // New York is UTC-4 in July (EDT) and UTC-5 in January (EST).
  assert.equal(new Date(zonedToUtc(2026, 7, 1, 540, 'America/New_York')).toISOString(), '2026-07-01T13:00:00.000Z');
  assert.equal(new Date(zonedToUtc(2026, 1, 15, 540, 'America/New_York')).toISOString(), '2026-01-15T14:00:00.000Z');
});

test('handles half-hour and far-east offsets', () => {
  assert.equal(new Date(zonedToUtc(2026, 7, 1, 540, 'Asia/Kolkata')).toISOString(), '2026-07-01T03:30:00.000Z');
  assert.equal(new Date(zonedToUtc(2026, 7, 1, 540, 'Australia/Sydney')).toISOString(), '2026-06-30T23:00:00.000Z');
});

test('returns null for a wall-clock time skipped by spring forward', () => {
  // 2026-03-08 in New York jumps 02:00 -> 03:00, so 02:30 never happens.
  assert.equal(zonedToUtc(2026, 3, 8, 150, 'America/New_York'), null);
  assert.notEqual(zonedToUtc(2026, 3, 8, 90, 'America/New_York'), null);
  assert.notEqual(zonedToUtc(2026, 3, 8, 210, 'America/New_York'), null);
});

test('resolves the ambiguous hour after falling back', () => {
  // 2026-11-01 repeats 01:00-01:59; either instant is valid, both must round-trip.
  const utc = zonedToUtc(2026, 11, 1, 90, 'America/New_York');
  assert.notEqual(utc, null);
  assert.equal(zonedParts(utc, 'America/New_York').minutes, 90);
});

test('dateKey reflects the local calendar day, not UTC', () => {
  assert.equal(dateKey(Date.UTC(2026, 6, 1, 2, 0), 'America/New_York'), '2026-06-30');
  assert.equal(dateKey(Date.UTC(2026, 6, 1, 2, 0), 'Asia/Tokyo'), '2026-07-01');
});

test('addDays crosses months and years', () => {
  assert.equal(addDays('2026-01-31', 1), '2026-02-01');
  assert.equal(addDays('2026-12-31', 1), '2027-01-01');
  assert.equal(addDays('2028-02-28', 1), '2028-02-29');
});

test('weekdayOf is calendar-correct', () => {
  assert.equal(weekdayOf('2026-09-14'), 1); // a Monday
  assert.equal(weekdayOf('2026-09-13'), 0);
});

test('time labels round-trip', () => {
  assert.equal(labelToMinutes('09:30'), 570);
  assert.equal(labelToMinutes('00:00'), 0);
  assert.equal(labelToMinutes('23:59'), 1439);
  assert.equal(labelToMinutes('25:00'), null);
  assert.equal(labelToMinutes('nonsense'), null);
  assert.equal(minutesToLabel(570), '9:30 AM');
  assert.equal(minutesToLabel(0), '12:00 AM');
  assert.equal(minutesToLabel(750), '12:30 PM');
});
