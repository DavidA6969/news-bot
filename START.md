# Starting the channel

Everything in this repo is built and tested. What is *not* done is the setup
that cannot live in a repository: the voice weights, your YouTube credentials,
and a release schedule. This page is the whole path from here to a published
video.

Read it once before you start the agents. Steps 1–3 are one-time.

---

## What already exists

- **A niche**, committed and held: `python3 niche.py show`
- **A reference cut** the agents are told to copy — `reference/`. Not a
  description of the style, the actual script, shot list and description of the
  video that was made.
- **Five gates** that refuse a bad video before it uploads: narration,
  retention, rights, freshness, monetisation.
- **A ledger** of footage already published, `clips_used.json`, so the channel
  cannot repeat itself.

Check the whole thing at any time:

```bash
python3 selfcheck.py
```

It ends with an "Outstanding setup" list. When that list is empty for the
YouTube side, you are ready.

---

## 1. The voice (~92MB)

Skip this entirely if you are going to record the narration yourself — see
*The one risk no script can fix* at the bottom. Otherwise it is required, and
it is the single most common reason a render fails on a new machine:
everything else is in the repo, the weights are not.

```bash
npm pack expo-kokoro@1.1.9
mkdir -p ~/kokoro && tar -xzf expo-kokoro-1.1.9.tgz -C ~/kokoro
pip install numpy onnxruntime
sudo apt install espeak-ng

export KOKORO_MODEL=~/kokoro/package/build/kokoro-quantized.onnx
export KOKORO_VOICES=~/kokoro/package/build/voices
```

Put those two `export` lines in your shell profile — the agents inherit them,
and without them `voice.py` falls back to a worse engine or fails outright.

Confirm:

```bash
python3 voice.py engines        # kokoro must say "ok"
```

The voice itself (`am_michael`, speed 1.28) is committed in `style.json`. Do
not change it per video; that is what makes every video sound like the same
channel.

## 2. YouTube credentials (required)

1. Google Cloud console → new project → enable **YouTube Data API v3**
2. OAuth consent screen → External → add yourself as a test user
3. Credentials → OAuth client ID → **Desktop app** → save the JSON beside
   `youtube.py` as `client_secret.json`
4. Authorise once:

```bash
python3 youtube.py login
```

`client_secret.json` and `youtube_token.json` are git-ignored. Never commit
either.

### Read this before you expect it to post by itself

Until your API project passes Google's
[compliance audit](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits),
**anything uploaded through the API is locked to private, permanently.** Making
it public later does not work, the API reports success anyway, and the result
cannot be appealed.

So at the start this pipeline **uploads and stages; you press publish.**
`youtube.py` detects the unaudited case, exits with code 2, and says so rather
than reporting a success you do not have. Once the audit passes, `--publish-at`
hands the go-live time to YouTube and the loop is genuinely unattended.

## 3. The release schedule (required once)

```bash
python3 schedule.py write-config      # writes schedule.json
python3 schedule.py plan              # see the next slots
python3 schedule.py next              # the one slot to aim at
```

Two slots a day, deterministic, with the minutes jittered so every video does
not land on the hour.

---

## Running one video

The agents chain in this order, each one starting where the last finished:

```
trend-research → scriptwriting → rendering → publishing
```

`niche-strategy` sits in front of that chain but is **done** — the niche is
committed. Do not re-run it.

Ask Claude for a video and name the film, because freshness is the constraint:

> Make the next Short. Use the `trend-research` agent to pick the subject,
> then `scriptwriting`, `rendering` and `publishing`. Build it like
> `reference/`. The footage must be a film we have not used — check
> `python3 render.py fresh` first.

### Or run it by hand

Sintel is **used up** — the reference video published 35 shots of it, and the
ledger will refuse a second. The next film is a different one; Elephants Dream
(CC BY 2.5) and Big Buck Bunny (CC BY 3.0) are the obvious candidates, and the
credit line has to match whichever you cut.

```bash
# 1. cut a folder of clips out of the film, licence written beside them.
#    cut_shots is a function, not a subcommand — it records origins.json,
#    which is what the ledger compares against later.
python3 -c "
import render as R
R.cut_shots('bunny.mp4', 'clips/', count=35, seconds=1.9,
            licence='CC BY 3.0 — Big Buck Bunny, © copyright Blender Foundation')
"

# 2. check the footage is new BEFORE cutting anything else
python3 render.py fresh render.json

# 3. script → voice → cut → captions → encode, with every gate
python3 short.py script.md --clips clips/ -o out/video.mp4 \
  --license "CC BY 3.0 — Big Buck Bunny, © copyright Blender Foundation" \
  --attribution "Big Buck Bunny — CC BY 3.0"

# 4. upload. The description and credits are generated, not written by hand
python3 youtube.py upload out/video.mp4 \
  --title "..." \
  --privacy private \
  --agent publishing
```

`short.py` writes `out/video.description.txt` and `out/video.rights.json`
beside the video, and `youtube.py upload` picks both up automatically. The
rights file is what makes the upload **refuse** when the description is missing
a credit the licence requires — that is the check that keeps the CC BY
attribution on every video without anyone having to remember it.

Add `--dry-run` to validate and send nothing.

---

## After every upload: commit the ledger

This is the part that is easy to skip and breaks the whole no-repeat guarantee.

```bash
git add clips_used.json                      # always
git add performance.json niche.json 2>/dev/null || true   # if they changed
git commit -m "published: <title>"
git push -u origin <branch>
```

`clips_used.json` is the channel's memory of which seconds of which film have
already gone out. It is **committed to the repository on purpose** — a ledger
that only exists on one machine cannot stop the channel repeating itself, and a
fresh checkout would start blind and happily re-cut footage you have already
published.

If `render.py fresh` ever says the ledger is empty and this is not your first
video, stop. The check is passing because it has no memory, not because the
footage is new:

```bash
git checkout origin/<branch> -- clips_used.json
```

---

## What the gates will refuse, and why that is the point

| gate | refuses |
| --- | --- |
| `narration` | takes that blitz two sentences together, splices inside a word |
| `retention` | a hook that does not earn the first three seconds |
| `rights` | any clip without a recorded licence, or a description missing a credit |
| `fresh` | the same seconds of the same film twice — and the same *film* twice, unless you set `"reuse_source": true` and mean it |
| `monetize` | the things that get a channel flagged as inauthentic |

A refusal is the system working. Do not route around one — the whole reason
these exist is that every one of them corresponds to something that either
breaks a video or risks the channel.

## The one risk no script can fix

The narration is text-to-speech. YouTube's inauthentic-content policy is aimed
squarely at channels that mass-produce templated videos with a synthetic voice,
and the pipeline's own consistency is what makes that pattern legible. The
gates cover the mechanical part. What actually separates this from what the
policy targets is **the writing** — a real angle, a real claim, something a
person would say. Keep the narratives specific and keep them yours.

Recording your own voice removes that risk entirely. Record one file per
numbered beat, name them `beat01.wav`, `beat02.wav` … and point the same
command at the folder:

```bash
python3 short.py script.md --clips clips/ -o out/video.mp4 --recorded mine/
```

Same pipeline, same gates, same levelling — your voice gets the identical
treatment so the channel still sounds like one channel. No Kokoro weights
needed on that path.
