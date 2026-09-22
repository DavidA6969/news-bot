# Agent Operations Dashboard

A local, dependency-free view of the Claude Code subagents in this project: who is
running, who is idle, and who broke. Agents write their own status into
`agents.json`; the dashboard polls that file and draws it as a live node graph.

There is no server, no database, and no build step. The only dependency is D3,
loaded from a CDN.

```
agents.json      state — the single source of truth
dashboard.html   the UI (open in a browser)
status.py        the writer helper your agents call
```

Running behind it are two pipelines the agents drive — a YouTube Shorts channel
and an Etsy shop — each held to one niche:

```
niche.py         commits a channel or shop to one niche, and holds it there
fetch_clips.py   finds footage with a licence attached (stock + PD/CC archives)
voice.py         narrates the script, and re-times the cut to the voice
style.py         the one committed editing style, versioned
render.py        cuts, captions, encodes — then verifies its own output
youtube.py       uploads via the Data API, Shorts only
schedule.py      deterministic release slots
etsy.py          listings, with the handmade rules enforced
suppliers.py     production partners, vetted and disclosed
performance.py   what actually worked, fed back to the agents
selfcheck.py     checks all of the above
```

## Check it first

```bash
python3 selfcheck.py
```

Verifies the whole setup: the state file parses and its dependencies resolve,
`status.py` can take its lock, the schedule is stable and within its cap, every
agent definition names a real agent and carries no leftover placeholder path, the
dashboard is wired to the right file, and **no credential is committed**.

`FAIL` means broken. `WARN` means a step you have not done yet (credentials, a
first upload) and is expected on a fresh clone.

## Run it

```bash
python3 -m http.server 8000
# then open http://localhost:8000/dashboard.html
```

> **Opening `dashboard.html` directly with `file://` will not work.** Browsers block
> `fetch()` against the filesystem, so the page cannot read `agents.json`. It detects
> this and tells you to start the server rather than showing you an empty graph.

On first run every agent is `idle` and the activity feed is empty. That is correct —
nothing is simulated, so the dashboard stays blank until a real agent reports in.

Try it:

```bash
python3 status.py start  trend-research "Scanning last 7 days in niche"
python3 status.py finish trend-research "3 topics selected, 2 rejected"
```

The node turns blue and pulses, a dot travels down the edge into the next stage, then
it turns green — within two seconds, without the layout moving.

## The state file

`agents.json` is the contract. Everything else reads or writes it.

```json
{
  "updated": "2026-09-14T13:00:00Z",
  "agents": [
    {
      "id": "trend-research",
      "name": "ATLAS",
      "role": "Pulls trending topics and filters for saturation",
      "status": "working",
      "currentTask": "Scanning last 7 days in niche",
      "startedAt": "2026-09-14T12:58:00Z",
      "lastRun": "2026-09-13T09:00:00Z",
      "lastOutput": "3 topics selected, 2 rejected",
      "runCount": 12,
      "failCount": 1,
      "avgDurationSec": 94,
      "dependsOn": []
    }
  ],
  "events": [
    { "ts": "2026-09-14T12:58:00Z", "agent": "trend-research", "level": "info", "message": "Run started" }
  ]
}
```

| field | notes |
| --- | --- |
| `id` | stable key; what `dependsOn` points at and what you pass to `status.py` |
| `name` | display label, drawn in caps under the node |
| `role` | shown on hover and in the dossier |
| `status` | `idle` · `working` · `blocked` · `done` · `error` |
| `currentTask` | `null` unless working |
| `startedAt` | drives the live elapsed timer; `null` when not running |
| `lastRun` / `lastOutput` | set by `finish` (and by `fail`, which records the error) |
| `runCount` / `failCount` | integers, start at `0` |
| `avgDurationSec` | lifetime rolling mean over completed runs; `null` until the first `finish`, shown as `—` |
| `recentRuns` | last 20 runs as `{ts, sec, ok}` — what the trend chart is drawn from. Optional: omit it and the chart is simply absent |
| `dependsOn` | array of agent ids — **this is what draws the edges** |
| `events[].level` | `info` · `success` · `warn` · `error`, colour-coded in the feed |

Edges point from dependency to dependant: `"dependsOn": ["scriptwriting"]` on
`rendering` draws **scriptwriting → rendering**. Unknown ids are reported in a warning
bar and their edge is skipped, rather than breaking the graph.

`events` is capped at 500, oldest dropped first.

## The writer helper

`status.py` is what agents call. Every write takes an exclusive file lock and lands via
an atomic rename, so concurrent agents cannot interleave and a reader polling every two
seconds never sees a half-written file.

| call | effect |
| --- | --- |
| `start(agent_id, task)` | status `working`, stamps `startedAt`, clears nothing else, logs an `info` event |
| `finish(agent_id, output)` | status `done`, sets `lastRun`/`lastOutput`, `runCount += 1`, updates `avgDurationSec`, logs `success` |
| `fail(agent_id, error)` | status `error`, `failCount += 1`, records the error as `lastOutput`, logs `error` |
| `log(agent_id, message, level="info")` | appends an event only — status untouched |

Three extras beyond the four core calls:

| call | effect |
| --- | --- |
| `block(agent_id, reason)` | status `blocked` — waiting on a quota, a queue, or a human |
| `idle(agent_id, note="")` | back to `idle`, clearing a previous `done`/`error` |
| `register(agent_id, name=, role=, depends_on=)` | adds a new agent record if it is missing |

