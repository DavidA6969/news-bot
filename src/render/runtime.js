/**
 * The script and styles embedded in every published site.
 *
 * Kept dependency-free and inline so a published page is self-contained: one
 * document, no build step, no external JS to go missing.
 */

export const widgetCss = `
.lk-book__calendar{margin:18px 0 0}
.lk-days{display:flex;gap:9px;overflow-x:auto;padding-bottom:10px;scrollbar-width:thin}
.lk-day{flex:none;min-width:78px;border:1px solid var(--border);background:var(--bg);color:var(--text);border-radius:var(--radius-sm);padding:10px 8px;text-align:center;cursor:pointer;font:inherit;transition:border-color .12s ease,background .12s ease}
.lk-day:hover:not(:disabled){border-color:var(--primary)}
.lk-day[aria-pressed="true"]{background:var(--primary);color:var(--primary-text);border-color:var(--primary)}
.lk-day:disabled{opacity:.38;cursor:not-allowed}
.lk-day b{display:block;font-family:var(--font-heading);font-size:1.25rem;line-height:1.2}
.lk-day span{display:block;font-size:.72rem;letter-spacing:.06em;text-transform:uppercase;opacity:.8}
.lk-day em{display:block;font-size:.68rem;font-style:normal;opacity:.75;margin-top:3px}
.lk-book__slots{display:flex;flex-wrap:wrap;gap:9px;margin:18px 0 0}
.lk-slot{border:1px solid var(--border);background:var(--bg);color:var(--text);border-radius:var(--radius-sm);padding:.6em 1em;cursor:pointer;font:inherit;font-size:.93rem;transition:border-color .12s ease}
.lk-slot:hover{border-color:var(--primary)}
.lk-slot[aria-pressed="true"]{background:var(--primary);color:var(--primary-text);border-color:var(--primary)}
.lk-slot small{display:block;font-size:.7rem;opacity:.75}
.lk-book__summary{margin:20px 0 4px;padding:14px 16px;border-radius:var(--radius-sm);background:var(--tint);font-size:.95rem}
.lk-book__summary b{font-weight:700}
[data-book-form]{margin-top:18px}
.lk-msg{margin-top:16px;padding:13px 16px;border-radius:var(--radius-sm);font-size:.94rem;border:1px solid var(--border)}
.lk-msg--ok{border-color:var(--accent);background:color-mix(in srgb, var(--accent) 12%, transparent)}
.lk-msg--err{border-color:#e5484d;background:color-mix(in srgb, #e5484d 12%, transparent)}
.lk-msg--info{color:var(--muted)}
.lk-empty{color:var(--muted);font-size:.95rem;margin:14px 0 0}
@supports not (background: color-mix(in srgb, red 10%, transparent)){
  .lk-msg--ok{background:var(--tint)}
  .lk-msg--err{background:var(--tint)}
}
`;

