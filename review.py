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
sys.path.insert(0, str(HERE))
import spec                                   # noqa: E402  the source of truth
SOURCES = HERE / "sources.csv"
REVIEW = HERE / "review"

# The spec's own wording, kept verbatim so a reader can check it against the
# brief rather than against my paraphrase.
# `source` is what the clip was cut out of, and it is not decoration: every
# render writes clip01.mp4, clip02.mp4 ... so a clip's NAME is not an identity.
# Keyed on the name alone, logging a new video's clip01.mp4 would silently
# replace the last video's, and sources.csv would only ever hold the most
# recent Short -- an audit log that quietly forgets is worse than none.
COLUMNS = ["clip", "source", "url", "creator", "license", "footage_type",
           "proof", "checked", "attribution", "release", "notes"]

# Section 1: a licence you cannot produce is not a licence. A marketplace
# receipt, or the screenshot of the DM or email the creator sent. The path is
# recorded rather than the file so the log stays small, and the gate checks the
# file is actually there -- a path to nothing is the same as no proof. The list
# of which licences need one is in spec.json.

# Section 1: these are refused unless a signed release exists, and the refusal
# is recorded rather than assumed.
SENSITIVE = ("minor", "minors", "child", "children", "kid", "kids", "baby",
             "medical", "illness", "ill", "sick", "injury", "injured", "hurt",
             "accident", "hospital", "funeral", "vulnerable", "distress",
             "distressed", "crying", "mocked", "humiliated", "prank")

MIN_SCORE = 7.0          # a clip's mean, below which it is not worth editing
FLOOR_ANY = 5            # and no single axis may sit under this

# Everything below is the spec's, read from spec.json rather than restated
# here. A second copy of a number is a second thing to forget to change, and
# the whole point of spec.json is that there is one place to look.
_V = spec.get("video")
SAFE_BOTTOM_PX = _V["height"] - spec.get("captions.safe_zone.y")[1]
SAFE_RIGHT_PX = _V["width"] - spec.get("captions.safe_zone.x")[1]
SAFE_TOP_PX = spec.get("captions.safe_zone.y")[0]
SAFE_LEFT_PX = spec.get("captions.safe_zone.x")[0]

TITLE_MAX = spec.get("metadata.title_max_chars")
MIN_HASHTAGS, MAX_HASHTAGS = spec.get("metadata.hashtags")
REQUIRED_FILES = tuple(spec.get("required_files"))

LICENCES_NEEDING_PROOF = tuple(spec.get("sources.licenses_needing_proof"))
LICENCES_ALLOWED = tuple(spec.get("sources.licenses_allowed"))
BANNED_KEYWORDS = tuple(spec.get("sources.banned_keywords"))
FOOTAGE_TYPE = spec.get("sources.footage_type")
INBOX = HERE / spec.get("sources.inbox")

# The safe zone is derived above from spec.json's x/y box. It is kept in pixels
# because that is what the players actually draw over -- 420px of title and
# buttons at the bottom, 180px of action rail on the right. Percentages are how
# the style stores it, so the check converts before comparing: 21% of 1920 is
# 403px, which looks like a pass and is seventeen pixels inside the like button.


class ReviewError(Exception):
    pass


def _today():
    return date.today().isoformat()


