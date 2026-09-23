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

## From clips to a Short, in one command

If you already have footage, this is the whole pipeline in one line:

```bash
python3 short.py script.md --clips assets/ -o out/today.mp4 \
  --slow 2,9 --attribution "Creator, CC BY 4.0 — https://..."
```

Clips are used in the order they sort, one per beat. `--slow` names the beats
the narrator should drop pace on — the payoff, the number, the last line.

**Every clip needs a licence and nothing renders without one.** Put a
`licenses.json` beside the clips (`{"clip01.mp4": "CC BY 4.0 — source url"}`)
or pass `--license` to apply one to all of them. If any licence requires
credit and no `--attribution` is given, it stops **before** narrating rather
than after — CC-BY without attribution is infringement, not a formality, and
the slow step should not be spent discovering that.

It runs the three gates before encoding and refuses on a failure:

```
  plan      8 beats from 8 clips
  narrated  kokoro, 8 lines
  retention pass
  rights    pass
  monetize  pass
  built     1080x1920  9.8s  4.1 MB
```

The steps underneath are all still separate commands, documented below, for
when you want to intervene between them.

### One long film into a folder of clips

A compilation starts from a source that is mostly not worth cutting to. Fades,
held blacks and empty establishing frames are all fine in a film and all dead
screen time in a twenty-second Short. `render.cut_shots` scores every position
in the source on how much there is to look at over the seconds that follow it,
throws out the near-black, and writes the winners out as numbered clips with
their licence beside them:

```python
import render
render.cut_shots("sintel.mp4", "assets/", 12, 2.6,
                 licence="CC BY 3.0 — Sintel, © Blender Foundation",
                 start=14.0, end=730.0, gap=20.0,
                 avoid=[(68, 95), (176, 235)])
```

`start` and `end` keep it out of the logo sting and the credits. `avoid` is for
spans a score cannot judge — brightness and detail do not know what an
advertiser will object to, so the scene with the blood in it goes here.

**Scoring alone does not give you a compilation.** One well-lit sequence
outscores the rest of a film, so the best twelve positions land inside ninety
seconds of it and the video looks like a single scene — on Sintel, nine of
twelve picks fell between 2:36 and 4:37. `spread` (on by default) divides the
source into one region per shot and takes the best of each instead; the same
twelve then run from 0:58 to 11:48. A region with nothing usable in it falls
back to the best that is left anywhere.

It raises rather than padding the list out. Quietly reusing a shot is what made
an earlier video look like it had four clips in it when it had seven beats.

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

### One take, cut underneath

The narration used to be synthesised one beat at a time. Every clause was
therefore spoken as its own sentence — its own falling intonation, its own
trailing breath — and because the cuts land on the beats, that arrived as the
voice stopping and starting again at every change of picture. Measured on a
41-beat video: **0.16s of silence at the median cut, and over 0.15s at 22 of
the 40.**

`voice.utterances` groups the beats instead. **A group is one sentence** — its
clauses are spoken as a single continuous take and the pictures cut underneath
them — and it also ends wherever a `hold` is asked for.

**It never runs two sentences together, and that is measured rather than
assumed.** Given several sentences in one utterance, Kokoro puts **0.06s** at
an internal full stop: the same as at a comma, and the same as at an arbitrary
point mid-clause. Joining sentences therefore *deletes* the pause between them
rather than shortening it, and the narration reads straight past the end of one
thought into the next. A wider grouping was tried and this is what it sounded
like.

**A line holding two sentences is split the same way.** Grouping cannot help
there -- the two sentences are one beat and one picture -- so `voice.py` speaks
such a line in one take per sentence and writes the silence in itself:

```
23. Not for weeks. For years.  {breath}
```

`voice.sentence_pause_seconds` is how long that stop runs, and it is longer
than `gap_seconds` on purpose. Between two takes the silence is the gap plus
whatever frame quantisation and the beat floor add to it, measured at a 0.36s
median. Inside a take there is none of that, so the setting has to stand in for
all of it. `sentence_pieces` decides where to split, and knows that `Dr.`,
`J. Smith` and `3.5` are not sentence ends; a semicolon or a dash gets half the
pause, because those are a breath rather than the end of a thought.

**A comma is a third thing, and it is not a take boundary.** Kokoro gives one
0.03-0.14s, which is not a breath, it is nothing: "Across deserts, through
forests, over mountains that nearly kill her." came out as one unbroken run.
The obvious fix -- break the take at the comma too -- is wrong, and measurably:

