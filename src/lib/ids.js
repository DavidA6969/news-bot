import crypto from 'node:crypto';

const ALPHABET = 'abcdefghijklmnopqrstuvwxyz0123456789';

/** Short, URL-safe, collision-resistant id with a readable prefix. */
export function id(prefix = '') {
  const bytes = crypto.randomBytes(16);
  let out = '';
  for (const b of bytes) out += ALPHABET[b % ALPHABET.length];
  return prefix ? `${prefix}_${out}` : out;
}

export function token(bytes = 24) {
  return crypto.randomBytes(bytes).toString('base64url');
}

/** Turn arbitrary text into a hostname-safe slug. */
export function slugify(input, fallback = 'site') {
  const slug = String(input || '')
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40)
    .replace(/-+$/g, '');
  return slug || fallback;
}

/**
 * Find a slug that isn't taken yet, appending a short suffix when needed.
 * `exists` is a predicate so this works against any table.
 */
export function uniqueSlug(base, exists) {
  const root = slugify(base);
  if (!exists(root)) return root;
  for (let i = 2; i <= 99; i++) {
    const candidate = `${root}-${i}`;
    if (!exists(candidate)) return candidate;
  }
  return `${root}-${id().slice(0, 6)}`;
}
