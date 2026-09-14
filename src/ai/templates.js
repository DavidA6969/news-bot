/**
 * Deterministic site generator.
 *
 * This runs when no ANTHROPIC_API_KEY is configured, or when a generation call
 * fails. It matches the prompt against a set of trade archetypes and builds a
 * complete, coherent site — services, products, hours and copy included — so
 * the product is never dead in the water.
 */
import { normalizeSpec } from './spec.js';
import { normalizeBusiness } from './business.js';

const ARCHETYPES = [
  {
    key: 'salon',
    match: ['salon', 'hair', 'barber', 'stylist', 'haircut', 'blowout', 'balayage', 'colour', 'color'],
    theme: 'linen',
    favicon: '💈',
    name: 'Copper & Comb',
    tagline: 'Precision cuts and lived-in color',
    headline: 'A chair that fits the way you actually wear your hair',
    sub: 'Independent stylists, unhurried appointments, and color that grows out gracefully.',
    services: [
      { name: 'Cut & Style', description: 'Consultation, shampoo, precision cut and a finish you can repeat at home.', durationMin: 60, price: 75, bufferMin: 10 },
      { name: 'Balayage & Gloss', description: 'Hand-painted lightening with a toning gloss. Includes a cut.', durationMin: 180, price: 245, deposit: 50, bufferMin: 15 },
      { name: 'Root Touch-Up', description: 'Single-process color on regrowth, plus a blow-dry.', durationMin: 90, price: 110, bufferMin: 10 },
      { name: 'Beard Trim', description: 'Shape, line-up and hot towel finish.', durationMin: 30, price: 35 },
    ],
    products: [
      { name: 'Daily Repair Shampoo', description: 'Sulphate-free, safe on colored hair. 300ml.', price: 28, inventory: 40 },
      { name: 'Texture Paste', description: 'Matte hold that reworks through the day. 75ml.', price: 24, inventory: 25 },
      { name: 'Gift Card', description: 'Any amount, redeemable on any service.', price: 100 },
    ],
    availability: { weekdays: [2, 3, 4, 5, 6], startTime: '10:00', endTime: '19:00' },
    faq: [
      ['How far ahead should I book color?', 'Two to three weeks for balayage, about a week for a root touch-up. Cuts can usually be squeezed in sooner.'],
      ['Do you take a deposit?', 'Only on color services over three hours. It comes straight off your final bill.'],
      ['What if I need to reschedule?', 'No charge with 24 hours notice. Inside 24 hours the deposit is retained.'],
    ],
  },
  {
    key: 'dental',
    match: ['dental', 'dentist', 'orthodont', 'clinic', 'doctor', 'medical', 'physio', 'chiroprac', 'therapist', 'counsel', 'psycholog'],
    theme: 'clinic',
    favicon: '🩺',
    name: 'Meridian Health',
    tagline: 'Calm, unhurried care',
    headline: 'Appointments that start on time',
    sub: 'Same-week availability, transparent pricing, and clinicians who explain what they are doing and why.',
    services: [
      { name: 'New Patient Consultation', description: 'Full assessment, history and a written plan. Allow a full hour.', durationMin: 60, price: 140, deposit: 40 },
      { name: 'Follow-Up Visit', description: 'Review, adjustment and next steps.', durationMin: 30, price: 85 },
      { name: 'Hygiene & Clean', description: 'Scale, polish and home-care coaching.', durationMin: 45, price: 110, bufferMin: 15 },
    ],
    products: [
      { name: 'Home Care Kit', description: 'Brush, interdental picks and rinse — the set we actually recommend.', price: 45, inventory: 60 },
    ],
    availability: { weekdays: [1, 2, 3, 4, 5], startTime: '08:00', endTime: '17:00' },
    faq: [
      ['Do you take insurance?', 'We are out of network but provide an itemised receipt that most plans reimburse against.'],
      ['How early should I arrive?', 'Ten minutes for a first visit so the paperwork does not eat your appointment.'],
      ['What is the cancellation policy?', 'Free up to 24 hours before. The deposit covers late cancellations.'],
    ],
  },
  {
    key: 'fitness',
    match: ['gym', 'fitness', 'yoga', 'pilates', 'trainer', 'crossfit', 'studio class', 'workout', 'strength', 'martial'],
    theme: 'neon',
    favicon: '🏋️',
    name: 'Groundwork',
    tagline: 'Strength, coached properly',
    headline: 'Small classes. Real coaching. Nobody left in the back row.',
    sub: 'Capped at twelve so a coach can actually see you lift. First class is on us.',
    services: [
      { name: 'Foundations Class', description: 'The on-ramp. Movement screen and the six lifts everything else is built on.', durationMin: 60, price: 0, capacity: 12 },
      { name: 'Strength Class', description: 'Coached barbell session — squat, press, pull, carry.', durationMin: 60, price: 28, capacity: 12 },
      { name: 'Vinyasa Flow', description: 'Breath-led flow for every level. Mats provided.', durationMin: 75, price: 24, capacity: 16 },
      { name: '1:1 Personal Training', description: 'One hour, one coach, a plan built around your schedule.', durationMin: 60, price: 95, deposit: 25 },
    ],
    products: [
      { name: '10-Class Pass', description: 'Ten classes, no expiry. Works on any group session.', price: 240 },
      { name: 'Monthly Unlimited', description: 'Every class, every week.', price: 165 },
      { name: 'Lifting Belt', description: '10mm leather, sized S–XL.', price: 78, inventory: 15 },
    ],
    availability: { weekdays: [1, 2, 3, 4, 5, 6], startTime: '06:00', endTime: '20:00' },
    faq: [
      ['I have never lifted before. Is that a problem?', 'No. Foundations exists exactly for that, and it is free.'],
      ['How full do classes get?', 'Twelve people maximum for strength, sixteen for flow. Booking is the only way to hold a spot.'],
      ['Can I freeze my membership?', 'Yes, up to two months a year, no charge.'],
    ],
  },
  {
    key: 'restaurant',
    match: ['restaurant', 'cafe', 'coffee', 'bistro', 'kitchen', 'dining', 'bakery', 'brunch', 'wine bar', 'pizzeria', 'eatery'],
    theme: 'botanic',
    favicon: '🍽️',
    name: 'Fennel & Ash',
    tagline: 'Seasonal plates, wood fire',
    headline: 'A short menu, changed every week, cooked over live fire',
    sub: 'Twenty-four covers a night. Book ahead — we only hold a few walk-in seats at the bar.',
    services: [
      { name: 'Dinner Reservation', description: 'Table for the evening service. Tell us about allergies in the notes.', durationMin: 120, price: 0, capacity: 6 },
      { name: "Chef's Counter", description: 'Six seats facing the fire. Set menu, paired pours, two and a half hours.', durationMin: 150, price: 125, deposit: 40, capacity: 6 },
      { name: 'Private Dining', description: 'The back room, up to sixteen guests, bespoke menu.', durationMin: 180, price: 0, deposit: 250, capacity: 16 },
    ],
    products: [
      { name: 'Gift Card', description: 'Redeemable against any booking or the bottle shop.', price: 100 },
      { name: 'House Chilli Oil', description: 'The one on every table. 200ml jar.', price: 14, inventory: 50 },
    ],
    availability: { weekdays: [3, 4, 5, 6, 0], startTime: '17:00', endTime: '22:00' },
    faq: [
      ['Do you cater to dietary requirements?', 'Yes — tell us in the booking notes and the kitchen will build around it.'],
      ['How long do we have the table?', 'Two hours for dinner, two and a half at the counter.'],
      ['Is there a cancellation fee?', 'Only on the counter and private dining, where the deposit is held inside 48 hours.'],
    ],
  },
  {
    key: 'photography',
    match: ['photo', 'photograph', 'videograph', 'film', 'portrait', 'wedding', 'headshot', 'creative studio'],
    theme: 'studio',
    favicon: '📷',
    name: 'North Light',
    tagline: 'Portraits with a pulse',
    headline: 'Photographs that look like the person, not the pose',
    sub: 'Natural-light studio, unrushed sessions, and every frame delivered in two weeks.',
    services: [
      { name: 'Headshot Session', description: 'Forty-five minutes, two looks, ten retouched finals.', durationMin: 45, price: 295, deposit: 95, bufferMin: 15 },
      { name: 'Portrait Session', description: 'Ninety minutes in studio or on location. Twenty-five finals.', durationMin: 90, price: 550, deposit: 150, bufferMin: 30 },
      { name: 'Wedding Consultation', description: 'A free half hour to talk through dates, coverage and what you actually want remembered.', durationMin: 30, price: 0 },
    ],
    products: [
      { name: 'Extra Retouched Image', description: 'Beyond the package. Delivered in 48 hours.', price: 45 },
      { name: 'Fine Art Print — A3', description: 'Hahnemühle cotton rag, signed.', price: 120, inventory: 20 },
      { name: 'Session Gift Card', description: 'For someone who hates being photographed. They will thank you later.', price: 300 },
    ],
    availability: { weekdays: [2, 3, 4, 5, 6], startTime: '09:00', endTime: '18:00' },
    faq: [
      ['What should I wear?', 'Solid colors photograph best. Bring two or three options and we will pick together.'],
      ['When do I get the images?', 'A gallery within fourteen days, retouched finals within three weeks.'],
      ['Is the deposit refundable?', 'It transfers to any date inside six months. Cancellations inside a week retain it.'],
    ],
  },
  {
    key: 'consulting',
    match: ['consult', 'coach', 'advisor', 'agency', 'freelance', 'strategy', 'accountant', 'bookkeep', 'legal', 'lawyer', 'attorney', 'tutor', 'lesson', 'teaching'],
    theme: 'midnight',
    favicon: '📈',
    name: 'Kessler & Co',
    tagline: 'Senior help, no bench',
    headline: 'The person you talk to is the person who does the work',
    sub: 'No juniors, no 40-page deck. A diagnosis in week one and something shipped by week four.',
    services: [
      { name: 'Intro Call', description: 'Thirty minutes to work out whether this is worth either of our time. Free.', durationMin: 30, price: 0 },
      { name: 'Strategy Session', description: 'Ninety minutes on one hard problem, with a written summary the same day.', durationMin: 90, price: 450, deposit: 150 },
      { name: 'Diagnostic Sprint', description: 'A half day reviewing the business, ending in a prioritised plan.', durationMin: 240, price: 1800, deposit: 500 },
    ],
    products: [
      { name: 'Playbook Bundle', description: 'The templates and models we use on every engagement.', price: 180 },
      { name: 'Async Review', description: 'Send a deck or a plan, get a recorded teardown in 72 hours.', price: 350 },
    ],
    availability: { weekdays: [1, 2, 3, 4, 5], startTime: '09:00', endTime: '17:00' },
    faq: [
      ['How quickly can we start?', 'Intro calls are usually available inside a week; engagements start the following month.'],
      ['Do you work with early-stage companies?', 'Yes, though the sprint tends to fit better than a retainer before Series A.'],
      ['What happens on the intro call?', 'You describe the problem, we tell you honestly whether we are the right help.'],
    ],
  },
  {
    key: 'spa',
    match: ['spa', 'massage', 'nails', 'beauty', 'facial', 'wax', 'lash', 'aesthet', 'tattoo', 'piercing', 'brow'],
    theme: 'botanic',
    favicon: '🌿',
    name: 'Still Room',
    tagline: 'An hour that actually belongs to you',
    headline: 'Treatments without the upsell',
    sub: 'One therapist, one room, no music you have to endure. Book online in under a minute.',
    services: [
      { name: 'Deep Tissue Massage', description: 'Sixty or ninety minutes of focused work on the parts that actually hurt.', durationMin: 60, price: 120, bufferMin: 15 },
      { name: 'Signature Facial', description: 'Cleanse, exfoliate, mask and massage, matched to your skin on the day.', durationMin: 75, price: 145, bufferMin: 15 },
      { name: 'Fine-Line Tattoo', description: 'Small custom piece. Design consultation included.', durationMin: 120, price: 280, deposit: 80, bufferMin: 30 },
    ],
    products: [
      { name: 'Aftercare Balm', description: 'Unscented, fragrance-free, the one we use in the room. 50ml.', price: 32, inventory: 45 },
      { name: 'Gift Card', description: 'Any treatment, valid twelve months.', price: 150 },
    ],
    availability: { weekdays: [2, 3, 4, 5, 6], startTime: '10:00', endTime: '19:00' },
    faq: [
      ['Will it hurt?', 'Deep tissue can be intense but never unbearable — say the word and pressure changes immediately.'],
      ['Do I need a patch test?', 'For tinting and some facials, yes — 48 hours ahead, and it takes five minutes.'],
      ['Can I book back-to-back treatments?', 'Yes. Book them separately and we will keep the room for you.'],
    ],
  },
  {
    key: 'home',
    match: ['plumb', 'electric', 'contractor', 'roofing', 'landscap', 'cleaning', 'handyman', 'hvac', 'garage', 'pest', 'moving', 'detailing', 'locksmith'],
    theme: 'studio',
    favicon: '🔧',
    name: 'Harbour Trades',
    tagline: 'Booked online, turns up on time',
    headline: 'A tradesperson who gives you a window and keeps it',
    sub: 'Fixed callout, itemised quote before any work starts, and a two-year guarantee on labour.',
    services: [
      { name: 'Diagnostic Callout', description: 'We come out, find the fault and quote before touching anything. Fee comes off the repair.', durationMin: 60, price: 95, bufferMin: 30 },
      { name: 'Standard Repair Visit', description: 'Two-hour window for common repairs. Parts billed at cost.', durationMin: 120, price: 180, bufferMin: 30 },
      { name: 'Annual Service', description: 'Full inspection, clean and safety certificate.', durationMin: 90, price: 145, bufferMin: 30 },
    ],
    products: [
      { name: 'Care Plan — Annual', description: 'Yearly service, priority booking and no callout fee.', price: 220 },
    ],
    availability: { weekdays: [1, 2, 3, 4, 5, 6], startTime: '07:00', endTime: '17:00' },
    faq: [
      ['Do you charge for quotes?', 'The callout covers diagnosis and is deducted from the repair if you go ahead.'],
      ['How tight is the arrival window?', 'Two hours, and you get a text when we are thirty minutes out.'],
      ['Are you insured?', 'Fully insured and certified. Certificates are on request, no questions asked.'],
    ],
  },
  {
    key: 'pets',
    match: ['pet', 'dog', 'cat', 'groom', 'vet', 'kennel', 'boarding', 'walker'],
    theme: 'linen',
    favicon: '🐾',
    name: 'Paws & Patience',
    tagline: 'Gentle grooming, no cages',
    headline: 'One dog at a time, start to finish',
    sub: 'No cage drying, no waiting around in a crate. You drop off, we groom, you collect a calm dog.',
    services: [
      { name: 'Full Groom — Small Breed', description: 'Bath, dry, clip, nails and ears. Up to 10kg.', durationMin: 90, price: 65, bufferMin: 20 },
      { name: 'Full Groom — Large Breed', description: 'The same, with more dog. Over 25kg.', durationMin: 150, price: 110, bufferMin: 30 },
      { name: 'Puppy Intro', description: 'A short, gentle first visit so grooming never becomes a fight.', durationMin: 30, price: 30 },
    ],
    products: [
      { name: 'Oatmeal Shampoo', description: 'What we use on sensitive skin. 500ml.', price: 22, inventory: 30 },
      { name: 'Grooming Gift Card', description: 'Any service, valid a year.', price: 80 },
    ],
    availability: { weekdays: [1, 2, 3, 4, 5, 6], startTime: '08:00', endTime: '17:00' },
    faq: [
      ['My dog is anxious. Can you help?', 'Start with a Puppy Intro regardless of age — a short positive visit changes everything.'],
      ['How long does a groom take?', 'Ninety minutes to two and a half hours depending on coat and size.'],
      ['Do you need vaccination records?', 'Yes, first visit only. A photo of the card is fine.'],
    ],
  },
];