| "Across deserts," | pitch over "deserts" |
| --- | --- |
| spoken inside the whole sentence | **falls** 17.6 Hz into the comma |
| spoken as a clause on its own | **rises** 15.4 Hz |
| spoken as the sentence "Across deserts." | rises 15.4 Hz — identical |

The engine cannot tell a comma from a full stop, so a clause synthesised alone
comes back with a sentence-final rise: a question mark where the script has a
comma. `clause_breaths` therefore leaves the take whole and opens it up
*afterwards*, in the recording. `_breathe` finds the word boundary from the
measured word durations, searches ±0.10s for the quietest instant so the splice
lands off a vowel, and writes in `voice.comma_pause_seconds` with a 6ms fade
either side. If the quietest point near a comma is still louder than 40% of the
take's median, there is no gap there to open and the line is left as spoken.

**A take that ends on a comma is a bug in the script, not in the engine.** A
`{breath}` mark ends a group, so putting one on a line that ends mid-sentence
splits the sentence across two takes: the first half comes back with a rising,
unfinished tune and then gets a full sentence-sized gap after it. Four of them
were doing exactly that. Mark the breath where the sentence ends, and let the
commas inside it breathe on their own.

```
  narrated  kokoro, 37 lines as 25 sentences   <- every take ends on a full stop
```

Measured on the finished 37-beat narration:

| | cuts | silence |
| --- | --- | --- |
| inside a sentence | 12 | 0.11s median -- a breath, not a stop |
| at a sentence boundary | 24 | **0.57s** median, 0.38s at the shortest |
| at a stop inside a line | 3 | **0.64-0.67s** |

```
  narrated  kokoro, 36 lines as 28 sentences
```

Two things follow from that:

- **Delivery belongs to a passage, not a clause.** `{slow}` on any beat in a
  group applies to the whole group, and the `hold` comes from its last beat.
  You cannot change pace halfway through a sentence, which is also true of
  people.
- **The beats inside a group have to sum to its audio exactly.** A rounding
  error there is drift between voice and picture that never comes back, so
  `voice.beat_lengths` hands out whole frames by the measured word durations,
  gives the remainder to the beats with the largest fractional parts, and only
  then borrows for anything under `INSIDE_FLOOR`. That floor is a safety net
  for a share too small to see, not a target: set to the style's
  `pacing.min_beat_seconds` it overrode the measurements entirely and a
  50/30/20 sentence came out as three equal beats, which is the picture
  ignoring the voice it is supposed to be cut to.

`voice.gap_seconds` is charged once per sentence rather than once per beat, and
is 0.14s — it *is* the between-sentence pause now, because the engine will not
supply one.

**An over-long line is no longer capped.** It used to be clamped to
`pacing.max_beat_seconds`, which cut the narration off mid-sentence — the exact
failure fitting to the voice exists to prevent — and with beats sharing an
utterance it would desync everything after it. The note says the line is too
long; the cut still follows the voice.

### The voice itself was the roughest one available

`am_michael` was the committed voice for no better reason than that it was the
first one tried. There are 54 of them, and they are not equally smooth.
Measured on five lines of the script -- jitter is how much the pitch period
wobbles cycle to cycle, shimmer how much the amplitude does, and both are what
"robotic" means when someone says it:

| voice | jitter | shimmer | periodicity |
| --- | --- | --- | --- |
| af_bella | 0.87% | 0.62 | 0.82 |
| **am_onyx** | **0.90%** | **0.51** | 0.62 |
| bm_lewis | 1.01% | 0.76 | 0.54 |
| am_adam | 1.25% | 0.81 | 0.58 |
| **am_michael** | **1.38%** | 0.66 | 0.64 |

Natural speech sits under 1% jitter. `am_onyx` has the lowest shimmer of all
fifteen tested and 35% less jitter than what was committed, and it keeps the
narrator male; `af_bella` measures smoother still on every count.

Two settings follow from the voice rather than from taste. `am_onyx` has a
fundamental at **85 Hz** and `voice.highpass_hz` was 85, which took **2.8 dB**
off it -- a deep voice made thin, which is its own kind of synthetic. The
highpass is 60 now. And `lowpass_hz` went 8500 to 11000: at a 24 kHz sample
rate 8500 was throwing away most of the air above the voice to "take the fizz
off", and the fizz is what the smoother voice does not have.

The compressor eased from ratio 3 to ratio 2 for the same reason. Levelling is
a separate measured step now, so it no longer has to carry the line-to-line
level as well -- measured, shimmer 0.53 to 0.48 and periodicity 0.62 to 0.64.
A compressor working less hard on a synthesised voice is a voice with fewer of
its own artefacts pulled up.

