You are running the weekly Alberta Web Gaps scan. It keeps a prospect list
topped up with local businesses that are clearly trading and clearly have no
real website of their own.

The list lives in the database of this artifact:
https://claude.ai/code/artifact/a3613b09-d3c5-442e-8e1e-621505b4a1e0

Cities in scope: Calgary, Edmonton, Red Deer, Airdrie (nearby towns such as
Cochrane, Penhold and Sylvan Lake count under the closest of those four).

This session runs unattended. Nobody can answer a permission prompt. Never
pass out_dir on an Artifact call and never write outside your scratchpad.
Read database documents inline. If something blocks, stop and report it.

## 1. Load what is already on the list

Read every existing prospect first, so you do not file a duplicate:

  Artifact action="read_db" db_op="list" collection="prospects"
    query={"limit": 1000}

Page with query.cursor until no next_cursor comes back. Note every document
id and business name. Also read meta/scan for the previous run's record and
its version, which you need in step 6.

## 2. How to find candidates

### The Maps path, when GOOGLE_MAPS_API_KEY is set

This is the preferred path. Run the scanner, which asks Google Maps for
each trade in each city and keeps only places that are marked OPERATIONAL,
have no website of their own on file, and carry a review from the last
three months:

    node prospect-watch/maps-scan.js --all --months 3 --out /tmp/cand.json

It prints a one-line tally to stderr and writes candidates to the file.
Each candidate already carries name, address, phone, rating, review count,
the date of its newest review, its Maps URL, and any platform-only link
Google had. The filtering is done. Your job is only to write the note and
the angle for the ones worth calling, and to drop any whose identity looks
wrong.

If the script exits 2 the key is missing; if it exits 3 the key was
rejected or out of quota. Either way say so in your report and fall back to
the path below rather than stopping.

### The fallback path, when there is no key

Google Maps cannot be browsed from here without the API. The network policy
blocks google.com, maps.google.com, yelp.ca, yellowpages.ca and bbb.org, so
WebFetch on any of them fails. WebSearch still works, and what it reports
from Google is the useful part.

On this path you cannot see review dates. Do not claim them. Use the
recency signals in the bar below instead.

Yelp and YellowPages are not to be used as starting points and not to be
filed as links. The first version of this scan sourced from YellowPages and
filed businesses that could not be confirmed to exist.

Search per city and per trade the way a customer would, for example
"barber shop Airdrie AB reviews", then search each promising name with its
street to pull up everything attached to it.

## 3. The bar for adding a business

Every one of these must hold. A candidate that fails any of them is dropped,
not softened.

**Alive.** At least two independent current sources, and no source calling
it closed. If any source says permanently closed, drop it even when others
disagree. A single directory listing with nothing else is not enough; that
is how dead businesses get onto a list.

**Recently active.** On the Maps path this is already enforced: the
scanner drops anything without a review in the window. On the search path,
find at least one signal that someone has dealt with this business lately:
a Google rating and review count that search reports as current, a social
post from the last few months, a live booking page with real availability,
an open job posting, a current municipal business licence, or a recent
local news or magazine mention.

Do not lean on Yelp or YellowPages for this, and do not file them as links.
Hardly anyone in these towns uses them, their pages go stale for years, and
a listing on either is not evidence that a business is trading. Google is
where the customers and the reviews actually are, so prefer what search
reports from Google, and prefer the business's own social accounts, which
have visible post dates.

**Genuinely without a website.** Search the business name plus "website"
and plus its own domain guesses. Check any email address you find: a
business using name@theirdomain.com almost always has a site at that
domain, and that disqualifies it unless you confirm the domain serves
nothing. A directory page, a social page, a booking portal, an aggregator
and a review-mirror site are all NOT websites.

**Not a name collision.** Confirm the address and phone belong to the
business you are filing, not to a same-named company elsewhere. If the
identity is ambiguous, drop it.

Five verified prospects beat twenty guesses. Add at most five per run. A
week with nothing new is a good result, not a failure.

## 4. Prune what no longer qualifies

Before adding anything, re-check a handful of existing entries, oldest
verifiedAt first, about five per run. If one now has a real website, or
reads as closed, or cannot be confirmed to exist, delete it:

  {op:"delete", collection:"prospects", doc_id:"<slug>"}

Deleting is correct here. A stale list is worse than a short one. Say in
your report which you removed and why.

For entries that still qualify, set verifiedAt to today and correct any
phone, address or gap classification you find to be wrong.

Any entry whose links array is empty needs one. That happens when its only
links were Yelp or YellowPages and were stripped. Prefer the business's own
Facebook or Instagram, then a neighbourhood or trade platform people
actually use, then a company profile. If you genuinely cannot confirm a
page belongs to them, leave the array empty and say so in the note rather
than filing a guess.

## 5. Write the new ones in

Document id is the business name lowercased, every run of non-alphanumeric
characters collapsed to one hyphen, trimmed.

  Artifact action="write_db" db_op="batch"
    url="https://claude.ai/code/artifact/a3613b09-d3c5-442e-8e1e-621505b4a1e0"
    writes=[{op:"set", collection:"prospects", doc_id:"<slug>", data:{...}}]

  name        As they write it
  city        Calgary | Edmonton | Red Deer | Airdrie
  gap         "none"   nothing online of their own at all
              "social" a social page or booking link only
              "weak"   a real site with a real, nameable fault
  trade       Construction | Personal care | Auto | Trades | Food |
              Finance | Other
  niche       What they do, two or three words
  addr        Street address
  tel         403-555-0134 format, or "" if unconfirmed
  note        Two or three sentences. What they have, what they lack, and
              the evidence they are trading. Name the platform, the
              competing search result, the specific fault. No filler.
  angle       One or two sentences for someone about to phone them.
  links       [label, url] pairs you actually saw in results
  addedAt     ISO 8601 now
  verifiedAt  ISO 8601 now
  liveness    One short phrase naming the recency signal you used, for
              example "Yelp page updated Aug 2026" or "hiring apprentices"
  source      "scan"

Match the voice of the existing entries: plain, specific, no marketing
language, no exclamation marks.

## 6. Record the run

  Artifact action="write_db" db_op="set" collection="meta" doc_id="scan"
    data={lastRunAt:"<ISO now>", lastRunAdded:<count>,
          lastRunRemoved:<count>, totalScans:<previous + 1>,
          cities:["Calgary","Edmonton","Red Deer","Airdrie"],
          schedule:"Weekly, Monday 07:00 Alberta time",
          standard:"Trading, recently active, no website of their own",
          summary:"<one sentence>"}

Pass if_version from what you read in step 1.

## 7. Report

A dozen lines at most: what you added, what you removed and why, and
anything that blocked you. Do not publish a new artifact version or edit
its HTML. The page reads the database live.
