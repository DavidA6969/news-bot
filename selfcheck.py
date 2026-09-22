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
import os
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
                "youtube.py", "performance.py", "render.py", "niche.py",
                "fetch_clips.py", "style.py", "etsy.py", "suppliers.py",
                "voice.py", "short.py"]
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

    section("[7] performance loops")
    try:
        perf = importlib.import_module("performance")
        store = perf.load()
        for scope in perf.SCOPES:
            tracked = perf.items(store, scope)
            noun = perf.NOUN[scope][1]
            measured = sum(1 for v in tracked if v.get("checks"))
            if not tracked:
                note(WARN, "%s: nothing recorded yet" % scope,
                     "the loop stays open until %s are recorded" % noun)
            elif measured < perf.MIN_FOR_PATTERNS:
                note(WARN, "%s: too few measured to learn from" % scope,
                     "%d of %d needed" % (measured, perf.MIN_FOR_PATTERNS))
            else:
                note(OK, "%s: enough data to draw conclusions" % scope,
                     "%d measured" % measured)
    except Exception as exc:
        note(FAIL, "performance.py not usable", str(exc)[:90])

    section("[8] niches")
    try:
        niche_mod = importlib.import_module("niche")
        data = niche_mod.load()
        for scope in niche_mod.SCOPES:
            one = data["niches"].get(scope)
            if one is None:
                note(WARN, "%s: no niche committed" % scope,
                     "python3 niche.py set --scope %s --name ..." % scope)
                continue
            note(OK, "%s: exactly one niche" % scope, one["name"])
            note(OK if one.get("keywords") else WARN, "%s: gating keywords" % scope,
                 ", ".join(one.get("keywords") or []) or "none — topics cannot be gated")
            switches = sum(1 for r in data.get("history", [])
                           if r.get("action") == "switch"
                           and r.get("scope", niche_mod.DEFAULT_SCOPE) == scope)
            note(OK if switches < 2 else WARN, "%s: held, not churned" % scope,
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

    try:
        style_mod = importlib.import_module("style")
        look = style_mod.current()
        if look.get("_default"):
            note(WARN, "no style committed", "using the default; run: python3 style.py init")
        else:
            note(OK, "one editing style committed",
                 "%s v%d" % (look.get("name", "?"), look.get("version", 0)))
        fmt = look["format"]
        note(OK, "every video uses one format",
             "%dx%d @ %dfps" % (fmt["width"], fmt["height"], fmt["fps"]))
        shorts = look.get("shorts") or {}
        vertical = fmt["height"] > fmt["width"]
        note(OK if vertical else FAIL, "the format is vertical, as Shorts require",
             "%dx%d" % (fmt["width"], fmt["height"]))
        note(OK if shorts.get("max_seconds", 999) <= 180 else FAIL,
             "inside YouTube's 180s Shorts limit",
             "max %.0fs, target %.0fs" % (shorts.get("max_seconds", 0),
                                          shorts.get("target_seconds", 0)))
        ok, why = style_mod.shorts_verdict(shorts.get("max_seconds", 0) + 1,
                                           fmt["width"], fmt["height"], look)
        note(OK if not ok else FAIL, "an over-length cut is refused",
             why[0][:60] if why else "it was accepted")
        changes = sum(1 for r in style_mod.load().get("history", [])
                      if r.get("action") == "set")
        note(OK if changes < 6 else WARN, "the look is holding still",
             "%d change(s)" % changes)
    except Exception as exc:
        note(FAIL, "style.py not usable", str(exc)[:90])

    try:
        fetch_mod = importlib.import_module("fetch_clips")
        # the point is not a fixed list of providers but that none of them is a
        # platform scraper — this survives adding legitimate sources
        platforms = ("youtube", "youtu.be", "tiktok", "instagram", "facebook")
        blob = " ".join(sorted(fetch_mod.PROVIDERS)).lower()
        import inspect as _inspect
        code = " ".join(_inspect.getsource(fn).lower()
                        for fn in fetch_mod.PROVIDERS.values())
        scraper = [pl for pl in platforms if pl in blob or (pl + ".com") in code]
        note(OK if not scraper else FAIL, "no clip source is a platform scraper",
             ", ".join(sorted(fetch_mod.PROVIDERS)) if not scraper
             else "reaches " + ", ".join(scraper))
        have = [n for n in fetch_mod.STOCK
                if os.environ.get("%s_API_KEY" % n.upper())]
        note(OK if have else WARN, "a stock API key is configured",
             ", ".join(have) if have else
             "set PEXELS_API_KEY or PIXABAY_API_KEY (both free) for b-roll")
        keyed = [n for n in fetch_mod.ARCHIVE
                 if "_API_KEY" in _inspect.getsource(fetch_mod.PROVIDERS[n])]
        note(OK if not keyed else FAIL, "archive footage needs no key",
             ", ".join(fetch_mod.ARCHIVE) if not keyed
             else "%s wants a key" % ", ".join(keyed))
        note(OK if not (set(fetch_mod.GROUPS) & set(fetch_mod.PROVIDERS)) else FAIL,
             "no provider group shadows a source name", ", ".join(fetch_mod.GROUPS))
    except Exception as exc:
        note(FAIL, "fetch_clips.py not usable", str(exc)[:90])

    try:
        voice_mod = importlib.import_module("voice")
        engines = [n for n, _ in voice_mod.usable_engines()]
        note(OK if engines else WARN, "a speech engine is available",
             ", ".join(engines) if engines else
             "install piper-tts or espeak-ng, or record the lines yourself "
             "(--recorded); your own voice is better anyway")
    except Exception as exc:
        note(FAIL, "voice.py not usable", str(exc)[:90])

    section("[10] etsy shop")
    try:
        etsy = importlib.import_module("etsy")
        note(OK if etsy.SCOPES == "listings_r listings_w shops_r" else FAIL,
             "requests only listing and shop-read scopes", etsy.SCOPES)
        # the reselling gate has to refuse, not warn
        try:
            etsy.build_listing(title="t", description="d", price=1.0, quantity=1,
                               taxonomy_id=1, who_made="someone_else",
                               when_made="made_to_order")
            note(FAIL, "reselling is refused", "it was accepted")
        except etsy.ResellRefused:
            note(OK, "reselling is refused")
        etsy.build_listing(title="t", description="d", price=1.0, quantity=1,
                           taxonomy_id=1, who_made="i_did", when_made="made_to_order")
        note(OK, "your own design passes")
        note(OK if (HERE / "etsy_token.json").exists() else WARN, "shop connected",
             "" if (HERE / "etsy_token.json").exists()
             else "run: python3 etsy.py login (needs ETSY_API_KEY)")

        sup = importlib.import_module("suppliers")
        partners = sup.load()["partners"]
        if not partners:
            note(WARN, "no production partners recorded",
                 "etsy-supplier records them; listings need one unless you make it yourself")
        else:
            ready = [p["id"] for p in partners if sup.ready(p["id"])[0]]
            note(OK, "production partners recorded",
                 "%d of %d ready to list against" % (len(ready), len(partners)))
        try:
            sup.add("AliExpress store 99", "Shenzhen", "ships", path=HERE / ".selfcheck-tmp.json")
            note(FAIL, "dropship sources are refused", "one was accepted")
        except sup.SupplierError:
            note(OK, "dropship sources are refused")
        finally:
            (HERE / ".selfcheck-tmp.json").unlink(missing_ok=True)
    except Exception as exc:
        note(FAIL, "etsy tooling not usable", str(exc)[:90])

    section("[11] agent definitions")
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

    section("[12] dashboard")
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

    section("[13] secrets")
    ignore = (HERE / ".gitignore").read_text(encoding="utf-8") if (HERE / ".gitignore").exists() else ""
    for secret in ("client_secret.json", "youtube_token.json", "etsy_token.json"):
        note(OK if secret in ignore else FAIL, "%s is git-ignored" % secret)
    try:
        tracked = subprocess.run(["git", "ls-files"], cwd=str(HERE),
                                 capture_output=True, text=True, timeout=20).stdout.split()
        leaked = [f for f in tracked
                  if f in ("client_secret.json", "youtube_token.json", "etsy_token.json")]
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
