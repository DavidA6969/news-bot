/** Tiny hyperscript. Text is always set via textContent, so data can't inject markup. */
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs && (typeof attrs !== 'object' || Array.isArray(attrs) || attrs instanceof Node)) {
    children.unshift(attrs);
    attrs = null;
  }
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key === 'style' && typeof value === 'object') Object.assign(el.style, value);
    else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === 'html') el.innerHTML = value;
    else if (key === 'value') el.value = value;
    else if (value === true) el.setAttribute(key, '');
    else el.setAttribute(key, value);
  }
  append(el, children);
  return el;
}

function append(parent, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) append(parent, child);
    else if (child instanceof Node) parent.appendChild(child);
    else parent.appendChild(document.createTextNode(String(child)));
  }
}

export const frag = (...children) => {
  const f = document.createDocumentFragment();
  append(f, children);
  return f;
};

export function mount(target, ...children) {
  target.textContent = '';
  append(target, children);
  return target;
}

export function toast(message, kind = '') {
  const host = document.getElementById('toast');
  const node = h('div', { class: `toast${kind ? ` toast--${kind}` : ''}` }, message);
  host.appendChild(node);
  setTimeout(() => {
    node.style.transition = 'opacity .25s';
    node.style.opacity = '0';
    setTimeout(() => node.remove(), 260);
  }, kind === 'err' ? 5200 : 3200);
}

/** Modal dialog. `render(close)` returns the body; resolves with whatever close() is given. */
export function modal(title, render) {
  return new Promise((resolve) => {
    const host = document.getElementById('modal-host');
    const close = (value) => {
      document.removeEventListener('keydown', onKey);
      host.textContent = '';
      resolve(value);
    };
    const onKey = (e) => { if (e.key === 'Escape') close(undefined); };
    document.addEventListener('keydown', onKey);

    const card = h('div', { class: 'modal__card', role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
      h('div', { class: 'modal__head' },
        h('h3', null, title),
        h('button', { class: 'modal__close', type: 'button', 'aria-label': 'Close', onclick: () => close(undefined) }, '×'),
      ),
      render(close),
    );
    const backdrop = h('div', {
      class: 'modal',
      onclick: (e) => { if (e.target === backdrop) close(undefined); },
    }, card);

    mount(host, backdrop);
    const focusable = card.querySelector('input,textarea,select,button:not(.modal__close)');
    if (focusable) focusable.focus();
  });
}

export function confirmDialog(title, message, { confirmLabel = 'Confirm', danger = true } = {}) {
  return modal(title, (close) => frag(
    h('p', { class: 'muted' }, message),
    h('div', { class: 'row', style: { justifyContent: 'flex-end', marginTop: '18px' } },
      h('button', { class: 'btn btn--ghost', type: 'button', onclick: () => close(false) }, 'Cancel'),
      h('button', { class: danger ? 'btn btn--danger' : 'btn btn--primary', type: 'button', onclick: () => close(true) }, confirmLabel),
    ),
  )).then((v) => v === true);
}

/** Swap a button into a spinner for the duration of an async action. */
export async function withBusy(button, label, fn) {
  const original = button.textContent;
  button.disabled = true;
  mount(button, h('span', { class: 'spin' }), label);
  try {
    return await fn();
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

export const money = (cents, currency = 'usd') => {
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency', currency: currency.toUpperCase(),
      minimumFractionDigits: cents % 100 === 0 ? 0 : 2,
    }).format(cents / 100);
  } catch {
    return `$${(cents / 100).toFixed(2)}`;
  }
};

export const duration = (min) => (min < 60 ? `${min} min` : `${Math.floor(min / 60)}h${min % 60 ? ` ${min % 60}m` : ''}`);

export const minutesToLabel = (m) => `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;

export const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
