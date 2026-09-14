PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id            TEXT PRIMARY KEY,
  email         TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  name          TEXT NOT NULL DEFAULT '',
  created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  id         TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at INTEGER NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS sites (
  id             TEXT PRIMARY KEY,
  user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name           TEXT NOT NULL,
  slug           TEXT NOT NULL UNIQUE,
  prompt         TEXT NOT NULL DEFAULT '',
  spec           TEXT NOT NULL,             -- working draft (JSON)
  published_spec TEXT,                      -- live snapshot (JSON)
  published_at   INTEGER,
  created_at     INTEGER NOT NULL,
  updated_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sites_user ON sites(user_id);

CREATE TABLE IF NOT EXISTS domains (
  id            TEXT PRIMARY KEY,
  site_id       TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  hostname      TEXT NOT NULL UNIQUE,
  token         TEXT NOT NULL,              -- value for the _launchkit TXT record
  status        TEXT NOT NULL DEFAULT 'pending',   -- pending | live | failed
  last_error    TEXT NOT NULL DEFAULT '',
  checked_at    INTEGER,
  verified_at   INTEGER,
  created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_domains_site ON domains(site_id);

-- ── Booking ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS services (
  id           TEXT PRIMARY KEY,
  site_id      TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  name         TEXT NOT NULL,
  description  TEXT NOT NULL DEFAULT '',
  duration_min INTEGER NOT NULL DEFAULT 60,
  price_cents  INTEGER NOT NULL DEFAULT 0,
  deposit_cents INTEGER NOT NULL DEFAULT 0,  -- 0 = pay later, >0 = pay to hold slot
  buffer_min   INTEGER NOT NULL DEFAULT 0,   -- cleanup time after each booking
  capacity     INTEGER NOT NULL DEFAULT 1,   -- >1 enables group/class booking
  active       INTEGER NOT NULL DEFAULT 1,
  position     INTEGER NOT NULL DEFAULT 0,
  created_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_services_site ON services(site_id);

CREATE TABLE IF NOT EXISTS availability (
  id         TEXT PRIMARY KEY,
  site_id    TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  service_id TEXT REFERENCES services(id) ON DELETE CASCADE,  -- NULL = all services
  weekday    INTEGER NOT NULL,   -- 0=Sunday .. 6=Saturday
  start_min  INTEGER NOT NULL,   -- minutes from local midnight
  end_min    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_availability_site ON availability(site_id);

CREATE TABLE IF NOT EXISTS blackouts (
  id        TEXT PRIMARY KEY,
  site_id   TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  starts_at INTEGER NOT NULL,   -- epoch ms, UTC
  ends_at   INTEGER NOT NULL,
  reason    TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_blackouts_site ON blackouts(site_id);

CREATE TABLE IF NOT EXISTS appointments (
  id         TEXT PRIMARY KEY,
  site_id    TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  service_id TEXT NOT NULL REFERENCES services(id) ON DELETE CASCADE,
  name       TEXT NOT NULL,
  email      TEXT NOT NULL,
  phone      TEXT NOT NULL DEFAULT '',
  notes      TEXT NOT NULL DEFAULT '',
  starts_at  INTEGER NOT NULL,   -- epoch ms, UTC
  ends_at    INTEGER NOT NULL,   -- includes buffer; what blocks the calendar
  service_end_at INTEGER NOT NULL, -- when the customer actually finishes
  status     TEXT NOT NULL DEFAULT 'confirmed', -- hold | confirmed | cancelled
  hold_expires_at INTEGER,       -- set while status = 'hold'
  order_id   TEXT,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_appointments_site_time ON appointments(site_id, starts_at);
CREATE INDEX IF NOT EXISTS idx_appointments_service ON appointments(service_id, starts_at);

-- ── Payments ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS products (
  id          TEXT PRIMARY KEY,
  site_id     TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  price_cents INTEGER NOT NULL DEFAULT 0,
  image       TEXT NOT NULL DEFAULT '',
  inventory   INTEGER,             -- NULL = unlimited
  active      INTEGER NOT NULL DEFAULT 1,
  position    INTEGER NOT NULL DEFAULT 0,
  created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_products_site ON products(site_id);

CREATE TABLE IF NOT EXISTS orders (
  id           TEXT PRIMARY KEY,
  site_id      TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL,        -- product | deposit
  status       TEXT NOT NULL,        -- pending | paid | cancelled | refunded
  amount_cents INTEGER NOT NULL,
  currency     TEXT NOT NULL DEFAULT 'usd',
  email        TEXT NOT NULL DEFAULT '',
  name         TEXT NOT NULL DEFAULT '',
  items        TEXT NOT NULL DEFAULT '[]',   -- JSON line items
  provider     TEXT NOT NULL,        -- stripe | sandbox
  provider_ref TEXT NOT NULL DEFAULT '',
  created_at   INTEGER NOT NULL,
  paid_at      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_orders_site ON orders(site_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_provider_ref
  ON orders(provider, provider_ref) WHERE provider_ref <> '';

CREATE TABLE IF NOT EXISTS messages (
  id         TEXT PRIMARY KEY,
  site_id    TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  name       TEXT NOT NULL DEFAULT '',
  email      TEXT NOT NULL DEFAULT '',
  body       TEXT NOT NULL DEFAULT '',
  read       INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_site ON messages(site_id, created_at);
