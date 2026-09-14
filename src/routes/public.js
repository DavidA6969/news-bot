import express from 'express';
import config from '../config.js';
import { route, notFound, badRequest, escapeHtml } from '../lib/http.js';
import { str, email as parseEmail, int } from '../lib/validate.js';
import { parseSpec } from '../services/sites.js';
import { siteForHost } from '../services/domains.js';
import { buildContext, renderSite, renderConfirmation } from '../render/renderer.js';
import { formatMoney } from '../render/blocks.js';
import {
  getService, availabilityCalendar, createAppointment, expireStaleHolds,
} from '../services/booking.js';
import {
  createOrder, startCheckout, markOrderPaid, attachAppointment,
  verifyStripeSignature, paymentMode,
} from '../services/payments.js';
import { formatInZone } from '../lib/time.js';

/** Small fixed-window limiter — enough to blunt abuse of the public writes. */
function rateLimiter({ windowMs = 60_000, max = 20 } = {}) {
  const hits = new Map();
  return (req, res, next) => {
    const now = Date.now();
    const key = `${req.ip}:${req.params.siteId || ''}`;
    const entry = hits.get(key);
    if (!entry || entry.reset < now) {
      hits.set(key, { count: 1, reset: now + windowMs });
    } else if (++entry.count > max) {
      return res.status(429).json({ error: 'Too many requests — please wait a moment and try again' });
    }
    if (hits.size > 5000) {
      for (const [k, v] of hits) if (v.reset < now) hits.delete(k);
    }
    next();
  };
}

