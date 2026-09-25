#!/usr/bin/env python3
"""Assemble a finished video from clips, captions and a voice track.

This is the part that actually makes the file: it trims each source clip to
the beat it covers, reframes it to vertical, burns in captions, lays the
voiceover over the top, concatenates everything and encodes an MP4 — then
probes the result and refuses to report success unless it is really there.

On footage
----------
Every asset must carry a ``license`` string saying where it came from and why
you may use it. Renders fail without one. Acceptable sources are your own
recordings, stock you have licensed, and public-domain or
permissively-licensed archives.

Clips ripped from someone else's YouTube, TikTok or Instagram are not
acceptable, and this is not squeamishness: it is copyright infringement, it
breaches those platforms' terms, and compiling other people's clips with
little added is the exact thing YouTube's Inauthentic Content policy
demonetizes. A channel built that way can work for months and then lose
everything at once. If an asset's license names a platform URL you must also
set ``"rights_confirmed": true`` to assert in writing that you hold permission.

Usage
-----
    python3 render.py plan script.md --clips assets/ -o render.json
    python3 render.py build render.json --agent rendering
    python3 render.py check out/video.mp4 --expect 44
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

__all__ = ["build", "load_plan", "probe", "RenderError", "ffmpeg_bin", "the_style"]


def the_style():
    """The one committed editing style. Every visual decision comes from here."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import style as style_mod
    return style_mod.current()

HERE = Path(__file__).resolve().parent
DEFAULT_W, DEFAULT_H, DEFAULT_FPS = 1080, 1920, 30
PLATFORMS = ("youtube.com", "youtu.be", "tiktok.com", "instagram.com",
             "facebook.com", "twitter.com", "x.com")


class RenderError(RuntimeError):
    """The video could not be built."""


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------
def ffmpeg_bin():
    for candidate in (os.environ.get("FFMPEG"), shutil.which("ffmpeg")):
        if candidate and Path(candidate).exists():
            return candidate
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    raise RenderError(
        "ffmpeg not found. Install it (macOS: brew install ffmpeg; Debian: "
        "apt install ffmpeg), or set FFMPEG to its path, or pip install "
        "imageio-ffmpeg for a bundled build."
    )


def ffprobe_bin():
    for candidate in (os.environ.get("FFPROBE"), shutil.which("ffprobe")):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _run(args, what):
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-6:])
        raise RenderError("%s failed:\n%s" % (what, tail))
    return proc


def probe(path):
    """Duration in seconds plus which streams exist. Works without ffprobe."""
    path = Path(path)
    if not path.exists():
        raise RenderError("no such file: %s" % path)
    if path.stat().st_size == 0:
        raise RenderError("%s is empty" % path)

    probe_exe = ffprobe_bin()
    if probe_exe:
        proc = subprocess.run(
            [probe_exe, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True)
        if proc.returncode == 0:
            data = json.loads(proc.stdout or "{}")
            kinds = {s.get("codec_type") for s in data.get("streams", [])}
            return {
                "duration": float(data.get("format", {}).get("duration", 0) or 0),
                "video": "video" in kinds, "audio": "audio" in kinds,
                "size": path.stat().st_size,
            }

    # no ffprobe: ffmpeg prints the same facts to stderr
    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", str(path)],
                          capture_output=True, text=True)
    text = proc.stderr or ""
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", text)
    duration = 0.0
    if match:
        h, m, s = match.groups()
        duration = int(h) * 3600 + int(m) * 60 + float(s)
    return {
        "duration": duration,
        "video": bool(re.search(r"Stream #\d+:\d+.*: Video:", text)),
        "audio": bool(re.search(r"Stream #\d+:\d+.*: Audio:", text)),
        "size": path.stat().st_size,
    }


# --------------------------------------------------------------------------
# plan
# --------------------------------------------------------------------------
def dimensions(path):
    """Width and height of a video file, via ffprobe or ffmpeg's own output."""
    path = Path(path)
    probe_exe = ffprobe_bin()
    if probe_exe:
        proc = subprocess.run(
            [probe_exe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", str(path)],
            capture_output=True, text=True)
        if proc.returncode == 0:
            streams = (json.loads(proc.stdout or "{}").get("streams") or [{}])[0]
            if streams.get("width"):
                return {"width": int(streams["width"]), "height": int(streams["height"])}
    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", str(path)],
                          capture_output=True, text=True)
    match = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})", proc.stderr or "")
    if not match:
        raise RenderError("could not read the dimensions of %s" % path)
    return {"width": int(match.group(1)), "height": int(match.group(2))}


def load_plan(path):
    path = Path(path)
    if not path.exists():
        raise RenderError("no such plan: %s" % path)
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RenderError("%s is not valid JSON (%s)" % (path, exc)) from exc
    return validate_plan(plan, path.parent)


def _check_license(asset, label):
    licence = (asset.get("license") or "").strip()
    if not licence:
        raise RenderError(
            "%s has no \"license\". Every asset must record where it came from "
            "and why you may use it — own recording, licensed stock, or a "
            "public-domain source. Renders do not proceed without it." % label
        )
    lowered = licence.lower()
    hit = next((p for p in PLATFORMS if p in lowered), None)
    if hit and not asset.get("rights_confirmed"):
        raise RenderError(
            "%s cites %s as its source. Re-uploading someone else's clip "
            "infringes copyright, breaches that platform's terms, and is what "
            'YouTube\'s Inauthentic Content policy targets. If you genuinely '
            'hold written permission, set "rights_confirmed": true on this '
            "asset to say so." % (label, hit)
        )
    return licence


# Licences that oblige you to credit the source. Public domain does not, but
# recording it anyway is what gives you an answer if a claim ever arrives.
ATTRIBUTION_REQUIRED = ("cc by", "cc-by", "creativecommons.org/licenses",
                        "attribution", "pexels", "pixabay")


# Where a licence lives, so a description can point at it and a dispute can
# cite it. Matched against the recorded licence text, longest marker first.
LICENCE_URLS = (
    ("cc by-sa 4", "https://creativecommons.org/licenses/by-sa/4.0/"),
    ("cc by 4", "https://creativecommons.org/licenses/by/4.0/"),
    ("cc by 3", "https://creativecommons.org/licenses/by/3.0/"),
    ("cc by-sa", "https://creativecommons.org/licenses/by-sa/4.0/"),
    ("cc by", "https://creativecommons.org/licenses/by/4.0/"),
    ("cc0", "https://creativecommons.org/publicdomain/zero/1.0/"),
    ("public domain", "https://creativecommons.org/publicdomain/mark/1.0/"),
)


def licence_url(text):
    """The canonical URL for a recorded licence, or "" if it has no single one."""
    low = (text or "").lower()
    for marker, url in LICENCE_URLS:
        if marker in low:
            return url
    return ""


def hook_line(script_path):
    """The script's `**Hook:**` line, which is what a description opens with."""
    try:
        text = Path(script_path).read_text(encoding="utf-8")
    except OSError:
        return ""
    found = re.search(r"^\s*\*\*Hook:?\*\*\s*(.+?)\s*$", text, re.M | re.I)
    return found.group(1).strip() if found else ""


def credits_due(plan):
    """The credit lines this plan obliges you to publish, in beat order.

    A licence that requires attribution is not satisfied by having the text in
    a JSON file next to the video. It has to be where a viewer can read it,
    which on every platform means the description. This is what has to go
    there, and `monetize_report` checks it actually did.
    """
    due, seen = [], set()
    for beat in plan.get("beats") or []:
        licence = str(beat.get("license") or "")
        if not any(m in licence.lower() for m in ATTRIBUTION_REQUIRED):
            continue
        line = str(beat.get("attribution") or "").strip() or licence.strip()
        url = licence_url(licence)
        if url and url not in line:
            line = "%s — %s" % (line, url)
        if line and line not in seen:
            seen.add(line)
            due.append(line)
    return due


def description(plan_path, hook="", extra=""):
    """The video description, with every credit the licences require in it.

    Generated rather than written by hand, because the one obligation a CC-BY
    licence puts on you is the one easiest to forget at upload time -- and a
    missing credit is not a policy risk, it is infringement.
    """
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    parts = []
    if hook:
        parts.append(hook.strip())
    due = credits_due(plan)
    if due:
        parts.append("\n".join(["Footage:"] + ["  %s" % line for line in due]))
    audio = plan.get("audio") or {}
    voice = str(audio.get("license") or "").strip()
    if voice:
        parts.append("Narration and edit: %s." % voice)
    music = str((plan.get("music") or {}).get("license") or "").strip()
    parts.append("Music: %s" % (music or "original, synthesised for this video."))
    if extra:
        parts.append(extra.strip())
    return "\n\n".join(p for p in parts if p).strip() + "\n"


