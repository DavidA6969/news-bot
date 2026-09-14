/**
 * Normalizes the operational half of a generated site: the services people can
 * book, the products they can buy, and the week the business actually trades.
 */
import { labelToMinutes } from '../lib/time.js';

const clampInt = (v, min, max, fallback) => {
  const n = Math.round(Number(v));
  return Number.isFinite(n) ? Math.max(min, Math.min(max, n)) : fallback;
};

const asText = (v, max) => (typeof v === 'string' ? v.trim().slice(0, max) : '');

/** Major units (dollars) -> minor units (cents), defensively. */
const toCents = (v, fallback = 0) => {
  const n = Number(v);
  if (!Number.isFinite(n) || n < 0) return fallback;
  return Math.min(Math.round(n * 100), 100_000_000);
};

export const DEFAULT_AVAILABILITY = { weekdays: [1, 2, 3, 4, 5], startMin: 9 * 60, endMin: 17 * 60 };

export function normalizeAvailability(raw) {
  // An out-of-range weekday is meaningless, so it is dropped rather than
  // clamped — clamping would invent an opening day nobody asked for.
  const weekdays = Array.isArray(raw?.weekdays)
    ? [...new Set(raw.weekdays
        .map((d) => Math.round(Number(d)))
        .filter((d) => Number.isInteger(d) && d >= 0 && d <= 6))].sort((a, b) => a - b)
    : [];
  const startMin = labelToMinutes(raw?.startTime);
  const endMin = labelToMinutes(raw?.endTime);

  const result = {
    weekdays: weekdays.length ? weekdays : DEFAULT_AVAILABILITY.weekdays,
    startMin: startMin ?? DEFAULT_AVAILABILITY.startMin,
    endMin: endMin ?? DEFAULT_AVAILABILITY.endMin,
  };
  // A window that closes before it opens would yield zero slots forever.
  if (result.endMin <= result.startMin) {
    result.startMin = DEFAULT_AVAILABILITY.startMin;
    result.endMin = DEFAULT_AVAILABILITY.endMin;
  }
  return result;
}

export function normalizeServices(raw) {
  if (!Array.isArray(raw)) return [];
  return raw.slice(0, 20).flatMap((s) => {
    const name = asText(s?.name, 90);
    if (!name) return [];
    const priceCents = toCents(s?.price);
    // A deposit larger than the price is a model slip, not an intent.
    const depositCents = Math.min(toCents(s?.deposit), priceCents || Infinity);
    return [{
      name,
      description: asText(s?.description, 400),
      durationMin: clampInt(s?.durationMin, 5, 8 * 60, 60),
      priceCents,
      depositCents: Number.isFinite(depositCents) ? depositCents : 0,
      bufferMin: clampInt(s?.bufferMin, 0, 240, 0),
      capacity: clampInt(s?.capacity, 1, 200, 1),
    }];
  });
}

export function normalizeProducts(raw) {
  if (!Array.isArray(raw)) return [];
  return raw.slice(0, 40).flatMap((p) => {
    const name = asText(p?.name, 90);
    if (!name) return [];
    const inventory = p?.inventory === undefined || p?.inventory === null
      ? null
      : clampInt(p.inventory, 0, 1_000_000, null);
    return [{
      name,
      description: asText(p?.description, 400),
      priceCents: toCents(p?.price),
      inventory,
      image: asText(p?.image, 500),
    }];
  });
}

export function normalizeBusiness(input = {}) {
  return {
    services: normalizeServices(input.services),
    products: normalizeProducts(input.products),
    availability: normalizeAvailability(input.availability),
  };
}