export default function publicRoutes(db) {
  const router = express.Router();

  const liveSite = (siteId) => {
    const site = db.prepare('SELECT * FROM sites WHERE id = ?').get(siteId);
    if (!site) throw notFound('Site not found');
    return site;
  };

  /** Draft is served for previews; published snapshot for anything live. */
  const specFor = (site, { draft = false } = {}) =>
    parseSpec(draft || !site.published_spec ? site.spec : site.published_spec);

  function serve(res, site, { pageSlug, basePath, draft }) {
    const spec = specFor(site, { draft });
    const ctx = buildContext(db, site, spec);
    if (pageSlug && pageSlug !== 'index' && !spec.pages.some((p) => p.slug === pageSlug)) {
      throw notFound('Page not found');
    }
    let html = renderSite(ctx, { pageSlug: pageSlug || 'index', apiBase: `/_lk/api/${site.id}` });
    // Under a path prefix, root-relative links must keep the prefix.
    if (basePath) html = html.replace(/(href|action)="\/(?!\/)/g, `$1="${basePath}/`);
    res.type('html').send(html);
  }

  // ── Published site by slug ───────────────────────────────────────────────
  router.get('/s/:slug/:page?', route((req, res) => {
    const site = db.prepare('SELECT * FROM sites WHERE slug = ?').get(req.params.slug);
    if (!site || !site.published_spec) throw notFound('This site has not been published yet');
    serve(res, site, { pageSlug: req.params.page, basePath: `/s/${site.slug}` });
  }));

  // ── Owner preview of unpublished work ────────────────────────────────────
  router.get('/preview/:siteId/:page?', route((req, res) => {
    const site = liveSite(req.params.siteId);
    if (!req.user || req.user.id !== site.user_id) throw notFound('Preview not available');
    serve(res, site, { pageSlug: req.params.page, basePath: `/preview/${site.id}`, draft: true });
  }));

  // ── Public API used by every published page ──────────────────────────────
  const api = express.Router({ mergeParams: true });
  const writeLimit = rateLimiter({ windowMs: 60_000, max: 12 });

  api.get('/availability', route((req, res) => {
    const site = liveSite(req.params.siteId);
    const spec = specFor(site);
    const service = getService(db, site.id, str(req.query.serviceId, 'Service', { min: 1, max: 60 }));
    const days = int(req.query.days, 'days', { min: 1, max: 60, fallback: 14 });

    const calendar = availabilityCalendar(db, {
      siteId: site.id,
      service,
      timezone: spec.settings.timezone,
      days,
    });

    res.json({
      timezone: spec.settings.timezone,
      service: {
        id: service.id,
        name: service.name,
        durationMin: service.duration_min,
        priceCents: service.price_cents,
        depositCents: service.deposit_cents,
        capacity: service.capacity,
      },
      days: calendar,
    });
  }));

  api.post('/appointments', writeLimit, route(async (req, res) => {
    const site = liveSite(req.params.siteId);
    const spec = specFor(site);
    const service = getService(db, site.id, str(req.body.serviceId, 'Service', { min: 1, max: 60 }));

    const startsAt = Number(req.body.startsAt);
    if (!Number.isFinite(startsAt) || startsAt <= 0) throw badRequest('Pick a time slot');

    const customer = {
      name: str(req.body.name, 'Name', { min: 1, max: 120 }),
      email: parseEmail(req.body.email),
      phone: str(req.body.phone ?? '', 'Phone', { max: 40 }),
      notes: str(req.body.notes ?? '', 'Notes', { max: 1000 }),
    };

    const requiresDeposit = service.deposit_cents > 0;
    const appointment = createAppointment(db, {
      siteId: site.id,
      service,
      startsAt,
      customer,
      timezone: spec.settings.timezone,
      requiresDeposit,
    });

    if (!requiresDeposit) {
      return res.status(201).json({ appointment, checkoutUrl: null });
    }

    const orderId = createOrder(db, {
      siteId: site.id,
      kind: 'deposit',
      amountCents: service.deposit_cents,
      currency: spec.settings.currency,
      email: customer.email,
      name: customer.name,
      items: [{
        name: `Deposit — ${service.name}`,
        description: appointment.label,
        unitAmount: service.deposit_cents,
        quantity: 1,
      }],
    });
    attachAppointment(db, orderId, appointment.id);

    const checkout = await startCheckout(db, {
      site,
      orderId,
      successUrl: `${config.appOrigin}/booked/${appointment.id}`,
      cancelUrl: `${config.appOrigin}/s/${site.slug}#booking`,
    });

    res.status(201).json({ appointment, orderId, checkoutUrl: checkout.url });
  }));

  api.post('/checkout', writeLimit, route(async (req, res) => {
    const site = liveSite(req.params.siteId);
    const spec = specFor(site);
    const productId = str(req.body.productId, 'Product', { min: 1, max: 60 });
    const quantity = int(req.body.quantity, 'Quantity', { min: 1, max: 20, fallback: 1 });

    const product = db
      .prepare('SELECT * FROM products WHERE id = ? AND site_id = ? AND active = 1')
      .get(productId, site.id);
    if (!product) throw notFound('That item is not available');
    if (product.inventory !== null && product.inventory < quantity) {
      throw badRequest(product.inventory > 0 ? `Only ${product.inventory} left in stock` : 'That item is sold out');
    }

    const orderId = createOrder(db, {
      siteId: site.id,
      kind: 'product',
      amountCents: product.price_cents * quantity,
      currency: spec.settings.currency,
      email: '',
      name: '',
      items: [{
        productId: product.id,
        name: product.name,
        description: product.description.slice(0, 300),
        unitAmount: product.price_cents,
        quantity,
      }],
    });

    const checkout = await startCheckout(db, {
      site,
      orderId,
      successUrl: `${config.appOrigin}/ordered/${orderId}`,
      cancelUrl: `${config.appOrigin}/s/${site.slug}#products`,
    });

    res.status(201).json({ orderId, checkoutUrl: checkout.url });
  }));

  api.post('/messages', writeLimit, route((req, res) => {
    const site = liveSite(req.params.siteId);
    db.prepare('INSERT INTO messages (id, site_id, name, email, body, created_at) VALUES (?,?,?,?,?,?)').run(
      `msg_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`,
      site.id,
      str(req.body.name ?? '', 'Name', { max: 120 }),
      parseEmail(req.body.email),
      str(req.body.body, 'Message', { min: 1, max: 2000 }),
      Date.now(),
    );
    res.status(201).json({ ok: true });
  }));

  router.use('/_lk/api/:siteId', api);

  // ── Sandbox card page (only reachable when Stripe is unconfigured) ───────
  router.get('/pay/:orderId', route((req, res) => {
    const order = db.prepare('SELECT * FROM orders WHERE id = ?').get(req.params.orderId);
    if (!order) throw notFound('Order not found');
    const site = liveSite(order.site_id);
    const spec = specFor(site);
    if (order.status === 'paid') return res.redirect(returnUrlFor(db, order));

    const items = JSON.parse(order.items || '[]');
    res.type('html').send(sandboxCheckoutPage({
      spec,
      order,
      items,
      amount: formatMoney(order.amount_cents, order.currency),
    }));
  }));

  router.post('/pay/:orderId', route((req, res) => {
    const order = db.prepare('SELECT * FROM orders WHERE id = ?').get(req.params.orderId);
    if (!order) throw notFound('Order not found');
    if (order.provider === 'stripe') throw badRequest('This order settles through Stripe');
    markOrderPaid(db, order.id, `sandbox_${Date.now()}`);
    res.redirect(returnUrlFor(db, db.prepare('SELECT * FROM orders WHERE id = ?').get(order.id)));
  }));

  // ── Confirmations ────────────────────────────────────────────────────────
  router.get('/booked/:appointmentId', route((req, res) => {
    const appointment = db.prepare('SELECT * FROM appointments WHERE id = ?').get(req.params.appointmentId);
    if (!appointment) throw notFound('Booking not found');
    const site = liveSite(appointment.site_id);
    const spec = specFor(site);
    const ctx = buildContext(db, site, spec);
    const service = db.prepare('SELECT name FROM services WHERE id = ?').get(appointment.service_id);

    expireStaleHolds(db, site.id);
    const fresh = db.prepare('SELECT status FROM appointments WHERE id = ?').get(appointment.id);
    const lines = [
      service ? service.name : 'Appointment',
      formatInZone(appointment.starts_at, spec.settings.timezone, { withYear: true }),
      `A confirmation has been sent to ${appointment.email}.`,
    ];
    if (fresh.status === 'hold') lines.push('We are still waiting on your deposit to confirm this slot.');

    res.type('html').send(renderConfirmation(ctx, {
      heading: fresh.status === 'confirmed' ? "You're booked" : 'Almost there',
      lines,
      backHref: `/s/${site.slug}`,
    }));
  }));

  router.get('/ordered/:orderId', route((req, res) => {
    const order = db.prepare('SELECT * FROM orders WHERE id = ?').get(req.params.orderId);
    if (!order) throw notFound('Order not found');
    const site = liveSite(order.site_id);
    const spec = specFor(site);
    const ctx = buildContext(db, site, spec);
    const items = JSON.parse(order.items || '[]');

    res.type('html').send(renderConfirmation(ctx, {
      heading: order.status === 'paid' ? 'Order confirmed' : 'Payment pending',
      lines: [
        ...items.map((i) => `${i.quantity} × ${i.name}`),
        `Total ${formatMoney(order.amount_cents, order.currency)}`,
        order.status === 'paid'
          ? 'A receipt is on its way to your inbox.'
          : 'We will email you as soon as the payment settles.',
      ],
      backHref: `/s/${site.slug}`,
    }));
  }));

  // ── Stripe webhook (raw body — signature covers the exact bytes) ─────────
  router.post('/webhooks/stripe', express.raw({ type: 'application/json' }), (req, res) => {
    const raw = req.body instanceof Buffer ? req.body.toString('utf8') : String(req.body || '');
    if (config.stripe.webhookSecret) {
      const ok = verifyStripeSignature(raw, req.get('stripe-signature'), config.stripe.webhookSecret);
      if (!ok) return res.status(400).json({ error: 'Invalid signature' });
    }
    let event;
    try {
      event = JSON.parse(raw);
    } catch {
      return res.status(400).json({ error: 'Invalid payload' });
    }

    if (event.type === 'checkout.session.completed') {
      const session = event.data?.object || {};
      const orderId = session.metadata?.order_id || session.client_reference_id;
      if (orderId) markOrderPaid(db, orderId, session.id || '');
    }
    res.json({ received: true });
  });

  return router;
}