### From the shell

This is how a subagent calls it — one `Bash` invocation per state change.

```bash
python3 status.py start  trend-research "Scanning last 7 days in niche"
python3 status.py log    trend-research "Rate limited, backing off" --level warn
python3 status.py finish trend-research "3 topics selected, 2 rejected"
python3 status.py fail   trend-research "All sources returned 503"
python3 status.py show   trend-research          # print the current record
```

`status.py` resolves `agents.json` **relative to its own location**, not your working
directory, so an absolute path works from anywhere:

```bash
python3 status.py start trend-research "..."   # <- your real path
```

Override the target file with `--file path/to/agents.json` or `AGENT_STATE_FILE`.

Exit code is `0` on success and `1` on failure, with the reason on stderr — an unknown
agent id is an error, not a silent no-op, so a typo surfaces immediately instead of
leaving a node that never updates.

### From Python

```python
import status

status.start("trend-research", "Scanning last 7 days in niche")
try:
    topics = do_the_work()
    status.finish("trend-research", f"{len(topics)} topics selected")
except Exception as exc:
    status.fail("trend-research", str(exc))
    raise
```

## Making the video

`render.py` builds the file: it trims each source clip to the beat it covers,
reframes it to vertical, burns in captions from a generated `.ass` file, lays the
voice track over the top, concatenates and encodes an MP4 — then probes its own
output and **deletes it rather than hand on** anything silent, truncated, or more
than 10% off the script's duration.

```bash
python3 render.py plan script.md --clips assets/ -o render.json
python3 fetch_clips.py autofill render.json --provider archive
python3 voice.py narrate script.md render.json
python3 render.py build render.json --agent rendering
python3 render.py check out/video.mp4 --expect 44
```

Needs `ffmpeg` on PATH (macOS `brew install ffmpeg`, Debian `apt install ffmpeg`),
or set `FFMPEG`, or `pip install imageio-ffmpeg` for a bundled build. A render plan
looks like this:

```json
{
  "output": "out/2026-09-20.mp4",
  "width": 1080, "height": 1920, "fps": 30,
  "audio": { "path": "voice.wav", "license": "original narration" },
  "beats": [
    { "clip": "assets/desk-01.mp4", "license": "CC0 — pexels.com/video/12345",
      "in": 1.0, "duration": 3.5, "caption": "Most side projects die in week two." }
  ]
}
```

### The narration is the video

The videos are commentary: found footage, cut and talked over. That form only
works if the **voice carries the original contribution** — the clips illustrate
what is being said, and the saying is the work. A montage of other people's
footage with music over it is the thing YouTube's Inauthentic Content policy
exists to demote; the same footage under narration that has a point is an
ordinary video essay.

So `voice.py` does the thing most pipelines get backwards. It synthesises each
beat, measures how long that line **actually takes to say**, and rewrites the
plan's beat durations to match:

```bash
python3 voice.py engines                        # what is available here
python3 voice.py speak script.md                # one wav per beat -> voice/
python3 voice.py fit render.json                # beats <- spoken lengths
python3 voice.py track render.json -o voice.wav # one track, gapped to fit
python3 voice.py narrate script.md render.json  # all three, in order
```

The video is cut to the voice instead of the voice being squeezed into arbitrary
durations. That is not a nicety: it is what makes the burned-in captions land
*with* the words rather than near them, and it removes the dead half-second at
the end of every beat that makes generated video feel generated.

`fit` will not stretch a beat past the style's `max_beat_seconds` to fit a long
line. It clamps, and says so:

```
beat 3 needs 9.4s to say but the style caps a beat at 7.0s — the line is too
long for this pacing, so shorten the line rather than stretching the beat.
```

That is the correct direction of the fix. The script is the cheap thing to
change.

Any one engine is enough. `voice.py` picks the best one present:

| engine | quality | install |
| --- | --- | --- |
| `elevenlabs` | the best there is, and the only one that costs money | `export ELEVENLABS_API_KEY=...` |
| `recorded` | your own voice — free, and still better than any synthesiser for commentary | drop `beat01.wav`, `beat02.wav` … in a folder, pass `--recorded` |
| **`kokoro`** | **neural, offline, free — the best voice here that costs nothing** | `pip install numpy onnxruntime`, point `KOKORO_MODEL`/`KOKORO_VOICES` at the weights |
| `piper` | neural, offline | `pip install piper-tts`, download a `.onnx` voice, set `PIPER_VOICE` |
| `pico2wave` | intelligible, and audibly a 2010 synthesiser | `apt install libttspico-utils` |
| `espeak-ng` | robotic, but everywhere | `apt install espeak-ng` |
| `say` | decent, built in | macOS only, nothing to install |

**Kokoro is the one to use if you are not recording yourself.** It is a small
neural model that runs on CPU with no network and no account, and it is not in
the same category as the others: given the same line, an offline speech
recogniser transcribed Kokoro's output word-perfect and heard pico2wave's
"rabbit" as "thread".

```bash
export KOKORO_MODEL=/path/to/kokoro-quantized.onnx     # ~92MB
export KOKORO_VOICES=/path/to/voices                   # one .bin per voice
python3 style.py set voice.kokoro_voice am_michael
```

The weights are a download, not part of this repo, and `voice.py engines` says
plainly when they are missing rather than failing at render time. Voices run
`af_*`/`am_*` American, `bf_*`/`bm_*` British; `tokenizer.json` must sit beside
the `.onnx`. espeak-ng does the phonemising — the voice you hear is Kokoro's.

