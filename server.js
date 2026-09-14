import path from 'node:path';
import { fileURLToPath } from 'node:url';
import express from 'express';
import config from './src/config.js';
import { db } from './src/db/index.js';
import { attachUser } from './src/lib/auth.js';
import { errorHandler } from './src/lib/http.js';
import authRoutes from './src/routes/auth.js';
import siteRoutes from './src/routes/sites.js';
import publicRoutes, { customDomainMiddleware } from './src/routes/public.js';

const here = path.dirname(fileURLToPath(import.meta.url));

export function createApp(database = db) {
  const app = express();
  app.disable('x-powered-by');
  app.set('trust proxy', true);

  // The Stripe webhook verifies a signature over the exact bytes, so it must
  // not be parsed as JSON before it gets there.
  app.use((req, res, next) =>
    req.path === '/webhooks/stripe' ? next() : express.json({ limit: '1mb' })(req, res, next));
  app.use(express.urlencoded({ extended: false, limit: '1mb' }));
  app.use(attachUser(database));

  // Connected domains resolve to their site before any builder page is served.
  app.use(customDomainMiddleware(database));

  app.use('/api/auth', authRoutes(database));
  app.use('/api', siteRoutes(database));

  app.get('/healthz', (_req, res) => res.json({ ok: true, mode: config.stripe.enabled ? 'stripe' : 'sandbox' }));

  app.use(express.static(path.join(here, 'public'), { extensions: ['html'], maxAge: '1h' }));
  app.use(publicRoutes(database));

  app.get('/app', (_req, res) => res.sendFile(path.join(here, 'public', 'app.html')));
  app.get('/app/*', (_req, res) => res.sendFile(path.join(here, 'public', 'app.html')));

  app.use((req, res) => {
    if (req.path.startsWith('/api/')) return res.status(404).json({ error: 'Not found' });
    res.status(404).sendFile(path.join(here, 'public', '404.html'), (err) => {
      if (err) res.status(404).type('txt').send('Not found');
    });
  });

  app.use(errorHandler);
  return app;
}

const isDirectRun = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isDirectRun) {
  createApp().listen(config.port, () => {
    console.log(`\n  LaunchKit running at ${config.appOrigin}`);
    console.log(`  AI generation : ${config.anthropic.enabled ? `on (${config.anthropic.model})` : 'off — using built-in templates'}`);
    console.log(`  Payments      : ${config.stripe.enabled ? 'Stripe' : 'sandbox (no card is charged)'}\n`);
  });
}

export default createApp;