```
                 jitter   shimmer
  before          1.70%      0.74
  after           1.02%      0.58
```

**A deep voice also broke the comma breaths, silently.** `_breathe` searches
for the quietest instant near a word boundary and refuses to splice if it is
not quiet enough. Searched full-band, an 85 Hz fundamental rings straight
through the gap and hides it -- every comma in the script was being refused
with a note on stderr. The search now runs above 400 Hz, where a word boundary
actually shows, while the splice still happens on the untouched audio. A word
boundary is a consonant event; the fundamental does not stop for it.

### A breath spliced into the middle of a word

This is the one that was actually being heard as glitching, and it took being
told three times to go and find it. The narration track was fine on every
measure that had been applied to it -- levels even, pauses present, no drift
against the picture, no take truncated. What none of those looked at was
**where** the silence had been put.

`_breathe` cuts the take and drops silence in. It is guarded: the chosen
instant has to be quiet, or the splice is abandoned. The guard was measuring
the wrong signal.

To find word boundaries on a deep voice, the search had been moved onto a
first-order high pass -- and the guard came with it. Differencing lifts 4 kHz
fricative energy about 20 dB over 400 Hz, so `s` and `f` tower over every
vowel, **the middle of a vowel becomes the quietest point in the line**, and
measured on that same signal a vowel looks quiet enough to cut. Five splices in
one narration landed inside a word. One of them sat on a vowel at full level:
140ms of silence dropped into the middle of a word, with a 6ms fade either
side.

Two things had to change, and they are different things:

- **Search** in 200-2500 Hz. Vowel formants are loud there, a deep voice's
  fundamental is below it and rings through gaps, fricatives are mostly above
  it. In that band a word boundary is actually the quiet part.
- **Judge** on the full-band signal, against the take's own speech level, over
  a **neighbourhood** rather than an instant. A stop consonant inside a word --
  the closure in "ba-by" -- is genuinely silent for 20ms. What a word boundary
  has and a closure does not is quiet on *both sides*.

```
  splices landing inside a word    5  ->  1  ->  0
  commas that get their breath    10  ->  8       (a refused comma just runs on)
```

A refused comma reads exactly as it did before the breaths existed. A splice
inside a word is a defect. When the guard has to choose, it refuses.

### Never ask the engine for more speed than it can say

A rate mark is a multiplier on the style's base, and the base has been raised
three times since the marks were chosen. `{faster}` meant 1.26 when the base
was 1.0; on a base of 1.20 it means **1.512**, and 13 of 35 beats were being
synthesised at 1.368 or above -- each of them 14 to 26% faster than its
neighbours, for a reason a listener can hear but not account for.

Past about 1.22 Kokoro's compression of a phrase stops being predictable.
Measured over five phrases, splitting each at its comma:

| speed | first half | last half | skew |
| --- | --- | --- | --- |
| 1.15 | 0.93x | 0.94x | 0.99 |
| 1.22 | 0.93x | 0.96x | 0.96 |
| 1.28 | 1.07x | 0.87x | 1.23 |
| 1.40 | 1.17x | 0.99x | 1.18 |
| 1.52 | 1.13x | 0.95x | 1.19 |

**A word about that table, because it was over-read once already.** It was
first taken as "the engine squeezes the end of a phrase", and a time-stretch
was added to work around it. A better measurement did not reproduce the
squeeze: correlating a uniform stretch of the fast take against the unhurried
one scored 0.567 with the stretch and 0.603 without. And measured across six
phrases by how close each landed to the speed asked for, the workaround was
*less* consistent than the thing it replaced:

```
  engine asked for 1.368      mean 1.091   spread 0.044
  1.22 + time-stretch         mean 0.969   spread 0.081
```

So the stretch is gone. What the table does support is that above 1.22 the
engine is erratic -- the skew swings 1.23, 0.90, 1.18, 1.19 with no trend --
and erratic is worse than slow, because two lines marked the same way come back
paced differently. `_engine_speed` clamps there and nothing is stretched to make
up the difference. The read spans **0.91x to 1.22x**: real variation, all of it
inside what comes back evenly, and one less stage in the signal path.

### The voice itself was the roughest one available

`am_michael` was the committed voice for no better reason than that it was the
first one tried. There are 54 of them, and they are not equally smooth.
Measured on five lines of the script -- jitter is how much the pitch period
wobbles cycle to cycle, shimmer how much the amplitude does, and both are what
"robotic" means when someone says it:

| voice | jitter | shimmer | periodicity |
| --- | --- | --- | --- |
| af_bella | 0.87% | 0.62 | 0.82 |
| **am_onyx** | **0.90%** | **0.51** | 0.62 |
| bm_lewis | 1.01% | 0.76 | 0.54 |
| am_adam | 1.25% | 0.81 | 0.58 |
| **am_michael** | **1.38%** | 0.66 | 0.64 |

Natural speech sits under 1% jitter. `am_onyx` has the lowest shimmer of all
fifteen tested and 35% less jitter than what was committed, and it keeps the
narrator male; `af_bella` measures smoother still on every count.

Two settings follow from the voice rather than from taste. `am_onyx` has a
fundamental at **85 Hz** and `voice.highpass_hz` was 85, which took **2.8 dB**
off it -- a deep voice made thin, which is its own kind of synthetic. The
highpass is 60 now. And `lowpass_hz` went 8500 to 11000: at a 24 kHz sample
rate 8500 was throwing away most of the air above the voice to "take the fizz
off", and the fizz is what the smoother voice does not have.

The compressor eased from ratio 3 to ratio 2 for the same reason. Levelling is
a separate measured step now, so it no longer has to carry the line-to-line
level as well -- measured, shimmer 0.53 to 0.48 and periodicity 0.62 to 0.64.
A compressor working less hard on a synthesised voice is a voice with fewer of
its own artefacts pulled up.

```
                 jitter   shimmer
  before          1.70%      0.74
  after           1.02%      0.58
```

**A deep voice also broke the comma breaths, silently.** `_breathe` searches
for the quietest instant near a word boundary and refuses to splice if it is
not quiet enough. Searched full-band, an 85 Hz fundamental rings straight
through the gap and hides it -- every comma in the script was being refused
with a note on stderr. The search now runs above 400 Hz, where a word boundary
actually shows, while the splice still happens on the untouched audio. A word
boundary is a consonant event; the fundamental does not stop for it.

### Never ask the engine for more speed than it can say

A rate mark is a multiplier on the style's base, and the base has been raised
three times since the marks were chosen. `{faster}` meant 1.26 when the base
was 1.0; on a base of 1.20 it means **1.512**, and 13 of 37 beats were being
synthesised at 1.368 or above.

Past about 1.22 Kokoro stops compressing a phrase evenly and starts squeezing
the end of it -- which is heard as a line that sets off at a sensible pace and
then runs out. Measured over five phrases, splitting each at its comma and
comparing how much each half actually shortened against how much was asked for:

| speed | first half | last half | skew |
| --- | --- | --- | --- |
| 1.15 | 0.93x | 0.94x | 0.99 |
| 1.22 | 0.93x | 0.96x | 0.96 |
| 1.28 | 1.07x | 0.87x | **1.23** |
| 1.40 | 1.17x | 0.99x | **1.18** |
| 1.52 | 1.13x | 0.95x | **1.19** |

Above 1.22 it is not a smooth degradation, it is erratic, which is worse: two
lines marked the same way come back paced differently.

So `_split_speed` asks the engine for at most `ARTICULATE_SPEED` and hands the
rest to `atempo`, which shortens the whole line by one factor and therefore
cannot squeeze its end. At the same finished speed:

| | skew | syllable valleys |
| --- | --- | --- |
| engine at 1.368 | 1.18 | 5.7 dB |
| engine at 1.22 + atempo | **0.96** | 5.6 dB |
| engine at 1.512 | 1.19 | **3.3 dB** |
| engine at 1.22 + atempo | **0.97** | 5.4 dB |

An unhurried 1.0 measures 6.4 dB between syllables; at 1.512 the engine's own
compression leaves 3.3 dB, which is syllables running into each other. In the
finished narration the `{fast}` and `{faster}` takes now measure 6.2 dB against
7.1 dB for everything else.

The stretch goes on before the tone, the level and the silence -- a pause
written in seconds must not be sped up along with the words.

### A sentence does not stop, it falls off

Two rounds of widening the pauses changed nothing a listener could hear, and
measuring the wrong artefact is why. The narration track had the silence in it
all along. What was missing was in front of it.

**The tail was being trimmed to 25ms.** `trim_silence` used `keep_head_ms` at
both ends, which is symmetrical and wrong: a sentence does not stop, it decays.
Measured over four of these lines, the fall-off after the last strong sound ran
**0.03-0.185s**. Cut to 25ms, the voice stops mid-fall and the next sentence
begins -- and that reads as two sentences run together *no matter how much
silence is put between them*, because the first one never finished.
`voice.keep_tail_ms` is 140ms and separate from the head for that reason.