ElevenLabs is **opt-in and never picked for you**: every other engine here runs
offline and for nothing, and this one bills per character. Set the key and it
becomes the first choice; unset it and it disappears from the list. The voice,
model and stability live in `style.json` under `voice.elevenlabs_*`, so the
channel keeps one voice the same way it keeps one look.

espeak-ng improves a lot with MBROLA diphone voices —
`apt install mbrola mbrola-en1`, then set `voice.engine_voice` to `mb-en1`.

```bash
python3 voice.py engines
```

prints which of them this machine can actually use, and why the others are not,
rather than failing at render time. It **exits non-zero unless something can
actually speak** — piper installed without a voice model is present and useless,
and a check that cannot tell those apart reports green and then fails on the
first line.

### Captions that arrive with the voice

| `captions.mode` | what is on screen |
| --- | --- |
| `karaoke` (default) | the whole line, with the word being spoken lit up |
| `word` | one word at a time and nothing else |
| `line` | the whole line for the length of its beat, not tracking the voice |

`karaoke` is the one to use. The sentence still reads as a sentence, and the
colour tells the eye where the voice is. Only the **colour** changes, never the
size: scaling a word mid-line re-flows everything after it, and a sentence that
twitches on every word is worse than no highlight at all.

The timings are **measured, not guessed**. `voice.py measure_words` asks the
engine that is about to narrate how long it takes to say each word, and those
durations become the word's share of the beat:

```bash
python3 voice.py narrate script.md render.json   # also writes per-word timings
```

Estimating this from spelling was the first attempt and it is a losing game:
counting syllables without a dictionary gets `video`, `creative` and
`everything` wrong, and each error slides the highlight off the word being
said. The syllable estimate survives only as the fallback for recorded
narration or a machine with no engine installed.

`captions.pop_ms` is how fast each word snaps up in `word` mode. Set it to `0`
for no animation. `captions.highlight` is the colour the spoken word takes in
`karaoke` mode.

**A caption that wraps past `captions.max_lines` (default 2) covers the footage
it is captioning**, so the line count is estimated before rendering and flagged
by both `style.py check` and the build itself:

```
note: beat 6's caption wraps to 5 lines and will cover the picture —
      shorten it to 2. "In 2008 a team built an entire film to break their..."
```

The estimate is not exact — it cannot be without the font metrics — but it is
calibrated against real output and errs strict. Finding this out by watching the
finished video is the expensive way.

### The voice is part of the style

`style.json` carries a `voice` section, for the same reason it carries the
captions: a channel is recognised by its voice before it is recognised by its
edit, and narration that changes level or timbre between uploads never becomes
recognisable.

```json
"voice": {
  "engine_voice": "en-gb-x-rp", "pico_language": "en-GB",
  "words_per_minute": 160, "pitch": 45, "word_gap_ms": 8,
  "highpass_hz": 85, "lowpass_hz": 8500,
  "compress": true, "loudness_lufs": -16.0
}
```

Every line gets the same treatment on the way out: rumble cut below the voice,
the fizz taken off the top, the level evened out, and loudness normalised to a
fixed target. That is most of the difference between narration that sounds
produced and narration that sounds pasted on, and it applies to a **recorded**
voice too — a real voice needs the levelling more than a synthesiser does.

`words_per_minute` means the same thing on every engine. espeak-ng takes a rate
directly; pico2wave has no rate control at all, so `voice.py` counts the words,
measures what the engine actually did, and corrects — landing within a couple of
percent of the target rather than wherever the engine happened to land.

Changing the look or the voice once in `style.json` moves every future video
together, and each render records the `styleVersion` it used.

For a channel you intend to keep, record the voice yourself. Commentary is a
person having a view, and synthesised narration is audibly not that.

### Footage that is not already vertical

Most found footage is landscape, and the obvious move — crop it to fill a 9:16
frame — throws away two thirds of the width. That is how you end up with half a
title card on screen and a portrait video that reads as a broken landscape one.

`format.fit` decides:

| value | what happens |
| --- | --- |
| `auto` (default) | crop when the shapes are close, blur-fill when they are not |
| `crop` | always fill by cutting the sides off |
| `blur` | always keep the whole frame, over a blurred enlargement of itself |

`format.blur_zoom` (default 1.4) trades a little off the sides for a bigger
picture — at 1.0 the whole source frame is visible but small on a phone. The
blurred fill is the standard way to put landscape footage in a vertical frame
without recomposing it, and it reads as deliberate, which a half-visible subject
does not.

**The push-in moves the picture, not the frame.** Zooming the finished
composite drags the blurred fill and the strip's own hard edges along with it,
and a straight edge creeping against a still background reads as a shake far
more than the image inside it ever does. Measured on a held frame: the blurred
region used to change in every one of 115 frames, and is now pixel-identical in
all of them. The zoom is also run on an oversampled frame, because `zoompan`
rounds its crop origin to whole pixels — on a slow push the ideal origin creeps
by a fraction of a pixel, so the rounded value sticks, jumps, sticks, and that
stutter is the rest of the shake.

### One editing style

Every video uses the single committed look in `style.json`. `render.py` takes the
format, caption styling, margins, push-in, pacing and encode settings from it, and
**refuses a render plan that sets any of them itself**.