def _flatten(text):
    """Lower-case with the separators taken out, so cc-by == CC BY == cc_by."""
    return re.sub(r"[\s._-]+", "", str(text or "").lower())


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
               notes="", checked=None, path=None, proof="", footage_type=None):
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
           "footage_type": str(footage_type or FOOTAGE_TYPE).strip(),
           "proof": str(proof or "").strip(),
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
        path_text = str(beat.get("clip") or "")
        clip = Path(path_text)
        book = clip.parent / "origins.json"
        if not book.exists():
            continue
        try:
            data = json.loads(book.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        entry = data.get(clip.name) or {}
        if entry.get("source"):
            found[path_text] = Path(str(entry["source"])).name
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

    # Distinct FILES, not distinct names: a plan can draw on two folders cut
    # from the same film and both hold a clip01.mp4. Deduping by name reported
    # twelve clips as six. The row that covers each is still found by name and
    # source, because those two files do share a provenance.
    clips = []
    for beat in beats:
        path_text = str(beat.get("clip") or "")
        if path_text and path_text not in clips:
            clips.append(path_text)

    origins = _origins(plan)
    missing, undated, unsigned, unproven = [], [], [], []
    wrong_type, not_allowed, banned = [], [], []
    for clip_path in clips:
        name = Path(clip_path).name
        row = source_for(name, path, origins.get(clip_path))
        if not row:
            missing.append(name)
            continue
        if not row.get("checked"):
            undated.append(name)
        blob = " ".join((row.get("notes", ""), row.get("creator", ""))).lower()
        if any(word in blob for word in SENSITIVE) and not row.get("release"):
            unsigned.append(name)
        licence = str(row.get("license", "")).lower()
        # "CC BY 4.0", "cc-by" and "cc_by" are the same licence written three
        # ways, and only one of them matches the spec's spelling. Flattening
        # the separators is the difference between a rule and a spelling test.
        flat = _flatten(licence)
        # The spec names which licences the channel may use at all. A licence
        # that is not on that list is not a judgement call -- it is footage we
        # have not agreed terms for.
        if not any(_flatten(word) in flat for word in LICENCES_ALLOWED):
            not_allowed.append("%s (%s)" % (name, row.get("license") or "blank"))
        kind = str(row.get("footage_type") or "").strip().lower()
        if kind != FOOTAGE_TYPE:
            wrong_type.append("%s (%s)" % (name, kind or "not recorded"))
        # Banned words are checked across everything the row says about the
        # clip, not just its licence: "Sintel" turns up in a URL or a creator
        # long before it turns up in a licence name.
        haystack = " ".join(str(row.get(c, "")) for c in COLUMNS).lower()
        hits = sorted({b for b in BANNED_KEYWORDS if b in haystack})
        if hits:
            banned.append("%s (%s)" % (name, ", ".join(hits)))
        if any(_flatten(word) in flat for word in LICENCES_NEEDING_PROOF):
            kept = str(row.get("proof") or "").strip()
            if not kept or not (HERE / kept).exists() and not Path(kept).exists():
                unproven.append("%s (%s)" % (name, kept or "no file recorded"))

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
    if wrong_type:
        findings.append((
            "fail", "every clip is %s" % FOOTAGE_TYPE,
            "%s. The spec takes live action only: no animation, no 3D, no "
            "AI-generated people." % "; ".join(wrong_type[:3])))
    if not_allowed:
        findings.append((
            "fail", "every licence is one the channel may use",
            "%s. Allowed: %s." % ("; ".join(not_allowed[:3]),
                                  ", ".join(LICENCES_ALLOWED))))
    if banned:
        findings.append((
            "fail", "no clip is from a banned source",
            "%s. These are refused outright by the spec, whatever else the "
            "row says." % "; ".join(banned[:3])))
    if unproven:
        findings.append((
            "fail", "a licensed clip can show its licence",
            "%s. A marketplace receipt or the creator's written permission has "
            "to be a file on disk: a licence you cannot produce is the same as "
            "none when a claim arrives." % "; ".join(unproven[:3])))
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


# --------------------------------------------------------------- the narration

# Section 3. The word count and the reading rate are the same constraint said
# twice -- 60-110 words at 160-175 a minute is 20-40 seconds -- so the check
# reports the seconds it implies rather than making someone divide.
SCRIPT_WORDS = tuple(spec.get("voiceover.words"))
SENTENCE_WORDS = tuple(spec.get("voiceover.sentence_words"))
WORDS_PER_MINUTE = tuple(spec.get("voiceover.words_per_minute"))
HOOK_OPTIONS = spec.get("voiceover.hook_options")


def _sentences(text):
    parts = re.split(r"(?<=[.!?…])\s+", str(text or "").strip())
    return [s.strip() for s in parts if s.strip()]


def script_report(text, hooks=()):
    """Gate: is this narration the shape the channel commits to?

    Shape only. Whether the hook actually earns the swipe is a judgement and
    stays one -- this refuses the things that are not judgements: a script too
    long to fit a Short, sentences too long to read aloud in a breath, a first
    line that closes rather than opens, and a hook chosen without alternatives
    to reject.
    """
    words = str(text or "").split()
    sentences = _sentences(text)
    findings = []

    low, high = SCRIPT_WORDS
    seconds = (len(words) / (sum(WORDS_PER_MINUTE) / 2.0)) * 60.0
    findings.append((
        "ok" if low <= len(words) <= high else "fail",
        "the script fits a Short",
        "%d words, about %.0fs — the band is %d-%d (%.0f-%.0fs)"
        % (len(words), seconds, low, high,
           low / (WORDS_PER_MINUTE[1] / 60.0), high / (WORDS_PER_MINUTE[0] / 60.0))))

    slow, shigh = SENTENCE_WORDS
    longest = max((len(s.split()) for s in sentences), default=0)
    over = [s for s in sentences if not slow <= len(s.split()) <= shigh]
    findings.append((
        "ok" if not over else "fail",
        "every line is short enough to land",
        "%d of %d outside %d-%d words, longest %d: %r"
        % (len(over), len(sentences), slow, shigh, longest, over[0][:60])
        if over else "%d lines, longest %d words" % (len(sentences), longest)))

    first = sentences[0] if sentences else ""
    # A hook that answers itself has nothing to keep watching for. This cannot
    # tell a good question from a bad one; it can tell that the line does not
    # close the loop it is supposed to open.
    opens = bool(first) and (
        first.rstrip().endswith("?")
        or any(w in first.lower().split() for w in
               ("thought", "didn't", "never", "about", "nobody", "everyone",
                "almost", "until", "seconds", "wrong", "last")))
    findings.append((
        "ok" if opens else "warn", "the first line opens a question",
        "%r does not obviously leave anything unanswered" % first[:60]
        if not opens else first[:70]))

    findings.append((
        "ok" if len(hooks) >= HOOK_OPTIONS else "fail",
        "the hook was chosen, not settled for",
        "%d option%s written, the rule is %d — the first line you think of is "
        "rarely the strongest" % (len(hooks), "" if len(hooks) == 1 else "s",
                                  HOOK_OPTIONS)
        if len(hooks) < HOOK_OPTIONS else "%d options" % len(hooks)))
    return not any(l == "fail" for l, _, _ in findings), findings


# ----------------------------------------------------------------- safe zones

def safe_zone_report(look=None):
    """Gate: captions clear the furniture the apps draw over the video.

    These are style settings rather than per-video ones, so this checks the
    committed style: if it is wrong it is wrong for every video, and finding
    that out one upload at a time is the expensive way.
    """
    if look is None:
        sys.path.insert(0, str(HERE))
        import style as style_mod
        look = style_mod.current()
    caps = look.get("captions") or {}
    fmt = look.get("format") or {}
    width = float(fmt.get("width", 1080) or 1080)
    height = float(fmt.get("height", 1920) or 1920)
    bottom = float(caps.get("margin_bottom_pct", 0) or 0) * height / 100.0
    side = float(caps.get("side_margin_pct", 0) or 0) * width / 100.0
    align = str(caps.get("align", "bottom")).lower()
    centre_pct = float(caps.get("center_y_pct", 0) or 0)
    # An explicit centre overrides the anchor, so it is the thing to judge:
    # half the block sits either side of it, and two lines of the committed
    # size is what has to fit between the edges.
    if centre_pct:
        line_px = height * float(caps.get("size_pct", 4.0) or 4.0) / 100.0
        half = line_px * float(caps.get("max_lines", 2) or 2) / 2.0
        centre_y = height * centre_pct / 100.0
        bottom = height - (centre_y + half)
        top = centre_y - half
        findings = [
            ("ok" if bottom >= SAFE_BOTTOM_PX else "fail",
             "captions clear the bottom %dpx" % SAFE_BOTTOM_PX,
             "the block reaches %dpx from the bottom" % round(bottom)
             if bottom < SAFE_BOTTOM_PX else "%dpx clear" % round(bottom)),
            ("ok" if top >= SAFE_TOP_PX else "fail",
             "captions clear the top %dpx" % SAFE_TOP_PX,
             "the block reaches %dpx from the top" % round(top)
             if top < SAFE_TOP_PX else "%dpx clear" % round(top)),
            ("ok" if side >= SAFE_RIGHT_PX else "fail",
             "captions clear the right %dpx" % SAFE_RIGHT_PX,
             "side margin is %dpx — the action rail sits over that text"
             % round(side) if side < SAFE_RIGHT_PX else "%dpx clear" % round(side)),
        ]
        return not any(l == "fail" for l, _, _ in findings), findings

    # The margin is measured from whichever edge the caption anchors against,
    # so it only answers the bottom-20% question for a caption that sits at the
    # bottom. A top or centred caption is nowhere near the buttons, and
    # checking its margin against this rule would refuse Style B for clearing
    # the wrong edge.
    if align == "bottom":
        findings = [("ok" if bottom >= SAFE_BOTTOM_PX else "fail",
                     "captions clear the bottom %dpx" % SAFE_BOTTOM_PX,
                     "the margin is %dpx — the title and the buttons sit over "
                     "that text" % round(bottom) if bottom < SAFE_BOTTOM_PX
                     else "%dpx clear" % round(bottom))]
    else:
        findings = [("ok", "captions clear the bottom %dpx" % SAFE_BOTTOM_PX,
                     "anchored %s, so the bottom of the frame is empty" % align)]
    findings.append((
        "ok" if side >= SAFE_RIGHT_PX else "fail",
        "captions clear the right %dpx" % SAFE_RIGHT_PX,
        "side margin is %dpx — the action rail sits over that text" % round(side)
        if side < SAFE_RIGHT_PX else "%dpx clear" % round(side)))
    return not any(l == "fail" for l, _, _ in findings), findings


# --------------------------------------------------------------- footage intake

def intake(folder=None):
    """What is in the inbox, and which of it may actually be used.

    While the licensing sites are unreachable the owner drops clips here by
    hand, each with the file that proves the licence beside it. A clip without
    proof is not a clip we have -- so this reports it rather than quietly
    building from it.
    """
    root = Path(folder or INBOX)
    out = {"folder": str(root), "usable": [], "unusable": []}
    if not root.exists():
        return out
    video = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
    for item in sorted(root.iterdir()):
        if not item.is_file() or item.suffix.lower() not in video:
            continue
        # The proof sits beside the clip under the same stem: clip.mp4 and
        # clip.pdf / clip.png / clip.txt. Same name, so nobody has to maintain
        # a mapping that can go stale.
        proof = [q for q in root.iterdir()
                 if q.is_file() and q.stem == item.stem and q != item]
        row = source_for(item.name)
        why = []
        if not proof:
            why.append("no licence proof beside it")
        if not row:
            why.append("no row in sources.csv")
        elif not any(_flatten(w) in _flatten(row.get("license", ""))
                     for w in LICENCES_ALLOWED):
            why.append("licence %r is not one the spec allows"
                       % row.get("license"))
        elif str(row.get("footage_type") or "").lower() != FOOTAGE_TYPE:
            why.append("footage_type is %r, not %s"
                       % (row.get("footage_type"), FOOTAGE_TYPE))
        (out["usable"] if not why else out["unusable"]).append(
            {"clip": str(item), "proof": [str(q) for q in proof], "why": why})
    return out


# ------------------------------------------------------------------- captions

def captions_json(plan_path, subtitles_path, out_path):
    """The caption track as data, beside the video.

    The .ass file is what ffmpeg burns in; this is the same timing in a form
    anything else can read -- a QC check measuring drift against the voice, or
    a human diffing what was said against what was shown. Written from the
    .ass rather than from the plan, because the .ass is what actually reached
    the picture.
    """
    import re as _re
    rows = []
    for line in Path(subtitles_path).read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(",", 9)
        if len(fields) < 10:
            continue
        text = _re.sub(r"\{[^}]*\}", "", fields[9]).replace("\\N", " ").strip()
        if not text:
            continue
        rows.append({"start": _ass_seconds(fields[1]),
                     "end": _ass_seconds(fields[2]),
                     "text": text,
                     "words": len(text.split())})
    rows.sort(key=lambda r: r["start"])
    payload = {"source": str(Path(plan_path).name), "groups": rows,
               "count": len(rows),
               "first_at": rows[0]["start"] if rows else None}
    Path(out_path).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                              encoding="utf-8")
    return payload


