# Inbox

Licensed **live-action** clips go here while the licensing and stock sites are
unreachable. The pipeline builds from this folder and from nowhere else.

Each clip needs two things:

1. **The file itself** — `.mp4`, `.mov`, `.m4v`, `.webm` or `.mkv`, at least
   1080p.
2. **The proof of licence beside it, under the same name** — `wave.mp4` and
   `wave.pdf` (or `.png` for a DM screenshot, or `.txt` for an email). Same
   stem, so there is no mapping to keep in step.

Then log it:

```bash
python3 review.py log wave.mp4 \
  --url "https://..." --creator "Name" --license "jukin" \
  --proof inbox/wave.pdf --footage-type live_action \
  --attribution "Name — licensed via Jukin"
python3 review.py intake
```

`intake` lists what is usable and, for anything that is not, says why. A clip
with no proof is not a clip we have.

**Nothing in this folder is committed** — see `.gitignore`. Video does not
belong in a repository, and a licence receipt is not ours to redistribute.

## Why this folder exists

Every site the spec allows as a source is refused by this environment's network
policy. Until that changes, the inbox is the only way footage gets in.

To source clips automatically instead, these domains need network access:

- `www.jukinmedia.com`
- `www.viralhog.com`
- `www.newsflare.com`
- `www.storyful.com`
- `www.catersnews.com`
- `api.pexels.com`
- `pixabay.com`
- `mixkit.co`

Add them under Network access in the environment's settings, or widen the
access level. `python3 fetch_clips.py reachable` reports which respond.

A licensed clip also needs a `PEXELS_API_KEY` or `PIXABAY_API_KEY` for the two
stock sources; the marketplaces are account-based and their clips arrive as
downloads with a receipt, which is what `--proof` records.