```bash
python3 style.py init                        # write style.json
python3 style.py show
python3 style.py set captions.uppercase true # one field, versioned and recorded
python3 style.py check render.json           # does this plan respect the style?
python3 style.py upgrade                     # write out settings a new version added
python3 style.py history
```

`upgrade` exists because a `style.json` written by an older version is missing
whatever the default has gained since. Those settings are filled in at load time
either way, so videos render correctly — but a render stamps the `styleVersion`
it used, and a file claiming to be that version should actually describe the
look. `upgrade` writes them out and records what it added; it is idempotent.

A plan supplies clips, timings and words. It cannot set `width`, `height`, `fps`
or caption styling — try and the render fails naming the conflict. Change the look
once in `style.json` and every future video moves together; each render records
the `styleVersion` it used, so you can tell when the look changed.

The style also carries a slow **push-in** on every clip (`motion.push_in`). It is
subtle, but it is what stops a run of stock footage reading as a slideshow, and
because it comes from the style it is identical in every video. Set it to `0` for
static framing.

`pacing` is advice rather than a block: `style.py check` flags beats under
`min_beat_seconds` (they flash past before they are read) or over
`max_beat_seconds` (where retention goes), but will not stop the render.

### Getting the footage — automatically

You do not place clips by hand. Give each beat a `search` term and let the
pipeline fetch them:

```bash
export PEXELS_API_KEY=...      # free: https://www.pexels.com/api/
export PIXABAY_API_KEY=...     # free: https://pixabay.com/api/docs/

python3 fetch_clips.py autofill render.json      # finds and downloads a clip per beat
python3 fetch_clips.py attribution render.json   # the credit block for the description
```

`autofill` writes both the `clip` path and its **real** `license`, so the gate
below is satisfied by recorded provenance rather than by typing something into a
field. Two things it does on purpose:

- **Never the same clip twice in one video**, and it avoids footage used in
  earlier videos. Identical b-roll across uploads is what makes a channel look
  mass-produced, which is the pattern the Inauthentic Content policy looks for.
- **Generates the attribution.** Pexels requires crediting the creator when
  footage comes through their API, so the credit block is produced automatically
  and HERALD appends it to the description. That is a licence condition, not a
  courtesy.

Write `search` terms that describe a *filmable scene* — "hands typing at a
cluttered desk" finds footage; "productivity" does not.

### Two kinds of source, and the difference matters

`fetch_clips.py` searches four providers, in two groups:

| group | providers | what it is | key |
| --- | --- | --- | --- |
| **`stock`** (default) | `pexels`, `pixabay` | clean, generic b-roll; free for commercial use | free API key |
| **`archive`** | `internetarchive`, `commons` (Wikimedia) | real film, newsreel, documentary and public-record footage | none |

Stock is for *illustrating* a point — nobody will recognise it, and nobody was
meant to. Archive footage is for **clipping and talking over**: it is footage
that is *about* something, which your narration can then be about in turn. That
is the raw material of a commentary channel, and it is the reason the archive
providers exist here at all.

Archive is opt-in, because mixing 1950s newsreel with a stock shot of a laptop
in one video looks like an accident rather than an edit:

```bash
python3 fetch_clips.py search "apollo launch" --provider archive
python3 fetch_clips.py autofill render.json --provider archive
```

**Neither group falls back to the other**, deliberately. An empty archive search
means rewrite the search term, not quietly drop a stock shot of a laptop into a
newsreel — a silent fallback would reintroduce exactly the mix the opt-in exists
to prevent. Pass a single source name (`internetarchive`, `commons`, `pexels`,
`pixabay`) to narrow further.

For the same reason, `autofill` **fails** rather than giving two beats the same
clip when a term runs out of distinct matches:

```
every clip found for 'same thing' is already used elsewhere in this video
(3 candidates, all taken). Two beats of the same footage is visible, so vary
this beat's search term instead.
```

A repeated clip inside one video is visible to the viewer, so it is an error
with a named fix rather than a degradation. (Reuse across *different* videos is
only a warning, and it takes the least recently used clip.)

Both archive providers **read the licence on every item and skip anything whose
rights are not clearly public domain or Creative Commons.** An archive that
happily handed back a copyrighted film would be worse than having no archive at
all, because the failure would surface as a claim months later rather than as an
empty search now.

### Footage you may use

**Every asset needs a `license`, and renders fail without one.** That is not red
tape — it is the single biggest risk to this channel.

Acceptable: your own recordings, stock you have licensed, public-domain and
permissively-licensed archives. Record the real URL and licence.

**Not acceptable: clips ripped from someone else's YouTube, TikTok or
Instagram.** `render.py` refuses a plan whose licence names a platform URL
unless you also set `"rights_confirmed": true` to assert in writing that you
hold permission, and `fetch_clips.py` has no provider that can reach those sites
at all.

Worth being precise about why, because "it's commentary, that's fair use" is
half a real argument and people stop there:

- **The download is a separate problem from the edit.** Those platforms' terms
  forbid downloading their content, whatever you do with it afterwards. Fair use
  is a copyright defence; it is not a defence to breaching the terms of the
  service you took the file from.
- **A CC-BY YouTube video really does grant you reuse rights** — and the
  sanctioned way to exercise them is YouTube's own editor, inside YouTube. The
  licence covers the copyright; the terms still cover the download. Both have to
  be satisfied, not either.
