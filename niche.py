#!/usr/bin/env python3
"""Hold the channel to exactly one niche.

A channel that changes subject every few weeks never builds an audience: the
people a video brings in are not the people the next one is for, so nothing
compounds. This keeps a single committed niche on disk, makes every agent read
it, and makes changing it a deliberate act that costs you an explanation and
gets written into a history you can look back at.

    python3 niche.py set --name "..." --audience "..." --format "..." --why "..."
    python3 niche.py show
    python3 niche.py check "a candidate topic"      # in-niche or not
    python3 niche.py switch --name "..." --reason "..."   # deliberate, recorded
    python3 niche.py history
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["load", "current", "set_niche", "switch", "NicheError"]

HERE = Path(__file__).resolve().parent
STORE = HERE / "niche.json"
# Below this, a switch is almost always impatience rather than evidence.
SETTLE_DAYS = 30
MIN_VIDEOS_BEFORE_SWITCH = 10


class NicheError(RuntimeError):
    """The niche could not be read or changed."""


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load(path=None):
    path = Path(path) if path else STORE
    if not path.exists():
        return {"niche": None, "history": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise NicheError("%s is not valid JSON (%s)" % (path, exc)) from exc
    if not isinstance(data, dict):
        raise NicheError("%s must contain a JSON object" % path)
    data.setdefault("history", [])
    if not isinstance(data["history"], list):
        raise NicheError('%s has a "history" that is not an array' % path)
    niche = data.get("niche")
    if niche is not None and (not isinstance(niche, dict) or not niche.get("name")):
        raise NicheError('%s has a "niche" without a name. There is exactly one '
                         "niche or none — never a list." % path)
    return data


def _save(data, path=None):
    path = Path(path) if path else STORE
    tmp = path.with_name(path.name + ".tmp.%d" % os.getpid())
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def current(path=None):
    """The one committed niche, or None."""
    return load(path).get("niche")


def set_niche(name, audience="", video_format="", why="", keywords=None, path=None):
    """Commit to a niche. Refuses if one is already set — use switch()."""
    if not (name or "").strip():
        raise NicheError("a niche needs a name")
    data = load(path)
    if data.get("niche"):
        raise NicheError(
            'already committed to "%s". There is only ever one niche. If you '
            "genuinely mean to change it, use: python3 niche.py switch --name ... "
            "--reason ..." % data["niche"]["name"]
        )
    data["niche"] = {
        "name": name.strip(),
        "audience": (audience or "").strip(),
        "format": (video_format or "").strip(),
        "why": (why or "").strip(),
        "keywords": [k.strip().lower() for k in (keywords or []) if k.strip()],
        "committedAt": _iso(_now()),
    }
    data["history"].append({"at": _iso(_now()), "action": "set",
                            "name": data["niche"]["name"], "reason": why or ""})
    _save(data, path)
    return data["niche"]


def _days_since(iso_value):
    if not iso_value:
        return None
    try:
        when = datetime.fromisoformat(str(iso_value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if not when.tzinfo:
        when = when.replace(tzinfo=timezone.utc)
    return (_now() - when).total_seconds() / 86400.0


def switch(name, reason, audience="", video_format="", keywords=None,
           force=False, path=None, videos_published=None):
    """Change niche. Deliberate, explained, and recorded."""
    if not (reason or "").strip():
        raise NicheError("a switch needs --reason. If you cannot say why in a "
                         "sentence, it is impatience rather than evidence.")
    data = load(path)
    old = data.get("niche")
    if not old:
        return set_niche(name, audience, video_format, reason, keywords, path)
    if old["name"].strip().lower() == (name or "").strip().lower():
        raise NicheError("already on that niche")

    age = _days_since(old.get("committedAt"))
    if not force:
        if age is not None and age < SETTLE_DAYS:
            raise NicheError(
                '"%s" is only %d days old. A niche needs about %d days before its '
                "numbers mean anything, and channels are abandoned far more often "
                "from impatience than from a dead niche. Re-run with --force if you "
                "are certain." % (old["name"], int(age), SETTLE_DAYS)
            )
        if videos_published is not None and videos_published < MIN_VIDEOS_BEFORE_SWITCH:
            raise NicheError(
                "only %d video(s) published in \"%s\". Fewer than %d cannot tell you "
                "whether the niche or the execution was wrong. Re-run with --force if "
                "you are certain." % (videos_published, old["name"], MIN_VIDEOS_BEFORE_SWITCH)
            )

    data["history"].append({
        "at": _iso(_now()), "action": "switch", "from": old["name"], "name": name.strip(),
        "reason": reason.strip(), "heldForDays": round(age, 1) if age is not None else None,
    })
    data["niche"] = {
        "name": name.strip(),
        "audience": (audience or old.get("audience", "")).strip(),
        "format": (video_format or old.get("format", "")).strip(),
        "why": reason.strip(),
        "keywords": [k.strip().lower() for k in (keywords or []) if k.strip()],
        "committedAt": _iso(_now()),
    }
    _save(data, path)
    return data["niche"]


def check(topic, path=None):
    """Does a candidate topic plausibly sit in the committed niche?

    Keyword overlap only — a hint, not a judge. The agent still has to think.
    """
    niche = current(path)
    if not niche:
        return {"niche": None, "verdict": "no niche committed",
                "detail": "run niche.py set, or have COMPASS do it"}
    words = set(w.strip(".,!?:;\"'()").lower()
                for w in str(topic).split() if len(w) > 3)
    keywords = set(niche.get("keywords") or [])
    if not keywords:
        return {"niche": niche["name"], "verdict": "unknown",
                "detail": "no keywords recorded for this niche — judge it yourself"}
    hits = sorted(k for k in keywords if k in words or any(k in w for w in words))
    return {
        "niche": niche["name"],
        "verdict": "in niche" if hits else "off niche",
        "matched": hits,
        "detail": ("matched %s" % ", ".join(hits)) if hits else
                  ("no overlap with %s — either reframe it into the niche or "
                   "drop it" % ", ".join(sorted(keywords)[:6])),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(prog="niche.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_set = sub.add_parser("set", help="commit to the one niche")
    p_set.add_argument("--name", required=True)
    p_set.add_argument("--audience", default="")
    p_set.add_argument("--format", dest="video_format", default="")
    p_set.add_argument("--why", default="")
    p_set.add_argument("--keywords", nargs="*", default=[])

    sub.add_parser("show", help="print the committed niche")
    sub.add_parser("history", help="every commitment and switch so far")

    p_chk = sub.add_parser("check", help="is a candidate topic in the niche?")
    p_chk.add_argument("topic")

    p_sw = sub.add_parser("switch", help="change niche, deliberately")
    p_sw.add_argument("--name", required=True)
    p_sw.add_argument("--reason", required=True)
    p_sw.add_argument("--audience", default="")
    p_sw.add_argument("--format", dest="video_format", default="")
    p_sw.add_argument("--keywords", nargs="*", default=[])
    p_sw.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "set":
            got = set_niche(args.name, args.audience, args.video_format,
                            args.why, args.keywords)
            print('committed to "%s"' % got["name"])
        elif args.command == "show":
            got = current()
            if not got:
                print("no niche committed yet — run: python3 niche.py set --name ...",
                      file=sys.stderr)
                return 1
            print(json.dumps(got, indent=2))
        elif args.command == "history":
            data = load()
            if not data["history"]:
                print("no history yet")
                return 0
            for row in data["history"]:
                if row.get("action") == "switch":
                    print("%s  switched from %s to %s after %s days — %s" % (
                        row["at"], row.get("from"), row.get("name"),
                        row.get("heldForDays"), row.get("reason")))
                else:
                    print("%s  committed to %s" % (row["at"], row.get("name")))
            switches = sum(1 for r in data["history"] if r.get("action") == "switch")
            if switches >= 2:
                print("\n%d switches so far. Each one restarts the audience from "
                      "nothing — that pattern, not the niche, is usually what stops "
                      "a channel growing." % switches)
        elif args.command == "check":
            got = check(args.topic)
            print("%s: %s" % (got["verdict"].upper(), got["detail"]))
            return 0 if got["verdict"] in ("in niche", "unknown") else 2
        else:
            videos = None
            try:
                sys.path.insert(0, str(HERE))
                import performance
                videos = len(performance.load()["videos"])
            except Exception:
                pass
            got = switch(args.name, args.reason, args.audience, args.video_format,
                         args.keywords, args.force, videos_published=videos)
            print('switched to "%s"' % got["name"])
        return 0
    except NicheError as exc:
        print("niche.py: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