**And the gap was never really the design.** `voice.gap_seconds` was 0.14s;
the 0.35s that got measured at a boundary was the frame quantisation and the
beat floor making up the difference, which is to say luck. Where the beat was
tight it collapsed to **0.17s**, which at this reading speed is a blink. The
gap is 0.26s now, so it is the stop, not a contribution to one.

```
                     before   after
  between sentences   0.35s    0.57s median
  the shortest one    0.17s    0.38s
  two sentences on one line
                      0.33s    0.65s
  inside a sentence   0.13s    0.11s   -- unchanged, still flowing
```

One thing this is *not*: the music bed filling the gaps. That was the first
suspect and the measurement cleared it -- through a narration gap the mix holds
steady within 0.4 dB, 18 dB below the speech. The bed does not rush in.

### One level, measured rather than normalised

Every take used to go through a single-pass `loudnorm`. Measured across 29
takes of one narration, the levels ran **-21.5 to -15.9 LUFS**: a 5.6 dB spread
on lines meant to sound like one person talking, with the short dramatic ones
-- `By her.`, `Something bigger sees him.` -- sitting at the quiet end.

Two causes, and `loudnorm` could not fix either:

- **It is a streaming normaliser and needs seconds to settle.** Half these
  takes are under two.
- **It ran after the silence this module writes into a line.** Integrated
  loudness counts that silence, so a line with a stop in it measures quiet and
  gets turned up. The correlation between a take's level and the share of it
  that was inserted silence was **-0.51**.

So levelling is its own step, in its own place in the order: say it, trim it,
apply the tone, **then level, then** write in the silence. `_level_together`
measures mean RMS -- which tracked LUFS to within 0.32 dB (sd 0.28) on this
material, and unlike an integrated reading does not need a minimum length --
and applies one gain, with a limiter holding the peak rather than the gain
being cut short to protect it. Capping the gain instead was tried and left
every take 2-3 dB under target.

One gain across all the pieces of a line, not one each: the parts of
"It breathes fire. She's faster." are a deliberate contrast, and levelling them
apart would undo the delivery the script asked for. Their relative levels are
the performance; their shared level is production.

```
  before   -21.5 to -15.9 LUFS   spread 5.6 dB
  after    -16.6 to -16.3        spread 0.3 dB on the speech, median -16.1
```

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

### Telling the narrator how to read it

A rate multiplier per beat gets part of the way to a delivery. The rest is
pitch, and above all where the silence goes. Those directions belong with the
words rather than in a command-line flag, so they are written into the script:

```
 7. She killed it. {slow}
 8. Then she looked at it. {slow, low, hold}
11. She had just killed him. {slower, lower, beat}
```

| direction | what it does |
| --- | --- |
| `fast` / `faster` | rate ×1.14 / ×1.26 |
| `slow` / `slower` | rate ×0.86 / ×0.76 |
| `high` / `higher` | pitch +1.6 / +3.0 semitones |
| `low` / `lower` | pitch −1.6 / −3.0 semitones |
| `breath` / `hold` / `beat` | 0.18s / 0.34s / 0.6s of silence after the line |

Two of a kind compound, so `{slow, slower}` is slower than either — writing
both was asking for that. An unrecognised word is an error rather than a
silent no-op.

**Silence needs using in both directions.** A pause at every cut sounds broken;
no pauses anywhere sounds like someone reading off a card. Three lengths exist
so the moment of choice, the loss, the line before a reveal and the last line
can each land differently, and everything between them can run on.

**A hold belongs to the line that asked for it, not to the passage around it.**
This was wrong for two builds and it cost the two biggest moments in the video.
The hold was added to the utterance's total and then split between its beats by
share, so `By her.` — marked for the longest pause in the script — received 29%
of a 0.6s hold and came out as a **0.77s shot**, while the clause before it took
the rest. `She kills it.` was 0.63s. The most important beats were the shortest
ones, because their lines were short. The hold's frames now go to the last beat
outright: 1.43s and 1.20s.

The style's `pacing.min_beat_seconds` applies *after* that, to the finished shot
length rather than to the spoken part — flooring the speech and then adding the
hold counts the same silence twice. The directions are stripped before the line is spoken *and*
before it is captioned, so the script stays the script.