export function runtimeJs({ siteId, apiBase, timezone, currency }) {
  const cfg = JSON.stringify({ siteId, apiBase, timezone, currency });
  return `(function(){
"use strict";
var CFG = ${cfg};

function money(cents){
  try { return new Intl.NumberFormat('en-US',{style:'currency',currency:CFG.currency.toUpperCase(),minimumFractionDigits:cents%100===0?0:2}).format(cents/100); }
  catch(e){ return '$'+(cents/100).toFixed(2); }
}
function el(tag, attrs, text){
  var n = document.createElement(tag);
  if(attrs) for(var k in attrs){ if(attrs[k]!=null) n.setAttribute(k, attrs[k]); }
  if(text!=null) n.textContent = text;
  return n;
}
function msg(host, kind, text){
  if(!host) return;
  host.innerHTML = '';
  host.appendChild(el('div',{class:'lk-msg lk-msg--'+kind}, text));
}
async function api(path, options){
  var res = await fetch(CFG.apiBase + path, Object.assign({
    headers: {'Content-Type':'application/json'}
  }, options||{}));
  var data = null;
  try { data = await res.json(); } catch(e){ data = null; }
  if(!res.ok) throw new Error((data && data.error) || 'Something went wrong. Please try again.');
  return data;
}

/* ── Mobile nav ─────────────────────────────────────────────────────────── */
var burger = document.querySelector('[data-burger]');
if(burger){
  burger.addEventListener('click', function(){
    var nav = document.querySelector('[data-nav]');
    if(!nav) return;
    var open = nav.getAttribute('data-open') === '1';
    nav.setAttribute('data-open', open ? '0' : '1');
    burger.setAttribute('aria-expanded', String(!open));
  });
}

/* ── Booking widget ─────────────────────────────────────────────────────── */
var root = document.querySelector('[data-booking]');
if(root){
  var select   = root.querySelector('[data-book-select]');
  var calendar = root.querySelector('[data-book-calendar]');
  var slotsBox = root.querySelector('[data-book-slots]');
  var form     = root.querySelector('[data-book-form]');
  var status   = root.querySelector('[data-book-status]');
  var state = { days: [], day: null, slot: null, service: null, loading: false };

  function currentService(){
    return select ? select.value : null;
  }

  function renderDays(){
    calendar.innerHTML = '';
    var open = state.days.filter(function(d){ return d.slots.length; });
    if(!open.length){
      calendar.appendChild(el('p',{class:'lk-empty'},'No availability in the next two weeks. Please get in touch and we will find a time.'));
      return;
    }
    var strip = el('div',{class:'lk-days'});
    state.days.forEach(function(d){
      var count = d.slots.length;
      var date = new Date(d.date + 'T12:00:00Z');
      var btn = el('button',{
        type:'button', class:'lk-day',
        'aria-pressed': String(d.date === state.day),
        'data-date': d.date
      });
      btn.appendChild(el('span',null, date.toLocaleDateString('en-US',{weekday:'short',timeZone:'UTC'})));
      btn.appendChild(el('b',null, String(date.getUTCDate())));
      btn.appendChild(el('em',null, count ? count + (count === 1 ? ' time' : ' times') : 'closed'));
      if(!count) btn.disabled = true;
      btn.addEventListener('click', function(){ selectDay(d.date); });
      strip.appendChild(btn);
    });
    calendar.appendChild(strip);
  }

  function renderSlots(){
    slotsBox.innerHTML = '';
    var day = state.days.filter(function(d){ return d.date === state.day; })[0];
    if(!day){ return; }
    if(!day.slots.length){
      slotsBox.appendChild(el('p',{class:'lk-empty'},'Nothing free on this day.'));
      return;
    }
    day.slots.forEach(function(s){
      var btn = el('button',{
        type:'button', class:'lk-slot',
        'aria-pressed': String(state.slot === s.startsAt)
      });
      btn.appendChild(document.createTextNode(s.label));
      if(s.remaining > 1) btn.appendChild(el('small',null, s.remaining + ' spots left'));
      btn.addEventListener('click', function(){
        state.slot = s.startsAt;
        renderSlots();
        showForm(s);
      });
      slotsBox.appendChild(btn);
    });
  }

  function showForm(slot){
    form.hidden = false;
    var existing = root.querySelector('.lk-book__summary');
    if(existing) existing.remove();
    var svc = state.service || {};
    var bits = [svc.name, slot.dateLabel || '', slot.label].filter(Boolean).join(' · ');
    var summary = el('div',{class:'lk-book__summary'});
    summary.appendChild(el('b',null, bits));
    if(svc.depositCents > 0){
      summary.appendChild(el('div',null, 'A ' + money(svc.depositCents) + ' deposit is taken now to hold this slot. The balance is due at your appointment.'));
    } else if(svc.priceCents > 0){
      summary.appendChild(el('div',null, money(svc.priceCents) + ', payable at your appointment.'));
    }
    form.parentNode.insertBefore(summary, form);
    form.scrollIntoView({behavior:'smooth', block:'nearest'});
  }

  function selectDay(date){
    state.day = date;
    state.slot = null;
    form.hidden = true;
    var summary = root.querySelector('.lk-book__summary');
    if(summary) summary.remove();
    renderDays();
    renderSlots();
  }

  async function load(opts){
    // After a failed booking the calendar is reloaded to pick up whatever
    // changed, but the reason it failed must survive that reload.
    var keepStatus = !!(opts && opts.keepStatus);
    if(state.loading) return;
    state.loading = true;
    state.slot = null;
    form.hidden = true;
    var summary = root.querySelector('.lk-book__summary');
    if(summary) summary.remove();
    slotsBox.innerHTML = '';
    calendar.innerHTML = '<p class="lk-empty">Loading availability…</p>';
    try {
      var data = await api('/availability?serviceId=' + encodeURIComponent(currentService()) + '&days=14');
      state.days = data.days || [];
      state.service = data.service || null;
      var firstOpen = state.days.filter(function(d){ return d.slots.length; })[0];
      state.day = firstOpen ? firstOpen.date : null;
      renderDays();
      renderSlots();
      if(status && !keepStatus) status.innerHTML = '';
    } catch(err){
      calendar.innerHTML = '';
      msg(status, 'err', err.message);
    } finally {
      state.loading = false;
    }
  }

  if(select) select.addEventListener('change', load);

  document.querySelectorAll('[data-book-service]').forEach(function(btn){
    btn.addEventListener('click', function(){
      var id = btn.getAttribute('data-book-service');
      if(select){ select.value = id; }
      root.scrollIntoView({behavior:'smooth', block:'start'});
      load();
    });
  });

  form.addEventListener('submit', async function(ev){
    ev.preventDefault();
    if(!state.slot){ msg(status,'err','Pick a time first.'); return; }
    var submit = form.querySelector('[data-book-submit]');
    submit.disabled = true;
    var original = submit.textContent;
    submit.textContent = 'Booking…';
    msg(status,'info','Holding your slot…');
    try {
      var f = form.elements;
      var body = {
        serviceId: currentService(),
        startsAt: state.slot,
        name: f.name.value,
        email: f.email.value,
        phone: f.phone.value,
        notes: f.notes.value
      };
      var result = await api('/appointments', { method:'POST', body: JSON.stringify(body) });
      if(result.checkoutUrl){
        msg(status,'info','Redirecting to secure checkout…');
        window.location.href = result.checkoutUrl;
        return;
      }
      form.hidden = true;
      var summaryEl = root.querySelector('.lk-book__summary');
      if(summaryEl) summaryEl.remove();
      calendar.innerHTML = '';
      slotsBox.innerHTML = '';
      msg(status,'ok','You are booked for ' + result.appointment.label + '. A confirmation is on its way to ' + body.email + '.');
    } catch(err){
      msg(status,'err', err.message);
      await load({ keepStatus: true });
    } finally {
      submit.disabled = false;
      submit.textContent = original;
    }
  });

  load();
}

/* ── Store checkout ─────────────────────────────────────────────────────── */
document.querySelectorAll('[data-buy-product]').forEach(function(btn){
  btn.addEventListener('click', async function(){
    var section = btn.closest('section');
    var status = section ? section.querySelector('[data-shop-status]') : null;
    btn.disabled = true;
    var original = btn.textContent;
    btn.textContent = 'Starting checkout…';
    try {
      var result = await api('/checkout', {
        method:'POST',
        body: JSON.stringify({ productId: btn.getAttribute('data-buy-product'), quantity: 1 })
      });
      window.location.href = result.checkoutUrl;
    } catch(err){
      msg(status,'err', err.message);
      btn.disabled = false;
      btn.textContent = original;
    }
  });
});

/* ── Contact form ───────────────────────────────────────────────────────── */
var contact = document.querySelector('[data-contact-form]');
if(contact){
  contact.addEventListener('submit', async function(ev){
    ev.preventDefault();
    var status = contact.querySelector('[data-contact-status]');
    var submit = contact.querySelector('button[type=submit]');
    submit.disabled = true;
    try {
      var c = contact.elements;
      await api('/messages', { method:'POST', body: JSON.stringify({
        name: c.name.value, email: c.email.value, body: c.body.value
      })});
      contact.reset();
      msg(status,'ok','Thanks — we have your message and will reply shortly.');
    } catch(err){
      msg(status,'err', err.message);
    } finally {
      submit.disabled = false;
    }
  });
}
})();`;
}
