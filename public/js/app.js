import { h, frag, mount, toast, modal, confirmDialog, withBusy, money, duration, minutesToLabel, WEEKDAYS } from './dom.js';
import { api } from './api.js';

const root = document.getElementById('root');
const state = { user: null, sites: [], siteId: null, detail: null, tab: 'overview', device: 'desktop' };

/* ── Routing ──────────────────────────────────────────────────────────── */
function readHash() {
  const m = /^#\/site\/([^/]+)(?:\/([a-z]+))?/.exec(location.hash);
  return m ? { siteId: m[1], tab: m[2] || 'overview' } : null;
}
function goto(siteId, tab = 'overview') {
  location.hash = siteId ? `#/site/${siteId}/${tab}` : '';
}
window.addEventListener('hashchange', render);

/* ── Boot ─────────────────────────────────────────────────────────────── */
(async function boot() {
  try {
    const { user } = await api.get('/api/auth/me');
    state.user = user;
  } catch { state.user = null; }
  await render();
})();

async function render() {
  if (!state.user) return renderAuth();

  const route = readHash();
  if (!route) {
    state.siteId = null;
    state.detail = null;
    await loadSites();
    return renderDashboard();
  }

  state.tab = route.tab;
  if (state.siteId !== route.siteId || !state.detail) {
    state.siteId = route.siteId;
    mount(root, h('div', { class: 'auth' }, h('div', { class: 'spin', style: { width: '26px', height: '26px' } })));
    try {
      state.detail = await api.get(`/api/sites/${route.siteId}`);
    } catch (err) {
      toast(err.message, 'err');
      location.hash = '';
      return;
    }
  }
  renderEditor();
}

async function reload() {
  state.detail = await api.get(`/api/sites/${state.siteId}`);
  renderEditor();
}

async function loadSites() {
  const { sites } = await api.get('/api/sites');
  state.sites = sites;
}

/* ── Auth ─────────────────────────────────────────────────────────────── */
function renderAuth() {
  let mode = 'signin';

  const draw = () => {
    const form = h('form', {
      onsubmit: async (e) => {
        e.preventDefault();
        const btn = form.querySelector('button[type=submit]');
        const body = {
          email: form.elements.email.value,
          password: form.elements.password.value,
          ...(mode === 'signup' ? { name: form.elements.name.value } : {}),
        };
        try {
          await withBusy(btn, mode === 'signup' ? 'Creating…' : 'Signing in…', () =>
            api.post(`/api/auth/${mode === 'signup' ? 'signup' : 'login'}`, body));
          const { user } = await api.get('/api/auth/me');
          state.user = user;
          await render();
        } catch (err) {
          toast(err.message, 'err');
        }
      },
    },
      mode === 'signup'
        ? h('div', { class: 'field' }, h('label', { for: 'f-name' }, 'Your name'),
            h('input', { id: 'f-name', name: 'name', autocomplete: 'name', maxlength: '120' }))
        : null,
      h('div', { class: 'field' }, h('label', { for: 'f-email' }, 'Email'),
        h('input', { id: 'f-email', name: 'email', type: 'email', required: true, autocomplete: 'email' })),
      h('div', { class: 'field' }, h('label', { for: 'f-pass' }, 'Password'),
        h('input', {
          id: 'f-pass', name: 'password', type: 'password', required: true,
          minlength: mode === 'signup' ? '8' : '1',
          autocomplete: mode === 'signup' ? 'new-password' : 'current-password',
        }),
        mode === 'signup' ? h('span', { class: 'small muted' }, 'At least 8 characters.') : null),
      h('button', { class: 'btn btn--primary btn--block', type: 'submit' },
        mode === 'signup' ? 'Create account' : 'Sign in'),
    );

    const tab = (key, label) => h('button', {
      type: 'button', 'aria-selected': String(mode === key),
      onclick: () => { mode = key; draw(); },
    }, label);

    mount(root, h('div', { class: 'auth' }, h('div', { class: 'card auth__card' },
      h('div', { class: 'brand', style: { marginBottom: '18px' } }, h('span', { class: 'brand__mark' }, '🚀'), 'LaunchKit'),
      h('div', { class: 'auth__tabs' }, tab('signin', 'Sign in'), tab('signup', 'Create account')),
      form,
      h('p', { class: 'small muted center', style: { margin: '16px 0 0' } },
        'Sites you build stay private until you publish them.'),
    )));
  };
  draw();
}