**Pitch moves are small on purpose, and that was learned the hard way.**
Shifting pitch by resampling drags the formants along with it, so the same
voice stops sounding like the same person. Measured on a finished track, lines
asked to drop 1.6–3 semitones came back with spectral centroids from 38% *below*
the rest of the narration to 18% above — which reads as the narrator being
swapped mid-sentence, not as someone dropping their voice. ffmpeg's `rubberband`
with `formant=preserved` was no better on speech this short (−9% at two
semitones, against −6% for plain resampling). So `low` and `lower` are now half
a semitone and one, the filter clamps at two, and pace and silence carry the
delivery instead. **The hold cannot live in the
audio** — the cut is timed off the beat, so a pause added to the recording
alone would arrive after the picture had already moved on. It goes into the
beat's length in `fit_plan`, which is why both the shot and the silence last
as long as each other.

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

**Slow down for the line that matters.** `speak()` takes an optional
`emphasis` of `{beat number: rate multiplier}` — below 1.0 slows a line, above
pushes it. A narrator drops the pace for the payoff and moves through the
setup; one that reads everything at a single rate is most of what people mean
by "it sounds like AI". On the second Short here that spreads the delivery from
148 words a minute on the payoff to 263 in the middle, where it used to be
flat. Engines with no rate control ignore it.

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

**Fill is not the whole answer, though.** A 16:9 frame dropped whole into a 9:16
one fills 32% of the height. That is a small window in the middle of a phone, and
three rounds of "the edit is sloppy" turned out to be mostly this. Cropping to
fill instead keeps every pixel of height and throws away two thirds of the width
blind to what was in it, which is how a two-shot loses one of the two people.

Neither is necessary, because detail in a frame is not spread evenly — it sits in
a band and the rest is background. Before each beat is cut, `render.py` samples a
few tiny greyscale frames from it, sums the gradient down each column, and finds
the narrowest band holding `format.focus_keep` (default 0.72) of that total. The
beat is cropped to the band and fitted as if it had been shot closer.

| setting | default | what it does |
| --- | --- | --- |
| `format.focus_keep` | 0.72 | share of a frame's detail the crop has to keep — higher crops less |
| `format.min_coverage` | 0.64 | how much of the output height the real picture should fill |
| `format.max_upscale` | 1.9 | how far the source may be blown up to get there |

The three fight, and the widest crop wins. `min_coverage` pulls the crop tighter;
`max_upscale` stops it there, because a 2.35:1 film would need a 2.2× blow-up to
fill a vertical frame and soft is worse than small. Measured on a synthetic
source with detail across the whole frame: 0.34 of the output height carried real
picture before, 0.69 after. On Sintel, a 2.35:1 source, the upscale cap binds
first and the crop stops at 568 of 1280 pixels.

The measurement decides *where*, not just how much. Given the same clip with its
detail on the left and on the right, the two crops land in opposite halves.

**The push-in moves the picture, not the frame.** Zooming the finished
composite drags the blurred fill and the strip's own hard edges along with it,
and a straight edge creeping against a still background reads as a shake far
more than the image inside it ever does. Measured on a held frame: the blurred
region used to change in every one of 115 frames, and is now pixel-identical in
all of them. The zoom is also run on an oversampled frame, because `zoompan`
rounds its crop origin to whole pixels — on a slow push the ideal origin creeps
by a fraction of a pixel, so the rounded value sticks, jumps, sticks, and that
stutter is the rest of the shake.

### Cutting to the story, not to the light

`pick_shots` scores what is worth looking at. It has no idea which shot is the
dragon, so a narration *about* a film has to be cut by hand — `cut_shots` takes
an explicit list of in-points for that.

**Read off a contact sheet, those in-points are wrong about a third of the time.**
The sampled frame is the shot you wanted; the two seconds after it are often a
different shot. Ten of the first forty cut this way had a cut inside them, which
lands in the finished video as a second cut nobody planned — the beat starts on
the shot you chose and finishes somewhere else. That is what "the cut scenes are
off" looks like from the inside.

So every in-point is now snapped: `shot_boundaries` finds where the source cuts
(ffmpeg's scene score, at 0.12 rather than the usual 0.3 — at 0.3 a dark film
like Sintel reads as 97 shots in fifteen minutes and at 0.12 as 222, which is
nearer the truth), and `snap_to_shot` pulls each clip back so it cannot straddle
one. A moment sitting in a shot too short to hold its beat is an error naming
the beat, not a picture that quietly changes half way through.

**A clip shorter than its beat is also an error now.** It used to make a short
part, and a short part is not a small problem: the transition offsets are
computed from the planned lengths, so one lands past the end of its input and
the chain collapses. Measured once — four beats a few frames short took a 59.2s
video to 50.2s.

### A phone is not a grading suite

