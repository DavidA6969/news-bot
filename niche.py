#!/usr/bin/env python3
"""Hold each business to exactly one niche.

An audience that cannot say what you do does not come back: the people one
video or listing brings in are not the people the next is for, so nothing
compounds. This keeps a single committed niche per business, makes every agent
read it, and makes changing it a deliberate act that costs you an explanation
and is written into a history you can look back at.

There are two businesses here and they are separate: the video channel and the
Etsy shop. Each holds exactly one niche, and they need not be related — though
if they are, CRIER can promote the shop through the channel.

    python3 niche.py set --scope etsy --name "..." --audience "..." --why "..."
    python3 niche.py show --scope etsy
    python3 niche.py check --scope etsy "a candidate product"
    python3 niche.py switch --scope etsy --name "..." --reason "..."
    python3 niche.py history

``--scope`` defaults to ``youtube``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["load", "current", "set_niche", "switch", "check", "NicheError", "SCOPES"]


def _scope(name):
    name = (name or DEFAULT_SCOPE).strip().lower()
    if name not in SCOPES:
        raise NicheError("scope must be one of %s (got %r)" % (", ".join(SCOPES), name))
    return name

HERE = Path(__file__).resolve().parent
STORE = HERE / "niche.json"
# Below this, a switch is almost always impatience rather than evidence.
SETTLE_DAYS = 30
MIN_VIDEOS_BEFORE_SWITCH = 10
SCOPES = ("youtube", "etsy")
DEFAULT_SCOPE = "youtube"


class NicheError(RuntimeError):
    """The niche could not be read or changed."""


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load(path=None):
    path = Path(path) if path else STORE
    if not path.exists():
        return {"niches": {}, "history": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise NicheError("%s is not valid JSON (%s)" % (path, exc)) from exc
    if not isinstance(data, dict):
        raise NicheError("%s must contain a JSON object" % path)
    data.setdefault("history", [])
    if not isinstance(data["history"], list):
        raise NicheError('%s has a "history" that is not an array' % path)

    # a file from before there were two businesses held one unscoped niche
    if "niche" in data:
        legacy = data.pop("niche")
        data.setdefault("niches", {})
        if legacy:
            data["niches"].setdefault(DEFAULT_SCOPE, legacy)
    data.setdefault("niches", {})
    if not isinstance(data["niches"], dict):
        raise NicheError('%s has a "niches" that is not an object' % path)
    for scope, niche in data["niches"].items():
        if niche is not None and (not isinstance(niche, dict) or not niche.get("name")):
            raise NicheError('%s: the %s niche has no name. Each scope holds exactly '
                             "one niche or none — never a list." % (path, scope))
    return data


def _save(data, path=None):
    path = Path(path) if path else STORE
    tmp = path.with_name(path.name + ".tmp.%d" % os.getpid())
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def current(path=None, scope=None):
    """The one committed niche for a scope, or None."""
    return load(path)["niches"].get(_scope(scope))


def set_niche(name, audience="", video_format="", why="", keywords=None, path=None,
              scope=None):
    """Commit a scope to a niche. Refuses if one is already set — use switch()."""
    scope = _scope(scope)
    if not (name or "").strip():
        raise NicheError("a niche needs a name")
    data = load(path)
    if data["niches"].get(scope):
        raise NicheError(
            'the %s side is already committed to "%s". Each business holds only '
            "ever one niche. If you genuinely mean to change it: python3 niche.py "
            "switch --scope %s --name ... --reason ..."
            % (scope, data["niches"][scope]["name"], scope)
        )
    data["niches"][scope] = {
        "name": name.strip(),
        "audience": (audience or "").strip(),
        "format": (video_format or "").strip(),
        "why": (why or "").strip(),
        "keywords": [k.strip().lower() for k in (keywords or []) if k.strip()],
        "committedAt": _iso(_now()),
    }
    data["history"].append({"at": _iso(_now()), "action": "set", "scope": scope,
                            "name": data["niches"][scope]["name"], "reason": why or ""})
    _save(data, path)
    return data["niches"][scope]


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
           force=False, path=None, videos_published=None, scope=None):
    """Change a scope's niche. Deliberate, explained, and recorded."""
    scope = _scope(scope)
    if not (reason or "").strip():
        raise NicheError("a switch needs --reason. If you cannot say why in a "
                         "sentence, it is impatience rather than evidence.")
    data = load(path)
    old = data["niches"].get(scope)
    if not old:
        return set_niche(name, audience, video_format, reason, keywords, path, scope)
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
        "at": _iso(_now()), "action": "switch", "scope": scope,
        "from": old["name"], "name": name.strip(),
        "reason": reason.strip(), "heldForDays": round(age, 1) if age is not None else None,
    })
    data["niches"][scope] = {
        "name": name.strip(),
        "audience": (audience or old.get("audience", "")).strip(),
        "format": (video_format or old.get("format", "")).strip(),
        "why": reason.strip(),
        "keywords": [k.strip().lower() for k in (keywords or []) if k.strip()],
        "committedAt": _iso(_now()),
    }
    _save(data, path)
    return data["niches"][scope]


