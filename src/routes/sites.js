import express from 'express';
import config from '../config.js';
import { id } from '../lib/ids.js';
import { route, badRequest, notFound } from '../lib/http.js';
import { str, int, money, bool, hostname as parseHostname, timezone as parseTz } from '../lib/validate.js';
import { requireUser } from '../lib/auth.js';
import { generateSite } from '../ai/generator.js';
import { THEME_PRESETS } from '../ai/spec.js';
import {
  createSite, getOwnedSite, saveSpec, publishSite, unpublishSite, applyBusiness, parseSpec,
} from '../services/sites.js';
import { addDomain, verifyDomain, dnsInstructions, targetHost } from '../services/domains.js';
import { cancelAppointment, expireStaleHolds, BOOKING_HORIZON_DAYS } from '../services/booking.js';
import { paymentMode } from '../services/payments.js';
import { labelToMinutes, minutesToLabel, formatInZone } from '../lib/time.js';

export default function siteRoutes(db) {
  const router = express.Router();
  router.use(requireUser);

  const owned = (req) => getOwnedSite(db, req.user.id, req.params.siteId);

  const publicUrl = (site) => `${config.appOrigin}/s/${site.slug}`;

  const siteSummary = (site) => {
    const spec = parseSpec(site.spec);
    const domain = db
      .prepare("SELECT hostname FROM domains WHERE site_id = ? AND status = 'live' ORDER BY verified_at LIMIT 1")
      .get(site.id);
    return {
      id: site.id,
      name: site.name,
      slug: site.slug,
      title: spec.meta.title,
      favicon: spec.meta.favicon,
      theme: spec.theme,
      published: Boolean(site.published_spec),
      publishedAt: site.published_at,
      updatedAt: site.updated_at,
      url: domain ? `https://${domain.hostname}` : publicUrl(site),
      previewUrl: `/preview/${site.id}`,
    };
  };

  // ── Sites ────────────────────────────────────────────────────────────────
  router.get('/sites', route((req, res) => {
    const sites = db
      .prepare('SELECT * FROM sites WHERE user_id = ? ORDER BY updated_at DESC')
      .all(req.user.id);
    res.json({ sites: sites.map(siteSummary) });
  }));

  router.post('/sites/generate', route(async (req, res) => {
    const prompt = str(req.body.prompt, 'Description', { min: 8, max: 4000 });
    const businessName = str(req.body.businessName ?? '', 'Business name', { max: 120 });

    const result = await generateSite({ prompt, businessName });
    const site = createSite(db, req.user.id, {
      name: businessName || result.spec.meta.title,
      prompt,
      spec: result.spec,
      business: result.business,
    });

    res.status(201).json({
      site: siteSummary(site),
      generation: { source: result.source, reason: result.reason || null, model: result.model || null },
    });
  }));

  router.get('/sites/:siteId', route((req, res) => {
    const site = owned(req);
    expireStaleHolds(db, site.id);
    const spec = parseSpec(site.spec);

    const counts = db
      .prepare(
        `SELECT
           (SELECT COUNT(*) FROM appointments WHERE site_id = ? AND status = 'confirmed' AND starts_at > ?) AS upcoming,
           (SELECT COUNT(*) FROM orders WHERE site_id = ? AND status = 'paid') AS paidOrders,
           (SELECT COALESCE(SUM(amount_cents),0) FROM orders WHERE site_id = ? AND status = 'paid') AS revenueCents,
           (SELECT COUNT(*) FROM messages WHERE site_id = ? AND read = 0) AS unread`,
      )
      .get(site.id, Date.now(), site.id, site.id, site.id);

    res.json({
      site: { ...siteSummary(site), prompt: site.prompt },
      spec,
      services: db.prepare('SELECT * FROM services WHERE site_id = ? ORDER BY position, created_at').all(site.id),
      products: db.prepare('SELECT * FROM products WHERE site_id = ? ORDER BY position, created_at').all(site.id),
      availability: db
        .prepare('SELECT weekday, start_min, end_min FROM availability WHERE site_id = ? AND service_id IS NULL ORDER BY weekday')
        .all(site.id),
      domains: db.prepare('SELECT * FROM domains WHERE site_id = ? ORDER BY created_at').all(site.id)
        .map((d) => ({ ...d, dns: dnsInstructions(d) })),
      stats: counts,
      themes: Object.entries(THEME_PRESETS).map(([key, t]) => ({ key, label: t.label, colors: t.colors })),
      paymentMode: paymentMode(),
      aiEnabled: config.anthropic.enabled,
    });
  }));

  router.put('/sites/:siteId/spec', route((req, res) => {
    const site = owned(req);
    if (!req.body.spec || typeof req.body.spec !== 'object') throw badRequest('A site spec is required');
    const spec = saveSpec(db, site.id, req.body.spec);
    res.json({ spec, site: siteSummary(db.prepare('SELECT * FROM sites WHERE id = ?').get(site.id)) });
  }));

  router.post('/sites/:siteId/regenerate', route(async (req, res) => {
    const site = owned(req);
    const prompt = str(req.body.prompt, 'Instructions', { min: 4, max: 4000 });
    const keepCatalog = bool(req.body.keepCatalog, true);

    const result = await generateSite({
      prompt,
      businessName: site.name,
      existing: parseSpec(site.spec),
    });

    saveSpec(db, site.id, result.spec);
    // A wording or theme change shouldn't silently rewrite a live catalogue.
    if (!keepCatalog) applyBusiness(db, site.id, result.business);
    else applyBusiness(db, site.id, { services: [], products: [], availability: result.business.availability });

    res.json({
      spec: parseSpec(db.prepare('SELECT spec FROM sites WHERE id = ?').get(site.id).spec),
      generation: { source: result.source, reason: result.reason || null },
    });
  }));

  router.post('/sites/:siteId/publish', route((req, res) => {
    const site = publishSite(db, owned(req).id);
    res.json({ site: siteSummary(site), url: publicUrl(site) });
  }));

  router.post('/sites/:siteId/unpublish', route((req, res) => {
    const site = owned(req);
    unpublishSite(db, site.id);
    res.json({ site: siteSummary(db.prepare('SELECT * FROM sites WHERE id = ?').get(site.id)) });
  }));

  router.delete('/sites/:siteId', route((req, res) => {
    db.prepare('DELETE FROM sites WHERE id = ?').run(owned(req).id);
    res.json({ ok: true });
  }));

  // ── Services ─────────────────────────────────────────────────────────────
  const serviceBody = (body) => {
    const priceCents = money(body.price, 'Price', { fallback: 0 });
    const depositCents = money(body.deposit, 'Deposit', { fallback: 0 });
    if (depositCents > priceCents && priceCents > 0) {
      throw badRequest('The deposit cannot be more than the price');
    }
    return {
      name: str(body.name, 'Name', { min: 1, max: 90 }),
      description: str(body.description ?? '', 'Description', { max: 400 }),
      durationMin: int(body.durationMin, 'Duration', { min: 5, max: 480, fallback: 60 }),
      priceCents,
      depositCents,
      bufferMin: int(body.bufferMin, 'Buffer', { min: 0, max: 240, fallback: 0 }),
      capacity: int(body.capacity, 'Capacity', { min: 1, max: 200, fallback: 1 }),
      active: bool(body.active, true) ? 1 : 0,
    };
  };

  router.post('/sites/:siteId/services', route((req, res) => {
    const site = owned(req);
    const s = serviceBody(req.body);
    const serviceId = id('svc');
    const position = db.prepare('SELECT COALESCE(MAX(position),-1)+1 AS n FROM services WHERE site_id = ?').get(site.id).n;
    db.prepare(
      `INSERT INTO services (id, site_id, name, description, duration_min, price_cents, deposit_cents, buffer_min, capacity, active, position, created_at)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
    ).run(serviceId, site.id, s.name, s.description, s.durationMin, s.priceCents, s.depositCents, s.bufferMin, s.capacity, s.active, position, Date.now());
    res.status(201).json({ service: db.prepare('SELECT * FROM services WHERE id = ?').get(serviceId) });
  }));

  router.put('/sites/:siteId/services/:id', route((req, res) => {
    const site = owned(req);
    const s = serviceBody(req.body);
    const result = db.prepare(
      `UPDATE services SET name=?, description=?, duration_min=?, price_cents=?, deposit_cents=?, buffer_min=?, capacity=?, active=?
        WHERE id=? AND site_id=?`,
    ).run(s.name, s.description, s.durationMin, s.priceCents, s.depositCents, s.bufferMin, s.capacity, s.active, req.params.id, site.id);
    if (!result.changes) throw notFound('Service not found');
    res.json({ service: db.prepare('SELECT * FROM services WHERE id = ?').get(req.params.id) });
  }));

  router.delete('/sites/:siteId/services/:id', route((req, res) => {
    const site = owned(req);
    const result = db.prepare('DELETE FROM services WHERE id = ? AND site_id = ?').run(req.params.id, site.id);
    if (!result.changes) throw notFound('Service not found');
    res.json({ ok: true });
  }));

  // ── Products ─────────────────────────────────────────────────────────────
  const productBody = (body) => ({
    name: str(body.name, 'Name', { min: 1, max: 90 }),
    description: str(body.description ?? '', 'Description', { max: 400 }),
    priceCents: money(body.price, 'Price', { fallback: 0 }),
    image: str(body.image ?? '', 'Image', { max: 500 }),
    inventory: body.inventory === '' || body.inventory === null || body.inventory === undefined
      ? null
      : int(body.inventory, 'Inventory', { min: 0, max: 1_000_000 }),
    active: bool(body.active, true) ? 1 : 0,
  });

  router.post('/sites/:siteId/products', route((req, res) => {
    const site = owned(req);
    const p = productBody(req.body);
    const productId = id('prd');
    const position = db.prepare('SELECT COALESCE(MAX(position),-1)+1 AS n FROM products WHERE site_id = ?').get(site.id).n;
    db.prepare(
      `INSERT INTO products (id, site_id, name, description, price_cents, image, inventory, active, position, created_at)
       VALUES (?,?,?,?,?,?,?,?,?,?)`,
    ).run(productId, site.id, p.name, p.description, p.priceCents, p.image, p.inventory, p.active, position, Date.now());
    res.status(201).json({ product: db.prepare('SELECT * FROM products WHERE id = ?').get(productId) });
  }));

  router.put('/sites/:siteId/products/:id', route((req, res) => {
    const site = owned(req);
    const p = productBody(req.body);
    const result = db.prepare(
      'UPDATE products SET name=?, description=?, price_cents=?, image=?, inventory=?, active=? WHERE id=? AND site_id=?',
    ).run(p.name, p.description, p.priceCents, p.image, p.inventory, p.active, req.params.id, site.id);
    if (!result.changes) throw notFound('Product not found');
    res.json({ product: db.prepare('SELECT * FROM products WHERE id = ?').get(req.params.id) });
  }));

  router.delete('/sites/:siteId/products/:id', route((req, res) => {
    const site = owned(req);
    const result = db.prepare('DELETE FROM products WHERE id = ? AND site_id = ?').run(req.params.id, site.id);
    if (!result.changes) throw notFound('Product not found');
    res.json({ ok: true });
  }));

  // ── Opening hours ────────────────────────────────────────────────────────
  router.put('/sites/:siteId/availability', route((req, res) => {
    const site = owned(req);
    const rows = Array.isArray(req.body.rules) ? req.body.rules : [];
    const parsed = rows.map((r) => {
      const weekday = int(r.weekday, 'Day', { min: 0, max: 6 });
      const start = labelToMinutes(r.start);
      const end = labelToMinutes(r.end);
      if (start === null || end === null) throw badRequest('Times must look like 09:00');
      if (end <= start) throw badRequest(`Closing time must be after opening time on ${['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][weekday]}`);
      return { weekday, start, end };
    });

    const write = db.transaction(() => {
      db.prepare('DELETE FROM availability WHERE site_id = ? AND service_id IS NULL').run(site.id);
      for (const r of parsed) {
        db.prepare('INSERT INTO availability (id, site_id, service_id, weekday, start_min, end_min) VALUES (?,?,NULL,?,?,?)')
          .run(id('av'), site.id, r.weekday, r.start, r.end);
      }
    });
    write();

    res.json({
      availability: db
        .prepare('SELECT weekday, start_min, end_min FROM availability WHERE site_id = ? AND service_id IS NULL ORDER BY weekday')
        .all(site.id),
    });
  }));

  // ── Bookings, orders, messages ───────────────────────────────────────────
  router.get('/sites/:siteId/appointments', route((req, res) => {
    const site = owned(req);
    expireStaleHolds(db, site.id);
    const spec = parseSpec(site.spec);
    const scope = req.query.scope === 'past' ? 'past' : 'upcoming';
    const now = Date.now();

    const rows = db.prepare(
      `SELECT a.*, s.name AS service_name FROM appointments a
         LEFT JOIN services s ON s.id = a.service_id
        WHERE a.site_id = ? AND a.starts_at ${scope === 'past' ? '<' : '>='} ?
        ORDER BY a.starts_at ${scope === 'past' ? 'DESC' : 'ASC'} LIMIT 200`,
    ).all(site.id, now);

    res.json({
      appointments: rows.map((a) => ({
        ...a,
        when: formatInZone(a.starts_at, spec.settings.timezone, { withYear: true }),
      })),
      timezone: spec.settings.timezone,
    });
  }));

  router.delete('/sites/:siteId/appointments/:id', route((req, res) => {
    const site = owned(req);
    cancelAppointment(db, site.id, req.params.id);
    res.json({ ok: true });
  }));

  router.get('/sites/:siteId/orders', route((req, res) => {
    const site = owned(req);
    res.json({
      orders: db.prepare('SELECT * FROM orders WHERE site_id = ? ORDER BY created_at DESC LIMIT 200').all(site.id)
        .map((o) => ({ ...o, items: JSON.parse(o.items || '[]') })),
    });
  }));

  router.get('/sites/:siteId/messages', route((req, res) => {
    const site = owned(req);
    const rows = db.prepare('SELECT * FROM messages WHERE site_id = ? ORDER BY created_at DESC LIMIT 200').all(site.id);
    db.prepare('UPDATE messages SET read = 1 WHERE site_id = ?').run(site.id);
    res.json({ messages: rows });
  }));

  // ── Domains ──────────────────────────────────────────────────────────────
  router.post('/sites/:siteId/domains', route((req, res) => {
    const site = owned(req);
    const host = parseHostname(req.body.hostname);
    const domain = addDomain(db, site.id, host);
    res.status(201).json({ domain: { ...domain, dns: dnsInstructions(domain) }, target: targetHost() });
  }));

  router.post('/sites/:siteId/domains/:id/verify', route(async (req, res) => {
    const site = owned(req);
    const exists = db.prepare('SELECT 1 FROM domains WHERE id = ? AND site_id = ?').get(req.params.id, site.id);
    if (!exists) throw notFound('Domain not found');
    const domain = await verifyDomain(db, req.params.id);
    res.json({ domain: { ...domain, dns: dnsInstructions(domain) } });
  }));

  router.delete('/sites/:siteId/domains/:id', route((req, res) => {
    const site = owned(req);
    const result = db.prepare('DELETE FROM domains WHERE id = ? AND site_id = ?').run(req.params.id, site.id);
    if (!result.changes) throw notFound('Domain not found');
    res.json({ ok: true });
  }));

  return router;
}

export { minutesToLabel, BOOKING_HORIZON_DAYS };
