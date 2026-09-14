import { escapeHtml } from '../lib/http.js';
import { themeCss, fontLink, placeholderImage } from './theme.js';
import { widgetCss, runtimeJs } from './runtime.js';
import { renderBlock, formatMoney } from './blocks.js';
import { zonedParts } from '../lib/time.js';

const emojiFavicon = (emoji) =>
  `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><text y="50" font-size="52">${emoji}</text></svg>`)}`;

/** Gather the live business data a page's blocks may need. */
export function buildContext(db, site, spec) {
  const services = db
    .prepare('SELECT * FROM services WHERE site_id = ? AND active = 1 ORDER BY position, created_at')
    .all(site.id);
  const products = db
    .prepare('SELECT * FROM products WHERE site_id = ? AND active = 1 ORDER BY position, created_at')
    .all(site.id);
  const availability = db
    .prepare('SELECT weekday, start_min, end_min FROM availability WHERE site_id = ? AND service_id IS NULL ORDER BY weekday, start_min')
    .all(site.id);

  return {
    site,
    spec,
    theme: spec.theme,
    services,
    products,
    availability,
    currency: spec.settings.currency,
    timezone: spec.settings.timezone,
    todayWeekday: zonedParts(Date.now(), spec.settings.timezone).weekday,
  };
}

function renderHeader(spec) {
  const { nav } = spec;
  const links = nav.links
    .map((l) => `<a href="${escapeHtml(l.href)}">${escapeHtml(l.label)}</a>`)
    .join('');
  const cta = nav.cta
    ? `<a class="btn btn--primary btn--sm" href="${escapeHtml(nav.cta.href)}">${escapeHtml(nav.cta.label)}</a>`
    : '';
  return `<header class="hdr"><div class="wrap hdr__in">
    <a class="hdr__brand" href="/">${escapeHtml(nav.brand)}</a>
    ${links || cta ? `<button class="hdr__burger" data-burger aria-expanded="false" aria-label="Menu">☰</button><nav class="hdr__nav" data-nav data-open="0">${links}${cta}</nav>` : ''}
  </div></header>`;
}

function renderFooter(spec) {
  const links = spec.footer.links
    .map((l) => `<a href="${escapeHtml(l.href)}">${escapeHtml(l.label)}</a>`)
    .join('');
  const text = spec.footer.text || `© ${new Date().getFullYear()} ${spec.nav.brand}`;
  return `<footer class="ftr"><div class="wrap ftr__in">
    <div>${escapeHtml(text)}</div>
    <div>${links}</div>
  </div></footer>`;
}

/** Render one page of a site to a complete HTML document. */
export function renderSite(ctx, { pageSlug = 'index', apiBase } = {}) {
  const { spec } = ctx;
  const page = spec.pages.find((p) => p.slug === pageSlug) || spec.pages[0];
  const body = page.blocks.map((block) => renderBlock(block, ctx)).join('\n');

  const title = page.slug === 'index'
    ? spec.meta.title
    : `${page.title} — ${spec.meta.title}`;

  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(title)}</title>
${spec.meta.description ? `<meta name="description" content="${escapeHtml(spec.meta.description)}">` : ''}
<meta property="og:title" content="${escapeHtml(title)}">
${spec.meta.description ? `<meta property="og:description" content="${escapeHtml(spec.meta.description)}">` : ''}
<meta property="og:type" content="website">
<link rel="icon" href="${emojiFavicon(spec.meta.favicon)}">
${fontLink(spec.theme)}
<style>${themeCss(spec.theme)}${widgetCss}</style>
</head>
<body>
${renderHeader(spec)}
<main>
${body}
</main>
${renderFooter(spec)}
<script>${runtimeJs({
    siteId: ctx.site.id,
    apiBase: apiBase || `/_lk/api/${ctx.site.id}`,
    timezone: ctx.timezone,
    currency: ctx.currency,
  })}</script>
</body>
</html>`;
}

/** Post-checkout / post-booking confirmation page, themed to match the site. */
export function renderConfirmation(ctx, { heading, lines, backHref = '/' }) {
  const { spec } = ctx;
  return `<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(heading)} — ${escapeHtml(spec.meta.title)}</title>
<link rel="icon" href="${emojiFavicon(spec.meta.favicon)}">
${fontLink(spec.theme)}
<style>${themeCss(spec.theme)}
.confirm{min-height:100vh;display:grid;place-items:center;padding:32px 20px}
.confirm__card{max-width:520px;width:100%;text-align:center}
.confirm__tick{width:64px;height:64px;border-radius:50%;background:var(--tint);color:var(--accent);display:grid;place-items:center;font-size:2rem;margin:0 auto 20px}
.confirm__list{list-style:none;margin:0 0 26px;padding:0;color:var(--muted);display:flex;flex-direction:column;gap:8px}
</style>
</head><body><div class="confirm"><div class="card confirm__card">
<div class="confirm__tick">✓</div>
<h1 style="font-size:1.7rem">${escapeHtml(heading)}</h1>
<ul class="confirm__list">${lines.map((l) => `<li>${escapeHtml(l)}</li>`).join('')}</ul>
<a class="btn btn--primary" href="${escapeHtml(backHref)}">Back to ${escapeHtml(spec.nav.brand)}</a>
</div></div></body></html>`;
}

export { formatMoney, placeholderImage };
