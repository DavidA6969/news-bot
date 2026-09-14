import express from 'express';
import { id } from '../lib/ids.js';
import { route, badRequest, conflict } from '../lib/http.js';
import { email as parseEmail, str } from '../lib/validate.js';
import {
  hashPassword, verifyPassword, createSession, destroySession,
  setSessionCookie, clearSessionCookie,
} from '../lib/auth.js';
import config from '../config.js';

export default function authRoutes(db) {
  const router = express.Router();
  const secure = config.appOrigin.startsWith('https://');

  router.post('/signup', route((req, res) => {
    const addr = parseEmail(req.body.email);
    const password = str(req.body.password, 'Password', { min: 8, max: 200, trim: false });
    const name = str(req.body.name ?? '', 'Name', { max: 120 });

    if (db.prepare('SELECT 1 FROM users WHERE email = ?').get(addr)) {
      throw conflict('An account with that email already exists');
    }

    const userId = id('usr');
    db.prepare('INSERT INTO users (id, email, password_hash, name, created_at) VALUES (?,?,?,?,?)')
      .run(userId, addr, hashPassword(password), name, Date.now());

    setSessionCookie(res, createSession(db, userId), secure);
    res.status(201).json({ user: { id: userId, email: addr, name } });
  }));

  router.post('/login', route((req, res) => {
    const addr = parseEmail(req.body.email);
    const password = str(req.body.password, 'Password', { min: 1, max: 200, trim: false });

    const user = db.prepare('SELECT * FROM users WHERE email = ?').get(addr);
    // Same message either way so the endpoint can't be used to enumerate accounts.
    if (!user || !verifyPassword(password, user.password_hash)) {
      throw badRequest('That email and password combination is not right');
    }

    setSessionCookie(res, createSession(db, user.id), secure);
    res.json({ user: { id: user.id, email: user.email, name: user.name } });
  }));

  router.post('/logout', route((req, res) => {
    destroySession(db, req.sessionId);
    clearSessionCookie(res);
    res.json({ ok: true });
  }));

  router.get('/me', route((req, res) => {
    res.json({ user: req.user });
  }));

  return router;
}