const GENERIC = {
  key: 'generic',
  theme: 'midnight',
  favicon: '🚀',
  name: 'Northside Studio',
  tagline: 'Book online in under a minute',
  headline: 'Everything you need, bookable in a minute',
  sub: 'Clear pricing, real availability, and confirmation the moment you book.',
  services: [
    { name: 'Introductory Session', description: 'A first appointment to understand what you need.', durationMin: 30, price: 0 },
    { name: 'Standard Appointment', description: 'The core service, start to finish.', durationMin: 60, price: 120 },
    { name: 'Extended Appointment', description: 'Ninety minutes for more involved work.', durationMin: 90, price: 175, deposit: 50 },
  ],
  products: [
    { name: 'Gift Card', description: 'Redeemable against any service.', price: 100 },
  ],
  availability: { weekdays: [1, 2, 3, 4, 5], startTime: '09:00', endTime: '17:00' },
  faq: [
    ['How do I book?', 'Pick a service, choose a time that suits you, and confirm. You get an email straight away.'],
    ['Can I reschedule?', 'Yes — free with 24 hours notice.'],
    ['How do I pay?', 'Online at the time of booking, or in person, depending on the service.'],
  ],
};

function pickArchetype(prompt) {
  const text = String(prompt || '').toLowerCase();
  let best = null;
  let bestScore = 0;
  for (const arch of ARCHETYPES) {
    let score = 0;
    for (const keyword of arch.match) if (text.includes(keyword)) score += keyword.length;
    if (score > bestScore) {
      bestScore = score;
      best = arch;
    }
  }
  return best || GENERIC;
}