/**
 * Serves a published site when the request arrives on a connected custom
 * domain. Mounted ahead of the builder's own pages so `example.com/` renders
 * the customer's site rather than the LaunchKit landing page.
 */
export function customDomainMiddleware(db) {
  return (req, res, next) => {
    if (req.method !== 'GET' && req.method !== 'HEAD') return next();
    let site;
    try {
      site = siteForHost(db, req.get('host'));
    } catch {
      return next();
    }
    if (!site) return next();

    // Reserved prefixes stay with the app even on a custom domain, so booking
    // and checkout callbacks keep working there.
    if (/^\/(?:_lk|api|pay|booked|ordered|webhooks|assets|preview|s)(?:\/|$)/.test(req.path)) return next();

    try {
      const spec = parseSpec(site.published_spec);
      const pageSlug = req.path === '/' ? 'index' : req.path.slice(1).split('/')[0];
      if (pageSlug !== 'index' && !spec.pages.some((p) => p.slug === pageSlug)) return next();
      const ctx = buildContext(db, site, spec);
      res.type('html').send(renderSite(ctx, { pageSlug, apiBase: `/_lk/api/${site.id}` }));
    } catch (err) {
      next(err);
    }
  };
}

function returnUrlFor(db, order) {
  const appointment = db.prepare('SELECT id FROM appointments WHERE order_id = ?').get(order.id);
  return appointment ? `/booked/${appointment.id}` : `/ordered/${order.id}`;
}

