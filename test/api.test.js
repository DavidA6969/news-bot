import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'launchkit-test-'));
process.env.DATA_DIR = dataDir;
process.env.APP_ORIGIN = 'http://127.0.0.1';

const { createApp } = await import('../server.js');

let server;
let base;
let cookie = '';

before(async () => {
  server = createApp().listen(0);
  await new Promise((r) => server.once('listening', r));
  base = `http://127.0.0.1:${server.address().port}`;
});

after(() => {
  server?.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

async function call(method, route, body, { keepCookie = true } = {}) {
  const res = await fetch(base + route, {
    method,
    headers: { 'Content-Type': 'application/json', ...(cookie ? { Cookie: cookie } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
    redirect: 'manual',
  });
  const set = res.headers.getSetCookie?.() || [];
  if (keepCookie && set.length) cookie = set.map((c) => c.split(';')[0]).join('; ');
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch { /* html */ }
  return { status: res.status, json, text, location: res.headers.get('location') };
}

let siteId;
let slug;

test('rejects a weak password', async () => {
  const res = await call('POST', '/api/auth/signup', { email: 'weak@example.com', password: 'short' });
  assert.equal(res.status, 400);
});

test('signs up and keeps a session', async () => {
  const res = await call('POST', '/api/auth/signup', { email: 'owner@example.com', password: 'a-good-password', name: 'Owner' });
  assert.equal(res.status, 201);
  const me = await call('GET', '/api/auth/me');
  assert.equal(me.json.user.email, 'owner@example.com');
});

test('will not register the same email twice', async () => {
  const res = await call('POST', '/api/auth/signup', { email: 'owner@example.com', password: 'another-password' });
  assert.equal(res.status, 409);
});

test('gives the same error for a wrong password and an unknown account', async () => {
  const unknown = await call('POST', '/api/auth/login', { email: 'nobody@example.com', password: 'whatever123' }, { keepCookie: false });
  const wrong = await call('POST', '/api/auth/login', { email: 'owner@example.com', password: 'wrong-password' }, { keepCookie: false });
  assert.equal(unknown.status, 400);
  assert.equal(wrong.json.error, unknown.json.error);
});

test('generates a site with services, products and hours', async () => {
  const res = await call('POST', '/api/sites/generate', { prompt: 'A dog grooming salon that also sells shampoo' });
  assert.equal(res.status, 201);
  siteId = res.json.site.id;
  slug = res.json.site.slug;

  const detail = await call('GET', `/api/sites/${siteId}`);
  assert.ok(detail.json.services.length > 0, 'services generated');
  assert.ok(detail.json.products.length > 0, 'products generated');
  assert.ok(detail.json.availability.length > 0, 'opening hours generated');
});

test('an unpublished site is not publicly reachable', async () => {
  const res = await call('GET', `/s/${slug}`);
  assert.equal(res.status, 404);
});

test('publishing renders a complete page with the widgets wired up', async () => {
  assert.equal((await call('POST', `/api/sites/${siteId}/publish`)).status, 200);
  const page = await call('GET', `/s/${slug}`);
  assert.equal(page.status, 200);
  assert.match(page.text, /<!doctype html>/i);
  assert.match(page.text, /data-booking/);
  assert.match(page.text, /data-buy-product/);
  assert.match(page.text, /_lk\/api\//);
});

test('another account cannot read or change the site', async () => {
  const mine = cookie;
  cookie = '';
  await call('POST', '/api/auth/signup', { email: 'stranger@example.com', password: 'stranger-password' });
  const read = await call('GET', `/api/sites/${siteId}`);
  const write = await call('POST', `/api/sites/${siteId}/publish`);
  const del = await call('DELETE', `/api/sites/${siteId}`);
  cookie = mine;
  assert.equal(read.status, 403);
  assert.equal(write.status, 403);
  assert.equal(del.status, 403);
});

test('signed-out visitors cannot reach the dashboard API', async () => {
  const mine = cookie;
  cookie = '';
  const res = await call('GET', '/api/sites');
  cookie = mine;
  assert.equal(res.status, 401);
});

test('booking a slot confirms it and blocks a second attempt', async () => {
  const detail = await call('GET', `/api/sites/${siteId}`);
  const service = detail.json.services.find((s) => s.deposit_cents === 0);
  const avail = await call('GET', `/_lk/api/${siteId}/availability?serviceId=${service.id}&days=14`);
  const slot = avail.json.days.find((d) => d.slots.length).slots[0];

  const first = await call('POST', `/_lk/api/${siteId}/appointments`, {
    serviceId: service.id, startsAt: slot.startsAt, name: 'Pat', email: 'pat@example.com',
  });
  assert.equal(first.status, 201);
  assert.equal(first.json.appointment.status, 'confirmed');

  const second = await call('POST', `/_lk/api/${siteId}/appointments`, {
    serviceId: service.id, startsAt: slot.startsAt, name: 'Sam', email: 'sam@example.com',
  });
  assert.equal(second.status, 409);
});

test('a booking with a bad email is rejected', async () => {
  const detail = await call('GET', `/api/sites/${siteId}`);
  const service = detail.json.services[0];
  const avail = await call('GET', `/_lk/api/${siteId}/availability?serviceId=${service.id}&days=14`);
  const slot = avail.json.days.find((d) => d.slots.length).slots[0];
  const res = await call('POST', `/_lk/api/${siteId}/appointments`, {
    serviceId: service.id, startsAt: slot.startsAt, name: 'Pat', email: 'not-an-email',
  });
  assert.equal(res.status, 400);
});

test('a deposit booking is held until the payment settles', async () => {
  const detail = await call('GET', `/api/sites/${siteId}`);
  let service = detail.json.services.find((s) => s.deposit_cents > 0);
  if (!service) {
    const created = await call('POST', `/api/sites/${siteId}/services`, {
      name: 'Deposit service', durationMin: 60, price: 200, deposit: 50,
    });
    service = created.json.service;
  }
  const avail = await call('GET', `/_lk/api/${siteId}/availability?serviceId=${service.id}&days=14`);
  const slot = avail.json.days.find((d) => d.slots.length).slots[0];

  const booked = await call('POST', `/_lk/api/${siteId}/appointments`, {
    serviceId: service.id, startsAt: slot.startsAt, name: 'Dana', email: 'dana@example.com',
  });
  assert.equal(booked.json.appointment.status, 'hold');
  assert.ok(booked.json.checkoutUrl, 'a checkout URL is issued');

  const payPath = new URL(booked.json.checkoutUrl).pathname;
  assert.equal((await call('POST', payPath)).status, 302);

  const list = await call('GET', `/api/sites/${siteId}/appointments`);
  const row = list.json.appointments.find((a) => a.id === booked.json.appointment.id);
  assert.equal(row.status, 'confirmed');
});

test('paying twice does not double-count the order', async () => {
  const detail = await call('GET', `/api/sites/${siteId}`);
  const product = detail.json.products.find((p) => p.inventory !== null);
  const before = product.inventory;

  const checkout = await call('POST', `/_lk/api/${siteId}/checkout`, { productId: product.id, quantity: 2 });
  const payPath = new URL(checkout.json.checkoutUrl).pathname;
  await call('POST', payPath);
  await call('POST', payPath); // replayed — must be a no-op

  const after = await call('GET', `/api/sites/${siteId}`);
  assert.equal(after.json.products.find((p) => p.id === product.id).inventory, before - 2);

  const orders = await call('GET', `/api/sites/${siteId}/orders`);
  const paid = orders.json.orders.filter((o) => o.id === checkout.json.orderId);
  assert.equal(paid.length, 1);
  assert.equal(paid[0].status, 'paid');
});

test('checkout refuses to oversell stock', async () => {
  const created = await call('POST', `/api/sites/${siteId}/products`, {
    name: 'Last one', price: 10, inventory: 1,
  });
  const res = await call('POST', `/_lk/api/${siteId}/checkout`, { productId: created.json.product.id, quantity: 5 });
  assert.equal(res.status, 400);
  assert.match(res.json.error, /Only 1 left/);
});

test('a deposit larger than the price is rejected', async () => {
  const res = await call('POST', `/api/sites/${siteId}/services`, {
    name: 'Bad', durationMin: 60, price: 50, deposit: 90,
  });
  assert.equal(res.status, 400);
});

test('opening hours that close before they open are rejected', async () => {
  const res = await call('PUT', `/api/sites/${siteId}/availability`, {
    rules: [{ weekday: 1, start: '17:00', end: '09:00' }],
  });
  assert.equal(res.status, 400);
});

test('domains are normalised and reported as unverified', async () => {
  const res = await call('POST', `/api/sites/${siteId}/domains`, { hostname: 'HTTPS://Shop.Example.com/pricing' });
  assert.equal(res.status, 201);
  assert.equal(res.json.domain.hostname, 'shop.example.com');

  const again = await call('POST', `/api/sites/${siteId}/domains`, { hostname: 'shop.example.com' });
  assert.equal(again.status, 409);

  const verified = await call('POST', `/api/sites/${siteId}/domains/${res.json.domain.id}/verify`);
  assert.equal(verified.json.domain.status, 'pending');
  assert.match(verified.json.domain.last_error, /TXT/);
});

test('an invalid hostname is refused', async () => {
  const res = await call('POST', `/api/sites/${siteId}/domains`, { hostname: 'not a domain' });
  assert.equal(res.status, 400);
});

test('the contact form stores a message and shows it in the inbox', async () => {
  const sent = await call('POST', `/_lk/api/${siteId}/messages`, {
    name: 'Jo', email: 'jo@example.com', body: 'Do you groom cats?',
  });
  assert.equal(sent.status, 201);
  const inbox = await call('GET', `/api/sites/${siteId}/messages`);
  assert.ok(inbox.json.messages.some((m) => m.body === 'Do you groom cats?'));
});

test('unpublishing takes the site offline again', async () => {
  assert.equal((await call('POST', `/api/sites/${siteId}/unpublish`)).status, 200);
  assert.equal((await call('GET', `/s/${slug}`)).status, 404);
  assert.equal((await call('POST', `/api/sites/${siteId}/publish`)).status, 200);
});
