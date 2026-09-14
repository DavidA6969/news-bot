import { BLOCK_TYPES, THEME_PRESETS } from './spec.js';

const link = {
  type: 'object',
  properties: {
    label: { type: 'string' },
    href: { type: 'string', description: 'An in-page anchor like "#booking", a page path like "/about", or a full URL.' },
  },
  required: ['label', 'href'],
};

/**
 * Block props vary by type, so `props` is intentionally open. The normalizer
 * in spec.js is what guarantees shape; this schema guides the model.
 */
export const EMIT_SITE_TOOL = {
  name: 'emit_site',
  description:
    'Emit the complete specification for a business website, including the booking services and sellable products the business needs.',
  input_schema: {
    type: 'object',
    properties: {
      site: {
        type: 'object',
        description: 'The page design.',
        properties: {
          meta: {
            type: 'object',
            properties: {
              title: { type: 'string', description: 'Browser title / business name.' },
              description: { type: 'string', description: 'SEO meta description, one sentence.' },
              favicon: { type: 'string', description: 'A single emoji used as the favicon.' },
            },
            required: ['title', 'description', 'favicon'],
          },
          settings: {
            type: 'object',
            properties: {
              timezone: { type: 'string', description: 'IANA timezone, e.g. "America/Chicago".' },
              currency: { type: 'string', description: 'ISO 4217 lowercase, e.g. "usd", "eur", "gbp".' },
              contactEmail: { type: 'string' },
              businessName: { type: 'string' },
            },
            required: ['timezone', 'currency', 'businessName'],
          },
          theme: {
            type: 'object',
            properties: {
              preset: { type: 'string', enum: Object.keys(THEME_PRESETS), description: 'Starting palette. Override individual colors below when the business deserves something specific.' },
              colors: {
                type: 'object',
                description: 'Hex colors. Text must stay readable on bg and surface.',
                properties: {
                  bg: { type: 'string' }, surface: { type: 'string' }, text: { type: 'string' },
                  muted: { type: 'string' }, primary: { type: 'string' }, primaryText: { type: 'string' },
                  accent: { type: 'string' }, border: { type: 'string' },
                },
              },
              radius: { type: 'integer', description: 'Corner radius in px, 0-32.' },
            },
            required: ['preset'],
          },
          nav: {
            type: 'object',
            properties: {
              brand: { type: 'string' },
              links: { type: 'array', items: link },
              cta: link,
            },
            required: ['brand', 'links'],
          },
          footer: {
            type: 'object',
            properties: { text: { type: 'string' }, links: { type: 'array', items: link } },
          },
          pages: {
            type: 'array',
            description: 'The first page is always the home page.',
            items: {
              type: 'object',
              properties: {
                slug: { type: 'string' },
                title: { type: 'string' },
                blocks: {
                  type: 'array',
                  items: {
                    type: 'object',
                    properties: {
                      type: { type: 'string', enum: BLOCK_TYPES },
                      props: { type: 'object', description: 'Props for this block type — see the system prompt for each type\'s fields.' },
                    },
                    required: ['type', 'props'],
                  },
                },
              },
              required: ['slug', 'title', 'blocks'],
            },
          },
        },
        required: ['meta', 'settings', 'theme', 'nav', 'pages'],
      },

      services: {
        type: 'array',
        description: 'Bookable services. Emit these whenever the site has a booking or services block. Empty array if the business truly takes no appointments.',
        items: {
          type: 'object',
          properties: {
            name: { type: 'string' },
            description: { type: 'string' },
            durationMin: { type: 'integer', description: 'Appointment length in minutes.' },
            price: { type: 'number', description: 'Price in major units (dollars, not cents).' },
            deposit: { type: 'number', description: 'Amount charged online to hold the slot. 0 means pay in person.' },
            bufferMin: { type: 'integer', description: 'Turnaround time blocked after each appointment.' },
            capacity: { type: 'integer', description: '1 for one-to-one. Higher for classes or group sessions.' },
          },
          required: ['name', 'description', 'durationMin', 'price'],
        },
      },

      products: {
        type: 'array',
        description: 'Things the site sells outright. Emit these whenever the site has a products or pricing block.',
        items: {
          type: 'object',
          properties: {
            name: { type: 'string' },
            description: { type: 'string' },
            price: { type: 'number', description: 'Price in major units.' },
            inventory: { type: 'integer', description: 'Units in stock. Omit for unlimited (digital goods, gift cards).' },
          },
          required: ['name', 'description', 'price'],
        },
      },

      availability: {
        type: 'object',
        description: 'The default weekly opening hours used to generate bookable slots.',
        properties: {
          weekdays: {
            type: 'array',
            items: { type: 'integer', minimum: 0, maximum: 6 },
            description: 'Open days. 0=Sunday through 6=Saturday.',
          },
          startTime: { type: 'string', description: '24h local opening time, e.g. "09:00".' },
          endTime: { type: 'string', description: '24h local closing time, e.g. "17:30".' },
        },
        required: ['weekdays', 'startTime', 'endTime'],
      },
    },
    required: ['site', 'services', 'products', 'availability'],
  },
};
