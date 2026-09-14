import test from 'node:test';
import assert from 'node:assert/strict';
import { createMemoryDb } from '../src/db/index.js';
import { normalizeSpec } from '../src/ai/spec.js';
import { generateFromTemplate, ARCHETYPE_KEYS } from '../src/ai/templates.js';
import { buildContext, renderSite } from '../src/render/renderer.js';
import { runtimeJs } from '../src/render/runtime.js';
import { themeCss, isDark, luminance } from '../src/render/theme.js';

function site() {
  const db = createMemoryDb();
  const now = Date.now();
  db.prepare('INSERT INTO users (id,email,password_hash,name,created_at) VALUES (?,?,?,?,?)').run('u1', 'a@b.co', 'x', '', now);
  db.prepare('INSERT INTO sites (id,user_id,name,slug,prompt,spec,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)')
    .run('s1', 'u1', 'T', 't', '', '{}', now, now);
  return { db, row: db.prepare('SELECT * FROM sites WHERE id = ?').get('s1') };
}

test('escapes hostile copy instead of emitting it as markup', () => {
  const { db, row } = site();
  const spec = normalizeSpec({
    meta: { title: '</title><script>alert(1)</script>' },
    nav: { brand: '"><img src=x onerror=alert(1)>' },
    pages: [{ blocks: [{ type: 'hero', props: { headline: '<script>alert("xss")</script>', subheadline: 'a " quote' } }] }],
  });
  const html = renderSite(buildContext(db, row, spec), {});

  // Hostile copy must survive only as inert text: no injected tags, no
  // attribute break-out. The escaped characters are what proves it.
  assert.ok(!html.includes('<script>alert(1)</script>'), 'no injected script tag');
  assert.ok(!html.includes('<script>alert("xss")</script>'), 'no injected script tag');
  assert.ok(!/<img\s/i.test(html), 'no injected img tag');
  assert.ok(!/<[a-z]+[^>]*\sonerror=/i.test(html), 'no event handler attribute');
  assert.match(html, /&lt;script&gt;/, 'angle brackets escaped');
  assert.match(html, /&quot;&gt;&lt;img src=x onerror=alert\(1\)&gt;/, 'brand rendered as text');

  // The document must still be well-formed: exactly the tags we emitted.
  const scriptOpens = (html.match(/<script\b/gi) || []).length;
  assert.equal(scriptOpens, 1, 'only the runtime script tag exists');
});

test('renders every block type without throwing', () => {
  const { db, row } = site();
  const blocks = [
    { type: 'hero', props: { headline: 'H', layout: 'split' } },
    { type: 'logos', props: { items: ['A', 'B'] } },
    { type: 'stats', props: { items: [{ value: '10', label: 'x' }] } },
    { type: 'features', props: { heading: 'F', items: [{ icon: '★', title: 'T', body: 'B' }] } },
    { type: 'pricing', props: { tiers: [{ name: 'Pro', price: '$9', features: ['a'] }] } },
    { type: 'gallery', props: { items: [{ caption: 'C' }] } },
    { type: 'testimonials', props: { items: [{ quote: 'Q', author: 'A' }] } },
    { type: 'team', props: { items: [{ name: 'N', role: 'R' }] } },
    { type: 'faq', props: { items: [{ q: 'Q', a: 'A' }] } },
    { type: 'richtext', props: { body: 'One.\n\nTwo.', layout: 'left' } },
    { type: 'hours', props: {} },
    { type: 'contact', props: {} },
    { type: 'cta', props: { headline: 'Go' } },
  ];
  const html = renderSite(buildContext(db, row, normalizeSpec({ pages: [{ blocks }] })), {});
  assert.match(html, /<!doctype html>/i);
  assert.match(html, /<\/html>/);
  assert.ok(html.length > 4000);
});

