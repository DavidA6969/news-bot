/**
 * The site specification: the single document that describes a published site.
 *
 * The AI writes one of these, the editor mutates it, and the renderer turns it
 * into HTML. Everything that reaches `normalizeSpec` comes out valid — model
 * output, hand edits, and older saved specs alike — so no other layer has to
 * defend against malformed input.
 */
import { id } from '../lib/ids.js';
import { timezone } from '../lib/validate.js';

export const SPEC_VERSION = 1;

export const BLOCK_TYPES = [
  'hero',
  'logos',
  'features',
  'services',
  'booking',
  'products',
  'pricing',
  'gallery',
  'stats',
  'testimonials',
  'team',
  'faq',
  'richtext',
  'hours',
  'contact',
  'cta',
];

export const THEME_PRESETS = {
  midnight: {
    label: 'Midnight',
    colors: { bg: '#0b1020', surface: '#141b33', text: '#eef2ff', muted: '#9aa6c8', primary: '#6c8cff', primaryText: '#ffffff', accent: '#39d6c3', border: '#243056' },
    fonts: { heading: "'Plus Jakarta Sans', system-ui, sans-serif", body: "system-ui, -apple-system, 'Segoe UI', sans-serif" },
    radius: 18,
  },
  linen: {
    label: 'Linen',
    colors: { bg: '#faf7f2', surface: '#ffffff', text: '#241f1a', muted: '#7a6f63', primary: '#b4552d', primaryText: '#ffffff', accent: '#3f6b52', border: '#e7ded1' },
    fonts: { heading: "'Fraunces', Georgia, serif", body: "system-ui, -apple-system, 'Segoe UI', sans-serif" },
    radius: 14,
  },
  clinic: {
    label: 'Clinic',
    colors: { bg: '#f6f9fc', surface: '#ffffff', text: '#0f2438', muted: '#5d7a92', primary: '#0f7ea8', primaryText: '#ffffff', accent: '#2bb673', border: '#dbe6ef' },
    fonts: { heading: "'Plus Jakarta Sans', system-ui, sans-serif", body: "system-ui, -apple-system, 'Segoe UI', sans-serif" },
    radius: 12,
  },
  studio: {
    label: 'Studio',
    colors: { bg: '#ffffff', surface: '#f6f6f4', text: '#111111', muted: '#6b6b68', primary: '#111111', primaryText: '#ffffff', accent: '#d94f2b', border: '#e4e4e0' },
    fonts: { heading: "'Space Grotesk', system-ui, sans-serif", body: "system-ui, -apple-system, 'Segoe UI', sans-serif" },
    radius: 4,
  },
  botanic: {
    label: 'Botanic',
    colors: { bg: '#f5f8f4', surface: '#ffffff', text: '#16251c', muted: '#5f7466', primary: '#2f6b45', primaryText: '#ffffff', accent: '#c98a2b', border: '#dbe6dc' },
    fonts: { heading: "'Fraunces', Georgia, serif", body: "system-ui, -apple-system, 'Segoe UI', sans-serif" },
    radius: 20,
  },
  neon: {
    label: 'Neon',
    colors: { bg: '#08090d', surface: '#13151d', text: '#f4f6ff', muted: '#8d94ad', primary: '#c3f53c', primaryText: '#0b0d12', accent: '#ff5fa2', border: '#242836' },
    fonts: { heading: "'Space Grotesk', system-ui, sans-serif", body: "system-ui, -apple-system, 'Segoe UI', sans-serif" },
    radius: 10,
  },
};

const HEX = /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i;

const asString = (v, max = 600) => (typeof v === 'string' ? v.trim().slice(0, max) : '');
const asArray = (v) => (Array.isArray(v) ? v : []);

function pickColor(value, fallback) {
  const v = asString(value, 32);
  return HEX.test(v) ? v : fallback;
}

function normalizeTheme(raw = {}) {
  const presetKey = Object.hasOwn(THEME_PRESETS, raw.preset) ? raw.preset : 'midnight';
  const preset = THEME_PRESETS[presetKey];
  const colors = {};
  for (const [key, fallback] of Object.entries(preset.colors)) {
    colors[key] = pickColor(raw.colors?.[key], fallback);
  }
  const radius = Number(raw.radius);
  return {
    preset: presetKey,
    colors,
    fonts: {
      heading: asString(raw.fonts?.heading, 160) || preset.fonts.heading,
      body: asString(raw.fonts?.body, 160) || preset.fonts.body,
    },
    radius: Number.isFinite(radius) ? Math.max(0, Math.min(32, Math.round(radius))) : preset.radius,
  };
}