- **Fair use is a defence, not a permission**, and it is decided after you are
  sued. Content ID does not adjudicate it at all — it matches audio and video
  and acts, and the appeal runs on the claimant's timetable.

So the pipeline gets the same *form* — found footage, clipped, narrated over —
from material that is actually cleared for it: public-domain and CC archives for
footage that is about something, stock for footage that illustrates. That is a
route to the video you wanted, not a lesser substitute for it.

### Shaped for the feed

`style.json` carries a `retention` section holding what the Shorts feed
actually rewards, as numbers rather than folklore:

```bash
python3 render.py retention render.json
```

| check | why, and where the number comes from |
| --- | --- |
| opening beat ≤ 2.0s | you have 1.5–2s to interrupt a thumb that is already moving. **This one fails the check**, it does not warn |
| no beat over 2.6s | a viewer re-asks "is this worth continuing?" every second or two |
| a cut every ~2.0s | the 2026 pacing target is a visual change every 1.5–2s |
| under 30s total | watch time **as a share of length** is what ranks now, so a longer cut has further to fall |
| the ending loops back | a loop turns one view into two, and rewatches count |

The loop test compares the words of the closing line against the opening one,
so it recognises an echo rather than demanding a repeat.

**Test the hook, not the idea.** Several versions of one idea can swing a Short
from a flop to a million; the variable is almost always the opening line. So
build the same body three times with three different hooks — a curiosity gap, a
bold claim, a contradiction — each with its closing line echoing its own hook so
the loop still closes, and let the numbers pick. `retention` will reject a hook
that runs long before you spend a slot on it.

### Staying monetizable

```bash
python3 render.py monetize render.json
```

YouTube renamed "repetitious content" to **inauthentic content** in mid-2025,
and in July 2026 split what it refuses to monetize into three buckets. The
policy targets low-effort templated work, **not AI** — AI-assisted video stays
monetizable where a person added something.

| what the check tests | which bucket |
| --- | --- |
| the video carries real narration, not footage with music on it | generic/template-based — **fails** |
| no synthetic voice *advising* on health, money or law | AI personas on sensitive topics — **fails** |
| no shot used twice; more than one source | what mass production looks like |
| the narration needs no synthetic-content disclosure | see below |

**On disclosure, the common belief is wrong.** A generic synthetic voice needs
no disclosure at all. YouTube requires the Altered Content tick only when a
voice is **cloned to sound like a specific real person**. AI-written scripts,
AI thumbnails and stylised visuals don't trigger it either. The check says so
explicitly rather than leaving you to guess, and flags a clone if your audio
licence mentions one.

**What the check cannot see**: the first bucket is about a *run* of videos, and
this reads one plan. Identical structure across every upload is the real
exposure — vary the shape, not just the subject. The check says that every
time, because no single-plan check can measure it.

It reports exposure, not a verdict. Monetization is a reviewer's decision and
no check can promise it.

### Getting in at all

Full YPP needs **1,000 subscribers** plus either **4,000 watch hours** in 12
months or **10 million Shorts views** in 90 days (a rolling window, updated
daily). Shorts views count toward the 10M path; they do not count toward the
4,000 hours. There is an early-access tier at 500 subscribers and 3 public
uploads in 90 days.

**On 1 February 2027 the bar doubles for new applicants** — 8,000 hours or 20
million Shorts views. If you are close, applying before that date is worth more
than any other optimisation in this README.

### Staying inside the copyright rules

Every obligation this pipeline takes on is checked rather than asserted:

```bash
python3 render.py rights render.json
```

| check | why it matters |
| --- | --- |
| every clip records a licence | no provenance, no defence |
| nothing lifted off a platform | their terms bind you separately from their copyright |
| credit where the licence demands it | CC-BY without attribution **is** infringement |
| the video carries its own narration | third-party footage with nothing added is what the reused-content policy demotes |
| source audio discarded, not re-used | beats are trimmed with `-an`, so music in the source cannot raise a claim |

It exits non-zero on any failure, and HERALD runs it before spending an upload.

It also warns, every time, that **a Content ID claim is still possible.** That
is honest rather than pessimistic: a valid licence is a defence you raise after
a claim, not a shield that prevents one, and widely-used footage gets matched
whatever its licence says. Keep the licence URL with the render — `fetch_clips`
already records it — so a dispute takes minutes.

## Shorts only

