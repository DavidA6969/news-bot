You are running the weekly Alberta Web Gaps scan. It keeps a prospect list
topped up with local businesses that have no website, only a social or
booking page, or a site with a real technical fault.

The list lives in the database of this artifact:
https://claude.ai/code/artifact/a3613b09-d3c5-442e-8e1e-621505b4a1e0

Cities in scope: Calgary, Edmonton, Red Deer, Airdrie (nearby towns such as
Cochrane, Penhold and Sylvan Lake count under the closest of those four).

## 1. Load what is already on the list

Read every existing prospect so you do not add duplicates:

  Artifact action="read_db" db_op="list" collection="prospects"
    query={"limit": 1000}

Do not pass out_dir on any call in this run, and do not write files outside
your own scratchpad directory. Saving elsewhere raises a permission prompt,
and nobody is watching a scheduled run to answer it, so the scan would stall
before it started. Read the documents inline instead.

Page with query.cursor until no next_cursor comes back. Note every document
id and business name. Also read meta/scan for the previous run's record.

The same applies to everything else you do here: this session runs
unattended. Prefer the option that does not prompt. If something does block
on a permission you cannot satisfy, stop and report it rather than waiting.

## 2. Look for candidates

Search for businesses in the four cities across these trades: construction
and renovation, personal care (barbers, salons, nails, brows, massage),
auto repair and body work, skilled trades (electrical, plumbing, painting,
HVAC, roofing), food service, and professional or financial services.

Useful starting points, in rough order of yield:
- YellowPages.ca category pages per city, which mark whether a listing has
  a website
- Recent Alberta corporate registrations and new business licences
- City business directories (Airdrie City View, Town of Penhold and similar)
- Facebook and Instagram business pages with no link in the bio
- Fresha, JaneApp, Setmore, Cojilio and MassageBook profiles, which are
  often a business's entire web presence

Favour businesses that look new this month, and businesses whose site has
recently broken. Both are the point of the scan.

## 3. Verify before adding anything

For each candidate, do all of these. A candidate that fails any check is
dropped, not guessed at.

- Search the business name plus its city and confirm no real website of
  their own exists. A directory entry, a social page, a booking portal or
  an aggregator is not a website.
- For a "weak site" candidate, fetch the site and confirm a specific,
  nameable fault you could screenshot: expired or missing certificate, an
  HTTPS address that falls back to plain HTTP, a free-host or template
  subdomain, a parked or "coming soon" page, a dead domain, or a site that
  does not load on a phone.
- Confirm the business is currently trading. Skip anything marked closed.
- Collect at least one real link you have actually loaded. Never invent a
  URL, an address or a phone number. Leave a field empty rather than guess.

Aim for quality over volume. Five verified prospects beat twenty guesses,
and a week with nothing new is a valid result.

## 4. Write the new ones in

For each verified new business, write one document. The id is the business
name lowercased with every run of non-alphanumeric characters replaced by a
single hyphen, trimmed (for example "Bayside Barber Shop Inc" becomes
"bayside-barber-shop-inc"). Check that id is not already in the collection
before writing.

  Artifact action="write_db" db_op="batch"
    url="https://claude.ai/code/artifact/a3613b09-d3c5-442e-8e1e-621505b4a1e0"
    writes=[{op:"set", collection:"prospects", doc_id:"<slug>", data:{...}}]

Document shape, all fields required unless noted:

  name    Business name as they write it
  city    Exactly one of: Calgary, Edmonton, Red Deer, Airdrie
  gap     "none"   no website at all
          "social" a social page or booking link only
          "weak"   a real site with a real, nameable fault
  trade   One of: Construction, Personal care, Auto, Trades, Food,
          Finance, Other
  niche   What they actually do, two or three words
  addr    Street address, or the city name if you only have that
  tel     Phone as 403-555-0134, or "" if you could not confirm one
  note    Two or three sentences on what you found and what is missing.
          Concrete and specific. Name the fault, the platform, the
          competing search result. No filler.
  angle   One or two sentences on how to open the conversation, written
          for someone about to phone them.
  links   Array of [label, url] pairs you have loaded yourself
  addedAt Current time, ISO 8601, for example "2026-09-14T13:04:00Z"
  source  "scan"

Match the voice of the existing entries: plain, specific, no marketing
language, no exclamation marks. Read a few of the existing notes first.

Never delete or overwrite an existing prospect. Only add. If a business on
the list has since built a real website, do not remove it: update only its
note to say so, and leave everything else alone.

## 5. Record the run

Always update the scan record, even when nothing was added:

  Artifact action="write_db" db_op="set" collection="meta" doc_id="scan"
    data={lastRunAt:"<ISO now>", lastRunAdded:<count added this run>,
          totalScans:<previous totalScans + 1>,
          cities:["Calgary","Edmonton","Red Deer","Airdrie"],
          schedule:"Weekly, Monday 07:00 Alberta time",
          summary:"<one sentence on what this run covered>"}

Pass if_version using the version you read in step 1 so a concurrent write
cannot be clobbered.

## 6. Report

Finish with a short plain-text summary: how many businesses you added,
their names and cities, and anything that blocked the scan. Do not publish
a new version of the artifact and do not edit its HTML. The page reads the
database live, so writing the documents is all that is needed.
