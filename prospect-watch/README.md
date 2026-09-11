# Prospect Watch

Keeps the **Alberta Web Gaps** prospect list topping itself up instead of
going stale the moment it is published.

Artifact: https://claude.ai/code/artifact/a3613b09-d3c5-442e-8e1e-621505b4a1e0

## How it fits together

The page used to carry all 37 prospects as a hardcoded array, so nothing
could ever be added to it without republishing the HTML. The list now lives
in the artifact's own database and the page reads it live.

    prospect-watch/                 this folder, the versioned source of truth
      prospects.seed.json           the original 37, as loaded into the store
      scan-prompt.md                what the weekly scan is told to do
         |
         v
    artifact database               the live list
      prospects/<slug>              one document per business
      status/<slug>                 New / Contacted / Replied / Client
      meta/scan                     when the last scan ran and what it added
         |
         v
    the page                        subscribes, renders, never holds the data

Three things write to it: the weekly scan, the add form at the bottom of the
page, and a person running the Artifact tool by hand.

## The standard for being on the list

Three things, all of which must hold:

1. **Trading now.** At least two independent current sources and no source
   calling it closed. A single directory listing is not evidence; that is
   how dead businesses get onto a list.
2. **Recently active.** A directory page updated in the last six months, a
   current review count, an open job posting, a live booking page, a
   municipal licence, or a recent press mention.
3. **No website of their own.** A social page, a booking portal, a
   directory entry and a review mirror are none of them a website. An email
   at a custom domain usually means a site exists, so it disqualifies until
   proven otherwise.

Google Maps cannot be read directly from this environment. The network
policy blocks google.com, maps.google.com, yelp.ca, yellowpages.ca and
bbb.org. Web search still reaches summaries of Google and Yelp data, which
is what the scan uses. Individual Google review dates are not available, so
the scan must not claim them; the recency signals above stand in.

Google's Places API host (maps.googleapis.com) IS reachable and would give
review timestamps, open/closed status and whether Google holds a website on
file. It needs an API key that nobody has supplied.

## The weekly scan

A Routine fires a fresh session every Monday at 13:00 UTC, which is 07:00
Alberta time in summer and 06:00 in winter. Cron `0 13 * * 1`. It runs
`scan-prompt.md`: read what is already listed, search the four cities for
businesses that have no site or a broken one, verify each candidate, and
write the new ones in.

It only ever adds. Nothing is deleted automatically, and a business that has
since built a real website gets a note rather than a removal.

## The four-week stop

A scheduled job with no end date keeps drawing usage whether or not anyone
is reading what it produces, so this one has a hard checkpoint.

A second, one-off Routine fires on 6 October 2026. It pauses the weekly
scan, reads what four runs actually added, spot-checks a few entries by
loading their links, and reports whether the output was worth the usage.
The scan stays off until someone turns it back on.

    weekly scan       trig_019qAJapVXjxBZBfKokvQZQ8   Mondays 13:00 UTC
    review and pause  trig_01PVN3vfvpqfWBGkXt3ckg2D   once, 2026-10-06

A run costs roughly one moderately heavy session. Either Routine can be
switched off at any time under Routines in claude.ai settings.

Edit `scan-prompt.md` and push, then update the Routine's prompt to match
(they are two copies; this folder is the readable one).

## Document shape

    {
      "name": "Bayside Barber Shop Inc",
      "city": "Airdrie",                    Calgary | Edmonton | Red Deer | Airdrie
      "gap": "none",                        none | social | weak
      "trade": "Personal care",             Construction | Personal care | Auto |
                                            Trades | Food | Finance | Other
      "niche": "Barber",
      "addr": "9-1301 8 St SW",
      "tel": "403-400-0553",                "" when unconfirmed
      "note": "What was found, specifically.",
      "angle": "How to open the call.",
      "links": [["YellowPages listing", "https://..."]],
      "addedAt": "2026-09-01T00:00:00Z",
      "source": "seed"                      seed | scan | manual
    }

Document ids are the business name lowercased, every run of non-alphanumeric
characters collapsed to one hyphen: `Bayside Barber Shop Inc` becomes
`bayside-barber-shop-inc`. That is what stops the scan filing the same shop
twice.

## Reseeding

If the store is ever emptied, `prospects.seed.json` puts the original 37
back. Each record needs `addedAt`, `source: "seed"` and its `slug` lifted
out to the document id.

## What the page shows

Prospects added since you last cleared them carry a blue highlight and a
`New` flag. That marker is per browser, held in `localStorage`; the list and
the status marks are shared and follow you to any device.
