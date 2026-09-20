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
  "audio": { "path": "voice.m4a", "license": "own recording" },
  "beats": [
    { "clip": "assets/desk-01.mp4", "license": "CC0 — pexels.com/video/12345",
      "in": 1.0, "duration": 3.5, "caption": "Most side projects die in week two." }
  ]
}
```

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

### Footage you may use

**Every asset needs a `license`, and renders fail without one.** That is not red
tape — it is the single biggest risk to this channel.

Acceptable: your own recordings, stock you have licensed, public-domain and
permissively-licensed archives. Record the real URL and licence.

**Not acceptable: clips taken from someone else's YouTube, TikTok or Instagram.**
Three independent reasons, any one sufficient: it infringes their copyright, it
breaches those platforms' terms, and compiling other people's clips with little
added is exactly what the Inauthentic Content policy demonetizes. `render.py`
refuses a plan whose licence names a platform URL unless you also set
`"rights_confirmed": true` to assert in writing that you hold permission, and
`fetch_clips.py` has no provider that can reach those sites at all.

Stock libraries exist precisely because this need is common, and they solve it
legally — which is why fetching is wired to them instead.

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

## One niche, held

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

```bash
python3 performance.py record VIDEOID --title "..." --angle "..." --confidence high
python3 performance.py refresh          # needs a Data API key, not OAuth
python3 performance.py digest           # writes performance.md
```

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

## The agents

`.claude/agents/` holds five subagent definitions. Each reports to the dashboard
through `status.py`, so a run is visible as it happens.

| agent | id | does |
| --- | --- | --- |
| COMPASS | `niche-strategy` | commits the channel to **one** niche, and kills ideas that cannot survive |
| ATLAS | `trend-research` | picks which specific video to make next |
| SCRIBE | `scriptwriting` | turns a topic into a shot-by-shot script |
| FORGE | `rendering` | clips the footage into a finished video, and verifies it |
| HERALD | `publishing` | uploads, schedules it, and records it for the feedback loop |

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