Footage cut from one film swings further than anything shot for a Short. On the
36 beats of the Sintel cut, the mean brightness of a beat ran from **216 in the
desert to 15.5 in the cave** — and *half the video sat under 45*, which on a
phone in daylight is a black rectangle with a caption on it. Cutting from 216 to
20 is also the harshest edit in the video, and nobody wrote it.

So every beat is measured before it is cut, and anything under
`format.min_luma` is lifted towards it. The exponent is solved, not guessed: for
a measured mean *m* and a target *f*, `(m/255)^(1/g) = f/255`, so
`g = ln(m/255) / ln(f/255)`. `format.max_lift` caps it, because past a point the
grain comes up faster than the picture does.

It is gamma and not brightness. Brightness adds a constant, which lifts the
blacks off zero and leaves the shot looking washed rather than lit; gamma moves
the midtones and leaves black where it was, which is what turning the lamp up
actually does. A little saturation goes with it, because lifting gamma alone
reads greyer at the top of the curve than at the bottom.

The lift goes on the source, ahead of the reframing crop and the blurred fill,
so the fill is built from the same lit picture rather than staying black behind
a brightened strip.

```
  beat 23/36  1.9s  clip23.mp4  reframed to 71% of the width  lifted from luma 18
```

Measured on the finished video: 19 of 36 beats lifted, mean brightness **77.8**,
and 3 sampled frames of 120 still under 30 rather than 15 clips of 41. The cave
is still a cave. You can see what is in it.

### A bed under it

Silence under a narration is the cheapest thing that makes a video feel thin,
so `music.py` synthesises one. **Not a licensed track**: a Content ID claim on
the audio takes the revenue off a video whose pictures were cleared
specifically to avoid that, and no library the channel has not paid for is
worth that risk. The bed is generated from scratch and carries the licence
"original composition", the same way the narration does.

```bash
python3 music.py bed --seconds 60 --mood grief -o bed.wav
python3 music.py moods
```

A drone, a pad that swells one scale note at a time, and a little filtered
noise. Nothing percussive and no melody to follow, because it is meant to sit
under a voice rather than be listened to.

**Where the energy sits matters more than what the notes are.** A first pass put
94% of it below 300 Hz, which is mud on a laptop and silence on a phone — most
phone speakers give up around 200 Hz, so a bed written down there is a bed
nobody hears. Measured on the current one:

| band | | |
| --- | --- | --- |
| under 150 Hz | 27% | felt, not heard |
| 150–400 Hz | 27% | where a phone speaker still works |
| 400 Hz–1 kHz | 21% | body |
| 1–4 kHz | **11%** | deliberately thin — consonants live here |
| over 4 kHz | 12% | air |

The mix ducks itself: the narration is the sidechain key, so the music drops
while a line is running and comes back in the gaps. `music.gain_db` (−21) sets
how far under it sits and `music.duck_db` (−7) how much further while someone
is speaking. A bed that cannot be made is a note and a quieter video, never a
failed render.

### Between the shots

`transition.kind` was in the style from the start and nothing read it, so every
video was a run of hard cuts. Hard cuts are not wrong — fast Shorts are built
from them — but on a compilation the brightness alone jumps fifteen times the
median frame-to-frame change at some of them, and that flash reads as loose.

| `transition.kind` | |
| --- | --- |
| `cut` | straight concatenation, no re-encode — the fastest path |
| `crossfade` (or `dissolve`) | the plain blend |
| `dip` / `white` | down through black or white and back |
| `wipe` / `slide` | for when the channel wants an edge |

Anything but `cut` needs `transition.seconds` above 0, and it is capped at 60%
of the shortest beat.

**The overlap comes out of the footage, not the timeline.** Beats are cut to
the voice, so a transition that shortened the video would pull the captions and
the narration a little further apart at every cut. Each part is given the extra
length instead — held on its last frame if the clip runs out — and the
transition is centred on the cut so it sits in the gap between two spoken lines
rather than across the next word. Everything is counted in whole frames, so the
overlap a part is handed and the overlap the transition eats are the same
number.

**While fixing that, a real one turned up.** Beats were cut with `-t`, which
rounds *up* to the next whole frame: a 1.38s beat at 30fps came out 1.433s.
Measured over six beats, +0.200s — by the end of a twenty-second video the
picture was six frames behind the line it belonged to. Beat lengths are now
quantised to whole frames when they are written, and each part is cut with
`-frames:v`, which cannot drift.

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

**What is actually reachable from here.** Fourteen stock and archive hosts,
`archive.org`, Wikimedia and `nasa.gov` are all blocked at this environment's
egress gateway, which is what killed the first niche. `raw.githubusercontent.com`
is not, and neither is `media.githubusercontent.com`, which serves Git LFS
objects — so a film committed to a public repository through LFS downloads at
full length. That is how the Blender open movies got here:

