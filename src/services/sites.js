import { id, uniqueSlug } from '../lib/ids.js';
import { notFound, forbidden } from '../lib/http.js';
import { normalizeSpec } from '../ai/spec.js';

export function slugTaken(db, slug) {
  return Boolean(db.prepare('SELECT 1 FROM sites WHERE slug = ?').get(slug));
}

export function getOwnedSite(db, userId, siteId) {
  const site = db.prepare('SELECT * FROM sites WHERE id = ?').get(siteId);
  if (!site) throw notFound('Site not found');
  if (site.user_id !== userId) throw forbidden('That site belongs to someone else');
  return site;
}

/** Replace a site's services/products/hours with a freshly generated set. */
export function applyBusiness(db, siteId, business) {
  const now = Date.now();
  const write = db.transaction(() => {
    db.prepare('DELETE FROM availability WHERE site_id = ?').run(siteId);
    // Services and products are only cleared when the generator supplies
    // replacements, so a re-theme never wipes a live catalogue.
    if (business.services.length) {
      db.prepare('DELETE FROM services WHERE site_id = ?').run(siteId);
      business.services.forEach((s, i) => {
        db.prepare(
          `INSERT INTO services (id, site_id, name, description, duration_min, price_cents, deposit_cents, buffer_min, capacity, active, position, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,1,?,?)`,
        ).run(id('svc'), siteId, s.name, s.description, s.durationMin, s.priceCents, s.depositCents, s.bufferMin, s.capacity, i, now);
      });
    }
    if (business.products.length) {
      db.prepare('DELETE FROM products WHERE site_id = ?').run(siteId);
      business.products.forEach((p, i) => {
        db.prepare(
          `INSERT INTO products (id, site_id, name, description, price_cents, image, inventory, active, position, created_at)
           VALUES (?,?,?,?,?,?,?,1,?,?)`,
        ).run(id('prd'), siteId, p.name, p.description, p.priceCents, p.image || '', p.inventory, i, now);
      });
    }
    for (const weekday of business.availability.weekdays) {
      db.prepare(
        'INSERT INTO availability (id, site_id, service_id, weekday, start_min, end_min) VALUES (?,?,NULL,?,?,?)',
      ).run(id('av'), siteId, weekday, business.availability.startMin, business.availability.endMin);
    }
  });
  write();
}

export function createSite(db, userId, { name, prompt, spec, business }) {
  const now = Date.now();
  const siteId = id('site');
  const slug = uniqueSlug(name || spec.meta.title, (s) => slugTaken(db, s));

  db.prepare(
    `INSERT INTO sites (id, user_id, name, slug, prompt, spec, created_at, updated_at)
     VALUES (?,?,?,?,?,?,?,?)`,
  ).run(siteId, userId, name || spec.meta.title, slug, prompt || '', JSON.stringify(spec), now, now);

  applyBusiness(db, siteId, business);
  return db.prepare('SELECT * FROM sites WHERE id = ?').get(siteId);
}

export function saveSpec(db, siteId, spec) {
  const normalized = normalizeSpec(spec);
  db.prepare('UPDATE sites SET spec = ?, name = ?, updated_at = ? WHERE id = ?').run(
    JSON.stringify(normalized),
    normalized.meta.title,
    Date.now(),
    siteId,
  );
  return normalized;
}

export function publishSite(db, siteId) {
  const site = db.prepare('SELECT spec FROM sites WHERE id = ?').get(siteId);
  if (!site) throw notFound('Site not found');
  const now = Date.now();
  db.prepare('UPDATE sites SET published_spec = ?, published_at = ?, updated_at = ? WHERE id = ?')
    .run(site.spec, now, now, siteId);
  return db.prepare('SELECT * FROM sites WHERE id = ?').get(siteId);
}

export function unpublishSite(db, siteId) {
  db.prepare('UPDATE sites SET published_spec = NULL, published_at = NULL, updated_at = ? WHERE id = ?')
    .run(Date.now(), siteId);
}

export const parseSpec = (json) => normalizeSpec(JSON.parse(json));
