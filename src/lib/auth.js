import crypto from 'node:crypto';
import { id, token } from './ids.js';
import { parseCookies, unauthorized } from './http.js';

const SESSION_COOKIE = 'lk_session';
const SESSION_TTL_MS = 30 * 24 * 60 * 60 * 1000;
const SCRYPT = { N: 16384, r: 8, p: 1, keylen: 64 };

export function hashPassword(password) {
  const salt = crypto.randomBytes(16);
  const hash = crypto.scryptSync(password, salt, SCRYPT.keylen, SCRYPT);
  return `scrypt$${salt.toString('base64')}$${hash.toString('base64')}`;
}

export function verifyPassword(password, stored) {
  const [scheme, saltB64, hashB64] = String(stored || '').split('$');
  if (scheme !== 'scrypt' || !saltB64 || !hashB64) return false;
  const expected = Buffer.from(hashB64, 'base64');
  let actual;
  try {
    actual = crypto.scryptSync(password, Buffer.from(saltB64, 'base64'), expected.length, SCRYPT);
  } catch {
    return false;
  }
  return actual.length === expected.length && crypto.timingSafeEqual(actual, expected);
}

export function createSession(db, userId) {
  const now = Date.now();
  const sid = `${id('ses')}.${token(18)}`;
  db.prepare('INSERT INTO sessions (id, user_id, expires_at, created_at) VALUES (?,?,?,?)').run(
    sid,
    userId,
    now + SESSION_TTL_MS,
    now,
  );
  return sid;
}

export function destroySession(db, sid) {
  if (sid) db.prepare('DELETE FROM sessions WHERE id = ?').run(sid);
}

export function setSessionCookie(res, sid, secure) {
  const attrs = [
    `${SESSION_COOKIE}=${encodeURIComponent(sid)}`,
    'Path=/',
    'HttpOnly',
    'SameSite=Lax',
    `Max-Age=${Math.floor(SESSION_TTL_MS / 1000)}`,
  ];
  if (secure) attrs.push('Secure');
  res.append('Set-Cookie', attrs.join('; '));
}

export function clearSessionCookie(res) {
  res.append('Set-Cookie', `${SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0`);
}

/** Resolve the signed-in user onto `req.user`, or leave it null. */
export function attachUser(db) {
  return (req, _res, next) => {
    req.user = null;
    req.sessionId = null;
    const sid = parseCookies(req.headers.cookie).lk_session;
    if (!sid) return next();

    const row = db
      .prepare(
        `SELECT u.id, u.email, u.name, s.expires_at
           FROM sessions s JOIN users u ON u.id = s.user_id
          WHERE s.id = ?`,
      )
      .get(sid);

    if (!row) return next();
    if (row.expires_at < Date.now()) {
      destroySession(db, sid);
      return next();
    }
    req.user = { id: row.id, email: row.email, name: row.name };
    req.sessionId = sid;
    next();
  };
}

export function requireUser(req, _res, next) {
  if (!req.user) return next(unauthorized());
  next();
}

export { SESSION_COOKIE };