| film | licence | where |
| --- | --- | --- |
| Elephants Dream (2006) | CC BY 2.5 | HLS segments in `italia/bootstrap-italia` |
| Sintel (2010) | CC BY 3.0 | LFS object in `andreubotella/media-events-test` |

**Check the file is the film before you credit it.** A contact sheet from the
first of these did not look like Elephants Dream at all; the subtitle track
settled it by naming a character. Sintel's file runs 887.999s against a stated
runtime of 14:48, which is the kind of agreement worth having before a credit
line goes out under a name that is not yours. The repository hosting a file
tells you nothing about the licence of what is in it — the film's own licence
governs, and a repo that declares none is not a source.

### Is the script worth listening to

Every timing check can pass on a script that is still a slog, because what
makes narration tiring is not its pace. The first cut of the Sintel story had
**seven of twelve beats opening with "She"** and thirty-two distinct words in
sixty. It was fitted to the voice, it cut cleanly, and it read as one long
sentence. None of the other gates had anything to say about it.

```
$ python3 render.py narration render.json
 FAIL  the lines do not all start the same way
         7 of 12 begin "she" — the limit is 3.
 FAIL  the script is not saying the same few words over and over
         32 distinct words in 60, 0.53 against a 0.55 floor
```

| setting | default | |
| --- | --- | --- |
| `narration.max_same_opening` | 3 | beats that may begin with the same word |
| `narration.min_word_variety` | 0.55 | distinct words over total words |
| `narration.repeat_lines_allowed` | 1 | the loop line, said twice, and nothing else |
| `narration.min_flow` | 0.35 | beats that run on from, or into, a neighbour |

**`min_flow` is the one that matters most, and it took two more rounds to find.**
A script can clear every other check and still not be a story, because writing
one self-contained sentence per beat turns narration into a caption track. This
passed everything:

> So she goes after it. Desert. Bamboo. Snow that nearly finishes her.
> Seasons. Then years. Less of the village girl each one.

Eight of forty-one beats ran on from the one before. That is a list of what is
on screen, read aloud. Splitting the same story at *clause* boundaries instead
lets the voice carry across the cuts, which is what narrated Shorts actually do:

> So she goes after him. Across deserts, through forests, over mountains that
> nearly kill her. Not for weeks. For years. Long enough to stop being that girl.

Thirty of forty-one now. The check counts a beat as flowing if it does not end
its sentence, or opens lower-case, or opens on a connective.

### What the biggest Shorts actually do

Worth being specific rather than repeating "hook them early". The structural
findings that changed the edit:

- **The hook is cognitive incompletion, not entertainment.** Start mid-action.
  *"She wins this fight. It ruins her life. Watch the wing."* — three beats,
  three unanswered questions, no preamble.
- **Open loops every 10–15 seconds, converging at the end.** The strongest
  long Shorts stack two or three unresolved threads. "Watch the wing" is planted
  at 3 seconds and paid off at 50.
- **The retention curve should hump, not slope.** A payoff in the *second third*
  retains through the middle and is what gets a Short shared. Here that is the
  dragon being taken, at around 0:30 of a 0:60.
- **40–55% completion is realistic at 30–60s**, against more for a 20s cut. A
  longer video is a worse-retaining video, so `retention.total_target_seconds`
  still warns past its number — going long is a decision, not a free upgrade.

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

## What reaches the agents

Everything in `render.py`, `voice.py`, `music.py` and `style.py` runs on every
video automatically — the reframing, the transitions, frame-exact cutting,
shot-boundary snapping, the four gates, the music bed, the grouped narration.
Nothing there has to be remembered or invoked.

The **agent definitions in `.claude/agents/` are a separate thing and they go
stale.** `scriptwriting.md` told the writer "one clause, one idea, one beat" —
which is exactly the advice that produces a caption track instead of a
narration, the failure the `narration` gate now exists to catch. Code that
fixes a mistake does not stop an agent being instructed to make it.

So when a rule changes, both move: the check goes in the code, and the reason
goes in the agent that would otherwise keep writing the old thing.

**One step is still not automated.** `pick_shots` finds shots worth looking at
by brightness and detail; it has no idea which shot is the dragon. When the
narration is about the footage, the in-points are chosen by eye and passed to
`cut_shots(at=[...])`, which then snaps them inside real shot boundaries. The
snapping is automatic; the choosing is not.

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
