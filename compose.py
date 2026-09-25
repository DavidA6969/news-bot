#!/usr/bin/env python3
"""Turning a script into things the rest of the pipeline can act on.

Two stages of the flow live here, both of which sat between steps that already
existed and were done by hand:

`keywords`   the script says what the video is about in sentences; the stock
             search wants nouns. This turns one into the other, per beat, so
             each shot is searched for separately rather than the whole video
             being illustrated by one query.

`thumbnail`  a frame plus a title. The frame is chosen rather than taken from
             a fixed offset -- the first frame of a Short is often motion blur,
             and a still with nothing in it is what makes a thumbnail look
             automated.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import spec                                    # noqa: E402


class ComposeError(Exception):
    pass


# Function words plus the narration verbs this house style leans on. A stock
# search for "watch" or "thought" returns nothing anyone wants to look at.
STOPWORDS = frozenset("""
a an the and or but so then than that this these those of to in on at by for
with from into onto over under about after before while during as is are was
were be been being am do does did done has have had having will would can
could shall should may might must not no nor only just very too much many more
most some any each every all both few own same such own it its it's he she they
them his her their you your i me my we us our who whom whose which what when
where why how if because until unless once again further here there
watch look see saw seen thought think knew know nobody everyone someone
happens happened happening moment moments second seconds minute
never always ever still yet almost nearly quite rather even also
get got gets getting go goes going went come comes came make makes made
take takes took put puts start starts started stop stops stopped
turn turns turned keep keeps kept let lets leave leaves left
behind across through between around above below against along beside near
upon inside outside without within toward towards past off out up down back
him himself herself itself themselves one ones thing things way ways
""".split())

# Words that describe the edit rather than the picture. Searching stock for
# these returns stock footage of stock footage.
META = frozenset("""
video clip clips footage shot shots camera screen frame frames caption captions
voice voiceover narration story channel short shorts
""".split())


def _words(text):
    return [w for w in re.findall(r"[a-z][a-z'-]+", str(text or "").lower())]


def keywords_for(line, limit=3):
    """Search terms for one beat, best first.

    Adjacent content words are kept together -- "frozen desert" finds what
    "frozen" and "desert" separately do not -- and the order of the line is
    preserved, because the subject of a sentence is usually the thing worth
    filming.
    """
    kept, run, runs = [], [], []
    for word in _words(line):
        if word in STOPWORDS or word in META or len(word) < 3:
            if run:
                runs.append(run)
                run = []
            continue
        run.append(word)
    if run:
        runs.append(run)
    for group in runs:
        # Pairs first: a two-word phrase is a better search than either half.
        for i in range(len(group) - 1):
            kept.append("%s %s" % (group[i], group[i + 1]))
        kept.extend(group)
    seen, out = set(), []
    for term in kept:
        if term not in seen:
            seen.add(term)
            out.append(term)
        if len(out) >= limit:
            break
    return out


def keywords(script_path, limit=3):
    """One search per beat, in beat order."""
    sys.path.insert(0, str(HERE))
    import voice as voice_mod
    lines = voice_mod.beats_from_script(script_path)
    out = []
    for index, line in enumerate(lines, 1):
        terms = keywords_for(line, limit)
        out.append({"beat": index, "line": line, "terms": terms,
                    "query": terms[0] if terms else ""})
    return out


def _frame_scores(video, seconds, samples=30):
    """How much is going on in each sampled frame, as (time, score).

    Score is the spread of brightness across the frame. A flat frame -- a wall,
    a blur, a cut to black -- scores low; a frame with a subject in it scores
    high. It is a crude proxy for "is there anything to look at", and it is a
    great deal better than taking whatever sits at 0.5s.
    """
    try:
        import numpy as np
    except ImportError:
        return []
    w, h = 64, 114
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-t", "%.3f" % seconds, "-i", str(video),
         "-vf", "fps=%d/%.3f,format=gray,scale=%d:%d" % (samples, seconds, w, h),
         "-frames:v", str(samples), "-f", "rawvideo", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    raw = proc.stdout or b""
    got = len(raw) // (w * h)
    if not got:
        return []
    grid = np.frombuffer(raw[:got * w * h], dtype=np.uint8).reshape(got, h, w)
    grid = grid.astype("float32")
    spread = grid.std(axis=(1, 2))
    # Edge energy too, so a smooth gradient does not beat a face.
    edges = np.abs(np.diff(grid, axis=2)).mean(axis=(1, 2))
    score = spread + edges * 2.0
    step = seconds / float(got)
    return [(round(i * step, 3), float(score[i])) for i in range(got)]


IMAGES = {".png", ".jpg", ".jpeg", ".webp"}


def thumbnail(video, title, out_path, seconds=None):
    """A still worth clicking, with the title on it.

    `video` may be a still already. It should be: a frame taken out of the
    FINISHED video carries the burned-in captions, so the thumbnail title lands
    on top of a caption and both become unreadable. `render.build` writes a
    clean still beside its output for exactly this, and that is what to pass.
    """
    video = Path(video)
    if not video.exists():
        raise ComposeError("no video at %s" % video)
    title = str(title or "").strip()
    cap = spec.get("thumbnail.title_max_chars")
    if len(title) > cap:
        raise ComposeError("thumbnail title is %d characters, the limit is %d: %r"
                           % (len(title), cap, title))
    if video.suffix.lower() in IMAGES:
        scored, at = [], 0.0
    else:
        window = float(seconds or spec.get("thumbnail.pick_from_first_seconds"))
        scored = _frame_scores(video, window)
        at = max(scored, key=lambda p: p[1])[0] if scored else window / 2.0

    sys.path.insert(0, str(HERE))
    import style as style_mod
    look = style_mod.current()
    caps = look["captions"]
    height = spec.get("thumbnail.height")
    width = spec.get("thumbnail.width")
    size = max(24, int(height * float(caps["size_pct"]) / 100.0))
    # The caption size is chosen for one to three words. A forty-character
    # title at that size runs off both edges, so it is shrunk to fit the same
    # safe width the captions respect -- roughly 0.52em per character in a
    # heavy sans, which is close enough to keep it inside the frame.
    usable = width - 2 * spec.get("captions.safe_zone.x")[0]
    if title:
        size = min(size, max(24, int(usable / (0.52 * len(title)))))
    outline = max(2, int(round(height * float(caps["outline_pct"]) / 100.0)))
    # drawtext needs its own escaping, and a title with a colon or an
    # apostrophe in it is not unusual.
    safe = (title.replace("\\", "\\\\").replace(":", r"\:")
            .replace("'", r"\\\'").replace("%", r"\%"))
    chain = ("drawtext=text='%s':fontcolor=white:fontsize=%d:borderw=%d:"
             "bordercolor=black:x=(w-text_w)/2:y=h*0.62:line_spacing=12"
             % (safe, size, outline))
    args = ["ffmpeg", "-v", "error", "-y"]
    if at:
        args += ["-ss", "%.3f" % at]
    args += ["-i", str(video), "-frames:v", "1", "-vf", chain, str(out_path)]
    subprocess.run(args, check=True)
    return {"path": str(out_path), "picked_at": at,
            "considered": len(scored), "title": title}


def best_still(video, out_path, seconds=None):
    """The most interesting frame of the first few seconds, written out clean."""
    window = float(seconds or spec.get("thumbnail.pick_from_first_seconds"))
    scored = _frame_scores(video, window)
    at = max(scored, key=lambda p: p[1])[0] if scored else window / 2.0
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "%.3f" % at,
                    "-i", str(video), "-frames:v", "1", str(out_path)], check=True)
    return {"path": str(out_path), "picked_at": at, "considered": len(scored)}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="compose.py", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)

    p_k = sub.add_parser("keywords", help="search terms per beat, from the script")
    p_k.add_argument("script")
    p_k.add_argument("--limit", type=int, default=3)
    p_k.add_argument("--json", action="store_true")

    p_t = sub.add_parser("thumbnail", help="pick a frame and put the title on it")
    p_t.add_argument("video")
    p_t.add_argument("--title", required=True)
    p_t.add_argument("-o", "--out", default="thumbnail.png")

    args = ap.parse_args(argv)
    try:
        if args.command == "keywords":
            got = keywords(args.script, args.limit)
            if args.json:
                print(json.dumps(got, indent=2, ensure_ascii=False))
            else:
                for row in got:
                    print("%2d  %-46s %s" % (row["beat"], row["line"][:46],
                                             ", ".join(row["terms"]) or "(nothing concrete)"))
            return 0
        got = thumbnail(args.video, args.title, args.out)
        print("%s — frame at %.2fs of %d considered"
              % (got["path"], got["picked_at"], got["considered"]))
        return 0
    except (ComposeError, OSError, subprocess.CalledProcessError) as exc:
        print("compose.py: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
