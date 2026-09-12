#!/usr/bin/env node
/**
 * Searches Google Maps for businesses that have no website and are visibly
 * alive, and prints them as candidate prospects.
 *
 * The filtering here is deterministic on purpose. Google already knows
 * whether a place has a website and when it was last reviewed, so there is
 * nothing to infer. A model only needs to write the note and the angle for
 * whatever survives, which keeps a weekly run cheap.
 *
 *   GOOGLE_MAPS_API_KEY=... node maps-scan.js --city Airdrie --months 3
 *   GOOGLE_MAPS_API_KEY=... node maps-scan.js --all --out candidates.json
 *
 * Exits 2 with a plain message if no key is set, so an unattended run fails
 * loudly instead of silently returning nothing.
 */

const API = "https://maps.googleapis.com/maps/api/place";
const KEY = process.env.GOOGLE_MAPS_API_KEY;

const CITIES = ["Calgary AB", "Edmonton AB", "Red Deer AB", "Airdrie AB"];

const CATEGORIES = [
  "barber shop", "hair salon", "nail salon", "massage therapist",
  "auto repair shop", "auto body shop", "tire shop",
  "electrician", "plumber", "painting contractor", "roofing contractor",
  "concrete contractor", "excavation contractor", "general contractor",
  "home renovation", "deck builder", "landscaping",
  "restaurant", "bakery", "cafe",
  "bookkeeping service", "accountant"
];

// A listing on a platform that is not the business's own site. Google
// sometimes records one of these in the website field; it should not count
// as having a website.
const NOT_A_SITE = [
  "facebook.com", "instagram.com", "linktr.ee", "msha.ke", "x.com",
  "twitter.com", "fresha.com", "janeapp.com", "setmore.com", "vagaro.com",
  "booksy.com", "squire.com", "massagebook.com", "cojilio.com",
  "yellowpages.ca", "yelp.ca", "yelp.com", "canpages.ca", "birdeye.com",
  "linktree", "sites.google.com", "business.site", "wixsite.com",
  "squarespace.com", "weebly.com", "webs.com", "wordpress.com",
  "salonpages.ca", "foodpages.ca", "gr8businesses.com", "groupon.com"
];

function arg(name, fallback) {
  const i = process.argv.indexOf("--" + name);
  return i === -1 ? fallback : (process.argv[i + 1] ?? true);
}
const has = (name) => process.argv.includes("--" + name);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function get(path, params) {
  const qs = new URLSearchParams({ ...params, key: KEY }).toString();
  const res = await fetch(`${API}/${path}/json?${qs}`);
  const body = await res.json();
  if (body.status === "REQUEST_DENIED" || body.status === "INVALID_REQUEST") {
    throw new Error(`${body.status}: ${body.error_message || "no detail"}`);
  }
  if (body.status === "OVER_QUERY_LIMIT") {
    throw new Error("OVER_QUERY_LIMIT: the key's quota or billing is exhausted");
  }
  return body;
}

/** Every place Google returns for one category in one city. */
async function search(query) {
  const out = [];
  let token = null;
  for (let page = 0; page < 3; page++) {
    const body = token
      ? await get("textsearch", { pagetoken: token })
      : await get("textsearch", { query });
    out.push(...(body.results || []));
    token = body.next_page_token;
    if (!token) break;
    await sleep(2000); // Google needs a moment before a page token is valid
  }
  return out;
}

async function details(placeId) {
  const body = await get("details", {
    place_id: placeId,
    reviews_sort: "newest",
    fields: [
      "name", "business_status", "website", "url",
      "user_ratings_total", "rating",
      "formatted_address", "formatted_phone_number",
      "reviews", "types"
    ].join(",")
  });
  return body.result || {};
}

function ownSite(website) {
  if (!website) return null;
  const host = (() => {
    try { return new URL(website).hostname.replace(/^www\./, ""); }
    catch { return ""; }
  })();
  if (!host) return null;
  return NOT_A_SITE.some((bad) => host.includes(bad)) ? null : host;
}

/** Newest review timestamp Google returned, in ms, or 0. */
function newestReview(place) {
  const times = (place.reviews || [])
    .map((r) => (typeof r.time === "number" ? r.time * 1000 : 0))
    .filter(Boolean);
  return times.length ? Math.max(...times) : 0;
}

function slug(name) {
  return String(name).toLowerCase()
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 80);
}