/* ── Dashboard ────────────────────────────────────────────────────────── */
function renderDashboard() {
  const promptBox = h('textarea', {
    id: 'gen-prompt',
    placeholder: 'A hair salon in Portland. We do balayage, cuts and beard trims, take a deposit on colour appointments, and sell shampoo at the front desk.',
    maxlength: '4000',
  });
  const nameBox = h('input', { id: 'gen-name', placeholder: 'Optional — leave blank and we will name it', maxlength: '120' });

  const generate = async (ev) => {
    const prompt = promptBox.value.trim();
    if (prompt.length < 8) { toast('Tell us a little more about the business', 'err'); promptBox.focus(); return; }
    try {
      const result = await withBusy(ev.currentTarget, 'Building your site…', () =>
        api.post('/api/sites/generate', { prompt, businessName: nameBox.value.trim() }));
      if (result.generation.source === 'template') {
        toast('Built from the template engine — add ANTHROPIC_API_KEY for bespoke copy.');
      } else {
        toast('Your site is ready', 'ok');
      }
      goto(result.site.id, 'design');
    } catch (err) {
      toast(err.message, 'err');
    }
  };

  const examples = [
    'A yoga studio with drop-in classes capped at 12 and a 10-class pass',
    'A dentist taking new-patient consultations with a deposit',
    'A tattoo studio — consultations free, sessions need a deposit',
    'A wine bar taking dinner reservations and selling gift cards',
  ];

  mount(root, h('div', { class: 'shell' },
    h('header', { class: 'topbar' }, h('div', { class: 'topbar__in' },
      h('a', { class: 'brand', href: '/' }, h('span', { class: 'brand__mark' }, '🚀'), 'LaunchKit'),
      h('div', { class: 'spacer' }),
      h('span', { class: 'small muted' }, state.user.email),
      h('button', {
        class: 'btn btn--ghost btn--sm',
        onclick: async () => { await api.post('/api/auth/logout'); state.user = null; location.hash = ''; render(); },
      }, 'Sign out'),
    )),

    h('div', { class: 'content', style: { maxWidth: '1100px', margin: '0 auto', width: '100%' } },
      h('div', { class: 'card', style: { marginBottom: '30px' } },
        h('h2', { style: { marginBottom: '4px' } }, 'What are you building?'),
        h('p', { class: 'muted small', style: { marginBottom: '18px' } },
          'Describe the business the way you would to a friend. Services, prices, opening hours and a matching theme all come back filled in.'),
        h('div', { class: 'field' }, h('label', { for: 'gen-prompt' }, 'Your business'), promptBox),
        h('div', { class: 'field' }, h('label', { for: 'gen-name' }, 'Business name'), nameBox),
        h('div', { class: 'row row--wrap' },
          h('button', { class: 'btn btn--primary btn--lg', onclick: generate }, 'Generate my site'),
          h('span', { class: 'small muted' }, 'Takes about twenty seconds.'),
        ),
        h('div', { class: 'row row--wrap', style: { marginTop: '16px', gap: '7px' } },
          h('span', { class: 'small muted' }, 'Try:'),
          ...examples.map((ex) => h('button', {
            class: 'btn btn--ghost btn--sm',
            onclick: () => { promptBox.value = ex; promptBox.focus(); },
          }, ex.length > 42 ? `${ex.slice(0, 40)}…` : ex)),
        ),
      ),

      h('h3', { style: { marginBottom: '14px' } }, `Your sites${state.sites.length ? ` (${state.sites.length})` : ''}`),
      state.sites.length
        ? h('div', { class: 'sites' }, ...state.sites.map(siteCard))
        : h('div', { class: 'card empty' },
            h('div', { class: 'empty__icon' }, '🌱'),
            h('p', null, 'Nothing here yet. Describe a business above and you will have a working site in under a minute.')),
    ),
  ));
}

function siteCard(site) {
  const c = site.theme.colors;
  return h('article', {
    class: 'sitecard', tabindex: '0', role: 'button',
    onclick: () => goto(site.id, 'design'),
    onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); goto(site.id, 'design'); } },
  },
    h('div', { class: 'sitecard__top', style: { background: `linear-gradient(135deg, ${c.primary}, ${c.accent})` } }, site.favicon),
    h('div', { class: 'sitecard__body' },
      h('h3', null, site.title),
      h('span', { class: 'small muted' }, site.published ? site.url.replace(/^https?:\/\//, '') : 'Draft — not published'),
      h('div', { class: 'sitecard__meta' },
        h('span', { class: `pill ${site.published ? 'pill--ok' : ''}` }, site.published ? 'Live' : 'Draft'),
      ),
    ),
  );
}

/* ── Editor shell ─────────────────────────────────────────────────────── */
const TABS = [
  { key: 'overview', label: 'Overview', icon: '◆' },
  { key: 'design', label: 'Design', icon: '✎' },
  { key: 'booking', label: 'Booking', icon: '📅' },
  { key: 'store', label: 'Store', icon: '🛍️' },
  { key: 'calendar', label: 'Bookings', icon: '🗓️' },
  { key: 'orders', label: 'Orders', icon: '💳' },
  { key: 'inbox', label: 'Inbox', icon: '✉️' },
  { key: 'domain', label: 'Domain', icon: '🌍' },
];

function renderEditor() {
  const { site, stats } = state.detail;

  const publish = async (ev) => {
    try {
      await withBusy(ev.currentTarget, site.published ? 'Updating…' : 'Publishing…', () =>
        api.post(`/api/sites/${site.id}/publish`));
      toast(site.published ? 'Live site updated' : 'Your site is live', 'ok');
      await reload();
    } catch (err) { toast(err.message, 'err'); }
  };

  const sideLink = (tab) => {
    const counts = { calendar: stats.upcoming, inbox: stats.unread, orders: stats.paidOrders };
    const n = counts[tab.key];
    return h('button', {
      class: 'side__link', 'aria-current': String(state.tab === tab.key),
      onclick: () => goto(site.id, tab.key),
    }, h('span', null, tab.icon), tab.label, n ? h('span', { class: 'n' }, String(n)) : null);
  };

  mount(root, h('div', { class: 'shell' },
    h('header', { class: 'topbar' }, h('div', { class: 'topbar__in' },
      h('button', { class: 'btn btn--ghost btn--sm', onclick: () => { location.hash = ''; } }, '← Sites'),
      h('div', { class: 'topbar__site' }, h('span', null, site.favicon), h('span', null, site.title)),
      h('span', { class: `pill ${site.published ? 'pill--ok' : ''}` }, site.published ? 'Live' : 'Draft'),
      h('div', { class: 'spacer' }),
      site.published
        ? h('a', { class: 'btn btn--ghost btn--sm', href: site.url, target: '_blank', rel: 'noopener' }, 'View live ↗')
        : null,
      h('button', { class: 'btn btn--primary btn--sm', onclick: publish },
        site.published ? 'Publish changes' : 'Publish'),
    )),

    h('div', { class: 'main' },
      h('nav', { class: 'side' },
        h('div', { class: 'side__group' },
          h('div', { class: 'side__label' }, 'Build'),
          ...TABS.slice(0, 4).map(sideLink)),
        h('div', { class: 'side__group' },
          h('div', { class: 'side__label' }, 'Run'),
          ...TABS.slice(4).map(sideLink)),
      ),
      h('div', { class: 'content' }, renderTab()),
    ),
  ));
}

function renderTab() {
  switch (state.tab) {
    case 'design': return tabDesign();
    case 'booking': return tabBooking();
    case 'store': return tabStore();
    case 'calendar': return tabCalendar();
    case 'orders': return tabOrders();
    case 'inbox': return tabInbox();
    case 'domain': return tabDomain();
    default: return tabOverview();
  }
}