test('booking and product blocks stay out of the page when there is nothing to sell', () => {
  const { db, row } = site();
  const spec = normalizeSpec({ pages: [{ blocks: [{ type: 'hero', props: {} }, { type: 'booking', props: {} }, { type: 'products', props: {} }] }] });
  const html = renderSite(buildContext(db, row, spec), {});
  // Assert on the rendered sections — the runtime script always carries these
  // selector strings, so a bare substring check would match itself.
  assert.ok(!/<section[^>]*id="booking"/.test(html), 'no calendar section without services');
  assert.ok(!/<section[^>]*id="products"/.test(html), 'no store section without products');
  assert.ok(!/<button[^>]*data-buy-product/.test(html), 'no buy button without products');
  assert.match(html, /<section class="hero/, 'the hero still renders');
});

test('the embedded runtime is valid JavaScript', () => {
  const js = runtimeJs({ siteId: 's1', apiBase: '/_lk/api/s1', timezone: 'UTC', currency: 'usd' });
  assert.doesNotThrow(() => new Function(js));
});

test('every archetype produces a renderable site', () => {
  for (const prompt of ['barber shop', 'yoga studio', 'dental clinic', 'restaurant', 'wedding photographer', 'business consultant', 'massage spa', 'plumber', 'dog grooming', 'something unusual']) {
    const { db, row } = site();
    const { spec, business } = generateFromTemplate({ prompt });
    assert.ok(business.services.length > 0, `${prompt} has services`);
    assert.ok(business.availability.weekdays.length > 0, `${prompt} is open some days`);
    const html = renderSite(buildContext(db, row, spec), {});
    assert.match(html, /<!doctype html>/i);
    assert.ok(!/lorem ipsum|your headline here|\[business name\]/i.test(html), `${prompt} has no placeholder copy`);
  }
  assert.ok(ARCHETYPE_KEYS.length >= 10);
});

test('theme text stays readable against its background', () => {
  const { spec } = generateFromTemplate({ prompt: 'barber shop' });
  const { colors } = spec.theme;
  // Light text on dark ground, or dark text on light ground — never the same side.
  assert.notEqual(isDark(colors.bg), isDark(colors.text));
  assert.notEqual(isDark(colors.primary), isDark(colors.primaryText));
  assert.ok(Math.abs(luminance(colors.bg) - luminance(colors.text)) > 0.4);
});

test('generated CSS carries the theme tokens', () => {
  const css = themeCss(normalizeSpec({ theme: { preset: 'neon' } }).theme);
  assert.match(css, /--primary:#c3f53c/);
  assert.match(css, /prefers-reduced-motion/);
  assert.match(css, /@media \(max-width:640px\)/);
});

test('a nav CTA button keeps its own colours, not the muted link colour', () => {
  const css = themeCss(normalizeSpec({ theme: { preset: 'linen' } }).theme);
  // `.hdr__nav a` would out-specify `.btn--primary` and render the CTA's
  // label in muted ink on its own primary background.
  assert.ok(!/\.hdr__nav a\{[^}]*color:var\(--muted\)/.test(css),
    'nav link colour must not apply to buttons');
  assert.match(css, /\.hdr__nav a:not\(\.btn\)\{[^}]*color:var\(--muted\)/);
});

test('webfonts never block first paint', async () => {
  const { fontLink } = await import('../src/render/theme.js');
  const link = fontLink(normalizeSpec({ theme: { preset: 'linen' } }).theme);
  assert.match(link, /media="print"/, 'stylesheet is deferred');
  assert.match(link, /onload="this\.media='all'/, 'promoted once loaded');
  assert.match(link, /<noscript>/, 'still styled without JS');
  assert.match(link, /display=swap/, 'fallback text shows immediately');
});

test('a split hero leads with copy on narrow screens', () => {
  const css = themeCss(normalizeSpec({ theme: {} }).theme);
  // Pulling the media above the copy would bury the headline below a
  // decorative placeholder on phones.
  assert.ok(!/\.hero__media\{order:-1\}/.test(css), 'hero media must not be reordered above the copy');
});
