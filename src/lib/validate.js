import { badRequest } from './http.js';

export function str(value, field, { min = 0, max = 5000, trim = true } = {}) {
  let v = value == null ? '' : String(value);
  if (trim) v = v.trim();
  if (v.length < min) throw badRequest(`${field} is required`);
  if (v.length > max) throw badRequest(`${field} must be under ${max} characters`);
  return v;
}

export function int(value, field, { min = -Infinity, max = Infinity, fallback } = {}) {
  if ((value === '' || value == null) && fallback !== undefined) return fallback;
  const n = Number(value);
  if (!Number.isFinite(n) || !Number.isInteger(n)) throw badRequest(`${field} must be a whole number`);
  if (n < min || n > max) throw badRequest(`${field} must be between ${min} and ${max}`);
  return n;
}

export function money(value, field, { fallback } = {}) {
  if ((value === '' || value == null) && fallback !== undefined) return fallback;
  const n = Math.round(Number(value) * 100) / 100;
  if (!Number.isFinite(n) || n < 0) throw badRequest(`${field} must be a positive amount`);
  if (n > 1_000_000) throw badRequest(`${field} is too large`);
  return Math.round(n * 100);
}

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

export function email(value, field = 'Email') {
  const v = str(value, field, { min: 1, max: 320 }).toLowerCase();
  if (!EMAIL.test(v)) throw badRequest(`${field} doesn't look like a valid address`);
  return v;
}

export function bool(value, fallback = false) {
  if (value === undefined || value === null || value === '') return fallback;
  return value === true || value === 'true' || value === 1 || value === '1' || value === 'on';
}

const HOSTNAME = /^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$/;

export function hostname(value, field = 'Domain') {
  let v = str(value, field, { min: 1, max: 253 }).toLowerCase();
  v = v.replace(/^https?:\/\//, '').replace(/\/.*$/, '').replace(/\.$/, '');
  if (!HOSTNAME.test(v)) throw badRequest(`${field} must look like example.com or book.example.com`);
  return v;
}

/** Reject a timezone the runtime can't resolve, so booking math stays sane. */
export function timezone(value, fallback = 'UTC') {
  const v = String(value || '').trim();
  if (!v) return fallback;
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: v });
    return v;
  } catch {
    return fallback;
  }
}