/** Pull a proper-noun-looking business name out of the prompt, if there is one. */
function detectName(prompt) {
  const quoted = /["“']([^"”']{2,40})["”']/.exec(prompt || '');
  if (quoted) return quoted[1].trim();
  const called = /\b(?:called|named)\s+([A-Za-z0-9&'’.\- ]{2,40})/i.exec(prompt || '');
  if (called) return called[1].trim().replace(/[.,]$/, '');
  return '';
}

export function generateFromTemplate({ prompt = '', businessName = '' } = {}) {
  const arch = pickArchetype(prompt);
  const name = businessName || detectName(prompt) || arch.name;

  const spec = normalizeSpec({
    meta: {
      title: name,
      description: `${name} — ${arch.tagline}. Book online in under a minute.`,
      favicon: arch.favicon,
    },
    settings: {
      timezone: 'America/New_York',
      currency: 'usd',
      businessName: name,
      contactEmail: `hello@${name.toLowerCase().replace(/[^a-z0-9]+/g, '')}.com`,
    },
    theme: { preset: arch.theme },
    nav: {
      brand: name,
      links: [
        { label: 'Services', href: '#services' },
        { label: 'Book', href: '#booking' },
        { label: 'FAQ', href: '#faq' },
        { label: 'Contact', href: '#contact' },
      ],
      cta: { label: 'Book now', href: '#booking' },
    },
    footer: { text: `© ${new Date().getFullYear()} ${name}. ${arch.tagline}.`, links: [] },
    pages: [
      {
        slug: 'index',
        title: 'Home',
        blocks: [
          {
            type: 'hero',
            props: {
              eyebrow: arch.tagline,
              headline: arch.headline,
              subheadline: arch.sub,
              layout: 'split',
              primaryCta: { label: 'Book an appointment', href: '#booking' },
              secondaryCta: { label: 'See services', href: '#services' },
              badges: ['Instant confirmation', 'Secure checkout', 'Free rescheduling'],
            },
          },
          {
            type: 'stats',
            props: {
              items: [
                { value: '4.9★', label: 'Average rating' },
                { value: '<1 min', label: 'To book online' },
                { value: '24h', label: 'Free cancellation' },
              ],
            },
          },
          { type: 'services', props: { heading: 'What we do', subheading: 'Pick a service and choose a time that works. Confirmation is instant.', showPrices: true } },
          { type: 'booking', props: { heading: 'Book an appointment', subheading: 'Live availability — pick a slot and it is yours.' } },
          ...(arch.products?.length
            ? [{ type: 'products', props: { heading: 'Shop', subheading: 'Checkout takes a card and emails a receipt.' } }]
            : []),
          { type: 'hours', props: { heading: 'Opening hours', note: 'Closed on public holidays.' } },
          {
            type: 'testimonials',
            props: {
              heading: 'What people say',
              items: [
                { quote: 'Booked in about thirty seconds on my phone, and the confirmation was in my inbox before I put it down.', author: 'Priya R.' },
                { quote: 'Turned up exactly when they said. That alone puts them ahead of everyone else I have used.', author: 'Marcus T.' },
              ],
            },
          },
          { type: 'faq', props: { heading: 'Frequently asked', items: arch.faq.map(([q, a]) => ({ q, a })) } },
          {
            type: 'contact',
            props: {
              heading: 'Get in touch',
              subheading: 'Questions before you book? Send a note and we will come back to you the same day.',
              showForm: true,
            },
          },
        ],
      },
    ],
  });

  return {
    spec,
    business: normalizeBusiness({
      services: arch.services,
      products: arch.products,
      availability: arch.availability,
    }),
  };
}

export const ARCHETYPE_KEYS = [...ARCHETYPES.map((a) => a.key), GENERIC.key];
