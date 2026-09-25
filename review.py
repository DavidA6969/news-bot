#!/usr/bin/env python3
"""What a finished Short has to carry before a human is asked to look at it.

Three things live here, and they are the three the production spec asks for
that nothing else in this repo was doing:

`sources.csv`   one row per clip: where it came from, who made it, under what
                licence, when that was checked, and the exact attribution text
                the licence obliges. `licenses.json` already records a licence
                per clip for the renderer's own refusal, but it is keyed by the
                file on disk and says nothing about the URL it came from or the
                day someone looked. A licence can be withdrawn; the date is
                what makes the claim checkable later.

scoring         hook strength, payoff clarity, rewatch value, 1-10 each. The
                point is not the arithmetic, it is refusing to edit a clip that
                scored badly -- the edit is the expensive part, so the decision
                belongs before it.

the handoff     one folder per Short in `review/`, holding the video and the
                three text files a human needs to approve it. Nothing here
                uploads anything.
"""

import argparse
import csv
import json
import os
import re
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "sources.csv"
REVIEW = HERE / "review"

# The spec's own wording, kept verbatim so a reader can check it against the
# brief rather than against my paraphrase.
# `source` is what the clip was cut out of, and it is not decoration: every
# render writes clip01.mp4, clip02.mp4 ... so a clip's NAME is not an identity.
# Keyed on the name alone, logging a new video's clip01.mp4 would silently
# replace the last video's, and sources.csv would only ever hold the most
# recent Short -- an audit log that quietly forgets is worse than none.
COLUMNS = ["clip", "source", "url", "creator", "license", "checked",
           "attribution", "release", "notes"]

# Section 1: these are refused unless a signed release exists, and the refusal
# is recorded rather than assumed.
SENSITIVE = ("minor", "minors", "child", "children", "medical", "injury",
             "injured", "accident", "hospital", "funeral", "vulnerable")

MIN_SCORE = 7.0          # a clip's mean, below which it is not worth editing
FLOOR_ANY = 5            # and no single axis may sit under this

# Section 8: the app's own furniture sits here, so our text must not.
SAFE_BOTTOM_PCT = 20.0
SAFE_SIDE_PCT = 15.0

TITLE_MAX = 60           # section 7
MIN_HASHTAGS, MAX_HASHTAGS = 3, 5


class ReviewError(Exception):
    pass


def _today():
    return date.today().isoformat()


# ---------------------------------------------------------------- sources.csv

def _rows(path=None):
    path = Path(path or SOURCES)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(r) for r in csv.DictReader(handle)]


def _write(rows, path=None):
    path = Path(path or SOURCES)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in COLUMNS})
    os.replace(tmp, path)


def log_source(clip, url, creator, licence, attribution, source="", release="",
               notes="", checked=None, path=None):
    """Record where one clip came from. Re-logging the same clip replaces it.

    "The same clip" means the same name cut from the same source, not the same
    name: see COLUMNS. Every field is required except `source`, `release` and
    `notes`, because a row with a blank licence or a blank attribution is worse
    than no row -- it looks like the work was done.
    """
    missing = [n for n, v in (("clip", clip), ("url", url), ("creator", creator),
                              ("license", licence), ("attribution", attribution))
               if not str(v or "").strip()]
    if missing:
        raise ReviewError(
            "a source row needs %s. A row with those blank looks like the "
            "checking was done when it was not." % ", ".join(missing))
    row = {"clip": Path(str(clip)).name,
           "source": Path(str(source or "")).name, "url": str(url).strip(),
           "creator": str(creator).strip(), "license": str(licence).strip(),
           "checked": str(checked or _today()).strip(),
           "attribution": str(attribution).strip(),
           "release": str(release or "").strip(), "notes": str(notes or "").strip()}
    rows = [r for r in _rows(path)
            if not (r.get("clip") == row["clip"]
                    and (r.get("source") or "") == row["source"])]
    rows.append(row)
    _write(rows, path)
    return row


def sources(path=None):
    return _rows(path)


def source_for(clip, path=None, source=None):
    """The row for this clip, preferring one that names the same source.

    Given a source, a row for a different source is not a match at all -- two
    videos can both hold a clip01.mp4 and they are not the same footage.
    """
    name = Path(str(clip)).name
    want = Path(str(source or "")).name
    loose = None
    for row in _rows(path):
        if row.get("clip") != name:
            continue
        if want and (row.get("source") or "") == want:
            return row
        if not want:
            return row
        if not (row.get("source") or ""):
            loose = loose or row
    return loose