const head = (title, sub, ...actions) => h('div', { class: 'content__head' },
  h('div', null, h('h2', null, title), sub ? h('p', null, sub) : null),
  h('div', { class: 'spacer' }),
  h('div', { class: 'row row--wrap' }, ...actions),
);

const empty = (icon, text, action) => h('div', { class: 'card empty' },
  h('div', { class: 'empty__icon' }, icon), h('p', null, text), action || null);

/* ── Overview ─────────────────────────────────────────────────────────── */
function tabOverview() {
  const { site, stats, spec, services, products, paymentMode, aiEnabled } = state.detail;
  const currency = spec.settings.currency;

  const copyUrl = async (url, ev) => {
    try {
      await navigator.clipboard.writeText(url);
      toast('Link copied', 'ok');
    } catch {
      ev.currentTarget.textContent = url;
    }
  };

  return frag(
    head('Overview', site.published ? 'Your site is live and taking bookings.' : 'Publish when you are ready — nothing is public yet.'),
    h('div', { class: 'kpis' },
      h('div', { class: 'kpi' }, h('b', null, String(stats.upcoming)), h('span', null, 'Upcoming bookings')),
      h('div', { class: 'kpi' }, h('b', null, money(stats.revenueCents, currency)), h('span', null, 'Collected')),
      h('div', { class: 'kpi' }, h('b', null, String(stats.paidOrders)), h('span', null, 'Paid orders')),
      h('div', { class: 'kpi' }, h('b', null, String(stats.unread)), h('span', null, 'Unread messages')),
    ),

    h('div', { class: 'grid2' },
      h('div', { class: 'card' },
        h('h3', null, 'Addresses'),
        h('div', { class: 'stack' },
          h('div', null,
            h('div', { class: 'small muted' }, 'LaunchKit address'),
            h('div', { class: 'row' },
              h('code', { class: 'mono' }, site.url.replace(/^https?:\/\//, '')),
              h('button', { class: 'copy', onclick: (e) => copyUrl(site.url, e) }, 'Copy')),
          ),
          h('div', null,
            h('div', { class: 'small muted' }, 'Preview (always the latest draft)'),
            h('a', { class: 'mono', href: site.previewUrl, target: '_blank', rel: 'noopener' }, `${site.previewUrl} ↗`),
          ),
          h('button', { class: 'btn btn--ghost btn--sm', onclick: () => goto(site.id, 'domain') }, 'Connect a custom domain →'),
        ),
      ),

      h('div', { class: 'card' },
        h('h3', null, 'Setup'),
        h('div', { class: 'stack' },
          checkRow(services.length > 0, `${services.length} bookable service${services.length === 1 ? '' : 's'}`, 'No services yet — add one so people can book'),
          checkRow(products.length > 0, `${products.length} product${products.length === 1 ? '' : 's'} for sale`, 'No products — optional if you only take bookings'),
          checkRow(paymentMode === 'stripe', 'Stripe connected — real cards', 'Sandbox payments — set STRIPE_SECRET_KEY to charge real cards'),
          checkRow(aiEnabled, 'AI generation on', 'Template engine — set ANTHROPIC_API_KEY for bespoke copy'),
          checkRow(site.published, 'Published', 'Not published yet'),
        ),
      ),
    ),
  );
}

const checkRow = (ok, okText, badText) => h('div', { class: 'row' },
  h('span', { style: { color: ok ? 'var(--ok)' : 'var(--ink-3)' } }, ok ? '✓' : '○'),
  h('span', { class: ok ? '' : 'muted', style: { fontSize: '.9rem' } }, ok ? okText : badText),
);

/* ── Design ───────────────────────────────────────────────────────────── */
function tabDesign() {
  const { site, spec, themes } = state.detail;

  const iframe = h('iframe', { src: `${site.previewUrl}?t=${Date.now()}`, title: 'Site preview', loading: 'lazy' });
  const refresh = () => { iframe.src = `${site.previewUrl}?t=${Date.now()}`; };

  const frame = h('div', { class: 'frame', 'data-view': state.device },
    h('div', { class: 'frame__bar' },
      h('i', { class: 'demo__dot' }), h('i', { class: 'demo__dot' }), h('i', { class: 'demo__dot' }),
      h('span', { class: 'demo__url' }, site.url.replace(/^https?:\/\//, '')),
      h('div', { class: 'frame__seg' },
        ...['desktop', 'phone'].map((d) => h('button', {
          type: 'button', 'aria-pressed': String(state.device === d),
          onclick: () => { state.device = d; frame.setAttribute('data-view', d); frame.querySelectorAll('.frame__seg button').forEach((b) => b.setAttribute('aria-pressed', String(b.textContent.toLowerCase() === d))); },
        }, d === 'desktop' ? 'Desktop' : 'Phone')),
      ),
      h('button', { class: 'copy', onclick: refresh }, 'Refresh'),
    ),
    iframe,
  );

  /* AI revision */
  const reviseBox = h('textarea', {
    placeholder: 'Make the hero warmer, add an FAQ about parking, and switch to a darker theme.',
    maxlength: '4000', style: { minHeight: '76px' },
  });
  const revise = async (ev) => {
    const prompt = reviseBox.value.trim();
    if (prompt.length < 4) { toast('Describe the change you want', 'err'); return; }
    try {
      const result = await withBusy(ev.currentTarget, 'Rewriting…', () =>
        api.post(`/api/sites/${site.id}/regenerate`, { prompt, keepCatalog: true }));
      reviseBox.value = '';
      if (result.generation.source === 'template') toast('Rebuilt from templates — add ANTHROPIC_API_KEY for bespoke edits.');
      else toast('Updated', 'ok');
      await reload();
    } catch (err) { toast(err.message, 'err'); }
  };

  /* Theme */
  const applyTheme = async (key) => {
    try {
      await api.put(`/api/sites/${site.id}/spec`, { spec: { ...spec, theme: { ...spec.theme, preset: key, colors: {} } } });
      await reload();
    } catch (err) { toast(err.message, 'err'); }
  };

  /* Blocks */
  const page = spec.pages[0];
  const moveBlock = async (index, delta) => {
    const blocks = [...page.blocks];
    const target = index + delta;
    if (target < 0 || target >= blocks.length) return;
    [blocks[index], blocks[target]] = [blocks[target], blocks[index]];
    await saveBlocks(blocks);
  };
  const removeBlock = async (index) => {
    const blocks = page.blocks.filter((_, i) => i !== index);
    if (!blocks.length) { toast('A page needs at least one section', 'err'); return; }
    await saveBlocks(blocks);
  };
  const saveBlocks = async (blocks) => {
    const next = { ...spec, pages: spec.pages.map((p, i) => (i === 0 ? { ...p, blocks } : p)) };
    try {
      await api.put(`/api/sites/${site.id}/spec`, { spec: next });
      await reload();
    } catch (err) { toast(err.message, 'err'); }
  };

  /* Site details */
  const metaForm = h('form', {
    onsubmit: async (e) => {
      e.preventDefault();
      const f = e.target.elements;
      const next = {
        ...spec,
        meta: { ...spec.meta, title: f.title.value, description: f.description.value, favicon: f.favicon.value },
        settings: { ...spec.settings, timezone: f.timezone.value, currency: f.currency.value, contactEmail: f.contactEmail.value },
        nav: { ...spec.nav, brand: f.brand.value },
      };
      try {
        await withBusy(e.target.querySelector('button[type=submit]'), 'Saving…', () =>
          api.put(`/api/sites/${site.id}/spec`, { spec: next }));
        toast('Saved', 'ok');
        await reload();
      } catch (err) { toast(err.message, 'err'); }
    },
  },
    h('div', { class: 'grid2' },
      field('Site title', h('input', { name: 'title', value: spec.meta.title, maxlength: '120', required: true })),
      field('Brand (header)', h('input', { name: 'brand', value: spec.nav.brand, maxlength: '60' })),
    ),
    field('Meta description', h('input', { name: 'description', value: spec.meta.description, maxlength: '300' })),
    h('div', { class: 'grid3' },
      field('Emoji', h('input', { name: 'favicon', value: spec.meta.favicon, maxlength: '4' })),
      field('Currency', h('input', { name: 'currency', value: spec.settings.currency, maxlength: '3' })),
      field('Contact email', h('input', { name: 'contactEmail', type: 'email', value: spec.settings.contactEmail })),
    ),
    field('Timezone', timezoneSelect('timezone', spec.settings.timezone),
      'Opening hours and every booking confirmation are shown in this zone.'),
    h('button', { class: 'btn btn--primary btn--sm', type: 'submit' }, 'Save details'),
  );

  return frag(
    head('Design', 'Edit on the right, watch it change on the left.'),
    h('div', { class: 'split' },
      frame,
      h('div', { class: 'stack' },
        h('div', { class: 'card' },
          h('h3', null, 'Ask for a change'),
          h('p', { class: 'small muted' }, 'Plain English. Your services and products are left alone.'),
          reviseBox,
          h('button', { class: 'btn btn--primary btn--block', style: { marginTop: '10px' }, onclick: revise }, 'Apply change'),
        ),

        h('div', { class: 'card' },
          h('h3', null, 'Theme'),
          h('div', { class: 'themes' }, ...themes.map((t) => h('button', {
            class: 'swatch', type: 'button', 'aria-pressed': String(spec.theme.preset === t.key),
            onclick: () => applyTheme(t.key),
          },
            h('div', { class: 'swatch__dots' },
              h('i', { style: { background: t.colors.bg } }),
              h('i', { style: { background: t.colors.primary } }),
              h('i', { style: { background: t.colors.accent } })),
            h('span', null, t.label),
          ))),
        ),

        h('div', { class: 'card' },
          h('h3', null, 'Sections'),
          h('div', { class: 'blocks' }, ...page.blocks.map((b, i) => h('div', { class: 'blk' },
            h('span', { class: 'blk__type' }, b.type),
            h('div', { class: 'blk__acts' },
              h('button', { title: 'Move up', disabled: i === 0, onclick: () => moveBlock(i, -1) }, '↑'),
              h('button', { title: 'Move down', disabled: i === page.blocks.length - 1, onclick: () => moveBlock(i, 1) }, '↓'),
              h('button', { title: 'Remove', onclick: () => removeBlock(i) }, '✕'),
            ),
          ))),
        ),

        h('div', { class: 'card' }, h('h3', null, 'Details'), metaForm),
      ),
    ),
  );
}

const field = (label, control, hint) => h('div', { class: 'field' },
  h('label', null, label), control, hint ? h('span', { class: 'small muted' }, hint) : null);

const ZONES = [
  'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'America/Phoenix',
  'America/Anchorage', 'Pacific/Honolulu', 'America/Toronto', 'America/Vancouver', 'America/Mexico_City',
  'America/Sao_Paulo', 'Europe/London', 'Europe/Dublin', 'Europe/Lisbon', 'Europe/Madrid', 'Europe/Paris',
  'Europe/Berlin', 'Europe/Amsterdam', 'Europe/Rome', 'Europe/Stockholm', 'Europe/Warsaw', 'Europe/Athens',
  'Europe/Istanbul', 'Africa/Lagos', 'Africa/Johannesburg', 'Africa/Cairo', 'Asia/Dubai', 'Asia/Karachi',
  'Asia/Kolkata', 'Asia/Bangkok', 'Asia/Singapore', 'Asia/Hong_Kong', 'Asia/Shanghai', 'Asia/Tokyo',
  'Asia/Seoul', 'Australia/Perth', 'Australia/Sydney', 'Pacific/Auckland', 'UTC',
];

function timezoneSelect(name, current) {
  const options = ZONES.includes(current) ? ZONES : [current, ...ZONES];
  return h('select', { name }, ...options.map((z) =>
    h('option', { value: z, selected: z === current }, z.replace(/_/g, ' '))));
}

/* ── Booking: services + opening hours ────────────────────────────────── */
function tabBooking() {
  const { site, services, availability, spec } = state.detail;
  const currency = spec.settings.currency;

  const rows = services.map((s) => h('tr', null,
    h('td', null,
      h('div', { style: { fontWeight: '600' } }, s.name),
      s.description ? h('div', { class: 'small muted' }, s.description.slice(0, 90)) : null),
    h('td', null, duration(s.duration_min), s.buffer_min ? h('div', { class: 'small muted' }, `+${s.buffer_min}m turnaround`) : null),
    h('td', null, s.price_cents > 0 ? money(s.price_cents, currency) : h('span', { class: 'muted' }, 'Free'),
      s.deposit_cents > 0 ? h('div', { class: 'small muted' }, `${money(s.deposit_cents, currency)} deposit`) : null),
    h('td', null, s.capacity > 1 ? `${s.capacity} seats` : '1-to-1'),
    h('td', null, h('span', { class: `pill ${s.active ? 'pill--ok' : ''}` }, s.active ? 'On' : 'Off')),
    h('td', null, h('div', { class: 'row' },
      h('button', { class: 'btn btn--ghost btn--sm', onclick: () => serviceModal(s) }, 'Edit'),
      h('button', {
        class: 'btn btn--danger btn--sm',
        onclick: async () => {
          if (!(await confirmDialog('Delete service', `Delete "${s.name}"? Bookings already made for it are cancelled too.`, { confirmLabel: 'Delete' }))) return;
          try { await api.del(`/api/sites/${site.id}/services/${s.id}`); toast('Deleted', 'ok'); await reload(); }
          catch (err) { toast(err.message, 'err'); }
        },
      }, 'Delete'),
    )),
  ));

  /* Opening hours */
  const byDay = new Map();
  for (const r of availability) byDay.set(r.weekday, r);

  const hoursForm = h('form', {
    onsubmit: async (e) => {
      e.preventDefault();
      const rules = [];
      for (let day = 0; day < 7; day++) {
        const on = e.target.elements[`on-${day}`];
        if (!on?.checked) continue;
        rules.push({ weekday: day, start: e.target.elements[`start-${day}`].value, end: e.target.elements[`end-${day}`].value });
      }
      try {
        await withBusy(e.target.querySelector('button[type=submit]'), 'Saving…', () =>
          api.put(`/api/sites/${site.id}/availability`, { rules }));
        toast('Opening hours saved', 'ok');
        await reload();
      } catch (err) { toast(err.message, 'err'); }
    },
  },
    ...WEEKDAYS.map((label, day) => {
      const rule = byDay.get(day);
      return h('div', { class: 'row', style: { marginBottom: '9px' } },
        h('label', { class: 'row', style: { width: '128px', gap: '8px', fontSize: '.9rem' } },
          h('input', { type: 'checkbox', name: `on-${day}`, checked: Boolean(rule) }), label),
        h('input', { type: 'time', name: `start-${day}`, value: minutesToLabel(rule ? rule.start_min : 540), style: { maxWidth: '130px' } }),
        h('span', { class: 'muted small' }, 'to'),
        h('input', { type: 'time', name: `end-${day}`, value: minutesToLabel(rule ? rule.end_min : 1020), style: { maxWidth: '130px' } }),
      );
    }),
    h('button', { class: 'btn btn--primary btn--sm', type: 'submit', style: { marginTop: '10px' } }, 'Save hours'),
  );

  return frag(
    head('Booking', 'What people can book, how long it takes, and when you are open.',
      h('button', { class: 'btn btn--primary btn--sm', onclick: () => serviceModal(null) }, '+ Add service')),

    services.length
      ? h('div', { class: 'card card--flush', style: { marginBottom: '24px' } },
          h('div', { class: 'tbl__wrap' }, h('table', { class: 'tbl' },
            h('thead', null, h('tr', null,
              h('th', null, 'Service'), h('th', null, 'Length'), h('th', null, 'Price'),
              h('th', null, 'Capacity'), h('th', null, 'Status'), h('th', null, ''))),
            h('tbody', null, ...rows))))
      : h('div', { style: { marginBottom: '24px' } },
          empty('📅', 'No services yet. Add one and the booking calendar appears on your site.',
            h('button', { class: 'btn btn--primary', onclick: () => serviceModal(null) }, 'Add a service'))),

    h('div', { class: 'card' },
      h('h3', null, 'Opening hours'),
      h('p', { class: 'small muted' }, `Local time in ${spec.settings.timezone.replace(/_/g, ' ')}. Slots are only offered inside these windows.`),
      hoursForm,
    ),
  );
}

async function serviceModal(service) {
  const { site, spec } = state.detail;
  const editing = Boolean(service);

  const saved = await modal(editing ? 'Edit service' : 'Add service', (close) => {
    const form = h('form', {
      onsubmit: async (e) => {
        e.preventDefault();
        const f = e.target.elements;
        const body = {
          name: f.name.value,
          description: f.description.value,
          durationMin: Number(f.durationMin.value),
          price: f.price.value === '' ? 0 : Number(f.price.value),
          deposit: f.deposit.value === '' ? 0 : Number(f.deposit.value),
          bufferMin: f.bufferMin.value === '' ? 0 : Number(f.bufferMin.value),
          capacity: f.capacity.value === '' ? 1 : Number(f.capacity.value),
          active: f.active.checked,
        };
        try {
          await withBusy(e.target.querySelector('button[type=submit]'), 'Saving…', () =>
            editing
              ? api.put(`/api/sites/${site.id}/services/${service.id}`, body)
              : api.post(`/api/sites/${site.id}/services`, body));
          close(true);
        } catch (err) { toast(err.message, 'err'); }
      },
    },
      field('Name', h('input', { name: 'name', required: true, maxlength: '90', value: service?.name || '' })),
      field('Description', h('textarea', { name: 'description', maxlength: '400', style: { minHeight: '68px' } }, service?.description || '')),
      h('div', { class: 'grid2' },
        field('Length (minutes)', h('input', { name: 'durationMin', type: 'number', min: '5', max: '480', required: true, value: String(service?.duration_min ?? 60) })),
        field('Turnaround after (minutes)', h('input', { name: 'bufferMin', type: 'number', min: '0', max: '240', value: String(service?.buffer_min ?? 0) }),
          'Blocked off before the next booking.'),
      ),
      h('div', { class: 'grid2' },
        field(`Price (${spec.settings.currency.toUpperCase()})`, h('input', { name: 'price', type: 'number', min: '0', step: '0.01', value: ((service?.price_cents ?? 0) / 100).toString() })),
        field('Deposit taken online', h('input', { name: 'deposit', type: 'number', min: '0', step: '0.01', value: ((service?.deposit_cents ?? 0) / 100).toString() }),
          'Above 0 means the slot is only held once paid.'),
      ),
      field('Seats per slot', h('input', { name: 'capacity', type: 'number', min: '1', max: '200', value: String(service?.capacity ?? 1) }),
        'Leave at 1 for one-to-one. Higher turns it into a class.'),
      h('label', { class: 'row', style: { gap: '8px', marginBottom: '16px' } },
        h('input', { type: 'checkbox', name: 'active', checked: service ? Boolean(service.active) : true }),
        h('span', { class: 'small' }, 'Bookable on the live site')),
      h('div', { class: 'row', style: { justifyContent: 'flex-end' } },
        h('button', { class: 'btn btn--ghost', type: 'button', onclick: () => close(false) }, 'Cancel'),
        h('button', { class: 'btn btn--primary', type: 'submit' }, editing ? 'Save' : 'Add service')),
    );
    return form;
  });

  if (saved) { toast(editing ? 'Service updated' : 'Service added', 'ok'); await reload(); }
}

/* ── Store ────────────────────────────────────────────────────────────── */
function tabStore() {
  const { site, products, spec, paymentMode } = state.detail;
  const currency = spec.settings.currency;

  const rows = products.map((p) => h('tr', null,
    h('td', null,
      h('div', { style: { fontWeight: '600' } }, p.name),
      p.description ? h('div', { class: 'small muted' }, p.description.slice(0, 80)) : null),
    h('td', null, money(p.price_cents, currency)),
    h('td', null, p.inventory === null
      ? h('span', { class: 'muted' }, 'Unlimited')
      : h('span', { class: p.inventory === 0 ? 'muted' : '' }, p.inventory === 0 ? 'Sold out' : `${p.inventory} in stock`)),
    h('td', null, h('span', { class: `pill ${p.active ? 'pill--ok' : ''}` }, p.active ? 'On' : 'Off')),
    h('td', null, h('div', { class: 'row' },
      h('button', { class: 'btn btn--ghost btn--sm', onclick: () => productModal(p) }, 'Edit'),
      h('button', {
        class: 'btn btn--danger btn--sm',
        onclick: async () => {
          if (!(await confirmDialog('Delete product', `Delete "${p.name}"?`, { confirmLabel: 'Delete' }))) return;
          try { await api.del(`/api/sites/${site.id}/products/${p.id}`); toast('Deleted', 'ok'); await reload(); }
          catch (err) { toast(err.message, 'err'); }
        },
      }, 'Delete'),
    )),
  ));

  return frag(
    head('Store', 'Anything you sell outright — retail, passes, gift cards.',
      h('button', { class: 'btn btn--primary btn--sm', onclick: () => productModal(null) }, '+ Add product')),

    paymentMode === 'sandbox'
      ? h('div', { class: 'card', style: { marginBottom: '20px', borderColor: 'rgba(245,181,68,.4)' } },
          h('div', { class: 'row' }, h('span', { class: 'pill pill--warn' }, 'Sandbox'),
            h('span', { class: 'small' }, 'Checkout works end to end but no card is charged. Set STRIPE_SECRET_KEY to take real payments.')))
      : null,

    products.length
      ? h('div', { class: 'card card--flush' }, h('div', { class: 'tbl__wrap' }, h('table', { class: 'tbl' },
          h('thead', null, h('tr', null, h('th', null, 'Product'), h('th', null, 'Price'), h('th', null, 'Stock'), h('th', null, 'Status'), h('th', null, ''))),
          h('tbody', null, ...rows))))
      : empty('🛍️', 'Nothing for sale yet. Add a product and a Buy button appears on your site.',
          h('button', { class: 'btn btn--primary', onclick: () => productModal(null) }, 'Add a product')),
  );
}

async function productModal(product) {
  const { site, spec } = state.detail;
  const editing = Boolean(product);

  const saved = await modal(editing ? 'Edit product' : 'Add product', (close) => h('form', {
    onsubmit: async (e) => {
      e.preventDefault();
      const f = e.target.elements;
      const body = {
        name: f.name.value,
        description: f.description.value,
        price: f.price.value === '' ? 0 : Number(f.price.value),
        image: f.image.value,
        inventory: f.unlimited.checked ? '' : Number(f.inventory.value),
        active: f.active.checked,
      };
      try {
        await withBusy(e.target.querySelector('button[type=submit]'), 'Saving…', () =>
          editing
            ? api.put(`/api/sites/${site.id}/products/${product.id}`, body)
            : api.post(`/api/sites/${site.id}/products`, body));
        close(true);
      } catch (err) { toast(err.message, 'err'); }
    },
  },
    field('Name', h('input', { name: 'name', required: true, maxlength: '90', value: product?.name || '' })),
    field('Description', h('textarea', { name: 'description', maxlength: '400', style: { minHeight: '68px' } }, product?.description || '')),
    h('div', { class: 'grid2' },
      field(`Price (${spec.settings.currency.toUpperCase()})`, h('input', { name: 'price', type: 'number', min: '0', step: '0.01', required: true, value: ((product?.price_cents ?? 0) / 100).toString() })),
      field('Stock', h('input', { name: 'inventory', type: 'number', min: '0', value: String(product?.inventory ?? 10), disabled: product?.inventory === null })),
    ),
    h('label', { class: 'row', style: { gap: '8px', marginBottom: '14px' } },
      h('input', {
        type: 'checkbox', name: 'unlimited', checked: product ? product.inventory === null : false,
        onchange: (e) => { e.target.form.elements.inventory.disabled = e.target.checked; },
      }),
      h('span', { class: 'small' }, 'Unlimited stock (digital goods, gift cards)')),
    field('Image URL', h('input', { name: 'image', maxlength: '500', placeholder: 'Leave blank for a themed placeholder', value: product?.image || '' })),
    h('label', { class: 'row', style: { gap: '8px', marginBottom: '16px' } },
      h('input', { type: 'checkbox', name: 'active', checked: product ? Boolean(product.active) : true }),
      h('span', { class: 'small' }, 'Show on the live site')),
    h('div', { class: 'row', style: { justifyContent: 'flex-end' } },
      h('button', { class: 'btn btn--ghost', type: 'button', onclick: () => close(false) }, 'Cancel'),
      h('button', { class: 'btn btn--primary', type: 'submit' }, editing ? 'Save' : 'Add product')),
  ));

  if (saved) { toast(editing ? 'Product updated' : 'Product added', 'ok'); await reload(); }
}

/* ── Bookings list ────────────────────────────────────────────────────── */
function tabCalendar() {
  const { site } = state.detail;
  const host = h('div', null, h('div', { class: 'skeleton', style: { height: '180px' } }));
  let scope = 'upcoming';

  const load = async () => {
    try {
      const { appointments, timezone } = await api.get(`/api/sites/${site.id}/appointments?scope=${scope}`);
      mount(host, appointments.length
        ? h('div', { class: 'card card--flush' }, h('div', { class: 'tbl__wrap' }, h('table', { class: 'tbl' },
            h('thead', null, h('tr', null,
              h('th', null, 'When'), h('th', null, 'Service'), h('th', null, 'Customer'),
              h('th', null, 'Status'), h('th', null, ''))),
            h('tbody', null, ...appointments.map((a) => h('tr', null,
              h('td', null, h('div', { style: { fontWeight: '600' } }, a.when),
                h('div', { class: 'small muted' }, timezone.replace(/_/g, ' '))),
              h('td', null, a.service_name || '—'),
              h('td', null, h('div', null, a.name),
                h('div', { class: 'small muted' }, a.email), a.phone ? h('div', { class: 'small muted' }, a.phone) : null,
                a.notes ? h('div', { class: 'small muted', style: { fontStyle: 'italic' } }, `“${a.notes}”`) : null),
              h('td', null, h('span', {
                class: `pill ${a.status === 'confirmed' ? 'pill--ok' : a.status === 'hold' ? 'pill--warn' : 'pill--err'}`,
              }, a.status === 'hold' ? 'Awaiting deposit' : a.status)),
              h('td', null, a.status !== 'cancelled' && scope === 'upcoming'
                ? h('button', {
                    class: 'btn btn--danger btn--sm',
                    onclick: async () => {
                      if (!(await confirmDialog('Cancel booking', `Cancel ${a.name}'s booking on ${a.when}?`, { confirmLabel: 'Cancel booking' }))) return;
                      try { await api.del(`/api/sites/${site.id}/appointments/${a.id}`); toast('Booking cancelled', 'ok'); load(); }
                      catch (err) { toast(err.message, 'err'); }
                    },
                  }, 'Cancel')
                : null),
            ))))))
        : empty('🗓️', scope === 'upcoming' ? 'No upcoming bookings yet.' : 'Nothing in the past.'));
    } catch (err) { mount(host, empty('⚠️', err.message)); }
  };
  load();

  const seg = (key, label) => h('button', {
    class: `btn btn--sm ${scope === key ? '' : 'btn--ghost'}`,
    onclick: (e) => {
      scope = key;
      e.currentTarget.parentElement.querySelectorAll('button').forEach((b) =>
        b.className = `btn btn--sm ${b.textContent === label ? '' : 'btn--ghost'}`);
      load();
    },
  }, label);

  return frag(
    head('Bookings', 'Everything customers have booked, newest first.',
      h('div', { class: 'row' }, seg('upcoming', 'Upcoming'), seg('past', 'Past'))),
    host,
  );
}

/* ── Orders ───────────────────────────────────────────────────────────── */
function tabOrders() {
  const { site, spec } = state.detail;
  const currency = spec.settings.currency;
  const host = h('div', null, h('div', { class: 'skeleton', style: { height: '180px' } }));

  api.get(`/api/sites/${site.id}/orders`).then(({ orders }) => {
    mount(host, orders.length
      ? h('div', { class: 'card card--flush' }, h('div', { class: 'tbl__wrap' }, h('table', { class: 'tbl' },
          h('thead', null, h('tr', null,
            h('th', null, 'Order'), h('th', null, 'Items'), h('th', null, 'Amount'),
            h('th', null, 'Status'), h('th', null, 'Placed'))),
          h('tbody', null, ...orders.map((o) => h('tr', null,
            h('td', null, h('code', { class: 'mono small' }, o.id.slice(-8)),
              h('div', { class: 'small muted' }, o.kind === 'deposit' ? 'Booking deposit' : 'Store order')),
            h('td', null, ...o.items.map((i) => h('div', { class: 'small' }, `${i.quantity} × ${i.name}`)),
              o.email ? h('div', { class: 'small muted' }, o.email) : null),
            h('td', null, money(o.amount_cents, currency)),
            h('td', null, h('span', {
              class: `pill ${o.status === 'paid' ? 'pill--ok' : o.status === 'pending' ? 'pill--warn' : 'pill--err'}`,
            }, o.status), h('div', { class: 'small muted' }, o.provider)),
            h('td', { class: 'small muted' }, new Date(o.created_at).toLocaleString()),
          ))))))
      : empty('💳', 'No orders yet. Deposits and store sales land here.'));
  }).catch((err) => mount(host, empty('⚠️', err.message)));

  return frag(head('Orders', 'Deposits and store payments, with their settlement state.'), host);
}

/* ── Inbox ────────────────────────────────────────────────────────────── */
function tabInbox() {
  const { site } = state.detail;
  const host = h('div', null, h('div', { class: 'skeleton', style: { height: '180px' } }));

  api.get(`/api/sites/${site.id}/messages`).then(({ messages }) => {
    mount(host, messages.length
      ? h('div', { class: 'stack' }, ...messages.map((m) => h('div', { class: 'card' },
          h('div', { class: 'row row--wrap' },
            h('strong', null, m.name || 'Someone'),
            h('a', { class: 'small', href: `mailto:${m.email}` }, m.email),
            h('div', { class: 'spacer' }),
            h('span', { class: 'small muted' }, new Date(m.created_at).toLocaleString())),
          h('p', { style: { margin: '10px 0 0' } }, m.body),
        )))
      : empty('✉️', 'No messages yet. Your contact form delivers here.'));
    state.detail.stats.unread = 0;
  }).catch((err) => mount(host, empty('⚠️', err.message)));

  return frag(head('Inbox', 'Messages from your contact form.'), host);
}

/* ── Domain ───────────────────────────────────────────────────────────── */
function tabDomain() {
  const { site, domains } = state.detail;

  const add = async (ev) => {
    const input = document.getElementById('dom-host');
    const hostname = input.value.trim();
    if (!hostname) { toast('Enter a domain', 'err'); return; }
    try {
      await withBusy(ev.currentTarget, 'Adding…', () => api.post(`/api/sites/${site.id}/domains`, { hostname }));
      input.value = '';
      toast('Domain added — now add the DNS records', 'ok');
      await reload();
    } catch (err) { toast(err.message, 'err'); }
  };

  const verify = async (domain, ev) => {
    try {
      const { domain: updated } = await withBusy(ev.currentTarget, 'Checking DNS…', () =>
        api.post(`/api/sites/${site.id}/domains/${domain.id}/verify`));
      if (updated.status === 'live') toast(`${updated.hostname} is live`, 'ok');
      else toast(updated.last_error || 'Not verified yet', 'err');
      await reload();
    } catch (err) { toast(err.message, 'err'); }
  };

  const copyable = (value) => h('span', null, value, h('button', {
    class: 'copy',
    onclick: async (e) => {
      try { await navigator.clipboard.writeText(value); e.currentTarget.textContent = 'Copied'; setTimeout(() => { e.currentTarget.textContent = 'Copy'; }, 1400); }
      catch { toast('Copy failed — select the text manually', 'err'); }
    },
  }, 'Copy'));

  const card = (d) => h('div', { class: 'card' },
    h('div', { class: 'row row--wrap', style: { marginBottom: '12px' } },
      h('h3', { style: { margin: 0 } }, d.hostname),
      h('span', { class: `pill ${d.status === 'live' ? 'pill--ok' : 'pill--warn'}` }, d.status === 'live' ? 'Live' : 'Pending DNS'),
      h('div', { class: 'spacer' }),
      h('button', { class: 'btn btn--ghost btn--sm', onclick: (e) => verify(d, e) }, 'Check DNS'),
      h('button', {
        class: 'btn btn--danger btn--sm',
        onclick: async () => {
          if (!(await confirmDialog('Remove domain', `Stop serving your site at ${d.hostname}?`, { confirmLabel: 'Remove' }))) return;
          try { await api.del(`/api/sites/${site.id}/domains/${d.id}`); toast('Domain removed', 'ok'); await reload(); }
          catch (err) { toast(err.message, 'err'); }
        },
      }, 'Remove'),
    ),

    d.status === 'live'
      ? h('p', { class: 'small muted', style: { margin: 0 } },
          'DNS verified. Traffic to this domain now serves your published site.')
      : frag(
          h('p', { class: 'small muted' }, 'Add both records at your registrar, then press Check DNS. Changes usually appear within a few minutes.'),
          h('div', { class: 'dns' },
            h('div', { class: 'dns__row' }, h('b', null, 'Type'), h('span', null, d.dns.verification.type)),
            h('div', { class: 'dns__row' }, h('b', null, 'Name'), h('span', null, copyable(d.dns.verification.name))),
            h('div', { class: 'dns__row' }, h('b', null, 'Value'), h('span', null, copyable(d.dns.verification.value))),
          ),
          h('div', { class: 'dns', style: { marginTop: '10px' } },
            h('div', { class: 'dns__row' }, h('b', null, 'Type'), h('span', null, d.dns.routing.type)),
            h('div', { class: 'dns__row' }, h('b', null, 'Name'), h('span', null, copyable(d.dns.routing.name))),
            h('div', { class: 'dns__row' }, h('b', null, 'Value'), h('span', null, copyable(d.dns.routing.value))),
          ),
          d.dns.routing.note ? h('p', { class: 'small muted', style: { margin: '9px 0 0' } }, d.dns.routing.note) : null,
          d.last_error ? h('p', { class: 'small', style: { margin: '9px 0 0', color: 'var(--warn)' } }, d.last_error) : null,
        ),
  );

  return frag(
    head('Domain', 'Point your own name at this site.'),
    !site.published
      ? h('div', { class: 'card', style: { marginBottom: '18px', borderColor: 'rgba(245,181,68,.4)' } },
          h('div', { class: 'row' }, h('span', { class: 'pill pill--warn' }, 'Not published'),
            h('span', { class: 'small' }, 'A domain only serves a published site. Publish first, then connect the name.')))
      : null,
    h('div', { class: 'card', style: { marginBottom: '18px' } },
      h('h3', null, 'Connect a domain'),
      h('div', { class: 'row row--wrap' },
        h('input', { id: 'dom-host', placeholder: 'book.mysalon.com', style: { flex: '1', minWidth: '220px' } }),
        h('button', { class: 'btn btn--primary', onclick: add }, 'Add domain')),
      h('p', { class: 'small muted', style: { margin: '10px 0 0' } },
        'Works with a subdomain like book.mysalon.com or an apex like mysalon.com.'),
    ),
    domains.length
      ? h('div', { class: 'stack' }, ...domains.map(card))
      : empty('🌍', 'No custom domain yet. Your site is live on its LaunchKit address in the meantime.'),
  );
}
