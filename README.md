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
| `avgDurationSec` | rolling mean over completed runs; `null` until the first `finish`, shown as `—` |
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
python3 /ABSOLUTE/PATH/TO/news-bot/status.py start trend-research "..."   # <- your real path
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

## Worked example: a Claude Code subagent

Save as `.claude/agents/trend-research.md`. The `name` matches the agent's `id` in
`agents.json` so the two line up.

**Replace `/ABSOLUTE/PATH/TO/news-bot` with the real path to this repo on your
machine** (`pwd` will tell you). It has to be absolute: a subagent's working directory
is not guaranteed to be the project root.

````markdown
---
name: trend-research
description: Pulls trending topics for the channel and filters out saturated ones. Use at the start of a video pipeline run.
tools: Bash, Read, Write, WebSearch
model: sonnet
---

You are ATLAS, the trend research stage of the video pipeline.

Report your status to the operations dashboard as you work. Always call the helper by
its absolute path — your working directory may not be the project root.

**1. Before anything else, announce that you have started:**

```bash
python3 /ABSOLUTE/PATH/TO/news-bot/status.py start trend-research "Scanning last 7 days in niche"
```

**2. While working, log anything a human would want to see later:**

```bash
python3 /ABSOLUTE/PATH/TO/news-bot/status.py log trend-research "Source rate-limited, backing off 30s" --level warn
```

**3. When you succeed, record a one-line summary of what you produced:**

```bash
python3 /ABSOLUTE/PATH/TO/news-bot/status.py finish trend-research "3 topics selected, 2 rejected"
```

**4. If you cannot complete the task, report the failure instead:**

```bash
python3 /ABSOLUTE/PATH/TO/news-bot/status.py fail trend-research "All sources returned 503"
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
  blob. The simulation stops once it cools.
- **No layout jump.** Each poll compares a topology signature (sorted ids + sorted
  edges). If only statuses changed — the common case — the simulation is never touched
  and only attributes are repainted. A new agent reheats the layout gently; existing
  nodes keep their positions.
- **Drag** a node to pin it there permanently; **double-click** to release it.
  *Reset view* clears zoom and all pins.
- **Click** a node for the dossier: full record, live elapsed time while running, and
  that agent's last 20 events. Escape or a click on the background closes it.
- **Activity feed** holds the 50 most recent events, newest first. It follows the
  newest event unless you have scrolled away, in which case it holds your position and
  offers a *N NEW* pill. Collapsed state is remembered.
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
