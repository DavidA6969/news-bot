/**
 * Payments.
 *
 * With STRIPE_SECRET_KEY set, checkout goes through Stripe Checkout Sessions.
 * Without it the app runs in sandbox mode: real orders are recorded and a local
 * card page settles them, so booking deposits and store checkout can be
 * exercised end to end before any keys exist.
 */
import crypto from 'node:crypto';
import config from '../config.js';
import { id } from '../lib/ids.js';
import { badRequest, notFound } from '../lib/http.js';
import { confirmAppointment } from './booking.js';

const STRIPE_API = 'https://api.stripe.com/v1';

export const paymentMode = () => (config.stripe.enabled ? 'stripe' : 'sandbox');

/** Stripe takes form-encoded bodies with bracketed paths for nested data. */
function encodeForm(obj, prefix = '', out = new URLSearchParams()) {
  for (const [key, value] of Object.entries(obj)) {
    if (value === undefined || value === null) continue;
    const path = prefix ? `${prefix}[${key}]` : key;
    if (Array.isArray(value)) {
      value.forEach((item, i) => {
        if (item !== null && typeof item === 'object') encodeForm(item, `${path}[${i}]`, out);
        else out.append(`${path}[${i}]`, String(item));
      });
    } else if (value !== null && typeof value === 'object') {
      encodeForm(value, path, out);
    } else {
      out.append(path, String(value));
    }
  }
  return out;
}

async function stripeRequest(path, body) {
  const res = await fetch(`${STRIPE_API}${path}`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${config.stripe.secretKey}`,
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: encodeForm(body).toString(),
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message = json?.error?.message || `Stripe request failed (${res.status})`;
    throw badRequest(message);
  }
  return json;
}

export function createOrder(db, { siteId, kind, amountCents, currency, email, name, items }) {
  const orderId = id('ord');
  db.prepare(
    `INSERT INTO orders (id, site_id, kind, status, amount_cents, currency, email, name, items, provider, provider_ref, created_at)
     VALUES (?,?,?,'pending',?,?,?,?,?,?,'',?)`,
  ).run(
    orderId,
    siteId,
    kind,
    amountCents,
    currency,
    email || '',
    name || '',
    JSON.stringify(items || []),
    paymentMode(),
    Date.now(),
  );
  return orderId;
}

/**
 * Hand back a URL that collects the card. Stripe-hosted when configured,
 * otherwise the local sandbox page.
 */
export async function startCheckout(db, { site, orderId, successUrl, cancelUrl }) {
  const order = db.prepare('SELECT * FROM orders WHERE id = ?').get(orderId);
  if (!order) throw notFound('Order not found');
  if (order.amount_cents <= 0) {
    // Nothing to collect — settle immediately.
    markOrderPaid(db, orderId, 'free');
    return { url: successUrl, provider: 'free' };
  }

  if (!config.stripe.enabled) {
    return { url: `${config.appOrigin}/pay/${orderId}`, provider: 'sandbox' };
  }

  const items = JSON.parse(order.items || '[]');
  const session = await stripeRequest('/checkout/sessions', {
    mode: 'payment',
    success_url: successUrl,
    cancel_url: cancelUrl,
    client_reference_id: orderId,
    ...(order.email ? { customer_email: order.email } : {}),
    metadata: { order_id: orderId, site_id: site.id },
    line_items: items.length
      ? items.map((item) => ({
          quantity: item.quantity || 1,
          price_data: {
            currency: order.currency,
            unit_amount: item.unitAmount,
            product_data: { name: item.name, ...(item.description ? { description: item.description } : {}) },
          },
        }))
      : [{
          quantity: 1,
          price_data: {
            currency: order.currency,
            unit_amount: order.amount_cents,
            product_data: { name: `${site.name} — payment` },
          },
        }],
  });

  db.prepare('UPDATE orders SET provider = ?, provider_ref = ? WHERE id = ?').run('stripe', session.id, orderId);
  return { url: session.url, provider: 'stripe', ref: session.id };
}

/**
 * Settle an order. Idempotent: re-delivered webhooks and a customer refreshing
 * the success page must not double-apply inventory or confirmations.
 */
export function markOrderPaid(db, orderId, providerRef = '') {
  const apply = db.transaction(() => {
    const order = db.prepare('SELECT * FROM orders WHERE id = ?').get(orderId);
    if (!order) return null;
    if (order.status === 'paid') return order;

    db.prepare('UPDATE orders SET status = ?, paid_at = ?, provider_ref = ? WHERE id = ?').run(
      'paid',
      Date.now(),
      providerRef || order.provider_ref,
      orderId,
    );

    for (const item of JSON.parse(order.items || '[]')) {
      if (item.productId) {
        db.prepare(
          `UPDATE products SET inventory = MAX(0, inventory - ?)
            WHERE id = ? AND inventory IS NOT NULL`,
        ).run(item.quantity || 1, item.productId);
      }
    }

    // A deposit order points back from the appointment it is holding.
    const held = db.prepare("SELECT id FROM appointments WHERE order_id = ? AND status = 'hold'").get(orderId);
    if (held) confirmAppointment(db, held.id, orderId);

    return db.prepare('SELECT * FROM orders WHERE id = ?').get(orderId);
  });
  return apply();
}

/** Link a deposit order to the appointment it holds, then settle if already paid. */
export function attachAppointment(db, orderId, appointmentId) {
  db.prepare('UPDATE appointments SET order_id = ? WHERE id = ?').run(orderId, appointmentId);
  const order = db.prepare('SELECT status FROM orders WHERE id = ?').get(orderId);
  if (order?.status === 'paid') confirmAppointment(db, appointmentId, orderId);
}

/** Constant-time verification of Stripe's `Stripe-Signature` header. */
export function verifyStripeSignature(rawBody, header, secret, toleranceSec = 300) {
  if (!secret || !header) return false;
  const parts = Object.fromEntries(
    String(header)
      .split(',')
      .map((p) => p.split('='))
      .filter((p) => p.length === 2)
      .map(([k, v]) => [k.trim(), v.trim()]),
  );
  const timestamp = Number(parts.t);
  if (!Number.isFinite(timestamp)) return false;
  if (Math.abs(Date.now() / 1000 - timestamp) > toleranceSec) return false;

  const expected = crypto
    .createHmac('sha256', secret)
    .update(`${timestamp}.${rawBody}`, 'utf8')
    .digest('hex');
  const provided = parts.v1 || '';
  const a = Buffer.from(expected, 'utf8');
  const b = Buffer.from(provided, 'utf8');
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}
