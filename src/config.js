import fs from 'node:fs';
import path from 'node:path';

// Load .env without a dependency. Node's own loader is used when available.
const envFile = path.resolve(process.cwd(), '.env');
if (fs.existsSync(envFile)) {
  try {
    process.loadEnvFile(envFile);
  } catch {
    for (const line of fs.readFileSync(envFile, 'utf8').split('\n')) {
      const m = /^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/i.exec(line);
      if (m && !(m[1] in process.env)) {
        process.env[m[1]] = m[2].replace(/^["'](.*)["']$/, '$1');
      }
    }
  }
}

const env = process.env;
const port = Number(env.PORT || 3000);

export const config = {
  port,
  appOrigin: (env.APP_ORIGIN || `http://localhost:${port}`).replace(/\/$/, ''),
  appDomain: env.APP_DOMAIN || `localhost:${port}`,
  sessionSecret: env.SESSION_SECRET || 'dev-only-change-me',
  dataDir: env.DATA_DIR || path.resolve(process.cwd(), 'data'),
  anthropic: {
    apiKey: env.ANTHROPIC_API_KEY || '',
    model: env.ANTHROPIC_MODEL || 'claude-opus-5',
    get enabled() {
      return Boolean(env.ANTHROPIC_API_KEY);
    },
  },
  stripe: {
    secretKey: env.STRIPE_SECRET_KEY || '',
    webhookSecret: env.STRIPE_WEBHOOK_SECRET || '',
    get enabled() {
      return Boolean(env.STRIPE_SECRET_KEY);
    },
  },
  isProd: env.NODE_ENV === 'production',
};

export default config;