function sandboxCheckoutPage({ spec, order, items, amount }) {
  const rows = items
    .map((i) => `<li><span>${escapeHtml(`${i.quantity} × ${i.name}`)}</span><span>${escapeHtml(formatMoney(i.unitAmount * i.quantity, order.currency))}</span></li>`)
    .join('');
  return `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Checkout — ${escapeHtml(spec.meta.title)}</title>
<style>
:root{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f2f4f7;font:16px/1.6 system-ui,-apple-system,'Segoe UI',sans-serif;color:#16202c;padding:24px}
.box{width:100%;max-width:420px;background:#fff;border-radius:14px;box-shadow:0 12px 40px rgba(16,32,52,.13);padding:28px}
h1{font-size:1.22rem;margin:0 0 4px}
.sub{color:#64748b;font-size:.9rem;margin:0 0 20px}
.badge{display:inline-block;background:#fff4d6;color:#8a5a00;border:1px solid #f2d48a;border-radius:999px;padding:3px 11px;font-size:.76rem;font-weight:600;margin-bottom:16px}
ul{list-style:none;margin:0 0 16px;padding:0;font-size:.94rem}
li{display:flex;justify-content:space-between;gap:14px;padding:8px 0;border-bottom:1px solid #eef1f5}
.total{display:flex;justify-content:space-between;font-weight:700;font-size:1.12rem;padding:12px 0 20px}
label{display:block;font-size:.83rem;font-weight:600;margin:0 0 5px}
input{width:100%;font:inherit;font-size:.97rem;padding:.7em .8em;border:1px solid #d6dde6;border-radius:8px;margin-bottom:13px;background:#fbfcfd}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
button{width:100%;padding:.85em;border:0;border-radius:8px;background:#0f172a;color:#fff;font:inherit;font-weight:600;font-size:1rem;cursor:pointer}
button:hover{background:#1e293b}
.note{font-size:.78rem;color:#94a3b8;text-align:center;margin:14px 0 0}
</style></head><body>
<form class="box" method="POST" action="/pay/${escapeHtml(order.id)}">
  <div class="badge">Sandbox mode — no card is charged</div>
  <h1>${escapeHtml(spec.meta.title)}</h1>
  <p class="sub">Add STRIPE_SECRET_KEY to take real payments here.</p>
  <ul>${rows}</ul>
  <div class="total"><span>Total</span><span>${escapeHtml(amount)}</span></div>
  <label for="card">Card number</label>
  <input id="card" value="4242 4242 4242 4242" inputmode="numeric" autocomplete="off">
  <div class="row">
    <div><label for="exp">Expiry</label><input id="exp" value="12/34"></div>
    <div><label for="cvc">CVC</label><input id="cvc" value="123"></div>
  </div>
  <button type="submit">Pay ${escapeHtml(amount)}</button>
  <p class="note">Test checkout provided by LaunchKit.</p>
</form></body></html>`;
}
