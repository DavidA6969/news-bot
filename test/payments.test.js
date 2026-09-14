import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { createMemoryDb } from '../src/db/index.js';
import { verifyStripeSignature, createOrder, markOrderPaid, attachAppointment } from '../src/services/payments.js';
import { id } from '../src/lib/ids.js';

const SECRET = 'whsec_testsecret';
const sign = (body, secret = SECRET, ts = Math.floor(Date.now() / 1000)) =>
  `t=${ts},v1=${crypto.createHmac('sha256', secret).update(`${ts}.${body}`).digest('hex')}`;

test('accepts a correctly signed webhook', () => {
  const body = JSON.stringify({ id: 'evt_1', type: 'checkout.session.completed' });
  assert.equal(verifyStripeSignature(body, sign(body), SECRET), true);
});

test('rejects tampering, a wrong secret, and a replayed timestamp', () => {
  const body = JSON.stringify({ id: 'evt_1' });
  const header = sign(body);
  assert.equal(verifyStripeSignature(`${body} `, header, SECRET), false, 'payload changed');
  assert.equal(verifyStripeSignature(body, header, 'whsec_other'), false, 'wrong secret');
  assert.equal(verifyStripeSignature(body, sign(body, SECRET, Math.floor(Date.now() / 1000) - 4000), SECRET), false, 'stale');
  assert.equal(verifyStripeSignature(body, '', SECRET), false);
  assert.equal(verifyStripeSignature(body, header, ''), false);
  assert.equal(verifyStripeSignature(body, 'garbage', SECRET), false);
});

function seed() {
  const db = createMemoryDb();
  const now = Date.now();
  db.prepare('INSERT INTO users (id,email,password_hash,name,created_at) VALUES (?,?,?,?,?)').run('u1', 'a@b.co', 'x', '', now);
  db.prepare('INSERT INTO sites (id,user_id,name,slug,prompt,spec,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)')
    .run('s1', 'u1', 'T', 't', '', '{}', now, now);
  db.prepare(`INSERT INTO products (id,site_id,name,description,price_cents,image,inventory,active,position,created_at)
              VALUES ('p1','s1','Thing','',2500,'',5,1,0,?)`).run(now);
  return db;
}

test('settling an order is idempotent for inventory', () => {
  const db = seed();
  const orderId = createOrder(db, {
    siteId: 's1', kind: 'product', amountCents: 5000, currency: 'usd', email: '', name: '',
    items: [{ productId: 'p1', name: 'Thing', unitAmount: 2500, quantity: 2 }],
  });

  markOrderPaid(db, orderId, 'ref_1');
  markOrderPaid(db, orderId, 'ref_1'); // redelivered webhook
  markOrderPaid(db, orderId, 'ref_1');

  assert.equal(db.prepare('SELECT inventory FROM products WHERE id = ?').get('p1').inventory, 3);
  assert.equal(db.prepare('SELECT status FROM orders WHERE id = ?').get(orderId).status, 'paid');
});

test('inventory never goes negative', () => {
  const db = seed();
  const orderId = createOrder(db, {
    siteId: 's1', kind: 'product', amountCents: 25000, currency: 'usd', email: '', name: '',
    items: [{ productId: 'p1', name: 'Thing', unitAmount: 2500, quantity: 99 }],
  });
  markOrderPaid(db, orderId, 'ref_2');
  assert.equal(db.prepare('SELECT inventory FROM products WHERE id = ?').get('p1').inventory, 0);
});

test('paying a deposit confirms the appointment it holds', () => {
  const db = seed();
  const now = Date.now();
  db.prepare(`INSERT INTO services (id,site_id,name,description,duration_min,price_cents,deposit_cents,buffer_min,capacity,active,position,created_at)
              VALUES ('sv1','s1','X','',60,10000,5000,0,1,1,0,?)`).run(now);
  const appointmentId = id('apt');
  db.prepare(`INSERT INTO appointments (id,site_id,service_id,name,email,phone,notes,starts_at,ends_at,service_end_at,status,hold_expires_at,order_id,created_at)
              VALUES (?,'s1','sv1','A','a@b.co','','',?,?,?,'hold',?,NULL,?)`)
    .run(appointmentId, now + 86400000, now + 90000000, now + 90000000, now + 600000, now);

  const orderId = createOrder(db, {
    siteId: 's1', kind: 'deposit', amountCents: 5000, currency: 'usd', email: 'a@b.co', name: 'A', items: [],
  });
  attachAppointment(db, orderId, appointmentId);
  markOrderPaid(db, orderId, 'ref_3');

  const row = db.prepare('SELECT status, order_id FROM appointments WHERE id = ?').get(appointmentId);
  assert.equal(row.status, 'confirmed');
  assert.equal(row.order_id, orderId);
});

test('an order already paid confirms a later-attached appointment', () => {
  const db = seed();
  const now = Date.now();
  db.prepare(`INSERT INTO services (id,site_id,name,description,duration_min,price_cents,deposit_cents,buffer_min,capacity,active,position,created_at)
              VALUES ('sv1','s1','X','',60,10000,5000,0,1,1,0,?)`).run(now);
  const orderId = createOrder(db, { siteId: 's1', kind: 'deposit', amountCents: 5000, currency: 'usd', email: '', name: '', items: [] });
  markOrderPaid(db, orderId, 'ref_4');

  const appointmentId = id('apt');
  db.prepare(`INSERT INTO appointments (id,site_id,service_id,name,email,phone,notes,starts_at,ends_at,service_end_at,status,hold_expires_at,order_id,created_at)
              VALUES (?,'s1','sv1','A','a@b.co','','',?,?,?,'hold',?,NULL,?)`)
    .run(appointmentId, now + 86400000, now + 90000000, now + 90000000, now + 600000, now);
  attachAppointment(db, orderId, appointmentId);

  assert.equal(db.prepare('SELECT status FROM appointments WHERE id = ?').get(appointmentId).status, 'confirmed');
});

test('a zero-amount order settles without a payment step', () => {
  const db = seed();
  const orderId = createOrder(db, { siteId: 's1', kind: 'product', amountCents: 0, currency: 'usd', email: '', name: '', items: [] });
  markOrderPaid(db, orderId, 'free');
  assert.equal(db.prepare('SELECT status FROM orders WHERE id = ?').get(orderId).status, 'paid');
});
