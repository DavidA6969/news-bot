import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeSpec, specCapabilities, THEME_PRESETS } from '../src/ai/spec.js';
import { normalizeBusiness, normalizeAvailability } from '../src/ai/business.js';

test('repairs junk into a renderable spec', () => {
  const spec = normalizeSpec(null);
  assert.equal(spec.pages.length, 1);
  assert.equal(spec.pages[0].slug, 'index');
  assert.ok(spec.pages[0].blocks.length > 0);
  assert.ok(spec.meta.favicon);
});

test('drops unknown block types but keeps valid ones', () => {
  const spec = normalizeSpec({ pages: [{ blocks: [{ type: 'hero' }, { type: 'wat' }, { type: 'faq', props: { items: [{ q: 'Q', a: 'A' }] } }] }] });
  assert.deepEqual(spec.pages[0].blocks.map((b) => b.type), ['hero', 'faq']);
});

test('rejects invalid colors and falls back to the preset', () => {
  const spec = normalizeSpec({ theme: { preset: 'linen', colors: { primary: 'javascript:alert(1)', bg: '#123456' } } });
  assert.equal(spec.theme.colors.primary, THEME_PRESETS.linen.colors.primary);
  assert.equal(spec.theme.colors.bg, '#123456');
});

test('every theme preset ships valid hex colors', () => {
  for (const [key, preset] of Object.entries(THEME_PRESETS)) {
    for (const [name, value] of Object.entries(preset.colors)) {
      assert.match(value, /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i, `${key}.${name} is not a hex color`);
    }
  }
});

test('forces a single home page and unique slugs', () => {
  const spec = normalizeSpec({
    pages: [
      { slug: 'about', blocks: [{ type: 'hero' }] },
      { slug: 'index', blocks: [{ type: 'hero' }] },
      { slug: 'index', blocks: [{ type: 'hero' }] },
    ],
  });
  assert.equal(spec.pages[0].slug, 'index');
  assert.equal(new Set(spec.pages.map((p) => p.slug)).size, spec.pages.length);
});

test('falls back to a valid timezone when given nonsense', () => {
  assert.equal(normalizeSpec({ settings: { timezone: 'Mars/Olympus' } }).settings.timezone, 'America/New_York');
  assert.equal(normalizeSpec({ settings: { timezone: 'Europe/Lisbon' } }).settings.timezone, 'Europe/Lisbon');
});

test('reports which backends a spec needs', () => {
  const caps = specCapabilities(normalizeSpec({ pages: [{ blocks: [{ type: 'booking' }, { type: 'products' }] }] }));
  assert.equal(caps.booking, true);
  assert.equal(caps.payments, true);
});

test('clamps implausible service data', () => {
  const { services } = normalizeBusiness({
    services: [
      { name: 'Ok', price: 100, deposit: 500, durationMin: 90 },      // deposit over price
      { name: 'Long', price: 10, durationMin: 100000 },               // absurd duration
      { name: '', price: 10 },                                        // nameless
      { name: 'Neg', price: -5, capacity: 0 },                        // negative / zero
    ],
  });
  assert.equal(services.length, 3);
  assert.equal(services[0].depositCents, 10000);
  assert.equal(services[1].durationMin, 480);
  assert.equal(services[2].priceCents, 0);
  assert.equal(services[2].capacity, 1);
});

test('rejects a closing time that precedes opening', () => {
  const a = normalizeAvailability({ weekdays: [1], startTime: '18:00', endTime: '09:00' });
  assert.ok(a.endMin > a.startMin);
});

test('deduplicates and sorts weekdays', () => {
  const a = normalizeAvailability({ weekdays: [5, 1, 1, 9, -2, 3], startTime: '08:00', endTime: '12:00' });
  assert.deepEqual(a.weekdays, [1, 3, 5]);
});