function normalizeLink(raw = {}) {
  return {
    label: asString(raw.label, 60) || 'Link',
    href: asString(raw.href, 300) || '#',
  };
}

/** Per-block prop repair. Unknown block types are dropped by the caller. */
const BLOCK_NORMALIZERS = {
  hero: (p) => ({
    eyebrow: asString(p.eyebrow, 80),
    headline: asString(p.headline, 140) || 'A headline that earns the next scroll',
    subheadline: asString(p.subheadline, 400),
    layout: ['center', 'split'].includes(p.layout) ? p.layout : 'center',
    primaryCta: p.primaryCta ? normalizeLink(p.primaryCta) : { label: 'Book now', href: '#booking' },
    secondaryCta: p.secondaryCta ? normalizeLink(p.secondaryCta) : null,
    image: asString(p.image, 500),
    badges: asArray(p.badges).slice(0, 4).map((b) => asString(b, 60)).filter(Boolean),
  }),
  logos: (p) => ({
    label: asString(p.label, 120),
    items: asArray(p.items).slice(0, 8).map((x) => asString(x, 40)).filter(Boolean),
  }),
  features: (p) => ({
    heading: asString(p.heading, 140),
    subheading: asString(p.subheading, 300),
    columns: [2, 3, 4].includes(Number(p.columns)) ? Number(p.columns) : 3,
    items: asArray(p.items).slice(0, 12).map((it) => ({
      icon: asString(it?.icon, 8) || '●',
      title: asString(it?.title, 90) || 'Feature',
      body: asString(it?.body, 320),
    })),
  }),
  services: (p) => ({
    heading: asString(p.heading, 140) || 'Services',
    subheading: asString(p.subheading, 300),
    showPrices: p.showPrices !== false,
  }),
  booking: (p) => ({
    heading: asString(p.heading, 140) || 'Book an appointment',
    subheading: asString(p.subheading, 300),
  }),
  products: (p) => ({
    heading: asString(p.heading, 140) || 'Shop',
    subheading: asString(p.subheading, 300),
  }),
  pricing: (p) => ({
    heading: asString(p.heading, 140) || 'Pricing',
    subheading: asString(p.subheading, 300),
    tiers: asArray(p.tiers).slice(0, 4).map((t) => ({
      name: asString(t?.name, 60) || 'Plan',
      price: asString(t?.price, 40),
      cadence: asString(t?.cadence, 40),
      description: asString(t?.description, 240),
      featured: Boolean(t?.featured),
      features: asArray(t?.features).slice(0, 10).map((f) => asString(f, 120)).filter(Boolean),
      cta: t?.cta ? normalizeLink(t.cta) : { label: 'Get started', href: '#booking' },
      productId: asString(t?.productId, 40),
    })),
  }),
  gallery: (p) => ({
    heading: asString(p.heading, 140),
    subheading: asString(p.subheading, 300),
    items: asArray(p.items).slice(0, 12).map((it) => ({
      image: asString(it?.image, 500),
      caption: asString(it?.caption, 120),
    })),
  }),
  stats: (p) => ({
    items: asArray(p.items).slice(0, 4).map((it) => ({
      value: asString(it?.value, 20) || '—',
      label: asString(it?.label, 80),
    })),
  }),
  testimonials: (p) => ({
    heading: asString(p.heading, 140),
    items: asArray(p.items).slice(0, 6).map((it) => ({
      quote: asString(it?.quote, 400) || '',
      author: asString(it?.author, 80),
      role: asString(it?.role, 80),
    })).filter((it) => it.quote),
  }),
  team: (p) => ({
    heading: asString(p.heading, 140) || 'The team',
    items: asArray(p.items).slice(0, 8).map((it) => ({
      name: asString(it?.name, 80) || 'Team member',
      role: asString(it?.role, 80),
      bio: asString(it?.bio, 240),
      image: asString(it?.image, 500),
    })),
  }),
  faq: (p) => ({
    heading: asString(p.heading, 140) || 'Frequently asked',
    items: asArray(p.items).slice(0, 12).map((it) => ({
      q: asString(it?.q, 200) || '',
      a: asString(it?.a, 900) || '',
    })).filter((it) => it.q),
  }),
  richtext: (p) => ({
    heading: asString(p.heading, 140),
    body: asString(p.body, 2400),
    image: asString(p.image, 500),
    layout: ['left', 'right', 'full'].includes(p.layout) ? p.layout : 'full',
  }),
  hours: (p) => ({
    heading: asString(p.heading, 140) || 'Opening hours',
    note: asString(p.note, 240),
  }),
  contact: (p) => ({
    heading: asString(p.heading, 140) || 'Get in touch',
    subheading: asString(p.subheading, 300),
    address: asString(p.address, 240),
    phone: asString(p.phone, 60),
    email: asString(p.email, 160),
    showForm: p.showForm !== false,
  }),
  cta: (p) => ({
    headline: asString(p.headline, 140) || 'Ready when you are',
    body: asString(p.body, 300),
    cta: p.cta ? normalizeLink(p.cta) : { label: 'Book now', href: '#booking' },
  }),
};

