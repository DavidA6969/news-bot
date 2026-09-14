import { escapeHtml } from '../lib/http.js';

const GOOGLE_FONTS = {
  'Plus Jakarta Sans': 'Plus+Jakarta+Sans:wght@400;500;600;700;800',
  Fraunces: 'Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700',
  'Space Grotesk': 'Space+Grotesk:wght@400;500;600;700',
};

/** Only request the families a theme actually names. */
export function fontLink(theme) {
  const wanted = new Set();
  for (const stack of [theme.fonts.heading, theme.fonts.body]) {
    for (const [family, spec] of Object.entries(GOOGLE_FONTS)) {
      if (stack.includes(family)) wanted.add(spec);
    }
  }
  if (!wanted.size) return '';
  const families = [...wanted].map((f) => `family=${f}`).join('&');
  const href = `https://fonts.googleapis.com/css2?${families}&display=swap`;
  // Loaded non-render-blocking: the page paints immediately in the fallback
  // stack and swaps the webfont in when it lands. A slow or unreachable font
  // CDN can then never hold up a customer's first paint.
  return `<link rel="preconnect" href="https://fonts.googleapis.com">`
    + `<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>`
    + `<link rel="stylesheet" href="${href}" media="print" onload="this.media='all';this.onload=null">`
    + `<noscript><link rel="stylesheet" href="${href}"></noscript>`;
}

const hexToRgb = (hex) => {
  let h = hex.replace('#', '');
  if (h.length === 3) h = h.split('').map((c) => c + c).join('');
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
};