def _origins(plan):
    """Which source each clip in the plan was cut from, where that is recorded."""
    found = {}
    for beat in plan.get("beats") or []:
        clip = Path(str(beat.get("clip") or ""))
        book = clip.parent / "origins.json"
        if not book.exists():
            continue
        try:
            data = json.loads(book.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        entry = data.get(clip.name) or {}
        if entry.get("source"):
            found[clip.name] = Path(str(entry["source"])).name
    return found


def sources_report(plan_path, path=None):
    """Gate: every clip this plan uses has a logged, dated, attributed source.

    This is deliberately stricter than the renderer's own licence check. That
    one asks "is there a licence string beside this file"; this one asks "can
    somebody else verify that claim" -- which needs the URL and the date.
    """
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    beats = plan.get("beats") or []
    findings = []
    if not beats:
        return False, [("fail", "the plan has clips to check", "none found")]

    clips = []
    for beat in beats:
        name = Path(str(beat.get("clip") or "")).name
        if name and name not in clips:
            clips.append(name)

    origins = _origins(plan)
    missing, undated, unsigned = [], [], []
    for name in clips:
        row = source_for(name, path, origins.get(name))
        if not row:
            missing.append(name)
            continue
        if not row.get("checked"):
            undated.append(name)
        blob = " ".join((row.get("notes", ""), row.get("creator", ""))).lower()
        if any(word in blob for word in SENSITIVE) and not row.get("release"):
            unsigned.append(name)

    findings.append((
        "fail" if missing else "ok", "every clip has a row in sources.csv",
        "%d with no row: %s" % (len(missing), ", ".join(missing[:4]))
        if missing else "%d clip%s logged" % (len(clips), "" if len(clips) == 1 else "s")))
    if undated:
        findings.append((
            "fail", "every source says when it was checked",
            "%s have no date. A licence can be withdrawn; undated is unverifiable."
            % ", ".join(undated[:4])))
    if unsigned:
        findings.append((
            "fail", "sensitive footage has a signed release",
            "%s are flagged sensitive with no release recorded. Section 1 refuses "
            "these outright unless a release exists." % ", ".join(unsigned[:4])))
    ok = not any(level == "fail" for level, _, _ in findings)
    return ok, findings


# -------------------------------------------------------------------- scoring

def score(hook, payoff, rewatch):
    """A clip's score, and whether it is worth the edit.

    The mean carries the decision, but a single very weak axis vetoes it: a
    clip with a 10 hook and a 3 payoff averages out respectably and is still
    a video that opens well and then disappoints, which is the worst shape a
    Short can have.
    """
    axes = {"hook": hook, "payoff": payoff, "rewatch": rewatch}
    for name, value in axes.items():
        if not isinstance(value, (int, float)) or not 1 <= value <= 10:
            raise ReviewError("%s must be 1-10, got %r" % (name, value))
    mean = round(sum(axes.values()) / 3.0, 2)
    weakest = min(axes, key=lambda k: axes[k])
    vetoed = axes[weakest] <= FLOOR_ANY
    return {
        "scores": axes, "mean": mean,
        "edit": bool(mean >= MIN_SCORE and not vetoed),
        "why": ("%s is only %g — a clip that opens well and then disappoints is "
                "the worst shape a Short can have" % (weakest, axes[weakest]))
               if vetoed else
               ("mean %g is under %g" % (mean, MIN_SCORE)) if mean < MIN_SCORE
               else "mean %g" % mean,
    }


# ----------------------------------------------------------------- safe zones

def safe_zone_report(look=None):
    """Gate: captions clear the furniture the apps draw over the video.

    Bottom 20% and right 15%, per section 8. These are style settings rather
    than per-video ones, so this checks the committed style: if it is wrong it
    is wrong for every video, and finding that out one video at a time is the
    expensive way.
    """
    if look is None:
        sys.path.insert(0, str(HERE))
        import style as style_mod
        look = style_mod.current()
    caps = look.get("captions") or {}
    bottom = float(caps.get("margin_bottom_pct", 0) or 0)
    side = float(caps.get("side_margin_pct", 0) or 0)
    align = str(caps.get("align", "bottom")).lower()

    # The margin is measured from whichever edge the caption anchors against,
    # so it only answers the bottom-20% question for a caption that sits at the
    # bottom. A top or centred caption is nowhere near the buttons, and
    # checking its margin against this rule would refuse Style B for clearing
    # the wrong edge.
    if align == "bottom":
        findings = [("ok" if bottom >= SAFE_BOTTOM_PCT else "fail",
                     "captions clear the bottom %g%%" % SAFE_BOTTOM_PCT,
                     "margin_bottom_pct is %g — the like and comment buttons and "
                     "the title sit over that text" % bottom
                     if bottom < SAFE_BOTTOM_PCT else "margin_bottom_pct %g" % bottom)]
    else:
        findings = [("ok", "captions clear the bottom %g%%" % SAFE_BOTTOM_PCT,
                     "anchored %s, so the bottom of the frame is empty" % align)]
    findings.append((
        "ok" if side >= SAFE_SIDE_PCT else "fail",
        "captions clear the right %g%%" % SAFE_SIDE_PCT,
        "side_margin_pct is %g — the action rail sits over that text" % side
        if side < SAFE_SIDE_PCT else "side_margin_pct %g" % side))
    return not any(l == "fail" for l, _, _ in findings), findings


# ----------------------------------------------------------------- the handoff

def _slug(text, limit=48):
    out = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return (out[:limit].rstrip("-")) or "short"


def check_title(title):
    """Section 7 and 8: short, and not a promise the video does not keep."""
    title = str(title or "").strip()
    problems = []
    if not title:
        problems.append("a title is required")
    if len(title) > TITLE_MAX:
        problems.append("%d characters, the limit is %d" % (len(title), TITLE_MAX))
    return title, problems


def check_description(text, required_credits=()):
    """One hook line, the attribution the licence obliges, 3-5 hashtags."""
    text = str(text or "")
    tags = re.findall(r"(?<!\w)#\w+", text)
    problems = []
    if not text.strip():
        problems.append("a description is required")
    if not MIN_HASHTAGS <= len(tags) <= MAX_HASHTAGS:
        problems.append("%d hashtags, the spec asks for %d-%d"
                        % (len(tags), MIN_HASHTAGS, MAX_HASHTAGS))
    for credit in required_credits:
        if credit and credit.strip() and credit.strip() not in text:
            problems.append("the attribution %r is not in the description"
                            % credit.strip()[:60])
    return tags, problems


def handoff(video, title, description, notes, dest=None, style="A", scored=None):
    """Put one finished Short where a human will find it. Uploads nothing.

    Returns the folder. Refuses rather than writing a folder that would fail
    the checks a reviewer is about to do by hand.
    """
    video = Path(video)
    if not video.exists():
        raise ReviewError("no video at %s" % video)
    title, bad_title = check_title(title)
    credits = []
    rights = video.with_suffix(".rights.json")
    if rights.exists():
        try:
            receipt = json.loads(rights.read_text(encoding="utf-8"))
            credits = [c for c in (receipt.get("credits_due") or []) if c]
        except (json.JSONDecodeError, OSError):
            credits = []
    _, bad_desc = check_description(description, credits)
    problems = bad_title + bad_desc
    if problems:
        raise ReviewError("not handing this over: " + "; ".join(problems))
    if str(style).upper() not in ("A", "B"):
        raise ReviewError('style must be "A" (curiosity) or "B" (story caption)')

    folder = Path(dest or REVIEW) / _slug(title)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(video, folder / "final.mp4")
    (folder / "title.txt").write_text(title + "\n", encoding="utf-8")
    (folder / "description.txt").write_text(str(description).rstrip() + "\n",
                                            encoding="utf-8")
    body = ["style: %s" % str(style).upper()]
    if scored:
        body.append("score: mean %s (hook %s, payoff %s, rewatch %s)" % (
            scored.get("mean"), scored["scores"]["hook"],
            scored["scores"]["payoff"], scored["scores"]["rewatch"]))
    body.append(str(notes or "").rstrip() or "nothing uncertain flagged")
    body.append("")
    body.append("A human approves this before it is uploaded. Nothing here "
                "posts, schedules or publishes on its own.")
    (folder / "notes.txt").write_text("\n".join(body) + "\n", encoding="utf-8")
    return folder


def pending(dest=None):
    """Every Short waiting for a human, newest first."""
    root = Path(dest or REVIEW)
    if not root.exists():
        return []
    out = []
    for folder in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not folder.is_dir():
            continue
        have = {n: (folder / n).exists()
                for n in ("final.mp4", "title.txt", "description.txt", "notes.txt")}
        title = ""
        if have["title.txt"]:
            title = (folder / "title.txt").read_text(encoding="utf-8").strip()
        out.append({"folder": str(folder), "title": title,
                    "complete": all(have.values()),
                    "missing": sorted(n for n, ok in have.items() if not ok)})
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(prog="review.py",
                                     description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_log = sub.add_parser("log", help="record where a clip came from")
    p_log.add_argument("clip")
    p_log.add_argument("--url", required=True)
    p_log.add_argument("--creator", required=True)
    p_log.add_argument("--license", dest="licence", required=True)
    p_log.add_argument("--attribution", required=True)
    p_log.add_argument("--source", default="",
                       help="the film or file this clip was cut out of — clip "
                            "names repeat between videos, sources do not")
    p_log.add_argument("--release", default="", help="where the signed release is kept")
    p_log.add_argument("--notes", default="")
    p_log.add_argument("--checked", default="", help="default: today")

    p_src = sub.add_parser("sources", help="check a plan's clips against sources.csv")
    p_src.add_argument("plan")

    p_sc = sub.add_parser("score", help="is this clip worth editing?")
    p_sc.add_argument("--hook", type=float, required=True)
    p_sc.add_argument("--payoff", type=float, required=True)
    p_sc.add_argument("--rewatch", type=float, required=True)

    sub.add_parser("safe", help="do the committed caption margins clear the UI?")

    p_h = sub.add_parser("handoff", help="put a finished Short in review/")
    p_h.add_argument("video")
    p_h.add_argument("--title", required=True)
    p_h.add_argument("--description-file", required=True)
    p_h.add_argument("--notes", default="")
    p_h.add_argument("--style", default="A", choices=["A", "B", "a", "b"])
    p_h.add_argument("--dest", default="")

    sub.add_parser("pending", help="what is waiting for a human")

    args = parser.parse_args(argv)
    try:
        if args.command == "log":
            row = log_source(args.clip, args.url, args.creator, args.licence,
                             args.attribution, args.source, args.release,
                             args.notes, args.checked or None)
            print("logged %s%s — %s, %s, checked %s"
                  % (row["clip"], " from " + row["source"] if row["source"] else "",
                     row["creator"], row["license"], row["checked"]))
            return 0
        if args.command == "sources":
            ok, findings = sources_report(args.plan)
            for level, headline, detail in findings:
                print("%s %s" % ({"ok": "  ok  ", "warn": " warn ",
                                  "fail": " FAIL "}[level], headline))
                if detail:
                    print("         %s" % detail)
            return 0 if ok else 1
        if args.command == "score":
            got = score(args.hook, args.payoff, args.rewatch)
            print("%s — %s (%s)" % ("EDIT IT" if got["edit"] else "SKIP IT",
                                    got["why"], got["scores"]))
            return 0 if got["edit"] else 2
        if args.command == "safe":
            ok, findings = safe_zone_report()
            for level, headline, detail in findings:
                print("%s %s" % ({"ok": "  ok  ", "fail": " FAIL "}[level], headline))
                if detail:
                    print("         %s" % detail)
            return 0 if ok else 1
        if args.command == "handoff":
            folder = handoff(args.video, args.title,
                             Path(args.description_file).read_text(encoding="utf-8"),
                             args.notes, args.dest or None, args.style)
            print("%s — waiting for a human. Nothing was uploaded." % folder)
            return 0
        rows = pending()
        if not rows:
            print("nothing waiting in review/")
            return 0
        for row in rows:
            print("%-52s %s" % (row["title"][:52] or Path(row["folder"]).name,
                                "ready" if row["complete"]
                                else "INCOMPLETE: missing " + ", ".join(row["missing"])))
        return 0
    except (ReviewError, OSError, json.JSONDecodeError) as exc:
        print("review.py: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
