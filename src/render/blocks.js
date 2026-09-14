import { escapeHtml } from '../lib/http.js';
import { placeholderImage } from './theme.js';
import { WEEKDAYS } from '../lib/time.js';

export function formatMoney(cents, currency = 'usd') {
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: currency.toUpperCase(),
      minimumFractionDigits: cents % 100 === 0 ? 0 : 2,
    }).format(cents / 100);
  } catch {
    return `$${(cents / 100).toFixed(2)}`;
  }
}

export function formatDuration(min) {
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m ? `${h}h ${m}m` : `${h}h`;
}

/** Paragraph-split plain text, escaped. */
const paragraphs = (text) =>
  String(text || '')
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => `<p>${escapeHtml(p).replace(/\n/g, '<br>')}</p>`)
    .join('');

const img = (src, alt, theme, seed, cls = '') =>
  `<img src="${escapeHtml(src || placeholderImage(theme, seed))}" alt="${escapeHtml(alt)}"${cls ? ` class="${cls}"` : ''} loading="lazy">`;

const sectionHead = (heading, subheading) =>
  heading || subheading
    ? `<div class="sec__head">${heading ? `<h2>${escapeHtml(heading)}</h2>` : ''}${subheading ? `<p>${escapeHtml(subheading)}</p>` : ''}</div>`
    : '';

const link = (l, cls) =>
  `<a class="${cls}" href="${escapeHtml(l.href)}">${escapeHtml(l.label)}</a>`;

/** Anchor id so nav links like "#booking" land on the right section. */
const anchor = (block) => `id="${escapeHtml(block.type)}" data-block="${escapeHtml(block.id)}"`;