def amend(reason, name=None, audience=None, video_format=None, keywords=None,
          path=None, scope=None):
    """Correct how a committed niche is described, without changing the niche.

    The name is the commitment. Audience, format and keywords are only the
    record of how that commitment is being executed, and they drift as the
    work teaches you what it actually is — a format written before the first
    video is a guess, and leaving a wrong one in place quietly misleads every
    agent that reads it. Refuses to touch the name: that is switch().

    committedAt is deliberately left alone. It is the clock the switch guard
    reads, and an amend that reset it would be a way around that guard.
    """
    scope = _scope(scope)
    if not (reason or "").strip():
        raise NicheError("an amend needs --reason: say what was wrong with the "
                         "old description")
    data = load(path)
    old = data["niches"].get(scope)
    if not old:
        raise NicheError("no %s niche committed yet — there is nothing to amend. "
                         "python3 niche.py set --scope %s --name ..." % (scope, scope))
    if name is not None and name.strip().lower() != old["name"].strip().lower():
        raise NicheError(
            'amend cannot rename a niche. "%s" is the commitment; changing it is a '
            "switch, with everything that costs: python3 niche.py switch --scope %s "
            "--name ... --reason ..." % (old["name"], scope)
        )

    fresh = dict(old)
    changed = []
    if audience is not None and audience.strip() != old.get("audience", ""):
        fresh["audience"] = audience.strip()
        changed.append("audience")
    if video_format is not None and video_format.strip() != old.get("format", ""):
        fresh["format"] = video_format.strip()
        changed.append("format")
    if keywords is not None:
        words = [k.strip().lower() for k in keywords if k.strip()]
        if words != (old.get("keywords") or []):
            fresh["keywords"] = words
            changed.append("keywords")
    if not changed:
        raise NicheError("nothing to amend — every field given already says that")

    data["niches"][scope] = fresh
    data["history"].append({"at": _iso(_now()), "action": "amend", "scope": scope,
                            "name": fresh["name"], "changed": changed,
                            "reason": reason.strip()})
    _save(data, path)
    return fresh


def check(topic, path=None, scope=None):
    """Does a candidate plausibly sit in that scope's committed niche?

    Keyword overlap only — a hint, not a judge. The agent still has to think.
    """
    niche = current(path, scope)
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
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--scope", choices=list(SCOPES), default=DEFAULT_SCOPE,
                        help="which business (default: %s)" % DEFAULT_SCOPE)

    parser = argparse.ArgumentParser(prog="niche.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_set = sub.add_parser("set", parents=[common], help="commit that business to its one niche")
    p_set.add_argument("--name", required=True)
    p_set.add_argument("--audience", default="")
    p_set.add_argument("--format", dest="video_format", default="")
    p_set.add_argument("--why", default="")
    p_set.add_argument("--keywords", nargs="*", default=[])

    sub.add_parser("show", parents=[common], help="print the committed niche")
    sub.add_parser("history", help="every commitment and switch, both businesses")

    p_chk = sub.add_parser("check", parents=[common], help="is a candidate in the niche?")
    p_chk.add_argument("topic")

    p_am = sub.add_parser("amend", parents=[common],
                          help="correct how the committed niche is described")
    p_am.add_argument("--reason", required=True)
    p_am.add_argument("--name", default=None,
                      help="optional guard: must match the committed name")
    p_am.add_argument("--audience", default=None)
    p_am.add_argument("--format", dest="video_format", default=None)
    p_am.add_argument("--keywords", nargs="*", default=None)

    p_sw = sub.add_parser("switch", parents=[common], help="change niche, deliberately")
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
                            args.why, args.keywords, scope=args.scope)
            print('%s committed to "%s"' % (args.scope, got["name"]))
        elif args.command == "show":
            got = current(scope=args.scope)
            if not got:
                print("no %s niche committed yet — run: python3 niche.py set --scope %s "
                      "--name ..." % (args.scope, args.scope), file=sys.stderr)
                return 1
            print(json.dumps(got, indent=2))
        elif args.command == "history":
            data = load()
            if not data["history"]:
                print("no history yet")
                return 0
            for row in data["history"]:
                scope = row.get("scope", DEFAULT_SCOPE)
                if row.get("action") == "switch":
                    print("%s  [%s] switched from %s to %s after %s days — %s" % (
                        row["at"], scope, row.get("from"), row.get("name"),
                        row.get("heldForDays"), row.get("reason")))
                elif row.get("action") == "amend":
                    print("%s  [%s] amended %s of %s — %s" % (
                        row["at"], scope, "/".join(row.get("changed") or []),
                        row.get("name"), row.get("reason")))
                else:
                    print("%s  [%s] committed to %s" % (row["at"], scope, row.get("name")))
            for scope in SCOPES:
                switches = sum(1 for r in data["history"]
                               if r.get("action") == "switch"
                               and r.get("scope", DEFAULT_SCOPE) == scope)
                if switches >= 2:
                    print("\n%s has switched %d times. Each one restarts the audience "
                          "from nothing — that pattern, not the niche, is usually what "
                          "stops a business growing." % (scope, switches))
        elif args.command == "amend":
            got = amend(args.reason, args.name, args.audience, args.video_format,
                        args.keywords, scope=args.scope)
            print('%s: amended the description of "%s"' % (args.scope, got["name"]))
        elif args.command == "check":
            got = check(args.topic, scope=args.scope)
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
                         args.keywords, args.force, videos_published=videos,
                         scope=args.scope)
            print('%s switched to "%s"' % (args.scope, got["name"]))
        return 0
    except NicheError as exc:
        print("niche.py: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