/** Fixture check for the filters, so the logic is verifiable without a key. */
function selfTest() {
  const cases = [
    ["own domain counts as a site", ownSite("https://www.bignoldpainting.com/"), "bignoldpainting.com"],
    ["facebook does not", ownSite("https://facebook.com/someshop"), null],
    ["fresha does not", ownSite("https://www.fresha.com/lvp/x"), null],
    ["a squarespace subdomain does not", ownSite("https://acautocare.squarespace.com/"), null],
    ["a template subdomain does not", ownSite("http://kenzeehaircompany.salonpages.ca/"), null],
    ["no website field", ownSite(""), null],
    ["junk url", ownSite("not a url"), null],
    ["newest review picked", newestReview({ reviews: [{ time: 100 }, { time: 900 }, { time: 500 }] }), 900000],
    ["no reviews", newestReview({ reviews: [] }), 0],
    ["missing reviews key", newestReview({}), 0],
    ["slug", slug("Bayside Barber Shop Inc"), "bayside-barber-shop-inc"],
    ["slug punctuation", slug("Bracha Concrete & Coatings Inc."), "bracha-concrete-coatings-inc"]
  ];
  let bad = 0;
  for (const [label, got, want] of cases) {
    const ok = got === want;
    if (!ok) bad++;
    console.log(`${ok ? "pass" : "FAIL"}  ${label}${ok ? "" : `  got ${JSON.stringify(got)} want ${JSON.stringify(want)}`}`);
  }
  console.log(bad ? `${bad} failing` : "all filter checks pass");
  process.exit(bad ? 1 : 0);
}

async function main() {
  if (has("self-test")) selfTest();

  if (!KEY) {
    console.error(
      "No GOOGLE_MAPS_API_KEY set.\n" +
      "Create a key in Google Cloud Console with the Places API enabled, then\n" +
      "add it to this environment's variables as GOOGLE_MAPS_API_KEY.\n" +
      "Without it Google Maps cannot be searched from here at all."
    );
    process.exit(2);
  }

  const months = Number(arg("months", 3));
  const minReviews = Number(arg("min-reviews", 3));
  const cutoff = Date.now() - months * 30 * 24 * 60 * 60 * 1000;
  const cities = has("all") ? CITIES
    : [String(arg("city", "Airdrie")).replace(/ AB$/, "") + " AB"];
  const cats = arg("category") && arg("category") !== true
    ? [String(arg("category"))] : CATEGORIES;

  const seen = new Set();
  const keep = [];
  const rejected = { hasSite: 0, closed: 0, stale: 0, thin: 0 };

  for (const city of cities) {
    for (const cat of cats) {
      let places;
      try {
        places = await search(`${cat} in ${city}`);
      } catch (e) {
        console.error(`search failed for "${cat} in ${city}": ${e.message}`);
        if (/REQUEST_DENIED|OVER_QUERY_LIMIT/.test(e.message)) process.exit(3);
        continue;
      }

      for (const p of places) {
        if (!p.place_id || seen.has(p.place_id)) continue;
        seen.add(p.place_id);

        let d;
        try { d = await details(p.place_id); }
        catch (e) { console.error(`details failed for ${p.name}: ${e.message}`); continue; }

        if (d.business_status && d.business_status !== "OPERATIONAL") {
          rejected.closed++; continue;
        }
        if (ownSite(d.website)) { rejected.hasSite++; continue; }
        if ((d.user_ratings_total || 0) < minReviews) { rejected.thin++; continue; }

        const newest = newestReview(d);
        if (!newest || newest < cutoff) { rejected.stale++; continue; }

        keep.push({
          slug: slug(d.name),
          name: d.name,
          city: city.replace(/ AB$/, ""),
          category: cat,
          addr: d.formatted_address || "",
          tel: d.formatted_phone_number || "",
          rating: d.rating || null,
          reviewCount: d.user_ratings_total || 0,
          newestReviewAt: new Date(newest).toISOString(),
          platformOnly: d.website || "",
          mapsUrl: d.url || "",
          placeId: p.place_id
        });
      }
    }
  }

  keep.sort((a, b) => b.newestReviewAt.localeCompare(a.newestReviewAt));

  const out = arg("out");
  const payload = JSON.stringify({ generatedAt: new Date().toISOString(), months, rejected, candidates: keep }, null, 2);
  if (out && out !== true) {
    require("fs").writeFileSync(String(out), payload);
    console.error(`${keep.length} candidates written to ${out}`);
  } else {
    console.log(payload);
  }
  console.error(
    `checked ${seen.size} places | kept ${keep.length} | ` +
    `had a site ${rejected.hasSite}, closed ${rejected.closed}, ` +
    `no recent review ${rejected.stale}, too few reviews ${rejected.thin}`
  );
}

main().catch((e) => { console.error(e.message); process.exit(1); });
