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

## The weekly scan

A Routine fires a fresh session every Monday at 13:00 UTC, which is 07:00
Alberta time in summer and 06:00 in winter. Cron `0 13 * * 1`. It runs
`scan-prompt.md`: read what is already listed, search the four cities for
businesses that have no site or a broken one, verify each candidate, and
write the new ones in.

It only ever adds. Nothing is deleted automatically, and a business that has
since built a real website gets a note rather than a removal.

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
