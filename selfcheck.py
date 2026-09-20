#!/usr/bin/env python3
"""Check that the pipeline is wired up correctly, before you rely on it.

    python3 selfcheck.py

Exits 0 if everything that must work does. FAIL means something is broken;
WARN means a step you have not set up yet (credentials, a first run) and is
expected on a fresh clone.
"""

from __future__ import annotations

import importlib
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OK, WARN, FAIL = "PASS", "WARN", "FAIL"
results = []


def note(level, label, detail=""):
    results.append((level, label, detail))
    colour = {"PASS": "  PASS  ", "WARN": "  WARN  ", "FAIL": "  FAIL  "}[level]
    print(colour + label + (("  ::  " + detail) if detail else ""))


def section(name):
    print("\n" + name)


def main():
    section("[1] environment")
    if sys.version_info >= (3, 9):
        note(OK, "python %d.%d" % sys.version_info[:2])
    else:
        note(FAIL, "python 3.9+ required", "found %d.%d" % sys.version_info[:2])

    section("[2] files")
    required = ["agents.json", "dashboard.html", "status.py", "schedule.py",
                "youtube.py", "performance.py", "render.py", "niche.py"]
    for name in required:
        note(OK if (HERE / name).exists() else FAIL, name,
             "" if (HERE / name).exists() else "missing")

    section("[3] state file")
    try:
        data = json.loads((HERE / "agents.json").read_text(encoding="utf-8"))
        agents = data["agents"]
        ids = [a["id"] for a in agents]
        note(OK, "agents.json parses", "%d agents" % len(agents))
        note(OK if len(ids) == len(set(ids)) else FAIL, "agent ids are unique")
        dangling = [d for a in agents for d in a.get("dependsOn", []) if d not in ids]
        note(OK if not dangling else FAIL, "every dependsOn resolves",
             "" if not dangling else "unknown: " + ", ".join(dangling))
        bad_status = [a["id"] for a in agents
                      if a.get("status") not in ("idle", "working", "blocked", "done", "error")]
        note(OK if not bad_status else FAIL, "all statuses are valid",
             "" if not bad_status else ", ".join(bad_status))
        note(OK if isinstance(data.get("events"), list) else FAIL, "events is an array")
    except Exception as exc:
        note(FAIL, "agents.json unusable", str(exc)[:90])
        agents, ids = [], []

    section("[4] writer")
    try:
        sys.path.insert(0, str(HERE))
        status = importlib.import_module("status")
        snap = status.snapshot()                       # read-only: takes the lock, writes nothing
        note(OK, "status.py imports and can take the lock", "%d agents visible" % len(snap["agents"]))
        note(OK if status.MAX_EVENTS == 500 else WARN, "events capped",
             "at %d" % status.MAX_EVENTS)
        stale = HERE / "agents.json.lock.d"
        note(OK if not stale.exists() else WARN, "no stale lock directory",
             "" if not stale.exists() else "delete %s" % stale)
    except Exception as exc:
        note(FAIL, "status.py not usable", str(exc)[:90])

    section("[5] schedule")
    try:
        schedule = importlib.import_module("schedule")
        cfg = schedule.load_config()
        slots = schedule.plan(days=7, config=cfg)
        note(OK if slots else FAIL, "produces slots", "%d over 7 days" % len(slots))
        again = schedule.plan(days=7, config=cfg)
        note(OK if slots == again else FAIL, "the plan is stable between calls",
             "" if slots == again else "it changes — the schedule means nothing")
        per_day = {}
        for s in slots:
            key = s.astimezone(cfg["_tz"]).date()
            per_day[key] = per_day.get(key, 0) + 1
        if per_day:
            lo, hi = min(per_day.values()), max(per_day.values())
            note(OK if hi <= cfg.get("max_per_day", 3) else FAIL,
                 "cadence within the cap", "%d-%d a day" % (lo, hi))
        taken = schedule.claimed()
        note(OK, "claimed-slot state readable", "%d claimed" % len(taken))
        if not (HERE / "schedule.json").exists():
            note(WARN, "no schedule.json", "using defaults; run: python3 schedule.py write-config")
    except Exception as exc:
        note(FAIL, "schedule.py not usable", str(exc)[:90])

    section("[6] youtube")
    try:
        yt = importlib.import_module("youtube")
        note(OK if yt.SCOPE.endswith("youtube.upload") else FAIL,
             "requests only the upload scope", yt.SCOPE.rsplit("/", 1)[-1])
        yt.build_body("selfcheck", "x", ["a"], "22", "private")
        note(OK, "metadata validation works")
        note(OK if (HERE / "client_secret.json").exists() else WARN, "client_secret.json",
             "" if (HERE / "client_secret.json").exists()
             else "not set up yet — see README, then: python3 youtube.py login")
        note(OK if (HERE / "youtube_token.json").exists() else WARN, "authorised",
             "" if (HERE / "youtube_token.json").exists() else "run: python3 youtube.py login")
    except Exception as exc:
        note(FAIL, "youtube.py not usable", str(exc)[:90])

    section("[7] performance loop")
    try:
        perf = importlib.import_module("performance")
        store = perf.load()
        n = len(store["videos"])
        note(OK, "performance.py readable", "%d video(s) tracked" % n)
        measured = sum(1 for v in store["videos"] if v.get("checks"))
        if n == 0:
            note(WARN, "nothing published yet", "the loop is open until HERALD records a video")
        elif measured < perf.MIN_FOR_PATTERNS:
            note(WARN, "not enough measured videos to learn from",
                 "%d of %d needed" % (measured, perf.MIN_FOR_PATTERNS))
        else:
            note(OK, "enough data to draw conclusions", "%d measured" % measured)
    except Exception as exc:
        note(FAIL, "performance.py not usable", str(exc)[:90])

    section("[8] niche")
    try:
        niche_mod = importlib.import_module("niche")
        data = niche_mod.load()
        one = data.get("niche")
        if one is None:
            note(WARN, "no niche committed yet",
                 "run niche-strategy, or: python3 niche.py set --name ...")
        else:
            note(OK, "exactly one niche committed", one["name"])
            note(OK if one.get("keywords") else WARN, "niche has gating keywords",
                 ", ".join(one.get("keywords") or []) or "none — ATLAS cannot gate topics")
        switches = sum(1 for r in data.get("history", []) if r.get("action") == "switch")
        note(OK if switches < 2 else WARN, "niche is being held, not churned",
             "%d switch(es)" % switches if switches else "no switches")
    except Exception as exc:
        note(FAIL, "niche.py not usable", str(exc)[:90])

    section("[9] renderer")
    try:
        render_mod = importlib.import_module("render")
        try:
            binary = render_mod.ffmpeg_bin()
            note(OK, "ffmpeg found", binary)
        except Exception as exc:
            note(FAIL, "ffmpeg missing", str(exc)[:110])
        # the licence gate must actually refuse, not just warn
        try:
            render_mod.validate_plan({"beats": [{"clip": __file__, "duration": 1}]})
            note(FAIL, "unlicensed footage is refused", "it was accepted")
        except render_mod.RenderError as exc:
            note(OK if "license" in str(exc) else FAIL, "unlicensed footage is refused")
        try:
            render_mod.validate_plan({"beats": [
                {"clip": __file__, "duration": 1, "license": "from youtube.com/watch?v=x"}]})
            note(FAIL, "ripped footage is refused", "it was accepted")
        except render_mod.RenderError as exc:
            note(OK if "copyright" in str(exc) else FAIL, "ripped footage is refused")
    except Exception as exc:
        note(FAIL, "render.py not usable", str(exc)[:90])

    section("[10] agent definitions")
    defs = sorted((HERE / ".claude" / "agents").glob("*.md"))
    if not defs:
        note(FAIL, "no agent definitions", "expected .claude/agents/*.md")
    for path in defs:
        text = path.read_text(encoding="utf-8")
        head = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        if not head:
            note(FAIL, path.name, "no YAML frontmatter")
            continue
        fields = dict(line.split(": ", 1) for line in head.group(1).split("\n") if ": " in line)
        name = fields.get("name", "")
        problems = []
        if not name or not re.fullmatch(r"[a-z][a-z0-9-]*", name):
            problems.append("bad name %r" % name)
        if not fields.get("description"):
            problems.append("no description")
        if ids and name not in ids:
            problems.append("no agent %r in agents.json" % name)
        if "ABSOLUTE/PATH" in text or "/path/to/" in text.lower():
            problems.append("still contains a placeholder path")
        note(OK if not problems else FAIL, path.name, "; ".join(problems))

    section("[11] dashboard")
    try:
        page = (HERE / "dashboard.html").read_text(encoding="utf-8")
        note(OK if "agents.json" in page else FAIL, "reads agents.json")
        pinned = re.search(r"d3@(\d+\.\d+\.\d+)", page)
        note(OK if pinned else WARN, "D3 pinned to an exact version",
             pinned.group(1) if pinned else "unpinned CDN URL")
        note(OK if "cache:'no-store'" in page.replace(" ", "") else WARN,
             "polls without caching")
    except Exception as exc:
        note(FAIL, "dashboard.html unreadable", str(exc)[:90])

    section("[12] secrets")
    ignore = (HERE / ".gitignore").read_text(encoding="utf-8") if (HERE / ".gitignore").exists() else ""
    for secret in ("client_secret.json", "youtube_token.json"):
        note(OK if secret in ignore else FAIL, "%s is git-ignored" % secret)
    try:
        tracked = subprocess.run(["git", "ls-files"], cwd=str(HERE),
                                 capture_output=True, text=True, timeout=20).stdout.split()
        leaked = [f for f in tracked if f in ("client_secret.json", "youtube_token.json")]
        note(OK if not leaked else FAIL, "no credential is committed",
             "" if not leaked else "TRACKED: " + ", ".join(leaked))
    except Exception:
        note(WARN, "could not ask git what is tracked")

    fails = [r for r in results if r[0] == FAIL]
    warns = [r for r in results if r[0] == WARN]
    print("\n" + "-" * 62)
    print("%d passed, %d warnings, %d failures" %
          (len(results) - len(fails) - len(warns), len(warns), len(fails)))
    if fails:
        print("\nBroken:")
        for _, label, detail in fails:
            print("  - %s%s" % (label, ("  ::  " + detail) if detail else ""))
    elif warns:
        print("\nNothing is broken. Outstanding setup:")
        for _, label, detail in warns:
            print("  - %s%s" % (label, ("  ::  " + detail) if detail else ""))
    else:
        print("\nEverything checks out.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
