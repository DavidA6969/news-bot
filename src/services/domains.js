/**
 * Custom domains.
 *
 * Connecting a domain is two DNS records: a TXT record proving the person
 * asking actually controls the name, and a CNAME/A record pointing traffic
 * here. Verification resolves both and reports precisely which one is missing,
 * because "it didn't work" is the least useful thing a builder can say.
 */
import dnsPromises from 'node:dns/promises';
import config from '../config.js';
import { id, token } from '../lib/ids.js';
import { conflict, notFound } from '../lib/http.js';

export const TXT_PREFIX = '_launchkit';

/** Hostname traffic should be pointed at (no port — DNS has no concept of one). */
export const targetHost = () => config.appDomain.replace(/:\d+$/, '');

export function dnsInstructions(domain) {
  const apex = domain.hostname.split('.').length <= 2;
  return {
    verification: {
      type: 'TXT',
      name: `${TXT_PREFIX}.${domain.hostname}`,
      value: domain.token,
    },
    routing: apex
      ? { type: 'A', name: domain.hostname, value: targetHost(), note: 'Some registrars call this an ALIAS or ANAME record for apex domains.' }
      : { type: 'CNAME', name: domain.hostname, value: targetHost(), note: '' },
  };
}

export function addDomain(db, siteId, hostname) {
  const existing = db.prepare('SELECT site_id FROM domains WHERE hostname = ?').get(hostname);
  if (existing) {
    throw conflict(
      existing.site_id === siteId
        ? 'That domain is already connected to this site'
        : 'That domain is already connected to another site',
    );
  }
  const domainId = id('dom');
  db.prepare(
    `INSERT INTO domains (id, site_id, hostname, token, status, created_at) VALUES (?,?,?,?,'pending',?)`,
  ).run(domainId, siteId, hostname, `launchkit-verify=${token(12)}`, Date.now());
  return db.prepare('SELECT * FROM domains WHERE id = ?').get(domainId);
}

async function resolveTxt(name) {
  try {
    const records = await dnsPromises.resolveTxt(name);
    return records.map((chunks) => chunks.join(''));
  } catch {
    return [];
  }
}

async function resolvesHere(hostname) {
  const target = targetHost();
  // Local development never resolves publicly; don't block the flow on it.
  if (target === 'localhost' || target.startsWith('127.')) return true;
  try {
    const cnames = await dnsPromises.resolveCname(hostname).catch(() => []);
    if (cnames.some((c) => c.replace(/\.$/, '').toLowerCase() === target.toLowerCase())) return true;
  } catch { /* fall through to A records */ }
  try {
    const [ours, theirs] = await Promise.all([
      dnsPromises.resolve4(target).catch(() => []),
      dnsPromises.resolve4(hostname).catch(() => []),
    ]);
    return ours.length > 0 && theirs.some((ip) => ours.includes(ip));
  } catch {
    return false;
  }
}

/** Re-check DNS and move the domain to `live` when both records are correct. */
export async function verifyDomain(db, domainId) {
  const domain = db.prepare('SELECT * FROM domains WHERE id = ?').get(domainId);
  if (!domain) throw notFound('Domain not found');

  const txtRecords = await resolveTxt(`${TXT_PREFIX}.${domain.hostname}`);
  const ownership = txtRecords.some((r) => r.trim() === domain.token);
  const routed = ownership ? await resolvesHere(domain.hostname) : false;

  let status = 'pending';
  let error = '';
  if (!ownership) {
    error = txtRecords.length
      ? `Found a TXT record at ${TXT_PREFIX}.${domain.hostname}, but the value doesn't match. DNS changes can take a few minutes to propagate.`
      : `No TXT record found at ${TXT_PREFIX}.${domain.hostname} yet.`;
  } else if (!routed) {
    error = `Ownership verified. Now point ${domain.hostname} at ${targetHost()} so traffic reaches your site.`;
  } else {
    status = 'live';
  }

  db.prepare(
    `UPDATE domains SET status = ?, last_error = ?, checked_at = ?, verified_at = COALESCE(verified_at, ?) WHERE id = ?`,
  ).run(status, error, Date.now(), status === 'live' ? Date.now() : null, domainId);

  return {
    ...db.prepare('SELECT * FROM domains WHERE id = ?').get(domainId),
    checks: { ownership, routed },
  };
}

/** Resolve an incoming Host header to a published site, if one claims it. */
export function siteForHost(db, host) {
  if (!host) return null;
  const hostname = String(host).toLowerCase().split(':')[0];
  return db
    .prepare(
      `SELECT s.* FROM domains d JOIN sites s ON s.id = d.site_id
        WHERE d.hostname = ? AND d.status = 'live' AND s.published_spec IS NOT NULL`,
    )
    .get(hostname) || null;
}