const RENDERERS = {
  hero(block, ctx) {
    const p = block.props;
    const media = p.layout === 'split'
      ? `<div class="hero__media">${img(p.image, ctx.spec.meta.title, ctx.theme, block.id)}</div>`
      : '';
    const badges = p.badges.length
      ? `<ul class="hero__badges">${p.badges.map((b) => `<li>${escapeHtml(b)}</li>`).join('')}</ul>`
      : '';
    return `<section class="hero hero--${p.layout}" ${anchor(block)}><div class="wrap hero__in"><div class="hero__copy">
      ${p.eyebrow ? `<span class="eyebrow">${escapeHtml(p.eyebrow)}</span>` : ''}
      <h1>${escapeHtml(p.headline)}</h1>
      ${p.subheadline ? `<p class="hero__sub">${escapeHtml(p.subheadline)}</p>` : ''}
      <div class="hero__cta">${link(p.primaryCta, 'btn btn--primary')}${p.secondaryCta ? link(p.secondaryCta, 'btn btn--ghost') : ''}</div>
      ${badges}
    </div>${media}</div></section>`;
  },

  logos(block) {
    const p = block.props;
    if (!p.items.length) return '';
    return `<section class="wrap" ${anchor(block)}><div class="logos">
      ${p.label ? `<div class="logos__label">${escapeHtml(p.label)}</div>` : ''}
      ${p.items.map((i) => `<span>${escapeHtml(i)}</span>`).join('')}
    </div></section>`;
  },

  stats(block) {
    const items = block.props.items;
    if (!items.length) return '';
    return `<section class="sec" ${anchor(block)}><div class="wrap"><div class="stats">
      ${items.map((s) => `<div><b>${escapeHtml(s.value)}</b><span>${escapeHtml(s.label)}</span></div>`).join('')}
    </div></div></section>`;
  },

  features(block) {
    const p = block.props;
    if (!p.items.length) return '';
    return `<section class="sec" ${anchor(block)}><div class="wrap">
      ${sectionHead(p.heading, p.subheading)}
      <div class="grid grid--${p.columns}">
        ${p.items.map((f) => `<div class="feat"><div class="feat__icon">${escapeHtml(f.icon)}</div><h3>${escapeHtml(f.title)}</h3><p>${escapeHtml(f.body)}</p></div>`).join('')}
      </div></div></section>`;
  },

  services(block, ctx) {
    const p = block.props;
    if (!ctx.services.length) return '';
    return `<section class="sec sec--tint" ${anchor(block)}><div class="wrap">
      ${sectionHead(p.heading, p.subheading)}
      <div class="grid grid--3">
        ${ctx.services.map((s) => {
          const price = s.price_cents > 0 ? formatMoney(s.price_cents, ctx.currency) : 'Free';
          const meta = [`<span>${formatDuration(s.duration_min)}</span>`];
          if (s.capacity > 1) meta.push(`<span>Up to ${s.capacity} people</span>`);
          if (s.deposit_cents > 0) meta.push(`<span>${formatMoney(s.deposit_cents, ctx.currency)} deposit</span>`);
          return `<article class="card svc">
            <div class="svc__top"><h3>${escapeHtml(s.name)}</h3>${p.showPrices ? `<div class="svc__price">${price}</div>` : ''}</div>
            ${s.description ? `<p>${escapeHtml(s.description)}</p>` : ''}
            <div class="svc__meta">${meta.join('')}</div>
            <button class="btn btn--primary btn--sm" data-book-service="${escapeHtml(s.id)}">Book this</button>
          </article>`;
        }).join('')}
      </div></div></section>`;
  },

  booking(block, ctx) {
    const p = block.props;
    if (!ctx.services.length) return '';
    return `<section class="sec" ${anchor(block)}><div class="wrap">
      ${sectionHead(p.heading, p.subheading)}
      <div class="card" id="lk-booking" data-booking>
        <div class="lk-book__step" data-step="service">
          <label class="field"><span>Service</span>
            <select data-book-select>
              ${ctx.services.map((s) => `<option value="${escapeHtml(s.id)}">${escapeHtml(s.name)} — ${formatDuration(s.duration_min)}${s.price_cents > 0 ? ` — ${formatMoney(s.price_cents, ctx.currency)}` : ''}</option>`).join('')}
            </select>
          </label>
        </div>
        <div data-book-calendar class="lk-book__calendar"><p class="muted">Loading availability…</p></div>
        <div data-book-slots class="lk-book__slots"></div>
        <form data-book-form hidden>
          <div class="field--row">
            <div class="field"><label for="lk-name">Your name</label><input id="lk-name" name="name" required maxlength="120" autocomplete="name"></div>
            <div class="field"><label for="lk-email">Email</label><input id="lk-email" name="email" type="email" required maxlength="320" autocomplete="email"></div>
          </div>
          <div class="field"><label for="lk-phone">Phone <span class="muted">(optional)</span></label><input id="lk-phone" name="phone" maxlength="40" autocomplete="tel"></div>
          <div class="field"><label for="lk-notes">Anything we should know? <span class="muted">(optional)</span></label><textarea id="lk-notes" name="notes" maxlength="1000"></textarea></div>
          <button class="btn btn--primary btn--block" type="submit" data-book-submit>Confirm booking</button>
        </form>
        <div data-book-status role="status" aria-live="polite"></div>
      </div>
    </div></section>`;
  },

  products(block, ctx) {
    const p = block.props;
    if (!ctx.products.length) return '';
    return `<section class="sec sec--tint" ${anchor(block)}><div class="wrap">
      ${sectionHead(p.heading, p.subheading)}
      <div class="grid grid--3">
        ${ctx.products.map((prod) => {
          const soldOut = prod.inventory !== null && prod.inventory <= 0;
          const stock = prod.inventory === null ? '' : soldOut ? 'Sold out' : `${prod.inventory} left`;
          return `<article class="card prod">
            ${img(prod.image, prod.name, ctx.theme, prod.id)}
            <div class="prod__body">
              <h3>${escapeHtml(prod.name)}</h3>
              ${prod.description ? `<p>${escapeHtml(prod.description)}</p>` : ''}
              <div class="prod__foot">
                <div><div class="prod__price">${formatMoney(prod.price_cents, ctx.currency)}</div>${stock ? `<div class="prod__stock">${escapeHtml(stock)}</div>` : ''}</div>
                <button class="btn btn--primary btn--sm" data-buy-product="${escapeHtml(prod.id)}"${soldOut ? ' disabled' : ''}>${soldOut ? 'Sold out' : 'Buy'}</button>
              </div>
            </div>
          </article>`;
        }).join('')}
      </div>
      <div data-shop-status role="status" aria-live="polite"></div>
    </div></section>`;
  },

  pricing(block, ctx) {
    const p = block.props;
    if (!p.tiers.length) return '';
    return `<section class="sec" ${anchor(block)}><div class="wrap">
      ${sectionHead(p.heading, p.subheading)}
      <div class="grid grid--${Math.min(p.tiers.length, 4)}">
        ${p.tiers.map((t) => `<article class="card tier${t.featured ? ' tier--featured' : ''}">
          <h3>${escapeHtml(t.name)}</h3>
          <div class="tier__price">${escapeHtml(t.price)}${t.cadence ? `<small> ${escapeHtml(t.cadence)}</small>` : ''}</div>
          ${t.description ? `<p class="muted">${escapeHtml(t.description)}</p>` : ''}
          ${t.features.length ? `<ul>${t.features.map((f) => `<li>${escapeHtml(f)}</li>`).join('')}</ul>` : ''}
          ${t.productId
            ? `<button class="btn btn--primary" data-buy-product="${escapeHtml(t.productId)}">${escapeHtml(t.cta.label)}</button>`
            : link(t.cta, 'btn btn--primary')}
        </article>`).join('')}
      </div>
      <div data-shop-status role="status" aria-live="polite"></div>
    </div></section>`;
  },

  gallery(block, ctx) {
    const p = block.props;
    if (!p.items.length) return '';
    return `<section class="sec" ${anchor(block)}><div class="wrap">
      ${sectionHead(p.heading, p.subheading)}
      <div class="grid grid--3 gal">
        ${p.items.map((it, i) => `<figure>${img(it.image, it.caption || 'Gallery image', ctx.theme, `${block.id}-${i}`)}${it.caption ? `<figcaption>${escapeHtml(it.caption)}</figcaption>` : ''}</figure>`).join('')}
      </div></div></section>`;
  },

  testimonials(block) {
    const p = block.props;
    if (!p.items.length) return '';
    return `<section class="sec sec--tint" ${anchor(block)}><div class="wrap">
      ${sectionHead(p.heading, '')}
      <div class="grid grid--${Math.min(p.items.length, 3)}">
        ${p.items.map((t) => `<blockquote class="card quote"><p>“${escapeHtml(t.quote)}”</p><footer><strong>${escapeHtml(t.author)}</strong>${t.role ? escapeHtml(t.role) : ''}</footer></blockquote>`).join('')}
      </div></div></section>`;
  },

  team(block, ctx) {
    const p = block.props;
    if (!p.items.length) return '';
    return `<section class="sec" ${anchor(block)}><div class="wrap">
      ${sectionHead(p.heading, '')}
      <div class="grid grid--${Math.min(p.items.length, 4)}">
        ${p.items.map((m, i) => `<div class="card team__card">${img(m.image, m.name, ctx.theme, `${block.id}-${i}`)}<h3>${escapeHtml(m.name)}</h3><div class="role">${escapeHtml(m.role)}</div>${m.bio ? `<p>${escapeHtml(m.bio)}</p>` : ''}</div>`).join('')}
      </div></div></section>`;
  },

  faq(block) {
    const p = block.props;
    if (!p.items.length) return '';
    return `<section class="sec" ${anchor(block)}><div class="wrap">
      ${sectionHead(p.heading, '')}
      <div class="faq">
        ${p.items.map((f) => `<details><summary>${escapeHtml(f.q)}</summary><p>${escapeHtml(f.a)}</p></details>`).join('')}
      </div></div></section>`;
  },

  richtext(block, ctx) {
    const p = block.props;
    const body = `<div class="rich__body">${p.heading ? `<h2>${escapeHtml(p.heading)}</h2>` : ''}${paragraphs(p.body)}</div>`;
    if (p.layout === 'full') {
      return `<section class="sec" ${anchor(block)}><div class="wrap" style="max-width:760px">${body}</div></section>`;
    }
    return `<section class="sec" ${anchor(block)}><div class="wrap rich rich--${p.layout}">
      ${body}<div class="rich__media">${img(p.image, p.heading || '', ctx.theme, block.id)}</div>
    </div></section>`;
  },

  hours(block, ctx) {
    const p = block.props;
    const byDay = new Map();
    for (const rule of ctx.availability) {
      if (!byDay.has(rule.weekday)) byDay.set(rule.weekday, []);
      byDay.get(rule.weekday).push(rule);
    }
    const fmt = (m) => {
      const h = Math.floor(m / 60);
      const mm = String(m % 60).padStart(2, '0');
      const suffix = h < 12 ? 'am' : 'pm';
      return `${h % 12 === 0 ? 12 : h % 12}:${mm}${suffix}`;
    };
    const rows = WEEKDAYS.map((label, day) => {
      const windows = (byDay.get(day) || []).sort((a, b) => a.start_min - b.start_min);
      const value = windows.length
        ? windows.map((w) => `${fmt(w.start_min)} – ${fmt(w.end_min)}`).join(', ')
        : '<span class="closed">Closed</span>';
      return `<li data-today="${day === ctx.todayWeekday ? 1 : 0}"><span>${label}</span><span>${value}</span></li>`;
    }).join('');
    return `<section class="sec sec--tint" ${anchor(block)}><div class="wrap">
      ${sectionHead(p.heading, '')}
      <ul class="hours">${rows}</ul>
      ${p.note ? `<p class="muted" style="margin-top:16px">${escapeHtml(p.note)}</p>` : ''}
    </div></section>`;
  },

  contact(block, ctx) {
    const p = block.props;
    const details = [];
    if (p.address) details.push(`<li><span>📍</span><span>${escapeHtml(p.address)}</span></li>`);
    if (p.phone) details.push(`<li><span>📞</span><a href="tel:${escapeHtml(p.phone.replace(/[^\d+]/g, ''))}">${escapeHtml(p.phone)}</a></li>`);
    const mail = p.email || ctx.spec.settings.contactEmail;
    if (mail) details.push(`<li><span>✉️</span><a href="mailto:${escapeHtml(mail)}">${escapeHtml(mail)}</a></li>`);

    const form = p.showForm
      ? `<form class="card" data-contact-form>
          <div class="field"><label for="lk-c-name">Name</label><input id="lk-c-name" name="name" required maxlength="120"></div>
          <div class="field"><label for="lk-c-email">Email</label><input id="lk-c-email" name="email" type="email" required maxlength="320"></div>
          <div class="field"><label for="lk-c-body">Message</label><textarea id="lk-c-body" name="body" required maxlength="2000"></textarea></div>
          <button class="btn btn--primary btn--block" type="submit">Send message</button>
          <div data-contact-status role="status" aria-live="polite"></div>
        </form>`
      : '';

    return `<section class="sec" ${anchor(block)}><div class="wrap"><div class="contact">
      <div>${sectionHead(p.heading, p.subheading)}${details.length ? `<ul class="contact__details">${details.join('')}</ul>` : ''}</div>
      ${form}
    </div></div></section>`;
  },

  cta(block) {
    const p = block.props;
    return `<section class="sec" ${anchor(block)}><div class="wrap"><div class="band">
      <h2>${escapeHtml(p.headline)}</h2>
      ${p.body ? `<p>${escapeHtml(p.body)}</p>` : ''}
      ${link(p.cta, 'btn')}
    </div></div></section>`;
  },
};

export function renderBlock(block, ctx) {
  const fn = RENDERERS[block.type];
  return fn ? fn(block, ctx) : '';
}