def rights_receipt(plan_path):
    """Everything needed to answer a Content ID claim, in one file.

    A wrongful claim on openly licensed footage is answered by saying exactly
    which seconds of which source were used and under what licence. Rebuilding
    that months later from a render plan is work; writing it at render time is
    not. Shot in-points are included because "we used 606.5s to 609.1s of
    Sintel" is a specific, checkable answer and "we used Sintel" is not.
    """
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    beats = plan.get("beats") or []
    shots, origins = [], {}
    for beat in beats:                       # origins.json sits beside the clips
        folder = Path(str(beat.get("clip") or "")).parent
        book = folder / "origins.json"
        if book.exists() and str(folder) not in origins:
            try:
                origins[str(folder)] = json.loads(book.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                origins[str(folder)] = {}
    for i, beat in enumerate(beats, 1):
        licence = str(beat.get("license") or "")
        clip = Path(str(beat.get("clip") or ""))
        came = (origins.get(str(clip.parent)) or {}).get(clip.name) or {}
        shot = {
            "beat": i,
            "clip": clip.name,
            "in_seconds": round(float(beat.get("in") or 0.0), 3),
            "duration_seconds": round(float(beat.get("duration") or 0.0), 3),
            "license": licence,
            "license_url": licence_url(licence),
        }
        if came:
            # what a dispute actually needs: which seconds of the original
            shot["source"] = came.get("source")
            shot["source_in_seconds"] = round(
                float(came.get("in_seconds") or 0.0) + float(beat.get("in") or 0.0), 3)
        shots.append(shot)
    audio = plan.get("audio") or {}
    return {
        "output": plan.get("output"),
        "credits_required_in_description": credits_due(plan),
        "narration": {"license": audio.get("license") or "",
                      "synthetic": "kokoro" in str(audio.get("license") or "").lower()
                      or "espeak" in str(audio.get("license") or "").lower()
                      or "piper" in str(audio.get("license") or "").lower()
                      or "elevenlabs" in str(audio.get("license") or "").lower()},
        "music": "original, synthesised for this video — no third-party recording",
        "made_for_kids": False,
        "shots": shots,
        "if_a_claim_arrives": (
            "Dispute it. The footage is used under the licence recorded against "
            "each shot; the licence URL is beside it, the credit the licence "
            "requires is in the description, and the in-points above say exactly "
            "which seconds were used. The narration and the music are original."),
    }


def rights_report(plan_path):
    """What this render's copyright position actually is, before it is uploaded.

    Every item here is a real obligation or a real risk, checked against the
    plan rather than asserted in a README. Returns (ok, findings) where a
    finding is (level, headline, detail) and level is "ok", "warn" or "fail".
    """
    # Read the plan WITHOUT the build-time validation. load_plan raises on the
    # first missing licence, which is right before a render and wrong for a
    # report: the point here is to see everything that is wrong at once rather
    # than fix-and-rerun six times.
    path = Path(plan_path)
    if not path.exists():
        raise RenderError("no such plan: %s" % path)
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RenderError("%s is not valid JSON (%s)" % (path, exc)) from exc
    if not isinstance(plan, dict):
        raise RenderError("%s does not look like a render plan" % path)
    beats = plan.get("beats") or []
    findings = []
    if not beats:
        findings.append(("fail", "the plan has beats to check", "none found"))

    # 1. provenance on every asset
    missing = [i + 1 for i, b in enumerate(beats) if not (b.get("license") or "").strip()]
    findings.append(("fail" if missing else "ok", "every clip records its licence",
                     "beats %s have none" % missing if missing
                     else "%d beat%s" % (len(beats), "" if len(beats) == 1 else "s")))

    # 2. nothing lifted off a platform without written permission
    lifted = []
    for i, b in enumerate(beats):
        low = (b.get("license") or "").lower()
        hit = next((pl for pl in PLATFORMS if pl in low), None)
        if hit and not b.get("rights_confirmed"):
            lifted.append("beat %d (%s)" % (i + 1, hit))
    findings.append(("fail" if lifted else "ok",
                     "no clip is lifted from a platform",
                     ", ".join(lifted) if lifted
                     else "re-uploading someone else's clip breaches their terms "
                          "as well as their copyright"))

    # 3. credit where the licence demands it
    owed, credited = [], []
    for i, b in enumerate(beats):
        low = (b.get("license") or "").lower()
        if any(marker in low for marker in ATTRIBUTION_REQUIRED):
            (credited if (b.get("attribution") or "").strip() else owed).append(i + 1)
    findings.append(("fail" if owed else "ok",
                     "credit is recorded where the licence requires it",
                     "beats %s need an attribution line" % owed if owed
                     else "%d of %d beat%s carry credit" % (len(credited), len(beats),
                                                            "" if len(beats) == 1 else "s")))

    # 4. narration. A montage of other people's footage with no commentary on it
    #    is what the reused-content policy demotes; narration is what makes it a
    #    video essay instead.
    audio = plan.get("audio") or {}
    findings.append(("warn" if not audio.get("path") else "ok",
                     "the video carries its own narration",
                     audio.get("license") or
                     "no voice track: third-party footage with nothing added is "
                     "what YouTube's reused-content policy targets"))

    # 5. the source's own audio never reaches the output -- beats are trimmed
    #    with -an, so any music in the source cannot raise a Content ID claim.
    findings.append(("ok", "source audio is discarded, not re-used",
                     "beats are trimmed with -an; only the narration is muxed"))

    # 6. the honest caveat
    third_party = [b for b in beats
                   if "own recording" not in (b.get("license") or "").lower()]
    if third_party:
        findings.append(("warn", "a Content ID claim is still possible",
                         "a valid licence is a defence, not a shield: widely "
                         "uploaded footage gets matched anyway. Keep the licence "
                         "URL to hand so a dispute takes minutes."))

    ok = not any(level == "fail" for level, _, _ in findings)
    return ok, findings


def _overlap(first, last):
    """How much of the opening line the closing line echoes, 0 to 1."""
    def words(text):
        return {w for w in re.findall(r"[a-z']+", (text or "").lower()) if len(w) > 2}
    opening, closing = words(first), words(last)
    if not opening or not closing:
        return 0.0
    return len(opening & closing) / float(min(len(opening), len(closing)))


def narration_report(plan_path):
    """Is the script varied enough to listen to, or is it one sentence long?

    Every timing check can pass on a script that is still a slog, because what
    makes narration tiring is not its pace. The video this was written for had
    seven of twelve beats opening with "She" and thirty-two distinct words in
    sixty; it cut cleanly, it was fitted to the voice, and it read as one long
    sentence. None of the other gates had anything to say about it.

    Returns (ok, findings) shaped like the others.
    """
    path = Path(plan_path)
    plan = json.loads(path.read_text(encoding="utf-8"))
    import style as style_mod
    look = style_mod._fill_defaults(json.loads(json.dumps(
        plan.get("_style") or the_style())))
    want = look.get("narration") or {}
    lines = [str(b.get("caption") or "").strip() for b in (plan.get("beats") or [])]
    lines = [l for l in lines if l]
    if not lines:
        return False, [("fail", "the plan has lines to check", "none found")]
    findings = []

    # Counted per SENTENCE, not per beat. A beat that continues a sentence
    # starts wherever the clause does -- "and calls him Scales.", "and she just
    # killed him." -- and three of those tripped a check meant to catch a
    # monotonous run of sentence openings. The thing being guarded against is
    # "she did this. she did that. she did the other", which is about
    # sentences, so a beat that does not begin one is not an opening at all.
    openings, fresh = {}, True
    for line in lines:
        if fresh:
            first = re.split(r"[^\w']+", line.lower(), 1)[0]
            if first:
                openings.setdefault(first, []).append(line)
        fresh = line.rstrip().endswith((".", "!", "?", "\u2026"))
    if not openings:                    # no line ends a sentence: fall back
        for line in lines:
            first = re.split(r"[^\w']+", line.lower(), 1)[0]
            if first:
                openings.setdefault(first, []).append(line)
    cap = int(want.get("max_same_opening", 3))
    worst = max(openings.items(), key=lambda kv: len(kv[1]))
    over = {w: len(v) for w, v in openings.items() if len(v) > cap}
    findings.append((
        "ok" if not over else "fail",
        "the lines do not all start the same way",
        ("%s — %s. Rewrite the openings; a run of them reads as one sentence "
         "however well it is cut." % (
             ", ".join('%d of %d begin "%s"' % (n, len(lines), w)
                       for w, n in sorted(over.items(), key=lambda kv: -kv[1])),
             "the limit is %d" % cap))
        if over else 'commonest opening is "%s", %d of %d (limit %d)'
        % (worst[0], len(worst[1]), len(lines), cap)))

    words = [w for line in lines for w in re.findall(r"[a-z']+", line.lower())]
    variety = len(set(words)) / float(len(words)) if words else 0.0
    floor = float(want.get("min_word_variety", 0.55))
    findings.append((
        "ok" if variety >= floor - 0.001 else "fail",
        "the script is not saying the same few words over and over",
        "%d distinct words in %d, %.2f against a %.2f floor%s"
        % (len(set(words)), len(words), variety, floor,
           "" if variety >= floor - 0.001
           else " — say more of it in different words")))

    seen = {}
    for line in lines:
        seen[line.lower().rstrip(".!?")] = seen.get(line.lower().rstrip(".!?"), 0) + 1
    dupes = {l: n for l, n in seen.items() if n > 1}
    allowed = int(want.get("repeat_lines_allowed", 1))
    findings.append((
        "ok" if len(dupes) <= allowed else "fail",
        "no line is said more often than the loop needs",
        ("%d repeated: %s — the ending running back into the opening is one "
         "line, not four" % (len(dupes), "; ".join('"%s" x%d' % (l[:34], n)
                                                   for l, n in list(dupes.items())[:3])))
        if len(dupes) > allowed else
        "%d repeated line%s, %d allowed" % (len(dupes),
                                            "" if len(dupes) == 1 else "s", allowed)))

    # Does it read as somebody telling a story, or as labels for the pictures?
    # A beat that does not finish its sentence, or that opens lower-case or on
    # a connective, is one the voice carries across the cut. A script with none
    # of those is forty captions in a row, which is what "random words from
    # what the clips show" sounds like from the outside.
    joins = ("and", "but", "so", "or", "which", "because", "while", "where",
             "who", "that", "with", "then", "until", "over", "through",
             "across", "instead", "a", "the", "one", "at", "for", "by")
    flowing = [l for l in lines
               if not l.rstrip().endswith((".", "!", "?"))
               or l[:1].islower()
               or re.split(r"[^\w']+", l.lower(), 1)[0] in joins]
    share = len(flowing) / float(len(lines))
    floor = float(want.get("min_flow", 0.35))
    findings.append((
        "ok" if share >= floor - 0.001 else "fail",
        "it reads as narration rather than as labels for the pictures",
        "%d of %d beats run on from or into another, %.0f%% against a %.0f%% "
        "floor%s" % (len(flowing), len(lines), 100 * share, 100 * floor,
                     "" if share >= floor - 0.001 else
                     " — split the sentences across the beats instead of "
                     "writing one per beat, so the voice carries over the cuts")))

    ok = not any(level == "fail" for level, _, _ in findings)
    return ok, findings


def retention_report(plan_path):
    """Does this cut match what the Shorts feed actually rewards?

    The numbers come from the style's `retention` section, not from taste. A
    viewer re-asks whether to keep watching every second or two, so a beat that
    outlasts that is where they leave; the opening beat has about as long as a
    thumb takes to move. Returns (ok, findings) shaped like rights_report.
    """
    path = Path(plan_path)
    plan = json.loads(path.read_text(encoding="utf-8"))
    # A plan carries the style it was BUILT with, which can predate a section
    # the style has since gained. Taking it raw means a missing section reads
    # as "no opinion" and the check quietly stops checking -- so fill it from
    # the defaults the same way loading a style file does.
    import style as style_mod
    look = plan.get("_style") or the_style()
    look = style_mod._fill_defaults(json.loads(json.dumps(look)))
    want = look.get("retention") or {}
    beats = plan.get("beats") or []
    findings = []
    if not beats:
        return False, [("fail", "the plan has beats to check", "none found")]

    durations = [float(b.get("duration") or 0) for b in beats]
    total = sum(durations)

    hook_limit = float(want.get("hook_seconds", 2.0))
    findings.append((
        "ok" if durations[0] <= hook_limit + 0.01 else "fail",
        "the opening beat fits the swipe window",
        "%.2fs against a %.1fs limit%s" % (
            durations[0], hook_limit,
            "" if durations[0] <= hook_limit + 0.01
            else " — a thumb is already moving; this one asks it to wait")))

    ceiling = float(want.get("beat_ceiling_seconds", 2.6))
    slow = [(i + 1, d) for i, d in enumerate(durations) if d > ceiling + 0.01]
    findings.append((
        "ok" if not slow else "warn",
        "no beat outstays the attention span",
        "%d of %d over %.1fs: %s" % (
            len(slow), len(beats), ceiling,
            ", ".join("beat %d at %.1fs" % b for b in slow[:4]))
        if slow else "longest is %.2fs, ceiling is %.1fs" % (max(durations), ceiling)))

    target = float(want.get("beat_target_seconds", 2.0))
    mean = total / len(durations)
    findings.append((
        "ok" if mean <= target + 0.4 else "warn",
        "something changes often enough to hold a viewer",
        "a cut every %.2fs on average, aiming for %.1fs" % (mean, target)))

    cap = float(want.get("total_target_seconds", 30.0))
    findings.append((
        "ok" if total <= cap + 0.01 else "warn",
        "the whole thing is short enough to be watched twice",
        "%.1fs against a %.0fs target — watch time as a share of length is what "
        "ranks now, and a longer cut has further to fall" % (total, cap)))

    if want.get("require_loop"):
        echo = _overlap(beats[0].get("caption"), beats[-1].get("caption"))
        findings.append((
            "ok" if echo >= 0.4 else "warn",
            "the ending runs back into the opening",
            "%.0f%% of the opening line comes back at the end%s" % (
                echo * 100,
                "" if echo >= 0.4
                else " — a loop is what turns one view into two, and rewatches count")))

    ok = not any(level == "fail" for level, _, _ in findings)
    return ok, findings


# The three buckets YouTube's July 2026 clarification of the inauthentic
# content policy names as non-monetizable. The policy targets low-effort
# templated work, not AI as such -- AI-assisted video stays monetizable where
# a person added something and any synthetic element is disclosed.
SENSITIVE = (
    "diagnos", "symptom", "cure", "treatment", "supplement", "dosage",
    "prescri", "medication", "invest", "stock", "crypto", "portfolio",
    "returns", "refinanc", "tax ", "lawsuit", "legal advice", "sue ",
    "attorney", "settlement",
)
# words a line uses when it TELLS a viewer to act, which is what turns a
# sensitive subject into advice
ADVISORY = ("you should", "you need to", "take ", "buy ", "sell ", "invest in",
            "stop taking", "start taking", "consult", "do this", "avoid ")


USED_LEDGER = HERE / "clips_used.json"


def _used_ledger(used_path=None):
    path = Path(used_path or USED_LEDGER)
    if not path.exists():
        return {"clips": [], "spans": [], "sources": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"clips": [], "spans": [], "sources": {}}
    data.setdefault("clips", [])
    data.setdefault("spans", [])
    data.setdefault("sources", {})
    return data


def plan_spans(plan_path):
    """(source, start, end) for every beat, in the SOURCE's own timeline.

    A beat's `in` is an offset into its clip file; `origins.json` says where
    that clip starts in the film it was cut from. Adding them is the only way
    to ask "have we used this footage before" and get a true answer -- two
    different clip files can hold the same seconds of the same source.
    """
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    origins, out = {}, []
    for beat in plan.get("beats") or []:
        clip = Path(str(beat.get("clip") or ""))
        folder = str(clip.parent)
        if folder not in origins:
            book = clip.parent / "origins.json"
            try:
                origins[folder] = json.loads(book.read_text(encoding="utf-8")) \
                    if book.exists() else {}
            except (json.JSONDecodeError, OSError):
                origins[folder] = {}
        came = (origins[folder] or {}).get(clip.name) or {}
        source = came.get("source") or clip.name
        start = float(came.get("in_seconds") or 0.0) + float(beat.get("in") or 0.0)
        out.append((source, round(start, 2),
                    round(start + float(beat.get("duration") or 0.0), 2)))
    return out


def unused_report(plan_path, used_path=None):
    """Is this footage new, or is the channel about to repeat itself?

    Two levels, because they are different problems. A SHOT used twice is the
    same seconds of the same film in two videos, which a viewer notices. A
    SOURCE used twice is a channel that mines one film over and over, which is
    what "a format rather than a body of work" means and what the Inauthentic
    Content policy is actually looking at.

    Set `"reuse_source": true` in the plan to allow the second deliberately --
    a long film can carry more than one Short. There is no override for the
    first: the same seconds twice is never what you meant.
    """
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    ledger = _used_ledger(used_path)
    spans = plan_spans(plan_path)
    findings = []
    if not spans:
        return False, [("fail", "the plan has beats to check", "none found")]

    seen = [(s[0], float(s[1]), float(s[2])) for s in ledger.get("spans", [])]
    clashes = []
    for source, start, end in spans:
        for was_source, was_start, was_end in seen:
            if was_source == source and start < was_end and was_start < end:
                clashes.append((source, start, end, was_start, was_end))
                break
    # An empty ledger passes everything, which is right on the first video and
    # a silent failure on the fiftieth. `clips_used.json` is local state, like
    # the niche and the performance history, so a run in a fresh checkout
    # starts with no memory -- and the one thing worse than repeating footage
    # is repeating it while a check says it did not.
    if not seen and not ledger.get("sources"):
        findings.append((
            "warn", "the channel remembers what it has used",
            "clips_used.json is empty, so nothing can be refused. That is "
            "correct for a first video and a warning sign on any other: the "
            "ledger is local state and does not survive a fresh checkout. Keep "
            "the working directory between runs, or carry the file with it."))
    findings.append((
        "fail" if clashes else "ok",
        "no shot has been used in an earlier video",
        "%d shot%s already used: %s" % (
            len(clashes), "" if len(clashes) == 1 else "s",
            "; ".join("%s %.1f-%.1fs overlaps %.1f-%.1fs" % c for c in clashes[:3]))
        if clashes else "%d shots, none seen before" % len(spans)))

    used_sources = set(ledger.get("sources", {}))
    mine = {s for s, _, _ in spans}
    repeats = sorted(mine & used_sources)
    allowed = bool(plan.get("reuse_source"))
    findings.append((
        "ok" if not repeats else ("warn" if allowed else "fail"),
        "the footage comes from a source this channel has not used",
        ("%s already made %s. Cut the next video from something else, or set "
         '"reuse_source": true in the plan if this film genuinely has another '
         "Short in it." % (", ".join(repeats[:3]),
                           ", ".join(sorted(
                               v for r in repeats[:3]
                               for v in ledger["sources"].get(r, [])[:2]) ) or "an earlier video"))
        if repeats and not allowed else
        ("%s used before, allowed by the plan" % ", ".join(repeats[:3])) if repeats else
        "%s, new to the channel" % ", ".join(sorted(mine)[:3])))
    return all(level != "fail" for level, _, _ in findings), findings


def remember_used(plan_path, used_path=None, name=None):
    """Record this video's footage so the next one cannot repeat it."""
    path = Path(used_path or USED_LEDGER)
    data = _used_ledger(path)
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    video = name or Path(str(plan.get("output") or "video")).name
    spans = plan_spans(plan_path)
    data["spans"] = (data["spans"] + [[s, a, b] for s, a, b in spans])[-4000:]
    for source in sorted({s for s, _, _ in spans}):
        data["sources"].setdefault(source, [])
        if video not in data["sources"][source]:
            data["sources"][source].append(video)
    data["clips"] = (data["clips"] +
                     [Path(str(b.get("clip") or "")).name
                      for b in plan.get("beats") or []])[-400:]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return {"video": video, "shots": len(spans),
            "sources": sorted({s for s, _, _ in spans})}


def monetize_report(plan_path, history_path=None, used_path=None):
    """Would this survive the Partner Program's inauthentic content policy?

    Checks the three things the policy actually names, plus the one disclosure
    that applies. It cannot promise monetization -- no check can, the decision
    is a reviewer's -- so it reports exposure rather than a verdict.
    """
    path = Path(plan_path)
    plan = json.loads(path.read_text(encoding="utf-8"))
    beats = plan.get("beats") or []
    findings = []
    if not beats:
        return False, [("fail", "the plan has beats to check", "none found")]

    captions = " ".join((b.get("caption") or "") for b in beats)
    words = len(re.findall(r"[\w']+", captions))
    total = sum(float(b.get("duration") or 0) for b in beats)
    audio = plan.get("audio") or {}

    # 1. is there an original contribution, or is this footage with music on it
    per_second = words / total if total else 0
    enough = bool(audio.get("path")) and per_second >= 1.2
    findings.append((
        "ok" if enough else "fail",
        "the video carries original commentary",
        "%d narrated words over %.0fs (%.1f a second)%s" % (
            words, total, per_second,
            "" if enough else
            " — third-party footage with little added is the first bucket the "
            "policy names, whoever or whatever narrated it")))

    # A synthesised narrator clears none of this on its own. The policy is
    # about the channel, not the file, and a reviewer looking at a run of
    # videos made to one template with a stock TTS voice is looking at exactly
    # what "mass-produced" describes. This cannot be checked from one plan, so
    # it is said rather than tested.
    synthetic = any(m in (audio.get("license") or "").lower()
                    for m in ("kokoro", "espeak", "piper", "elevenlabs", "pico"))
    if synthetic:
        findings.append((
            "warn", "the narration is not synthesised",
            "narrated by %s. The words and the edit are original and that is "
            "most of the way there, but a channel of TTS over other people's "
            "footage, cut to one template, is what a reviewer means by "
            "mass-produced. `voice.py --recorded <dir>` takes a real voice, and "
            "it is the single biggest thing that moves this out of doubt."
            % (audio.get("license") or "a speech engine")))

    # 2. an AI persona advising on health, money or the law is named explicitly
    lowered = captions.lower()
    topics = sorted({t.strip() for t in SENSITIVE if t in lowered})
    advisory = [a.strip() for a in ADVISORY if a in lowered]
    if topics and advisory:
        findings.append((
            "fail", "no synthetic voice giving advice on a sensitive subject",
            "touches %s AND tells the viewer to act (%s) — a synthetic narrator "
            "advising on health, finance or law is named as non-monetizable"
            % (", ".join(topics[:3]), ", ".join(advisory[:2]))))
    elif topics:
        findings.append((
            "warn", "no synthetic voice giving advice on a sensitive subject",
            "mentions %s but does not tell the viewer to act — keep it that way"
            % ", ".join(topics[:3])))
    else:
        findings.append((
            "ok", "no synthetic voice giving advice on a sensitive subject",
            "nothing here touches health, finance or law"))

    # 3. footage recycled from earlier videos is what mass production looks like
    ledger = Path(used_path) if used_path else (HERE / "clips_used.json")
    seen = set()
    if ledger.exists():
        try:
            seen = set(json.loads(ledger.read_text(encoding="utf-8")).get("clips", []))
        except (json.JSONDecodeError, OSError):
            seen = set()
    # A shot is a file AND a position in it: fourteen segments of one film are
    # fourteen different shots, and counting filenames calls them one.
    shots = [(Path(b.get("clip") or "").stem, round(float(b.get("in") or 0), 1))
             for b in beats]
    repeats = len(shots) - len(set(shots))
    findings.append((
        "ok" if not repeats else "warn",
        "no shot is used twice inside this video",
        "%d beat%s repeat a shot" % (repeats, "" if repeats == 1 else "s")
        if repeats else "%d distinct shots" % len(set(shots))))

    sources = {name for name, _ in shots}
    findings.append((
        "ok" if len(sources) > 1 else "warn",
        "the video draws on more than one source",
        "%d sources" % len(sources) if len(sources) > 1 else
        "every shot comes from one file. That is not against the policy, but a "
        "channel whose videos each mine a single source looks like a format "
        "rather than a body of work"))

    already = [s for s in shots
               if any(str(s[0]) in key for key in seen)] if seen else []
    if already:
        findings.append((
            "warn", "footage has not appeared in an earlier video",
            "%d shot%s drawn from footage the ledger has seen before"
            % (len(already), "" if len(already) == 1 else "s")))

    # 4. the credit the licence requires, where a viewer can actually read it
    due = credits_due(plan)
    if due:
        out = Path(str(plan.get("output") or ""))
        beside = [out.with_suffix(".description.txt"), out.parent / "description.txt"]
        found = next((f for f in beside if f.exists()), None)
        text = found.read_text(encoding="utf-8") if found else ""
        missing = [d for d in due if d.split(" — ")[0].strip() not in text]
        findings.append((
            "ok" if found and not missing else "fail",
            "the credit the licence requires is in the description",
            "%s carries %d credit%s" % (found.name, len(due), "" if len(due) == 1 else "s")
            if found and not missing else
            ("no description file beside the video — write one with "
             "`render.description(plan)`; a CC-BY credit that lives only in a "
             "JSON file has not been given" if not found else
             "%s does not name %s" % (found.name, "; ".join(m[:48] for m in missing)))))
    else:
        findings.append((
            "ok", "the credit the licence requires is in the description",
            "no clip here is under a licence that obliges one"))

    # 5. this is not children's content and the upload has to say so
    findings.append((
        "warn", "the upload is marked not made for kids",
        "youtube.py defaults to selfDeclaredMadeForKids=false and this video "
        "should keep it. Anything with injury or death in it is not children's "
        "content, and mislabelling is its own strike."))

    # 6. the one disclosure that actually applies
    voice_licence = (audio.get("license") or "").lower()
    cloned = any(m in voice_licence for m in ("clone", "cloned", "likeness", "impersonat"))
    findings.append((
        "warn" if cloned else "ok",
        "the narration needs no synthetic-content disclosure",
        "this track says it clones a voice — tick Altered Content at upload; "
        "cloning a REAL person's voice requires disclosure, a generic synthetic "
        "voice does not" if cloned else
        "a generic synthetic voice does not require disclosure; only a clone of "
        "a specific real person does"))

    # 5. the thing this check cannot see
    findings.append((
        "warn", "a run of videos is judged together, not one at a time",
        "this reads one plan. The policy's first bucket is template-driven work "
        "at scale, so identical structure across uploads is the real exposure — "
        "vary the shape, not just the subject"))

    ok = not any(level == "fail" for level, _, _ in findings)
    return ok, findings


def validate_plan(plan, base=None):
    base = Path(base or ".")
    if not isinstance(plan, dict):
        raise RenderError("the plan must be a JSON object")
    beats = plan.get("beats")
    if not isinstance(beats, list) or not beats:
        raise RenderError('the plan needs a non-empty "beats" array')

    look = the_style()
    for key in ("width", "height", "fps"):
        if key in plan and plan[key] != look["format"][key]:
            raise RenderError(
                'the plan sets %s=%r but the committed style says %r. Every video '
                "uses one style — change it in style.json if you mean to change it "
                "for all of them, not here for one." % (key, plan[key], look["format"][key]))
    plan["width"] = look["format"]["width"]
    plan["height"] = look["format"]["height"]
    plan["fps"] = look["format"]["fps"]
    plan["_style"] = look
    plan["_styleVersion"] = look.get("version", 0)

    total = 0.0
    for i, beat in enumerate(beats):
        label = "beats[%d]" % i
        if not isinstance(beat, dict):
            raise RenderError("%s must be an object" % label)
        clip = beat.get("clip")
        if not clip:
            raise RenderError('%s has no "clip"' % label)
        resolved = Path(clip)
        if not resolved.is_absolute():
            resolved = base / clip
        if not resolved.exists():
            raise RenderError("%s: no such clip: %s" % (label, resolved))
        beat["_path"] = str(resolved)
        _check_license(beat, label)

        duration = beat.get("duration")
        if not isinstance(duration, (int, float)) or duration <= 0:
            raise RenderError('%s needs a positive "duration" in seconds' % label)
        if duration > 120:
            raise RenderError("%s is %gs — beats longer than 120s are almost "
                              "certainly a mistake" % (label, duration))
        start = beat.get("in", 0)
        if not isinstance(start, (int, float)) or start < 0:
            raise RenderError('%s has a negative "in" point' % label)
        beat["in"] = float(start)
        beat["duration"] = float(duration)
        total += beat["duration"]

    audio = plan.get("audio")
    if audio:
        if not isinstance(audio, dict) or not audio.get("path"):
            raise RenderError('"audio" needs a "path"')
        apath = Path(audio["path"])
        if not apath.is_absolute():
            apath = base / audio["path"]
        if not apath.exists():
            raise RenderError("no such audio: %s" % apath)
        audio["_path"] = str(apath)
        _check_license(audio, "audio")

    plan["_total"] = total
    plan["_base"] = str(base)

    # This channel publishes Shorts. Catch an over-length cut here rather than
    # after four minutes of encoding.
    import style as style_mod
    ok, reasons = style_mod.shorts_verdict(total, look["format"]["width"],
                                           look["format"]["height"], look)
    if not ok:
        raise RenderError(
            "this cut would not be a Short: " + " ".join(reasons) +
            " Trim the beats, or raise shorts.max_seconds in style.json if the "
            "channel is deliberately changing format.")
    target = look["shorts"]["target_seconds"]
    if total > target:
        print("render.py: %.1fs is over the style's %.0fs target — still a Short, "
              "but short-form retention falls away the longer it runs."
              % (total, target), file=sys.stderr)
    return plan


# --------------------------------------------------------------------------
# captions
# --------------------------------------------------------------------------
# Above this ratio of source aspect to target aspect, cropping to fill would
# throw away so much of the frame that the composition is gone. 16:9 into 9:16
# is 3.16 -- two thirds of the width cut off, which is how a title card ends up
# sliced down the middle.
BLUR_ABOVE = 1.5


def choose_fit(fit, w, h, src_w=None, src_h=None):
    """crop or blur for this source. `auto` decides on how different the shapes are."""
    if fit in ("crop", "blur"):
        return fit
    if not (src_w and src_h and w and h):
        return "crop"                     # unknown source: behave as before
    source = float(src_w) / float(src_h)
    target = float(w) / float(h)
    return "blur" if source / target > BLUR_ABOVE else "crop"


# Fitting a whole 16:9 frame into a 9:16 one leaves the picture filling 32% of
# the height with blurred fill above and below it: a small window in the middle
# of a phone, not a Short. Cropping to fill instead keeps every pixel of height
# but throws away two thirds of the width, blind to what was in it -- which is
# how a two-shot loses one of the two people.
#
# Neither is necessary. Detail in a frame is not spread evenly: it sits in a
# band, and the rest is background. Measure where that band is, crop to it, and
# fill covers whatever is left over -- with the subject still inside the picture
# rather than half outside it. A 16:9 source gets to about two thirds of the
# frame; a 2.35:1 one stops lower, because the upscale cap binds before the
# coverage target does.
FOCUS_SAMPLES = 5
FOCUS_COLS = 96
FOCUS_ROWS = 54


def _grey_frames(path, start=None, duration=None, fps=4.0, limit=None,
                 cols=FOCUS_COLS, rows=FOCUS_ROWS):
    """Tiny greyscale frames from `path` as an (n, rows, cols) array.

    None when numpy is missing or the clip cannot be decoded, which every
    caller treats as "measure nothing and behave as before". Thumbnails this
    small cost less to decode and score than the trim that follows them.
    """
    try:
        import numpy as np
    except ImportError:
        return None
    args = [ffmpeg_bin(), "-v", "error"]
    if start is not None:
        args += ["-ss", "%.3f" % float(start)]
    if duration is not None:
        args += ["-t", "%.3f" % float(duration)]
    args += ["-i", str(path),
             "-vf", "fps=%.4f,format=gray,scale=%d:%d" % (fps, cols, rows)]
    if limit:
        args += ["-frames:v", str(int(limit))]
    args += ["-f", "rawvideo", "-"]
    try:
        raw = subprocess.run(args, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=180).stdout
    except Exception:
        return None
    size = cols * rows
    got = len(raw) // size
    if not got:
        return None
    return np.frombuffer(raw[:got * size], dtype=np.uint8) \
             .reshape(got, rows, cols).astype(np.float32)


def _detail_columns(path, start, duration, samples=FOCUS_SAMPLES,
                    cols=FOCUS_COLS, rows=FOCUS_ROWS):
    """Per-column detail energy across a beat, or None if it cannot be read.

    Gradient magnitude rather than brightness: an edge in a dark corner is
    something worth keeping, and a flat bright wall is not.
    """
    n = max(1, int(samples))
    span = max(0.04, float(duration or 0.5))
    a = _grey_frames(path, float(start or 0), span, fps=n / span, limit=n,
                     cols=cols, rows=rows)
    if a is None:
        return None
    import numpy as np
    across = np.abs(np.diff(a, axis=2)).sum(axis=(0, 1))     # cols - 1
    down = np.abs(np.diff(a, axis=1)).sum(axis=(0, 1))       # cols
    energy = down.astype(np.float64)
    energy[1:] += across
    energy[:-1] += across
    return energy


# A long source is mostly not worth cutting to. Fades, held blacks, empty
# establishing frames: all fine in a film, all dead screen time in an eighteen
# second Short, where a beat is a second and a half and the viewer's thumb is
# already moving. Scoring every position before choosing is what stops a shot
# with nothing in it getting picked because it happened to fall on the stride.
DARK_FLOOR = 30.0          # mean luma, 0-255, below which a shot reads as black
SCAN_FPS = 4.0


# ffmpeg's scene score, above which the picture is a different shot. 0.3 is the
# usual suggestion and it misses too much in a dark film -- at 0.3 Sintel reads
# as 97 shots over fifteen minutes and at 0.12 as 222, which is nearer the
# truth. A boundary found that is not really there only nudges an in-point; one
# missed puts a cut in the middle of a beat.
SCENE_THRESHOLD = 0.12


class ShotError(RenderError):
    """The source could not supply the shots asked of it."""


def pick_shots(path, count, seconds, start=0.0, end=None, gap=None,
               scan_fps=SCAN_FPS, dark_floor=DARK_FLOOR, spread=True,
               avoid=()):
    """`count` in-points in `path` worth cutting to, in time order.

    Every position is scored on how much there is to look at over the seconds
    that follow it, and the near-black is thrown out. Raises rather than
    padding the list out: quietly reusing a shot is what made an earlier video
    look like it had four clips in it when it had seven beats.

    `spread` divides the source into one region per shot and takes the best of
    each, instead of the best twelve overall. Scoring alone does not give a
    compilation -- one well-lit sequence outscores the whole rest of a film,
    so twelve beats land inside ninety seconds of it and the video looks like
    a single scene. A region with nothing usable in it falls back to the best
    that is left anywhere.

    `avoid` is (from, to) spans to leave alone: credits, a logo sting, or the
    scene with the blood in it. Brightness and detail are all this measures,
    and neither one knows what an advertiser will object to.
    """
    count = int(count)
    if count < 1:
        raise ShotError("asked for %d shots" % count)
    seconds = max(0.2, float(seconds))
    gap = float(gap if gap is not None else max(2.0, seconds * 1.5))
    a = _grey_frames(path, fps=scan_fps)
    if a is None or len(a) < 2:
        raise ShotError("could not read frames from %s — is it a video file?" % path)
    import numpy as np
    detail = (np.abs(np.diff(a, axis=2)).mean(axis=(1, 2))
              + np.abs(np.diff(a, axis=1)).mean(axis=(1, 2)))
    luma = a.mean(axis=(1, 2))
    step = 1.0 / scan_fps
    width = max(1, int(round(seconds * scan_fps)))
    last = len(a) - width
    if last < 0:
        raise ShotError("%s is shorter than one %.1fs beat" % (path, seconds))
    lo = max(0, int(round(float(start) / step)))
    hi = last if end is None else min(last, int(round(float(end) / step)) - width)
    if hi < lo:
        raise ShotError("no room between %.1fs and %s in %s"
                        % (start, "the end" if end is None else "%.1fs" % end, path))

    blocked = [(float(x), float(y)) for x, y in (avoid or ())]
    scored = []
    for i in range(lo, hi + 1):
        at = i * step
        if any(x - seconds < at < y for x, y in blocked):
            continue
        window_luma = float(luma[i:i + width].mean())
        blackish = float((luma[i:i + width] < dark_floor).mean())
        if window_luma < dark_floor or blackish > 0.34:
            continue                      # a beat spent on black is a beat lost
        scored.append((float(detail[i:i + width].mean()), at))
    if not scored:
        raise ShotError(
            "every position in %s is too dark to cut to (nothing above a mean "
            "luma of %g). Pick a different source, or lower dark_floor if the "
            "footage really is meant to look like that." % (path, dark_floor))

    scored.sort(key=lambda pair: -pair[0])
    chosen = []

    def take(pool):
        for score, at in pool:
            if at not in chosen and all(abs(at - t) >= gap for t in chosen):
                chosen.append(at)
                return True
        return False

    if spread:
        first, final = lo * step, hi * step
        region = (final - first) / float(count)
        for b in range(count):
            edge_lo, edge_hi = first + b * region, first + (b + 1) * region
            take([pair for pair in scored if edge_lo <= pair[1] < edge_hi])
    while len(chosen) < count and take(scored):
        pass
    if len(chosen) < count:
        raise ShotError(
            "%s yielded %d shot%s worth using, not %d: %.0fs of usable footage "
            "cannot hold %d beats %.1fs apart. Use a longer source, cut the "
            "script, or lower the spacing."
            % (path, len(chosen), "" if len(chosen) == 1 else "s", count,
               len(a) * step, count, gap))
    return sorted(chosen)


def shot_boundaries(path, threshold=SCENE_THRESHOLD):
    """Times where the source cuts to a different shot, in seconds.

    A clip that straddles one of these is a clip with a cut inside it, which
    lands in the finished video as a second cut nobody planned -- the beat
    starts on the shot you chose and finishes somewhere else. Picking an
    in-point off a contact sheet cannot see this: the sampled frame is the shot
    you wanted and the two seconds after it are not.
    """
    try:
        out = subprocess.run(
            [ffmpeg_bin(), "-v", "error", "-i", str(path),
             "-vf", "select='gt(scene,%.3f)',metadata=print:file=-" % float(threshold),
             "-an", "-f", "null", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=900,
            text=True).stdout
    except Exception:
        return []
    return sorted(float(m) for m in re.findall(r"pts_time:([0-9.]+)", out or ""))


def snap_to_shot(at, seconds, cuts, lead=0.12):
    """Move `at` so a `seconds` clip starting there stays inside one shot.

    Returns (start, room) -- where to cut from, and how much unbroken shot
    there is. `room` under `seconds` means the shot itself is too short and no
    start avoids the cut; the caller decides whether that is worth having.
    """
    at = float(at)
    before = [c for c in cuts if c <= at + 0.001]
    after = [c for c in cuts if c > at + 0.001]
    opens = max(before) if before else 0.0
    closes = min(after) if after else float("inf")
    room = closes - opens
    if at + seconds <= closes - 0.04:
        return at, room                   # already clear of the next cut
    # the clip would run over the end of this shot; pull it back towards the
    # start of the shot it was aimed at rather than jumping to another one
    return max(opens + lead, min(at, closes - seconds - 0.04)), room


def cut_shots(path, out_dir, count, seconds, licence=None, at=None,
              cuts=None, **kwargs):
    """Cut `count` clips out of one source into `out_dir`, with their licence.

    This is the step between "a film is downloadable" and "a folder of clips":
    the same provenance rule as every other clip source applies, so the licence
    is written beside them and nothing renders without it.

    `at` is an explicit list of in-points, in beat order, for when the cut has
    to follow the story rather than the light. Scoring finds the shots worth
    looking at; it has no idea which one is the dragon. Given `at`, nothing is
    scored and the order is kept exactly as passed -- but each one is still
    snapped so the clip cannot straddle a cut in the source, and a moment
    sitting in a shot too short to hold the clip is an error rather than a beat
    that quietly changes picture half way through. `cuts` passes in a cached
    boundary list; finding them means decoding the whole source.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if at is not None:
        at = [float(t) for t in at]
        if len(at) != count:
            raise ShotError("%d in-points given for %d shots" % (len(at), count))
        cuts = shot_boundaries(path) if cuts is None else list(cuts)
        if cuts:
            snapped, cramped = [], []
            for i, t in enumerate(at, 1):
                start, room = snap_to_shot(t, seconds, cuts)
                if room < seconds:
                    cramped.append((i, t, room))
                snapped.append(start)
            if cramped:
                raise ShotError(
                    "%s in a shot shorter than the %.1fs asked for: %s. Pick a "
                    "different moment, or cut shorter clips."
                    % ("shot %d is" % cramped[0][0] if len(cramped) == 1
                       else "%d shots are" % len(cramped), seconds,
                       ", ".join("#%d at %.1fs has %.1fs" % c for c in cramped[:4])))
            at = snapped
    else:
        at = pick_shots(path, count, seconds, **kwargs)
    ff = ffmpeg_bin()
    made, ledger, origins = [], {}, {}
    for i, t in enumerate(at, 1):
        dst = out_dir / ("clip%02d.mp4" % i)
        _run([ff, "-hide_banner", "-loglevel", "error", "-y",
              "-ss", "%.3f" % t, "-t", "%.3f" % seconds, "-i", str(path),
              "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
              "-pix_fmt", "yuv420p", str(dst)], "cutting shot %d" % i)
        made.append(dst)
        origins[dst.name] = {"source": Path(path).name, "in_seconds": round(t, 3),
                             "duration_seconds": round(float(seconds), 3)}
        if licence:
            ledger[dst.name] = licence
    if licence:
        (out_dir / "licenses.json").write_text(
            json.dumps(ledger, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
    # Where each clip came from IN THE SOURCE. Without this a rights receipt can
    # only say "clip07.mp4 at 0.0s", which answers nothing: a Content ID dispute
    # is won by naming the seconds of the original that were used.
    (out_dir / "origins.json").write_text(
        json.dumps(origins, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return made


def _narrowest_band(energy, keep):
    """(centre, width) of the shortest run of columns holding `keep` of the
    total, both as fractions of the whole width."""
    n = len(energy)
    total = float(sum(energy))
    if n == 0 or total <= 0:
        return 0.5, 1.0
    want = total * float(keep)
    best_width, best_start = n, 0
    running, lo = 0.0, 0
    for hi in range(n):
        running += float(energy[hi])
        while running - float(energy[lo]) >= want and lo < hi:
            running -= float(energy[lo])
            lo += 1
        if running >= want and (hi - lo + 1) < best_width:
            best_width, best_start = hi - lo + 1, lo
    return (best_start + best_width / 2.0) / n, best_width / float(n)


def focus_window(path, start=0.0, duration=1.0, keep=0.72):
    """Where a beat's detail sits horizontally, as (centre, width) fractions.

    (0.5, 1.0) -- the whole frame, which is what reframing did before this
    existed -- whenever the measurement is unavailable.
    """
    energy = _detail_columns(path, start, duration)
    if energy is None:
        return 0.5, 1.0
    return _narrowest_band(energy, keep)


def mean_luma(path, start, duration, samples=12):
    """Average brightness of a beat, 0-255, or None if it cannot be read."""
    frames = _grey_frames(path, start, duration, SCAN_FPS, samples, 64, 36)
    if frames is None or not len(frames):
        return None
    return float(frames.mean())


def lift_filter(mean, floor, max_gamma=2.2):
    """A gamma that brings a dark beat up to `floor`, or "" if it is fine.

    Brightness would do it by adding a constant, which lifts the blacks off
    zero and leaves the shot looking washed rather than lit. Gamma moves the
    midtones and leaves black where it was, which is what "turn the lamp up"
    actually means.

    The exponent is solved rather than guessed: for a mean m and a target f,
    (m/255)^(1/g) = f/255, so g = ln(m/255) / ln(f/255). Capped, because past
    a certain point the grain comes up faster than the picture.

    Saturation goes with it. Lifting gamma alone flattens colour -- the same
    shot reads greyer at the top of the curve than it did at the bottom.
    """
    if mean is None or floor <= 0 or mean <= 0 or mean >= floor:
        return ""
    gamma = math.log(mean / 255.0) / math.log(min(254.0, floor) / 255.0)
    gamma = max(1.0, min(float(max_gamma), gamma))
    if gamma <= 1.02:
        return ""
    return "eq=gamma=%.3f:saturation=%.3f" % (gamma, min(1.25, 1.0 + (gamma - 1.0) * 0.22))


def precrop(src_w, src_h, w, h, focus, coverage, max_upscale=1.9):
    """(crop_w, crop_x): how much of the source width to keep, and from where.

    None means keep all of it. Three floors decide how narrow the crop may go,
    and the widest wins: the band the detail sits in, the width that fills
    `coverage` of the output, and the width below which the source would have
    to be blown up by more than `max_upscale`. That last one is what a 2.35:1
    film needs -- filling a vertical frame from one would mean a 2.2x upscale,
    and soft is worse than small.
    """
    if not (src_w and src_h and w and h):
        return None
    centre, span = focus
    fills = float(src_h) * float(w) / max(0.05, float(coverage)) / float(h)
    sharp = float(w) / max(1.0, float(max_upscale))
    need = max(float(span) * src_w, fills, sharp)
    cw = int(min(float(src_w), max(2.0, need))) // 2 * 2
    if cw >= src_w - 1:
        return None                       # nothing to gain: keep the frame
    cx = int(round(float(centre) * src_w - cw / 2.0))
    return cw, max(0, min(src_w - cw, cx)) // 2 * 2


# transition.kind was in the style from the beginning and nothing read it, so
# every video has been twelve hard cuts between unrelated shots. A hard cut is
# not wrong -- fast-cut Shorts are built from them -- but at these lengths the
# brightness alone jumps fifteen times the median frame-to-frame change at some
# of them, and that flash is what reads as loose.
#
# The overlap has to come out of the footage, not the timeline: the beats are
# cut to the voice, so if a transition shortened the video the captions and the
# narration would drift apart a little more at every cut. Each part is given
# the extra length instead, and the transition is centred on the cut so it sits
# in the gap between two spoken lines rather than across the next word.
TRANSITIONS = {
    "cut": None,
    "crossfade": "fade",          # the plain cross-dissolve
    "dissolve": "fade",           # an alias, because both names get typed
    "dip": "fadeblack",           # down to black and back up
    "white": "fadewhite",
    "wipe": "wiperight",
    "slide": "slideleft",
}


def transition_plan(kind, seconds, durations, fps=30):
    """(name, seconds, [extra frames per part], [offsets]) for an xfade chain.

    (None, 0, ...) means hard cuts, which is a straight concatenation and no
    re-encode. Everything is counted in whole frames, so the overlap a part is
    given and the overlap the transition consumes are the same number and the
    timeline cannot drift. The last part carries half the overlap because the
    transition is centred: the first half runs off the end of the beat before
    it, and only the second half needs footage from this one.
    """
    name = TRANSITIONS.get(str(kind or "cut").lower(), None)
    seconds = float(seconds or 0)
    n = len(durations)
    fps = float(fps or 30)
    if not name or seconds <= 0.001 or n < 2:
        return None, 0.0, [0] * n, []
    # a transition cannot be longer than the beats it sits between
    seconds = min(seconds, 0.6 * min(durations))
    over = max(2, int(round(seconds * fps)) // 2 * 2)     # even: it halves
    extra = [over] * (n - 1) + [over // 2]
    offsets, run = [], 0
    for d in durations[:-1]:
        run += max(1, int(round(d * fps)))
        offsets.append(round((run - over / 2.0) / fps, 4))
    return name, over / fps, extra, offsets


def xfade_graph(name, seconds, offsets, count):
    """The filter_complex that folds `count` parts into one with transitions."""
    steps, last = [], "0:v"
    for i, off in enumerate(offsets):
        label = "vx%d" % i
        steps.append("[%s][%d:v]xfade=transition=%s:duration=%.3f:offset=%.3f[%s]"
                     % (last, i + 1, name, seconds, off, label))
        last = label
    return ";".join(steps), last


# zoompan recomputes its crop window every frame and rounds the origin to whole
# pixels. On a slow push the ideal origin creeps by a fraction of a pixel, so
# the rounded value sticks, jumps, sticks -- a stutter rather than a drift.
# Running it on an oversampled frame makes that snap a fraction of an output
# pixel. 2x measured a little over half the jitter of 1x; 4x is not better
# enough to pay four times the pixels for.
PUSH_OVERSAMPLE = 2


def _strip_size(w, h, zoom, src_w, src_h):
    """The visible picture's size inside a blurred fill, in output pixels."""
    fw = min(int(round(w * max(1.0, zoom))) // 2 * 2, h)
    if not (src_w and src_h):
        return fw, h
    scale = min(fw / float(src_w), h / float(src_h))
    sw = min(int(round(src_w * scale)) // 2 * 2, w)
    sh = min(int(round(src_h * scale)) // 2 * 2, h)
    return max(2, sw), max(2, sh)


def _push(w, h, fps, push, duration, out=False):
    """A slow zoom on a w x h image, oversampled. `out` starts in and pulls back.

    Alternating the direction beat to beat is what stops a run of cuts reading
    as one long slow creep. It is still one rule applied to every video, so the
    channel keeps a single look -- it just stops every shot moving identically.
    """
    if push <= 0:
        return ""
    frames = max(1, int(round(duration * fps)))
    step = push / frames
    big_w, big_h = w * PUSH_OVERSAMPLE, h * PUSH_OVERSAMPLE
    zoom = ("max(%.4f-%.8f*on,1.0)" % (1.0 + push, step) if out
            else "min(1+%.8f*on,%.4f)" % (step, 1.0 + push))
    # setpts is not optional. zoompan hands on frames whose timestamps do not
    # match the declared timebase, so the encoded beat claims a duration 512x
    # its real one -- a 1.69s beat measuring 870s. Concatenation papered over
    # most of it, which is why it survived: the finished video came out roughly
    # right while every part file was nonsense. Rebuilding the timestamps from
    # the frame number makes a pushed beat measure exactly what an unpushed one
    # does.
    return (",scale=%d:%d,zoompan=z='%s':d=1"
            ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            ":s=%dx%d:fps=%d,setpts=N/FRAME_RATE/TB"
            % (big_w, big_h, zoom, w, h, fps))


def _reframe(w, h, mode, zoom=1.0):
    """Fill the frame, either by cropping the sides or by blurring behind."""
    if mode != "blur":
        return ("scale=%d:%d:force_original_aspect_ratio=increase,"
                "crop=%d:%d" % (w, h, w, h))
    # even to keep libx264 happy, and never wider than the frame is tall
    fw = min(int(round(w * max(1.0, zoom))) // 2 * 2, h)
    # The whole source frame, centred, over a blurred and darkened enlargement
    # of itself. This is the standard way to put landscape footage in a vertical
    # frame without recomposing it, and it reads as deliberate rather than as a
    # mistake -- which a half-visible subject does not.
    return ("split[__bg][__fg];"
            "[__bg]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,"
            "gblur=sigma=%d,eq=brightness=-0.13:saturation=0.9[__bgb];"
            "[__fg]scale=%d:%d:force_original_aspect_ratio=decrease,"
            "crop='min(iw,%d)':'min(ih,%d)'%%s[__fgs];"
            "[__bgb][__fgs]overlay=(W-w)/2:(H-h)/2"
            % (w, h, w, h, max(8, int(w / 38)), fw, h, w, h))


def _beat_filter(w, h, fps, push, duration, fit="crop", src_w=None, src_h=None,
                 blur_zoom=1.0, push_out=False, crop_to=None, lift=""):
    """Reframe to the style's format, and apply its push-in.

    The slow push is what stops a run of stock clips reading as a slideshow;
    because it comes from the style it is identical in every video.

    Where the picture sits in a blurred fill, the push goes on the PICTURE and
    not on the finished frame. Zooming the composite drags the strip's own
    edges across a static background, and a hard edge creeping against
    stillness reads as a shake far more than the image inside it ever does.

    `crop_to` is (width, x) from `precrop`: the band of the source worth
    keeping. Everything downstream then treats that band as the whole frame,
    so a shot that would have been a letterboxed strip is fitted as if it had
    been shot closer.

    `lift` is `lift_filter`'s gamma for a dark beat. It goes on first, before
    anything is scaled or blurred, so the blurred fill is lit from the same
    picture as the strip in front of it rather than staying black behind a
    brightened one.
    """
    head = "%s," % lift if lift else ""
    if crop_to and src_h:
        cw, cx = crop_to
        head += "crop=%d:%d:%d:0," % (cw, src_h, cx)
        src_w = cw
    mode = choose_fit(fit, w, h, src_w, src_h)
    if mode == "blur":
        sw, sh = _strip_size(w, h, blur_zoom, src_w, src_h)
        chain = _reframe(w, h, mode, blur_zoom) % _push(sw, sh, fps, push,
                                                        duration, push_out)
        return "%s%s,fps=%d,setsar=1" % (head, chain, fps)
    chain = _reframe(w, h, mode, blur_zoom)
    # Driven off `on` (the output frame counter), not the accumulating `zoom`
    # variable: with d=1 zoom resets on every input frame, so the usual
    # zoom+step recipe silently produces no motion at all.
    chain += _push(w, h, fps, push, duration, push_out)
    return "%s%s,fps=%d,setsar=1" % (head, chain, fps)


# How loud the narration has to get before the bed gets out of its way. The
# first attempt used 0.02, which a normalised voice track is over essentially
# all the time -- the bed was ducked from -34 dBFS to -47 and could not be
# heard at all, in the gaps or anywhere else. At 0.15 only actual speech
# triggers it, and the bed comes back between the lines, which is the entire
# point of ducking rather than just turning it down.
DUCK_THRESHOLD = 0.15


def _duck_ratio(duck_db):
    """A compressor ratio that pulls the bed down by about `duck_db`.

    sidechaincompress is set by ratio, not by an amount, so the style's "how
    much quieter while someone is talking" has to become one. Measured against
    a real narration at this threshold, 1 + |duck|/3 lands close: -7 dB asked
    gives a ratio of 3.3 and a measured 4.9 dB of ducking. Approximate on
    purpose -- the exact figure depends on the programme, and what matters is
    that the voice stays on top and the music returns in the gaps.
    """
    return max(1.5, min(20.0, 1.0 + abs(float(duck_db)) / 3.0))


def _make_bed(look, seconds, tmp, progress):
    """A music bed for this render, or None if the style does not want one."""
    want = look.get("music") or {}
    if not want.get("enabled"):
        return None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import music as music_mod
        return music_mod.make(seconds + 0.5, Path(tmp) / "bed.wav",
                              mood=str(want.get("mood") or "grief"))
    except Exception as exc:
        # A missing bed is a quieter video, not a failed render.
        progress("  note: no music bed (%s) — the narration carries it alone"
                 % str(exc)[:70])
        return None


def _source_size(beat):
    """(width, height) of a beat's clip, or (None, None) if it cannot be read.

    Used to decide crop vs blur. Unreadable dimensions fall back to cropping,
    which is the old behaviour -- a reframing choice is not worth failing a
    render over.
    """
    try:
        got = dimensions(beat.get("_path") or beat.get("clip"))
        return got.get("width"), got.get("height")
    except Exception:
        return None, None


def _ass_time(seconds):
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return "%d:%02d:%05.2f" % (h, m, s)


def _ass_escape(text):
    return (str(text).replace("\\", "\\\\").replace("{", "(").replace("}", ")")
            .replace("\n", "\\N"))


# Mean advance width of a bold sans glyph as a fraction of the font size.
# Calibrated against rendered output: at size 103px in a 907px column this
# predicts 17 characters a line, and "In 2008 a team built an entire film to
# break their own software." (64 chars) came out as the 4 lines it predicts.
CHAR_ADVANCE = 0.52


def caption_line_count(text, look, width, height):
    """How many lines this caption will wrap to, near enough to catch a wall.

    Not exact -- it cannot be without the font metrics -- but a caption that
    overflows its band covers the picture, and finding that out by watching the
    finished video is the expensive way.
    """
    caps = look["captions"]
    text = (text or "").strip()
    if not text:
        return 0
    size = max(12, int(height * caps["size_pct"] / 100.0))
    column = width * (1.0 - 2.0 * caps["side_margin_pct"] / 100.0)
    per_line = max(6, int(column / (CHAR_ADVANCE * size)))
    # greedy wrap on whole words, which is what WrapStyle 0 does
    lines, current = 1, 0
    for word in text.split():
        need = len(word) + (1 if current else 0)
        if current + need > per_line and current:
            lines += 1
            current = len(word)
        else:
            current += need
    return lines


def overlong_captions(plan, look=None):
    """Beats whose caption wraps past captions.max_lines: [(index, lines, text)]."""
    look = look or plan.get("_style") or the_style()
    limit = int(look["captions"].get("max_lines", 2) or 0)
    if limit <= 0:
        return []
    # A plan straight off disk carries no width/height -- load_plan injects
    # them from the style. Fall back to the style so this is usable on a raw
    # plan, which is exactly when you want to catch a caption that is too long.
    width = plan.get("width") or look["format"]["width"]
    height = plan.get("height") or look["format"]["height"]
    out = []
    for i, beat in enumerate(plan.get("beats") or []):
        text = (beat.get("caption") or "").strip()
        count = caption_line_count(text, look, width, height)
        if count > limit:
            out.append((i + 1, count, text))
    return out


def _syllables(word):
    """Rough syllable count. Speaking time tracks syllables far better than
    letters: "strengths" is one beat and "areas" is three."""
    cleaned = re.sub(r"[^a-z]", "", word.lower())
    if not cleaned:
        return 1
    # y is counted only as a final syllable after a consonant. Treating it as a
    # plain vowel merges it with its neighbours -- "players" becomes one group
    # ("aye") and so reads as one syllable instead of two.
    count = len(re.findall(r"[aeiou]+", cleaned))
    if cleaned.endswith("y") and len(cleaned) > 1 and cleaned[-2] not in "aeiou":
        count += 1
    if cleaned.endswith("e") and count > 1 and not cleaned.endswith(("le", "ee", "ye")):
        count -= 1                                   # silent final e
    return max(1, count)


def word_timings(caption, start, duration, gap_weight=0.45, measured=None):
    """When each word lands, spread across the beat by how long it takes to say.

    The beat's length is already the measured length of the narration for that
    line, so the words only have to be apportioned within it.

    `measured` is [[word, seconds], ...] from voice.py, which timed each word on
    the engine that is going to say it. When it is present it is used, because
    an estimate from spelling is exactly that. The syllable weighting is the
    fallback for recorded narration or a machine with no engine installed.
    """
    words = [w for w in re.split(r"\s+", (caption or "").strip()) if w]
    if not words or duration <= 0:
        return []
    weights = None
    if measured and len(measured) == len(words):
        got = [float(pair[1]) for pair in measured if len(pair) > 1 and pair[1]]
        if len(got) == len(words) and all(v > 0 for v in got):
            weights = got
    if weights is None:
        weights = [_syllables(w) + gap_weight for w in words]
    total = sum(weights) or 1.0
    out, at = [], 0.0
    for word, weight in zip(words, weights):
        share = duration * weight / total
        out.append((word, start + at, start + min(duration, at + share)))
        at += share
    # absorb rounding into the last word so the line always fills its beat
    last_word, last_start, _ = out[-1]
    out[-1] = (last_word, last_start, start + duration)
    return out


def build_subtitles(plan, path):
    """An .ass file built entirely from the committed style.

    The subtitles filter is used rather than drawtext because it handles
    wrapping and timing, and many ffmpeg builds ship without libfreetype.
    """
    import style as style_mod
    look = plan.get("_style") or the_style()
    caps = look["captions"]
    w, h = plan["width"], plan["height"]
    size = max(12, int(h * caps["size_pct"] / 100.0))
    outline = max(0, int(round(h * caps["outline_pct"] / 100.0)))
    margin_v = int(h * caps["margin_bottom_pct"] / 100.0)
    margin_h = int(w * caps["side_margin_pct"] / 100.0)
    lines = [
        "[Script Info]", "ScriptType: v4.00+",
        "PlayResX: %d" % w, "PlayResY: %d" % h, "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
        "Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Caption,%s,%d,%s,%s,&H80000000,%d,1,%d,1,2,%d,%d,%d,1" % (
            caps["font"], size,
            style_mod.ass_colour(caps["colour"]), style_mod.ass_colour(caps["outline"]),
            -1 if caps.get("bold") else 0, outline, margin_h, margin_h, margin_v),
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    mode = caps.get("mode", "line")
    pop = int(caps.get("pop_ms", 110) or 0)
    hi_fill = style_mod.ass_override_colour(caps.get("highlight") or caps["colour"])
    hi_line = style_mod.ass_override_colour(caps.get("highlight_outline") or caps["outline"])
    at = 0.0
    any_caption = False
    for beat in plan["beats"]:
        caption = (beat.get("caption") or "").strip()
        if caps.get("uppercase"):
            caption = caption.upper()
        if caption:
            any_caption = True
            if mode == "karaoke":
                # The whole line stays on screen and the word being spoken
                # changes colour. Only the colour changes, never the size:
                # scaling a word mid-line re-flows everything after it, and a
                # sentence that twitches on every word is worse than no
                # highlight at all.
                timings = word_timings(caption, at, beat["duration"],
                                       measured=beat.get("words"))
                words = [w for w, _, _ in timings]
                for index, (_word, w_start, w_end) in enumerate(timings):
                    parts = []
                    for j, other in enumerate(words):
                        if j == index:
                            parts.append("{\\c%s\\3c%s}%s{\\r}"
                                         % (hi_fill, hi_line, _ass_escape(other)))
                        else:
                            parts.append(_ass_escape(other))
                    lines.append("Dialogue: 0,%s,%s,Caption,,0,0,0,,%s" % (
                        _ass_time(w_start), _ass_time(w_end), " ".join(parts)))
            elif mode == "word":
                # one word at a time, each snapping up to full size as it is
                # said. Nothing to read ahead of the voice, which is what makes
                # it hold a viewer who arrived by accident.
                for word, w_start, w_end in word_timings(
                        caption, at, beat["duration"], measured=beat.get("words")):
                    # A single word carrying a full stop reads as a typo rather
                    # than as punctuation. ? and ! stay: they carry tone, and a
                    # one-word question without its mark is a different line.
                    word = re.sub(r"[.,;:]+$", "", word) or word
                    effect = ""
                    if pop > 0:
                        effect = ("{\\fscx74\\fscy74\\t(0,%d,\\fscx106\\fscy106)"
                                  "\\t(%d,%d,\\fscx100\\fscy100)}" % (pop, pop, pop * 2))
                    lines.append("Dialogue: 0,%s,%s,Caption,,0,0,0,,%s%s" % (
                        _ass_time(w_start), _ass_time(w_end), effect, _ass_escape(word)))
            else:
                lines.append("Dialogue: 0,%s,%s,Caption,,0,0,0,,%s" % (
                    _ass_time(at), _ass_time(at + beat["duration"]), _ass_escape(caption)))
        at += beat["duration"]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return any_caption


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------
def build(plan_path, output=None, agent=None, keep_temp=False, progress=print):
    plan = load_plan(plan_path)
    base = Path(plan["_base"])
    out = Path(output or plan.get("output") or "out/video.mp4")
    if not out.is_absolute():
        out = base / out
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        raise RenderError("%s already exists — renders never overwrite. Move it "
                          "or choose another name." % out)

    ff = ffmpeg_bin()
    look = plan["_style"]
    w, h, fps = plan["width"], plan["height"], plan["fps"]
    push = float(look["motion"].get("push_in", 0) or 0)
    enc = look["encode"]
    fit = look["format"].get("fit", "auto")
    blur_zoom = float(look["format"].get("blur_zoom", 1.0) or 1.0)
    coverage = float(look["format"].get("min_coverage", 0.64) or 0.64)
    focus_keep = float(look["format"].get("focus_keep", 0.72) or 0.72)
    max_upscale = float(look["format"].get("max_upscale", 1.9) or 1.9)
    min_luma = float(look["format"].get("min_luma", 0.0) or 0.0)
    max_lift = float(look["format"].get("max_lift", 2.2) or 2.2)
    fade, fade_len, fade_extra, fade_at = transition_plan(
        look["transition"].get("kind"), look["transition"].get("seconds"),
        [float(b["duration"]) for b in plan["beats"]], fps)
    alternate = bool(look["motion"].get("alternate", False))
    crf = str(enc["crf"])
    preset = str(enc["preset"])
    reporter = _Reporter(agent, out.name)
    reporter.start(len(plan["beats"]))

    tmp = Path(tempfile.mkdtemp(prefix="render-"))
    try:
        # 1. normalise every beat to identical codec/size/fps so concat is safe
        parts = []
        for i, beat in enumerate(plan["beats"]):
            part = tmp / ("part%03d.mp4" % i)
            src_w, src_h = _source_size(beat)
            crop_to = precrop(src_w, src_h, w, h,
                              focus_window(beat["_path"], beat["in"],
                                           beat["duration"], focus_keep),
                              coverage, max_upscale)
            luma = mean_luma(beat["_path"], beat["in"], beat["duration"]) \
                if min_luma > 0 else None
            lift = lift_filter(luma, min_luma, max_lift)
            progress("  beat %d/%d  %.1fs  %s%s%s" % (
                i + 1, len(plan["beats"]), beat["duration"],
                Path(beat["_path"]).name,
                "  reframed to %d%% of the width" % round(100.0 * crop_to[0] / src_w)
                if crop_to else "",
                "  lifted from luma %d" % round(luma) if lift else ""))
            # The transition eats into the NEXT beat, so this one has to supply
            # the frames for it. tpad holds the last frame if the clip runs out
            # -- a source shorter than its beat plus the overlap would otherwise
            # make a short part, and every offset after it would be wrong.
            # -frames:v, not -t. Input -t rounds up to the next whole frame,
            # so every beat came out 20-50ms long and the picture fell steadily
            # behind the voice. An exact frame count cannot drift.
            frames = max(1, int(round(beat["duration"] * fps))) + fade_extra[i]
            want = frames / float(fps)
            chain = _beat_filter(w, h, fps, push, beat["duration"], fit,
                                 src_w, src_h, blur_zoom=blur_zoom,
                                 push_out=(alternate and i % 2 == 1),
                                 crop_to=crop_to, lift=lift)
            if fade_extra[i]:
                chain += (",tpad=stop_mode=clone:stop_duration=%.4f"
                          % (fade_extra[i] / float(fps)))
            _run([
                ff, "-hide_banner", "-loglevel", "error", "-y",
                "-ss", "%.3f" % beat["in"], "-t", "%.3f" % (want + 0.2),
                "-i", beat["_path"], "-vf", chain,
                "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", crf,
                "-pix_fmt", "yuv420p", "-frames:v", str(frames), str(part),
            ], "trimming beat %d" % (i + 1))
            got = probe(part)
            if not got["video"]:
                raise RenderError("beat %d produced no video — is %s really a video file?"
                                  % (i + 1, beat["_path"]))
            # A clip with less material than its beat needs makes a short part,
            # and a short part is not a small problem: the xfade offsets are
            # computed from the planned lengths, so one of them lands past the
            # end of its input and the whole chain collapses. Measured once:
            # four beats a few frames short took a 59.2s video to 50.2s. Catch
            # it here, where the beat and its clip can be named.
            short_by = want - got["duration"]
            if short_by > 1.5 / float(fps):
                have = 0.0
                try:
                    have = probe(beat["_path"])["duration"] - float(beat["in"])
                except Exception:
                    pass
                raise RenderError(
                    "beat %d came out %.2fs, not %.2fs. Its line needs %.2fs%s "
                    "and %s only has %.2fs after its in-point of %.2fs. Cut a "
                    "longer clip for this beat, or shorten the line."
                    % (i + 1, got["duration"], want, beat["duration"],
                       " plus %.2fs of transition overlap"
                       % (fade_extra[i] / float(fps)) if fade_extra[i] else "",
                       Path(beat["_path"]).name, have, float(beat["in"])))
            parts.append(part)

        # 2. join them, with transitions if the style asks for any
        silent = tmp / "silent.mp4"
        if fade:
            progress("  joining %d beats with a %.0fms %s"
                     % (len(parts), fade_len * 1000,
                        look["transition"].get("kind")))
            graph, out_label = xfade_graph(fade, fade_len, fade_at, len(parts))
            args = [ff, "-hide_banner", "-loglevel", "error", "-y"]
            for part in parts:
                args += ["-i", str(part)]
            _run(args + ["-filter_complex", graph, "-map", "[%s]" % out_label,
                         "-c:v", "libx264", "-preset", "veryfast", "-crf", crf,
                         "-pix_fmt", "yuv420p", str(silent)],
                 "joining the beats")
        else:
            listing = tmp / "parts.txt"
            listing.write_text("".join("file '%s'\n" % p for p in parts),
                               encoding="utf-8")
            progress("  joining %d beats" % len(parts))
            _run([ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
                  "-safe", "0", "-i", str(listing), "-c", "copy", str(silent)],
                 "joining the beats")

        # 3. captions + audio in one finishing pass
        subs = tmp / "captions.ass"
        for index, count, text in overlong_captions(plan, look):
            progress("  note: beat %d's caption wraps to %d lines and will cover "
                     "the picture — shorten it to %d. \"%s\""
                     % (index, count, look["captions"].get("max_lines", 2),
                        text[:58] + ("..." if len(text) > 58 else "")))
        has_captions = build_subtitles(plan, subs)
        args = [ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(silent)]
        audio = plan.get("audio")
        if audio:
            args += ["-i", audio["_path"]]
        bed = _make_bed(look, probe(silent)["duration"], tmp, progress) if audio else None
        if bed:
            args += ["-i", str(bed)]
        if has_captions:
            escaped = str(subs).replace("\\", "/").replace(":", r"\:")
            args += ["-vf", "subtitles='%s'" % escaped]
            progress("  burning in captions")
        args += ["-c:v", "libx264", "-preset", preset, "-crf", crf,
                 "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        if audio:
            # The video is the reference length; pad the track and cut the whole
            # output to it. Not -shortest: with apad feeding it unbounded audio
            # it overshoots by however much sits in the buffer -- measured at
            # 6.55s against a 5.80s video. That drift hid inside the 10%
            # tolerance for as long as the gaps between lines were loose, and
            # surfaced the moment they were tightened. An explicit -t cannot
            # drift.
            args += ["-c:a", "aac",
                     "-b:a", "%dk" % int(enc.get("audio_kbps", 160)),
                     "-ar", str(int(enc.get("audio_rate", 48000))),
                     "-ac", str(int(enc.get("audio_channels", 2)))]
            if bed:
                # The bed ducks itself out of the way: the narration is the
                # sidechain key, so the music drops while a line is running and
                # comes back in the gaps. Set by ear-independent numbers in the
                # style rather than by riding a fader.
                music = look.get("music") or {}
                args += ["-filter_complex",
                         "[1:a]apad,atrim=0:%.3f,asplit=2[vv][vk];"
                         "[2:a]volume=%.1fdB[bedq];"
                         "[bedq][vk]sidechaincompress=threshold=%.3f:ratio=%.2f"
                         ":attack=12:release=420[duck];"
                         "[vv][duck]amix=inputs=2:duration=first:normalize=0[aout]"
                         % (probe(silent)["duration"],
                            float(music.get("gain_db", -19.0)),
                            DUCK_THRESHOLD,
                            _duck_ratio(float(music.get("duck_db", -7.0)))),
                         "-map", "0:v:0", "-map", "[aout]",
                         "-t", "%.3f" % probe(silent)["duration"]]
                progress("  laying the voice over a %s bed"
                         % (music.get("mood") or "grief"))
            else:
                args += ["-map", "0:v:0", "-map", "1:a:0",
                         "-af", "apad", "-t", "%.3f" % probe(silent)["duration"]]
                progress("  laying the voice track")
        else:
            args += ["-an"]
        args.append(str(out))
        progress("  encoding")
        _run(args, "encoding the final file")

        # 4. never report success without checking
        got = probe(out)
        expected = plan["_total"]
        drift = abs(got["duration"] - expected) / expected if expected else 0
        problems = []
        if not got["video"]:
            problems.append("no video stream")
        if audio and not got["audio"]:
            problems.append("a voice track was given but the output has no audio")
        if drift > 0.10:
            problems.append("duration is %.1fs, expected %.1fs (%.0f%% out)"
                            % (got["duration"], expected, drift * 100))
        if got["size"] < 1024:
            problems.append("only %d bytes" % got["size"])
        if problems:
            out.unlink(missing_ok=True)
            raise RenderError("the render came out wrong, so it was deleted rather "
                              "than passed on: " + "; ".join(problems))

        result = {"output": str(out), "duration": round(got["duration"], 2),
                  "beats": len(parts), "size": got["size"],
                  "resolution": "%dx%d" % (w, h), "audio": got["audio"],
                  "styleVersion": plan["_styleVersion"]}
        reporter.finish(result)
        return result
    except RenderError as exc:
        reporter.fail(str(exc))
        raise
    finally:
        if keep_temp:
            progress("  temp kept at %s" % tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# plan scaffolding from a script
# --------------------------------------------------------------------------
def plan_from_script(script_path, clips_dir, output="out/video.mp4"):
    """Draft a render plan from script.md, for a human or FORGE to fill in."""
    text = Path(script_path).read_text(encoding="utf-8")
    beats = []
    for line in text.splitlines():
        match = re.match(r"\s*(\d+)[.)]\s+(.*\S)", line)
        if match:
            beats.append(match.group(2))
    if not beats:
        beats = [ln.strip("- ").strip() for ln in text.splitlines()
                 if ln.strip().startswith("-")][:8]
    if not beats:
        raise RenderError("could not find numbered beats in %s — write them as "
                          "'1. ...', '2. ...'" % script_path)

    clips = sorted([p for p in Path(clips_dir).glob("*")
                    if p.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm")])
    look = the_style()
    plan = {"output": output, "styleVersion": look.get("version", 0), "beats": []}
    for i, caption in enumerate(beats):
        plan["beats"].append({
            "clip": str(clips[i % len(clips)]) if clips else "REPLACE-with-a-clip.mp4",
            "license": "" if clips else "REQUIRED: where this came from and why you may use it",
            "in": 0.0, "duration": 4.0,
            "caption": caption[:120],
        })
    return plan


class _Reporter:
    """Mirror the render onto the operations dashboard, if status.py is around."""

    def __init__(self, agent_id, label):
        self.agent, self.label, self.mod = agent_id, label, None
        if not agent_id:
            return
        try:
            sys.path.insert(0, str(HERE))
            import status
            self.mod = status
        except Exception:
            print("render.py: status.py not usable — continuing without dashboard reporting",
                  file=sys.stderr)

    def _safe(self, fn):
        if not self.mod:
            return
        try:
            fn()
        except Exception as exc:
            print("render.py: dashboard update failed: %s" % exc, file=sys.stderr)

    def start(self, beats):
        self._safe(lambda: self.mod.start(self.agent, "Rendering %s (%d beats)"
                                          % (self.label, beats)))

    def finish(self, result):
        self._safe(lambda: self.mod.finish(
            self.agent, "%s (%s, %.1fs)" % (Path(result["output"]).name,
                                            result["resolution"], result["duration"])))

    def fail(self, msg):
        self._safe(lambda: self.mod.fail(self.agent, msg[:300]))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(prog="render.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="draft a render plan from script.md")
    p_plan.add_argument("script")
    p_plan.add_argument("--clips", default="assets")
    p_plan.add_argument("-o", "--out", default="render.json")
    p_plan.add_argument("--output", default="out/video.mp4")

    p_build = sub.add_parser("build", help="render the plan into a video file")
    p_build.add_argument("plan")
    p_build.add_argument("-o", "--output")
    p_build.add_argument("--agent", help="report to this dashboard agent id")
    p_build.add_argument("--keep-temp", action="store_true")

    p_rights = sub.add_parser("rights", help="the copyright position of a plan")
    p_rights.add_argument("plan")

    p_ret = sub.add_parser("retention", help="does this cut match what the feed rewards?")
    p_ret.add_argument("plan")

    p_nar = sub.add_parser("narration", help="is the script varied enough to listen to?")
    p_nar.add_argument("plan")

    p_mon = sub.add_parser("monetize", help="exposure under the inauthentic content policy")
    p_mon.add_argument("plan")

    p_fresh = sub.add_parser("fresh", help="is this footage new to the channel?")
    p_fresh.add_argument("plan")
    p_fresh.add_argument("--remember", action="store_true",
                         help="record it as used, so no later video may repeat it")
    p_fresh.add_argument("--forget", metavar="SOURCE",
                         help="drop a source from the ledger and let it be used again")
    p_fresh.add_argument("--used", help="the ledger to read (default: clips_used.json)")

    p_desc = sub.add_parser("describe", help="write the description and rights receipt")
    p_desc.add_argument("plan")
    p_desc.add_argument("--script", help="script.md, so the description opens on the hook")
    p_desc.add_argument("--extra", default="", help="anything else to append")
    p_desc.add_argument("--print", dest="show", action="store_true",
                        help="print the description instead of writing it")

    p_check = sub.add_parser("check", help="probe a finished file")
    p_check.add_argument("video")
    p_check.add_argument("--expect", type=float, help="expected duration in seconds")

    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            plan = plan_from_script(args.script, args.clips, args.output)
            Path(args.out).write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
            print("wrote %s with %d beats — fill in the license on every clip "
                  "before building" % (args.out, len(plan["beats"])))
            return 0
        if args.command == "build":
            result = build(args.plan, args.output, args.agent, args.keep_temp)
            print("\n%s  %s  %.1fs  %.1f MB%s" % (
                result["output"], result["resolution"], result["duration"],
                result["size"] / 1048576, "" if result["audio"] else "  (no audio)"))
            return 0
        if args.command == "fresh":
            book = Path(args.used) if args.used else USED_LEDGER
            if args.forget:
                data = _used_ledger(book)
                spans = [s for s in data["spans"] if s[0] != args.forget]
                dropped = len(data["spans"]) - len(spans)
                data["spans"] = spans
                data["sources"].pop(args.forget, None)
                book.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8")
                print("forgot %s — %d shot%s of it are available again"
                      % (args.forget, dropped, "" if dropped == 1 else "s"))
                return 0
            ok, findings = unused_report(args.plan, used_path=book)
            for level, headline, detail in findings:
                print("%s %s" % ({"ok": "  ok  ", "warn": " warn ",
                                  "fail": " FAIL "}[level], headline))
                if detail:
                    print("         %s" % detail)
            if args.remember:
                if not ok:
                    print("\nNot recording footage that did not pass.", file=sys.stderr)
                    return 1
                kept = remember_used(args.plan, used_path=book)
                print("\nrecorded %d shots of %s"
                      % (kept["shots"], ", ".join(kept["sources"])))
            return 0 if ok else 1

        if args.command == "describe":
            hook = hook_line(args.script) if args.script else ""
            text = description(args.plan, hook=hook, extra=args.extra)
            if args.show:
                print(text, end="")
                return 0
            plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
            out = Path(str(plan.get("output") or ""))
            if not out.name:
                raise RenderError("%s has no output path, so there is nowhere "
                                  "to put the description" % args.plan)
            out.parent.mkdir(parents=True, exist_ok=True)
            desc = out.with_suffix(".description.txt")
            rights = out.with_suffix(".rights.json")
            desc.write_text(text, encoding="utf-8")
            rights.write_text(json.dumps(rights_receipt(args.plan), indent=2,
                                         ensure_ascii=False) + "\n", encoding="utf-8")
            due = credits_due(plan)
            print("wrote %s (%d credit%s) and %s"
                  % (desc.name, len(due), "" if len(due) == 1 else "s", rights.name))
            if not due:
                print("  nothing here is under a licence that obliges a credit")
            return 0

        if args.command == "monetize":
            ok, findings = monetize_report(args.plan)
            for level, headline, detail in findings:
                print("%s %s" % ({"ok": "  ok  ", "warn": " warn ",
                                  "fail": " FAIL "}[level], headline))
                if detail:
                    print("         %s" % detail)
            if not ok:
                print("\nThis would not survive review. Fix the failures.",
                      file=sys.stderr)
                return 1
            print("\nNothing here breaks the policy. It is still a reviewer's "
                  "call, not a check's.")
            return 0
        if args.command == "narration":
            ok, findings = narration_report(args.plan)
            for level, headline, detail in findings:
                print("%s %s" % ({"ok": "  ok  ", "warn": " warn ",
                                  "fail": " FAIL "}[level], headline))
                if detail:
                    print("         %s" % detail)
            if not ok:
                print("\nThe script will read as one long sentence. Rewrite it "
                      "before cutting to it.", file=sys.stderr)
            return 0 if ok else 4
        if args.command == "retention":
            ok, findings = retention_report(args.plan)
            for level, headline, detail in findings:
                print("%s %s" % ({"ok": "  ok  ", "warn": " warn ",
                                  "fail": " FAIL "}[level], headline))
                if detail:
                    print("         %s" % detail)
            warns = sum(1 for level, _, _ in findings if level == "warn")
            if not ok:
                print("\nThe opening will not hold. Fix that before anything else.",
                      file=sys.stderr)
                return 1
            print("\n%s" % ("Cut is shaped for the feed."
                             if not warns else
                             "Shaped for the feed, with %d thing%s to tighten."
                             % (warns, "" if warns == 1 else "s")))
            return 0
        if args.command == "rights":
            ok, findings = rights_report(args.plan)
            for level, headline, detail in findings:
                print("%s %s" % ({"ok": "  ok  ", "warn": " warn ",
                                  "fail": " FAIL "}[level], headline))
                if detail:
                    print("         %s" % detail)
            if not ok:
                print("\nDo not upload this. Fix what is marked FAIL first.",
                      file=sys.stderr)
                return 1
            warns = sum(1 for level, _, _ in findings if level == "warn")
            print("\nClear to upload%s."
                  % (" — read the %d warning%s first"
                     % (warns, "" if warns == 1 else "s") if warns else ""))
            return 0
        got = probe(args.video)
        print(json.dumps(got, indent=2))
        if args.expect and abs(got["duration"] - args.expect) / args.expect > 0.10:
            print("render.py: duration %.1fs is more than 10%% off the expected %.1fs"
                  % (got["duration"], args.expect), file=sys.stderr)
            return 1
        return 0
    except RenderError as exc:
        print("render.py: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