This channel publishes Shorts, and that is enforced rather than intended.
[A vertical or square video of three minutes or less is automatically treated as
a Short](https://www.shortsync.app/resources/youtube-shorts-upload-requirements-2026);
one second over and YouTube silently files it as an ordinary video that never
enters the Shorts feed.

The envelope lives in `style.json` under `shorts`, and two gates enforce it:

- **`render.py` refuses an over-length cut before encoding**, so you find out in
  a second rather than after four minutes of rendering.
- **`youtube.py` inspects the actual file before uploading** and refuses anything
  that would not be filed as a Short, exiting 3 with the reason. It checks the
  file rather than the plan, so a hand-swapped render, a stale file or a
  landscape export is caught too — and it refuses a file it cannot read at all
  rather than uploading blind.

Vertical and square both qualify; landscape does not. The style itself cannot be
set outside the envelope: `style.py` rejects a landscape format or a limit above
180s.

## Publishing to YouTube

`youtube.py` uploads a finished render to your own channel through the **YouTube
Data API v3** with OAuth 2.0. It does not drive youtube.com in a browser and never
touches your Google password — automating the website breaches YouTube's Terms of
Service, the API does not. Only the `youtube.upload` scope is requested: enough to
insert a video, not enough to read analytics, edit other videos, or delete anything.

### Read this before your first upload

**An API project that has not passed YouTube's compliance audit can only create
private videos.** Ask for `public` and the upload still lands private, the API
reports success, and [the result cannot be appealed](https://support.google.com/youtube/answer/7300965)
— the only remedy is to re-upload through an audited client or by hand.

So until you pass [the audit](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits),
this pipeline **uploads and stages**; a human publishes. `youtube.py` detects the
case, exits with code 2, and says so rather than reporting a success you do not have.

Quota is not your constraint. Uploads moved to a dedicated bucket of **100 per day**,
separate from the 10,000-unit pool. One or two a day is nothing.

### Setup

1. Google Cloud console → new project → enable **YouTube Data API v3**.
2. OAuth consent screen → External → add yourself as a test user.
3. Credentials → OAuth client ID → **Desktop app** → save the JSON next to
   `youtube.py` as `client_secret.json`.
4. `python3 youtube.py login` — one browser round trip, stores a refresh token in
   `youtube_token.json` (mode 600).

Both files are git-ignored. Never commit either.

```bash
python3 youtube.py upload out/video.mp4 --title "..." --dry-run      # validate, send nothing
python3 youtube.py upload out/video.mp4 --title "..." \
  --description-file description.txt --tags a b c \
  --publish-at 2026-09-20T17:00:00Z --agent publishing
```

`--publish-at` hands the go-live to YouTube, which beats keeping a machine awake to
press publish. `--agent publishing` mirrors the upload onto the dashboard.

## One niche each, held

There are two businesses here and they hold **separate** niches. `niche.py` is
scoped; `--scope` defaults to `youtube`.

```bash
python3 niche.py set  --scope etsy --name "..." --keywords linen apron kitchen
python3 niche.py show --scope etsy
python3 niche.py check --scope etsy "Bitcoin mug"
python3 niche.py history                      # both businesses, interleaved
```

Each scope holds exactly one niche and switching is refused inside 30 days or
under 10 published items, per scope. A file written before the shop existed
migrates its single niche into the `youtube` scope automatically.

A channel that changes subject every few weeks never builds an audience — the
people one video brings in are not the people the next is for, so nothing
compounds. `niche.py` keeps **exactly one** committed niche on disk and makes
every agent read it.

```bash
python3 niche.py set --name "..." --audience "..." --format "..." \
  --why "..." --keywords focus productivity "side project"
python3 niche.py show
python3 niche.py check "Bitcoin price prediction"   # in niche, or not
python3 niche.py history
```

`set` refuses if a niche already exists — there is never a list. Changing it takes
a deliberate `switch` with a written reason, and that is **refused inside the
first 30 days or under 10 published videos**, because before then the numbers
cannot tell you whether the niche or the execution was wrong. Every switch is
recorded with how long the old one was held, and after two the history says
plainly that the switching is the problem rather than the niches.

ATLAS gates every candidate topic through `niche.py check`. Off-niche means
reframe it or drop it — never publish it anyway because the demand looks good.

## Learning from results

Without this the pipeline is blind: COMPASS picks a niche, ATLAS picks topics, and
nothing ever finds out whether any of it worked.

Both businesses have one, kept in separate books:

```bash
python3 performance.py record --scope youtube VIDEOID --title "..." --confidence high
python3 performance.py refresh --scope youtube          # Data API key, not OAuth
python3 performance.py digest  --scope youtube          # writes performance.md

python3 performance.py record --scope etsy LISTING_ID --title "..." --confidence high
python3 performance.py refresh --scope etsy --shop-id <id>
python3 performance.py digest  --scope etsy             # writes performance-etsy.md
```

The Etsy side reads listing **views and favourites** through the same OAuth the
listing client uses — no extra scope, and unlike sales it needs no billing
permission. Each digest is written for its own reader (ATLAS or LOOM) and in its
own vocabulary, and the two never share a file.

HERALD records each upload against the topic that produced it. `refresh` reads
public view counts, which needs only a **Data API key** — no extra permission on
your account. `digest` writes `performance.md`, which ATLAS reads *before* choosing
the next topics.

Three things make the digest worth reading:

- **Views are normalised by age**, so a week-old video is comparable to a new one.
- **Videos with no statistics yet** (private, scheduled) are excluded and said so,
  never counted as zero — counting them would poison every average.
- **It checks whether ATLAS's own confidence ratings predict anything.** If the
  high-confidence picks are not beating the low-confidence ones, it says so and
  tells the agent to stop leaning on them. A pipeline that cannot notice its own
  judgement is useless is not learning, it is accumulating files.

Below five measured videos it refuses to draw patterns and says why. Four data
points cannot tell you anything, and pretending otherwise is how a channel gets
abandoned one bad week in.

## Release cadence

`schedule.py` decides *when* videos go live — one or two a day inside configured
windows, never closer together than a minimum gap.

```bash
python3 schedule.py write-config      # creates schedule.json with a per-install salt
python3 schedule.py plan --days 7     # show the week
python3 schedule.py next              # peek at the next free slot
python3 schedule.py claim             # take it, so it is not handed out twice
python3 schedule.py release <slot>    # give one back after a failed upload
```

Each day's slots are derived from `salt + date`, so **the plan for a given day is
fixed**: asking twice gets the same answer, and planning 7 days or 14 agrees about
the days they share. `claim` records a slot in `schedule_state.json` so two runs on
the same day cannot both be told to publish at the same moment.

To be explicit about what this is and is not: it spaces releases so the channel
publishes on a rhythm an audience can follow. It is **not** an attempt to look
un-automated. Your uploads carry your OAuth token, so YouTube knows they are API
uploads — which is allowed, and needs no disguise. Do not add anything that tries
to evade platform detection; that would breach the Terms of Service, and cadence
is not what puts a channel at risk anyway.

What does is content. YouTube's [Inauthentic Content policy](https://support.google.com/youtube/answer/1311392)
(July 2025, formerly "repetitious content") demonetizes mass-produced, templated,
minimal-variation content **at any frequency**. Twice a day is fine. Twice a day
with the nouns swapped is the thing the policy exists to catch. AI-assisted
production is explicitly fine when the result is original and adds value.

## The Etsy shop

A second pipeline, independent of the video one. It shows as its own band on the
dashboard.

```bash
export ETSY_API_KEY=<keystring>     # etsy.com/developers/register
python3 etsy.py login
python3 suppliers.py add --name "..." --location "..." --role "..."
python3 etsy.py draft --title "..." --price 44.00 --taxonomy 1234 \
  --partner linen-works --dry-run
```

### The rule the whole thing is shaped around

[Etsy prohibits dropshipping and reselling](https://help.etsy.com/hc/en-us/articles/23948763872151-Does-Etsy-Allow-Drop-Shipping-or-Reselling).
Sourcing ready-made goods and listing them as your own gets shops suspended, and
it is not a grey area.

What *is* allowed is a **production partner**: a manufacturer making something
**you designed**, [disclosed by name, location and role](https://www.etsy.com/legal/handmade/).
Failing to disclose one is treated the same as selling prohibited items.

So the tools enforce that shape rather than advising it:

- `etsy.py` **refuses** a listing declaring `who_made="someone_else"` on a
  non-supply, non-vintage item, because that describes reselling. "Vintage" is
  derived from Etsy's 20-year rule against the current year, not matched against
  a hardcoded list, so it stays correct as the calendar moves.
- `suppliers.py` **refuses** to record a "partner" whose details point at a
  sourcing marketplace — that is a reseller relationship, not a manufacturer.
- A partner cannot be listed against until a **sample has been received** and
  the partner is registered in Etsy Shop Manager. Listing a product you have
  never held is how a shop earns its first one-star review.
- `--partner` pulls the disclosure sentence and appends it to the description
  automatically, and sends Etsy the partner id.

Listings are created as **drafts**. A human checks the photos and price before
anything goes live; there is no publish path here, deliberately.

Note that Etsy's API needs approval: a Personal App first, then
[Commercial Access is a separate, manually reviewed request](https://developers.etsy.com/documentation/essentials/rate-limits/).

## The agents

`.claude/agents/` holds ten subagent definitions, in two independent chains.
Each reports to the dashboard through `status.py`, so a run is visible as it
happens.

| agent | id | does |
| --- | --- | --- |
| COMPASS | `niche-strategy` | commits the channel to **one** niche, and kills ideas that cannot survive |
| ATLAS | `trend-research` | picks which specific video to make next |
| SCRIBE | `scriptwriting` | turns a topic into narration, beat by beat |
| FORGE | `rendering` | fetches the footage, records the voice, cuts to it, verifies the file |
| HERALD | `publishing` | uploads, schedules it, and records it for the feedback loop |

The Etsy shop is a second chain, independent of the video one:

| agent | id | does |
| --- | --- | --- |
| WARP | `etsy-niche` | commits the shop to **one** niche, independent of the channel's |
| LOOM | `etsy-product` | decides what the shop makes, on demand evidence and a margin that survives fees |
| KILN | `etsy-supplier` | finds and vets production partners, and records the disclosure |
| STALL | `etsy-listing` | writes and posts the draft listing |
| CRIER | `etsy-promo` | promotes it through channels the shop owns |

All commands in the definitions are relative and run from the project root, so
there is nothing to edit before first use. `selfcheck.py` fails if a definition
still carries a placeholder path or names an agent that is not in `agents.json`.

COMPASS is the one worth reading. It does not brainstorm; it argues from evidence.
Its core test is the **outlier test**: find videos whose views are 10× or more the
uploading channel's subscriber count in the last 90 days. That ratio means the
*topic* pulled the video rather than an existing audience, which is the clearest
available signal of demand that supply is not meeting. Three or more on a theme is
a real opportunity; zero means nobody wants it or the incumbents already have it
covered.

It then scores six checks — winnable supply, saturation, repeatability at your
actual capacity, monetizable audience, durability, and your unfair advantage — and
applies one **veto**: what does our version add that a template does not? If that
question has no honest answer, the niche is rejected on the authenticity gate, no
matter how good the demand looks. A niche that fails there can win views for months
and then lose monetization all at once.

Every brief ends with a **kill criterion** — a falsifiable threshold like "if the
first five videos average under 1,000 views in 14 days, stop." Without one you will
publish into silence for months.

## Worked example: a Claude Code subagent

Save as `.claude/agents/trend-research.md`. The `name` matches the agent's `id` in
`agents.json` so the two line up.

Commands are relative to the project root, which is where a subagent's shell
starts. No path editing is needed.

````markdown
---
name: trend-research
description: Pulls trending topics for the channel and filters out saturated ones. Use at the start of a video pipeline run.
tools: Bash, Read, Write, WebSearch
model: sonnet
---

You are ATLAS, the trend research stage of the video pipeline.

Report your status to the operations dashboard as you work. All commands below run
from the project root (the folder holding `status.py`); if one reports `can't open
file`, `cd` there first.

**1. Before anything else, announce that you have started:**

```bash
python3 status.py start trend-research "Scanning last 7 days in niche"
```

**2. While working, log anything a human would want to see later:**

```bash
python3 status.py log trend-research "Source rate-limited, backing off 30s" --level warn
```

**3. When you succeed, record a one-line summary of what you produced:**

```bash
python3 status.py finish trend-research "3 topics selected, 2 rejected"
```

**4. If you cannot complete the task, report the failure instead:**

```bash
python3 status.py fail trend-research "All sources returned 503"
```

Call `finish` or `fail` exactly once, as the last thing you do. If you skip it you will
show as still working forever, and the operator will think you have hung.

Your actual job: search for topics trending in the channel's niche over the last seven
days, discard anything already saturated, and write the survivors to `topics.json` with
a one-line justification each.
````

The same shape works for the other three stages — swap the id, the task strings, and
the closing instruction. Because `dependsOn` already chains them, each `start` also
animates a dot travelling down the edge from the stage that handed off.

If you would rather not rely on the model remembering to call `finish`, a subagent
definition can also declare `hooks` in its frontmatter to run the command
automatically. Prompt-driven calls are used above because they let the agent report a
*meaningful* summary rather than a generic "done".

## What the UI does

- **Graph.** Force-directed, but each node is pulled toward a column derived from its
  longest dependency path, so a pipeline reads left to right instead of settling into a
  blob. The simulation stops once it cools, then the view scales to fit the window.
- **No layout jump.** Each poll compares a topology signature (sorted ids + sorted
  edges). If only statuses changed — the common case — the simulation is never touched
  and only attributes are repainted. A new agent reheats the layout gently; existing
  nodes keep their positions.
- **A running clock under every working node**, ticking once a second independently of
  the 2s poll, so you can see how long something has been going without clicking it.
- **Hung agents are caught.** An agent that dies without calling `finish` or `fail`
  stays `working` forever — the one failure this design can't see in the file itself.
  So the UI infers it: once a working agent passes `max(2 × avgDurationSec,
  avgDurationSec + 60s)` it gets a dashed amber ring, its clock turns amber, and the
  header counts it as **OVERRUNNING**. The dossier says how far past its average it is
  and why that matters. This is inference in the UI only — nothing is written back to
  `agents.json`, and the agent's real status is left alone.
- **Flaky agents are marked** with a small red dot when at least a fifth of their runs
  have failed (minimum three runs). Hover any node for its full record.
- **Blast radius.** When an agent errors or blocks, everything downstream of it is
  dimmed and counted as **STALLED** — those agents read as `idle`, but they are not
  idle, they are waiting on a break. The dossier names the agent that caused it.
- **The tab itself reports trouble.** A dashboard on a second monitor is useless if it
  only complains inside its own viewport, so the title becomes `(2) Agent Operations`
  and the favicon turns red while anything needs attention.
- **A run-duration chart** in the dossier, drawn from `recentRuns`. Bar height is
  duration; outcome rides a separate marker rail beneath the axis rather than colour,
  because the done-green and error-red used elsewhere measure ΔE 3.2 under
  deuteranopia — indistinguishable to a red-green colourblind reader. The median is
  drawn and labelled, and when the last three runs' median is 1.5× the earlier median
  the chart says so outright. A lifetime average cannot tell you an agent is degrading;
  this can.
- **Screen readers** get status transitions through a polite live region.
- **Drag** a node to pin it there permanently; **double-click** to release it. *Fit*
  re-frames the graph; *Reset view* also releases every pin. Once you pan or zoom by
  hand the view is yours — polling never yanks it back.
- **Click** a node for the dossier: full record, live elapsed time while running, and
  that agent's last 20 events. Tab to a node and press Enter to do the same from the
  keyboard. Escape or a click on the background closes it.
- **Activity feed** holds the 50 most recent events, newest first, and filters to
  **PROBLEMS** (warnings and errors) when the full stream is too noisy. Click the agent
  name on any row to open that agent. The feed follows the newest event unless you have
  scrolled away, in which case it holds your position and offers a *N NEW* pill.
  Collapsed state and filter are remembered.
- **Failure is visible.** A missing, unreadable, or malformed `agents.json` shows a
  specific error card naming the problem, with the last known graph dimmed behind it —
  never a silently empty screen. It recovers by itself once the file is valid again.

## Notes

- Polling is every 2s with `cache: no-store` and a cache-busting query string;
  `python3 -m http.server` will otherwise serve a stale file.
- `agents.json.lock` and `agents.json.tmp.*` are working files created by `status.py`.
  They are git-ignored and safe to delete when nothing is running.
- If a write fails with a lock timeout, an agent died mid-write. Delete the lock file.
- `status.py` refuses to write over an `agents.json` it cannot parse, so a corrupt file
  is never made worse.

---

## news-bot

The original contents of this README, preserved:

> # news-bot[index.js](https://github.com/user-attachments/files/26694016/index.js)
> [package.json](https://github.com/user-attachments/files/26694017/package.json)