/** Relative luminance, used to decide whether a surface reads light or dark. */
export function luminance(hex) {
  const [r, g, b] = hexToRgb(hex).map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export const isDark = (hex) => luminance(hex) < 0.5;

export function rgba(hex, alpha) {
  const [r, g, b] = hexToRgb(hex);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/**
 * A deterministic gradient placeholder, so a site without photography still
 * looks composed rather than broken.
 */
export function placeholderImage(theme, seed = '') {
  let hash = 0;
  for (let i = 0; i < String(seed).length; i++) hash = (hash * 31 + seed.charCodeAt(i)) >>> 0;
  const angle = hash % 360;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600"><defs><linearGradient id="g" gradientTransform="rotate(${angle} .5 .5)"><stop offset="0%" stop-color="${theme.colors.primary}"/><stop offset="55%" stop-color="${theme.colors.accent}"/><stop offset="100%" stop-color="${theme.colors.surface}"/></linearGradient></defs><rect width="800" height="600" fill="url(#g)"/><circle cx="${120 + (hash % 400)}" cy="${100 + (hash % 300)}" r="${120 + (hash % 140)}" fill="${rgba(theme.colors.surface, 0.28)}"/></svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

export function themeCss(theme) {
  const c = theme.colors;
  const dark = isDark(c.bg);
  return `
:root{
  --bg:${c.bg}; --surface:${c.surface}; --text:${c.text}; --muted:${c.muted};
  --primary:${c.primary}; --primary-text:${c.primaryText}; --accent:${c.accent}; --border:${c.border};
  --radius:${theme.radius}px; --radius-sm:${Math.max(2, Math.round(theme.radius * 0.5))}px;
  --font-heading:${theme.fonts.heading}; --font-body:${theme.fonts.body};
  --shadow:0 1px 2px ${rgba(dark ? '#000000' : '#0b1020', dark ? 0.4 : 0.06)}, 0 12px 32px ${rgba(dark ? '#000000' : '#0b1020', dark ? 0.35 : 0.08)};
  --tint:${rgba(c.primary, dark ? 0.18 : 0.09)};
  --overlay:${rgba(c.bg, 0.82)};
}
*,*::before,*::after{box-sizing:border-box}
html{scroll-behavior:smooth;-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--font-body);font-size:17px;line-height:1.65;-webkit-font-smoothing:antialiased}
img{max-width:100%;display:block}
h1,h2,h3,h4{font-family:var(--font-heading);line-height:1.12;letter-spacing:-0.02em;margin:0 0 .5em;font-weight:700}
h1{font-size:clamp(2.1rem,5.4vw,3.9rem)}
h2{font-size:clamp(1.65rem,3.4vw,2.6rem)}
h3{font-size:1.2rem}
p{margin:0 0 1em}
a{color:inherit}
.wrap{width:100%;max-width:1140px;margin:0 auto;padding-inline:20px}
.sec{padding-block:clamp(56px,8vw,104px)}
.sec--tint{background:var(--surface)}
.sec__head{max-width:660px;margin-bottom:40px}
.sec__head p{color:var(--muted);font-size:1.06rem;margin:0}
.eyebrow{display:inline-block;font-size:.78rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--primary);margin-bottom:14px}
.muted{color:var(--muted)}

.btn{display:inline-flex;align-items:center;justify-content:center;gap:.5em;padding:.82em 1.5em;border-radius:var(--radius-sm);border:1px solid transparent;font:inherit;font-weight:600;font-size:.98rem;cursor:pointer;text-decoration:none;transition:transform .12s ease,filter .12s ease,background .12s ease;white-space:nowrap}
.btn:hover{transform:translateY(-1px)}
.btn:active{transform:translateY(0)}
.btn:disabled{opacity:.5;cursor:not-allowed;transform:none}
.btn--primary{background:var(--primary);color:var(--primary-text)}
.btn--primary:hover{filter:brightness(1.08)}
.btn--ghost{background:transparent;color:var(--text);border-color:var(--border)}
.btn--ghost:hover{background:var(--tint)}
.btn--sm{padding:.6em 1.05em;font-size:.9rem}
.btn--block{width:100%}

.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:26px}
.grid{display:grid;gap:22px}
.grid--2{grid-template-columns:repeat(2,1fr)}
.grid--3{grid-template-columns:repeat(3,1fr)}
.grid--4{grid-template-columns:repeat(4,1fr)}

/* Header */
.hdr{position:sticky;top:0;z-index:40;backdrop-filter:blur(12px);background:var(--overlay);border-bottom:1px solid var(--border)}
.hdr__in{display:flex;align-items:center;gap:18px;min-height:66px}
.hdr__brand{font-family:var(--font-heading);font-weight:700;font-size:1.16rem;text-decoration:none;letter-spacing:-.02em}
.hdr__nav{display:flex;gap:22px;margin-left:auto;align-items:center}
.hdr__nav a:not(.btn){text-decoration:none;font-size:.95rem;color:var(--muted);font-weight:500}
.hdr__nav a:not(.btn):hover{color:var(--text)}
.hdr__nav a.btn{text-decoration:none}
.hdr__burger{display:none;margin-left:auto;background:none;border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);padding:8px 11px;font-size:1.1rem;cursor:pointer;line-height:1}

/* Hero */
.hero{padding-block:clamp(60px,9vw,120px);position:relative;overflow:hidden}
.hero::before{content:"";position:absolute;inset:-30% -10% auto;height:70%;background:radial-gradient(60% 60% at 50% 0%,var(--tint),transparent 70%);pointer-events:none}
.hero__in{position:relative}
.hero--split .hero__in{display:grid;grid-template-columns:1.05fr .95fr;gap:52px;align-items:center}
.hero--center .hero__in{max-width:820px;margin:0 auto;text-align:center}
.hero p.hero__sub{font-size:clamp(1.04rem,1.6vw,1.22rem);color:var(--muted);max-width:56ch;margin-bottom:30px}
.hero--center p.hero__sub{margin-inline:auto}
.hero__cta{display:flex;gap:12px;flex-wrap:wrap}
.hero--center .hero__cta{justify-content:center}
.hero__badges{display:flex;gap:8px;flex-wrap:wrap;margin-top:26px;padding:0;list-style:none}
.hero--center .hero__badges{justify-content:center}
.hero__badges li{font-size:.83rem;color:var(--muted);border:1px solid var(--border);border-radius:999px;padding:5px 13px;background:var(--surface)}
.hero__media img{width:100%;aspect-ratio:4/3;object-fit:cover;border-radius:var(--radius);box-shadow:var(--shadow)}

/* Logos + stats */
.logos{display:flex;flex-wrap:wrap;gap:14px 34px;align-items:center;justify-content:center;padding-block:30px;border-block:1px solid var(--border)}
.logos span{font-family:var(--font-heading);font-weight:600;color:var(--muted);font-size:1.02rem;letter-spacing:.01em}
.logos__label{width:100%;text-align:center;font-size:.78rem;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin-bottom:4px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:20px;text-align:center}
.stats b{display:block;font-family:var(--font-heading);font-size:clamp(1.9rem,4vw,2.8rem);line-height:1;color:var(--primary)}
.stats span{color:var(--muted);font-size:.94rem}

/* Features */
.feat{display:flex;flex-direction:column;gap:10px}
.feat__icon{width:44px;height:44px;display:grid;place-items:center;border-radius:var(--radius-sm);background:var(--tint);font-size:1.3rem}
.feat h3{margin:0;font-size:1.08rem}
.feat p{margin:0;color:var(--muted);font-size:.96rem}

/* Services */
.svc{display:flex;flex-direction:column;gap:12px;height:100%}
.svc__top{display:flex;justify-content:space-between;gap:14px;align-items:flex-start}
.svc h3{margin:0;font-size:1.1rem}
.svc__price{font-family:var(--font-heading);font-weight:700;font-size:1.1rem;white-space:nowrap}
.svc__meta{display:flex;flex-wrap:wrap;gap:8px;font-size:.82rem;color:var(--muted);margin-top:auto;padding-top:6px}
.svc__meta span{background:var(--bg);border:1px solid var(--border);border-radius:999px;padding:3px 10px}
.svc p{margin:0;color:var(--muted);font-size:.95rem}

/* Products */
.prod{display:flex;flex-direction:column;padding:0;overflow:hidden}
.prod img{width:100%;aspect-ratio:4/3;object-fit:cover}
.prod__body{padding:20px;display:flex;flex-direction:column;gap:9px;flex:1}
.prod h3{margin:0;font-size:1.04rem}
.prod p{margin:0;color:var(--muted);font-size:.92rem}
.prod__foot{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:auto;padding-top:6px}
.prod__price{font-family:var(--font-heading);font-weight:700;font-size:1.12rem}
.prod__stock{font-size:.8rem;color:var(--muted)}

/* Pricing */
.tier{display:flex;flex-direction:column;gap:14px;height:100%}
.tier--featured{border-color:var(--primary);box-shadow:var(--shadow);position:relative}
.tier--featured::after{content:"Most popular";position:absolute;top:-11px;left:26px;background:var(--primary);color:var(--primary-text);font-size:.72rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;padding:3px 10px;border-radius:999px}
.tier__price{font-family:var(--font-heading);font-size:2.3rem;font-weight:700;line-height:1}
.tier__price small{font-size:.92rem;font-weight:500;color:var(--muted);font-family:var(--font-body)}
.tier ul{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:9px;font-size:.94rem}
.tier li{display:flex;gap:9px;align-items:flex-start;color:var(--muted)}
.tier li::before{content:"✓";color:var(--accent);font-weight:700;flex:none}
.tier .btn{margin-top:auto}

/* Gallery / team */
.gal img{width:100%;aspect-ratio:1/1;object-fit:cover;border-radius:var(--radius)}
.gal figure{margin:0}
.gal figcaption{font-size:.86rem;color:var(--muted);margin-top:8px}
.team__card{text-align:center}
.team__card img{width:96px;height:96px;border-radius:50%;object-fit:cover;margin:0 auto 14px}
.team__card h3{margin:0 0 2px;font-size:1.02rem}
.team__card .role{color:var(--primary);font-size:.87rem;font-weight:600}
.team__card p{color:var(--muted);font-size:.9rem;margin:8px 0 0}

/* Testimonials */
.quote{display:flex;flex-direction:column;gap:14px;height:100%}
.quote p{margin:0;font-size:1.03rem;line-height:1.6}
.quote footer{margin-top:auto;font-size:.9rem;color:var(--muted)}
.quote strong{color:var(--text);display:block;font-weight:600}

/* FAQ */
.faq{max-width:760px}
.faq details{border-bottom:1px solid var(--border);padding:18px 0}
.faq summary{cursor:pointer;font-weight:600;font-size:1.03rem;list-style:none;display:flex;justify-content:space-between;gap:16px;align-items:center}
.faq summary::-webkit-details-marker{display:none}
.faq summary::after{content:"+";color:var(--primary);font-size:1.4rem;line-height:1;flex:none}
.faq details[open] summary::after{content:"−"}
.faq p{margin:12px 0 0;color:var(--muted);font-size:.98rem}

/* Hours */
.hours{max-width:520px;list-style:none;margin:0;padding:0}
.hours li{display:flex;justify-content:space-between;gap:20px;padding:11px 0;border-bottom:1px solid var(--border);font-size:.98rem}
.hours li[data-today="1"]{font-weight:700;color:var(--primary)}
.hours .closed{color:var(--muted)}

/* Rich text */
.rich{display:grid;gap:36px;align-items:center}
.rich--left,.rich--right{grid-template-columns:1fr 1fr}
.rich--right .rich__media{order:-1}
.rich__body p{color:var(--muted)}
.rich img{border-radius:var(--radius);aspect-ratio:4/3;object-fit:cover;width:100%}

/* Contact */
.contact{display:grid;grid-template-columns:1fr 1fr;gap:38px;align-items:start}
.contact__details{list-style:none;margin:22px 0 0;padding:0;display:flex;flex-direction:column;gap:12px;font-size:.97rem}
.contact__details li{display:flex;gap:11px;color:var(--muted)}
.contact__details a{color:var(--text);text-decoration:none}

/* Forms */
.field{display:flex;flex-direction:column;gap:6px;margin-bottom:14px}
.field label{font-size:.87rem;font-weight:600}
.field input,.field textarea,.field select{font:inherit;font-size:.97rem;padding:.72em .85em;border-radius:var(--radius-sm);border:1px solid var(--border);background:var(--bg);color:var(--text);width:100%}
.field input:focus,.field textarea:focus,.field select:focus{outline:2px solid var(--primary);outline-offset:1px;border-color:transparent}
.field textarea{min-height:120px;resize:vertical}
.field--row{display:grid;grid-template-columns:1fr 1fr;gap:14px}

/* CTA band */
.band{background:var(--primary);color:var(--primary-text);border-radius:var(--radius);padding:clamp(32px,5vw,56px);text-align:center}
.band h2{margin-bottom:.35em}
.band p{opacity:.88;max-width:54ch;margin:0 auto 26px}
.band .btn{background:var(--primary-text);color:var(--primary)}

/* Footer */
.ftr{border-top:1px solid var(--border);padding-block:34px;color:var(--muted);font-size:.9rem}
.ftr__in{display:flex;flex-wrap:wrap;gap:16px;justify-content:space-between;align-items:center}
.ftr a{color:var(--muted);text-decoration:none;margin-right:18px}
.ftr a:hover{color:var(--text)}
.ftr__mark{opacity:.75}

@media (max-width:900px){
  .grid--3,.grid--4{grid-template-columns:repeat(2,1fr)}
  .hero--split .hero__in{grid-template-columns:1fr;gap:36px}
  /* Narrow screens lead with the headline; the image follows the copy. */
  .hero__media img{aspect-ratio:16/10}
  .rich--left,.rich--right{grid-template-columns:1fr}
  .rich--right .rich__media{order:0}
  .contact{grid-template-columns:1fr}
}
@media (max-width:640px){
  body{font-size:16px}
  .grid--2,.grid--3,.grid--4{grid-template-columns:1fr}
  .field--row{grid-template-columns:1fr}
  .hdr__burger{display:block}
  .hdr__nav{display:none;position:absolute;top:100%;left:0;right:0;background:var(--bg);border-bottom:1px solid var(--border);flex-direction:column;align-items:stretch;gap:0;padding:8px 20px 18px}
  .hdr__nav[data-open="1"]{display:flex}
  .hdr__nav a:not(.btn){padding:11px 0;border-bottom:1px solid var(--border)}
  .hdr__nav .btn{margin-top:12px}
}
@media (prefers-reduced-motion:reduce){
  *{animation-duration:.01ms !important;transition-duration:.01ms !important;scroll-behavior:auto !important}
}
`;
}

export { escapeHtml };