def _ass_seconds(stamp):
    hours, minutes, seconds = str(stamp).strip().split(":")
    return round(int(hours) * 3600 + int(minutes) * 60 + float(seconds), 3)


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


def handoff(video, title, description, notes, dest=None, style="A", scored=None,
            script="", hooks=(), captions=None, sources_csv=None, qc=True):
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
    # A narrated Short hands over its narration. Checking it here rather than
    # only at write time means a script that drifted after the gate ran cannot
    # reach a reviewer unnoticed.
    if script:
        ok_script, script_findings = script_report(script, hooks)
        if not ok_script:
            problems += [d for level, _, d in script_findings if level == "fail"]
    if problems:
        raise ReviewError("not handing this over: " + "; ".join(problems))
    if str(style).upper() not in ("A", "B"):
        raise ReviewError('style must be "A" (curiosity) or "B" (story caption)')

    # Assembled in a staging folder and measured there. A Short that fails QC
    # must never appear in review/ at all -- a reviewer who finds a folder
    # there is entitled to assume it passed, and a half-finished one that
    # merely carries a bad report is the same mistake as no gate.
    root = Path(dest or REVIEW)
    folder = root / (_slug(title) + ".staging")
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
    if script:
        # The hooks that were rejected are part of the record: the next script
        # is written by someone reading this one, and knowing which openings
        # were tried is worth more than knowing only which one won.
        page = [script.strip(), ""]
        if hooks:
            page.append("Hook options considered:")
            page += ["  %d. %s" % (i, h) for i, h in enumerate(hooks, 1)]
        (folder / "script.txt").write_text("\n".join(page).rstrip() + "\n",
                                           encoding="utf-8")
    if captions and Path(captions).exists():
        shutil.copy2(captions, folder / "captions.json")
    # The Short carries the provenance of its own clips, not the channel's
    # whole history: a reviewer answering a claim about THIS video should not
    # have to find the rows among every clip ever logged.
    rows = _rows(sources_csv) if sources_csv else []
    if rows:
        _write(rows, folder / "sources.csv")
    for row in rows:
        kept = str(row.get("proof") or "").strip()
        if kept and Path(kept).exists():
            shutil.copy2(kept, folder / Path(kept).name)

    if qc:
        import qc as qc_mod                    # imported here: qc imports this
        report = qc_mod.run(folder, folder / "qc_report.json")
        if not report["ship"]:
            bad = [r["check"] for r in report["checks"] if r["status"] != "pass"]
            raise ReviewError(
                "QC failed, so this is not in review/: %s. The staged folder and "
                "its qc_report.json are at %s — fix those and hand off again."
                % (", ".join(bad[:6]), folder))
    final = root / _slug(title)
    if final.exists():
        shutil.rmtree(final)
    folder.rename(final)
    return final


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
    p_log.add_argument("--footage-type", default=None,
                       help="spec requires %s" % "live_action")
    p_log.add_argument("--proof", default="",
                       help="path to the receipt, DM screenshot or email that "
                            "proves the licence")
    p_log.add_argument("--source", default="",
                       help="the film or file this clip was cut out of — clip "
                            "names repeat between videos, sources do not")
    p_log.add_argument("--release", default="", help="where the signed release is kept")
    p_log.add_argument("--notes", default="")
    p_log.add_argument("--checked", default="", help="default: today")

    p_src = sub.add_parser("sources", help="check a plan's clips against sources.csv")
    p_src.add_argument("plan")

    p_scr = sub.add_parser("script", help="is the narration the right shape?")
    p_scr.add_argument("script_file")
    p_scr.add_argument("--hook", action="append", default=[])

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
    p_h.add_argument("--script-file", default="",
                     help="the narration as spoken — written out as script.txt")
    p_h.add_argument("--hook", action="append", default=[],
                     help="a hook option that was considered; pass it %d times"
                          % HOOK_OPTIONS)
    p_h.add_argument("--style", default="A", choices=["A", "B", "a", "b"])
    p_h.add_argument("--captions", default="", help="captions.json for this Short")
    p_h.add_argument("--sources", default="",
                     help="the sources.csv rows covering this Short's clips")
    p_h.add_argument("--no-qc", action="store_true",
                     help="assemble without measuring — staging only, never ships")
    p_h.add_argument("--dest", default="")

    sub.add_parser("pending", help="what is waiting for a human")

    args = parser.parse_args(argv)
    try:
        if args.command == "log":
            row = log_source(args.clip, args.url, args.creator, args.licence,
                             args.attribution, args.source, args.release,
                             args.notes, args.checked or None, proof=args.proof,
                             footage_type=args.footage_type)
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
        if args.command == "script":
            ok, findings = script_report(
                Path(args.script_file).read_text(encoding="utf-8"), args.hook)
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
                             args.notes, args.dest or None, args.style,
                             script=(Path(args.script_file).read_text(encoding="utf-8")
                                     if args.script_file else ""),
                             hooks=args.hook,
                             captions=args.captions or None,
                             sources_csv=args.sources or None,
                             qc=not args.no_qc)
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