function normalizeBlock(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const type = asString(raw.type, 30);
  if (!BLOCK_TYPES.includes(type)) return null;
  return {
    id: asString(raw.id, 40) || id('blk'),
    type,
    props: BLOCK_NORMALIZERS[type](raw.props && typeof raw.props === 'object' ? raw.props : {}),
  };
}

function normalizePage(raw, index) {
  const slug = (asString(raw?.slug, 40) || (index === 0 ? 'index' : `page-${index + 1}`))
    .toLowerCase()
    .replace(/[^a-z0-9-]/g, '-')
    .replace(/^-+|-+$/g, '') || `page-${index + 1}`;
  const blocks = asArray(raw?.blocks).map(normalizeBlock).filter(Boolean);
  return {
    slug: index === 0 ? 'index' : slug,
    title: asString(raw?.title, 120) || (index === 0 ? 'Home' : slug),
    blocks,
  };
}

export function normalizeSpec(raw) {
  const input = raw && typeof raw === 'object' ? raw : {};
  const pages = asArray(input.pages).map(normalizePage).filter((p) => p.blocks.length);
  if (!pages.length) {
    pages.push({ slug: 'index', title: 'Home', blocks: [normalizeBlock({ type: 'hero', props: {} })] });
  }
  // Only one home page, and it must come first.
  const home = pages.shift();
  const seen = new Set(['index']);
  const rest = [];
  for (const page of pages) {
    let slug = page.slug === 'index' ? 'page' : page.slug;
    while (seen.has(slug)) slug = `${slug}-2`;
    seen.add(slug);
    rest.push({ ...page, slug });
  }

  const currency = asString(input.settings?.currency, 3).toLowerCase() || 'usd';

  return {
    version: SPEC_VERSION,
    meta: {
      title: asString(input.meta?.title, 120) || 'New site',
      description: asString(input.meta?.description, 300),
      favicon: asString(input.meta?.favicon, 8) || '🚀',
    },
    settings: {
      timezone: timezone(input.settings?.timezone, 'America/New_York'),
      currency: /^[a-z]{3}$/.test(currency) ? currency : 'usd',
      contactEmail: asString(input.settings?.contactEmail, 160),
      businessName: asString(input.settings?.businessName, 120),
    },
    theme: normalizeTheme(input.theme),
    nav: {
      brand: asString(input.nav?.brand, 60) || asString(input.meta?.title, 60) || 'Brand',
      links: asArray(input.nav?.links).slice(0, 6).map(normalizeLink),
      cta: input.nav?.cta ? normalizeLink(input.nav.cta) : null,
    },
    footer: {
      text: asString(input.footer?.text, 300),
      links: asArray(input.footer?.links).slice(0, 6).map(normalizeLink),
    },
    pages: [home, ...rest],
  };
}

/** Does this spec use a feature that needs the booking or payments backend? */
export function specCapabilities(spec) {
  const types = new Set();
  for (const page of spec.pages) for (const block of page.blocks) types.add(block.type);
  return {
    booking: types.has('booking') || types.has('services') || types.has('hours'),
    payments: types.has('products') || types.has('pricing'),
    contact: types.has('contact'),
  };
}
