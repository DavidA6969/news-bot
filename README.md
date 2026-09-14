# LaunchKit

An AI website builder that ships the parts other builders leave out: a real
booking calendar, card checkout, and your own domain.

Describe a business in a sentence and LaunchKit generates the landing page **and
the machinery behind it** — services with real durations and prices, opening
hours for that trade, deposits that hold a slot, a product catalogue with stock,
and a theme that matches. Publish in one click; point a domain at it when ready.

```bash
npm install
npm start          # http://localhost:3000
```

It runs with no configuration at all. Without an API key the built-in template
engine writes the site; without Stripe keys checkout runs in sandbox mode. Both
paths are complete — you can book an appointment, pay a deposit and see the
slot confirm before configuring anything.

## Why this is different

A landing page is not a business. The button that says "Book now" is the product,
and it's the part that usually turns out to be a placeholder.

| What a business needs | Typical AI builder | LaunchKit |
| --- | --- | --- |
| Landing page, custom domain | Yes | Yes |
| Live availability calendar | Embed someone else's widget | Built in |
| Double-booking prevention | — | Re-checked inside the write transaction |
| Deposits that hold a slot | — | Pay-to-confirm, holds auto-expire |
| Group classes with capacity | — | Seats tracked per session |
| Card checkout and inventory | Paste a store embed | Stripe Checkout, stock decremented on payment |
| Timezone and DST correctness | — | Handled |

## How it works

1. **Describe it** — one paragraph about the business.
2. **Generate** — Claude returns a full site spec *plus* the services, products
   and trading week. Everything is normalised and repaired server-side, so
   imperfect model output can never produce a broken site.
3. **Publish** — the draft is snapshotted; the live site serves that snapshot
   while you keep editing.
4. **Connect a domain** — add two DNS records, LaunchKit verifies both.

## Configuration

Copy `.env.example` to `.env`. Every value is optional.

| Variable | Effect when set |
| --- | --- |
| `ANTHROPIC_API_KEY` | Sites are written by Claude instead of the template engine |
| `ANTHROPIC_MODEL` | Defaults to `claude-opus-5` |
| `STRIPE_SECRET_KEY` | Checkout uses Stripe instead of the sandbox card page |
| `STRIPE_WEBHOOK_SECRET` | Webhook signatures are verified (set this in production) |
| `APP_ORIGIN` | Public origin used to build checkout and confirmation links |
| `APP_DOMAIN` | Hostname customers point their DNS at |
| `SESSION_SECRET` | Change in production |
| `PORT`, `DATA_DIR` | Defaults: `3000`, `./data` |

Stripe webhook endpoint: `POST /webhooks/stripe`, subscribed to
`checkout.session.completed`.

## The booking engine

The part worth reading is `src/services/booking.js`.

**Two kinds of service contend for time differently.** An *exclusive* service
(`capacity = 1`) blocks the calendar against every other exclusive appointment it
overlaps, because one provider can't be in two places. A *group* service
(`capacity > 1`) is a class in its own room: bookings at the same start time share
the seat count, and it neither blocks nor is blocked by one-to-one appointments.

**Slots respect more than opening hours.** Service duration, turnaround buffer
after each booking, a minimum lead time, blackout dates, and a 60-day horizon all
narrow what's offered. A service that can't finish before closing isn't offered
at that time at all.

**Two people clicking the same slot produce one booking.** Availability is
re-checked inside the same transaction that writes the appointment, so the loser
gets a 409 rather than a collision.

**Deposits hold, they don't confirm.** A service with a deposit creates a `hold`,
not a booking. The slot stays blocked while the customer pays and is released
automatically if they don't — no manual cleanup, no permanently stuck slots.

**Time is stored in UTC; hours are written in local wall-clock.** Conversion goes
through `Intl.DateTimeFormat`, so DST is handled properly — including the hour
that doesn't exist on a spring-forward morning, which is never offered as a slot.

## Architecture

```
server.js                 Express app assembly
src/config.js             Env loading, feature detection
src/db/                   SQLite schema + connection
src/lib/                  time (DST-aware), auth, validation, http helpers
src/ai/                   spec schema + normaliser, prompt, tool schema,
                          generator (Claude), templates (offline fallback)
src/render/               spec -> HTML: theme/CSS, blocks, page, client runtime
src/routes/               auth, dashboard API, published sites + public API
src/services/             booking engine, payments, domains, site persistence
public/                   landing page + builder UI (no build step)
test/                     70 tests
```

**The site spec is the contract.** One JSON document describes a site; the AI
writes it, the editor mutates it, the renderer consumes it. Everything entering
`normalizeSpec()` comes out valid — unknown block types dropped, invalid colours
replaced, timezones checked — so no later layer has to defend itself.

**Published sites are self-contained.** One HTML document with inlined CSS and
the booking/checkout runtime. No build step, no external JavaScript, and webfonts
load non-render-blocking so a slow font CDN can't delay a customer's first paint.

**Two datastores, one file.** Design lives in the spec; bookings, orders and
inventory live in SQLite tables. Regenerating a site's copy never touches a live
catalogue.

## Tests

```bash
npm test
```

70 tests covering DST and timezone conversion, slot generation, buffers, capacity,
hold expiry, double-booking, payment idempotency and webhook signature
verification, ownership isolation, XSS escaping, and the full HTTP flow from
signup through booking and checkout.

## Security notes

- Passwords hashed with scrypt; sessions are httpOnly, SameSite=Lax cookies.
- Login gives one error for a wrong password and an unknown account, so the
  endpoint can't enumerate registered emails.
- Every dashboard route checks site ownership.
- All rendered content is escaped — generated copy is treated as untrusted.
- Stripe webhooks verify the HMAC signature over the raw request body, with a
  timestamp tolerance against replay.
- Public write endpoints are rate limited.
