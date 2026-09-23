#!/usr/bin/env python3
"""One editing style, used by every video.

A channel is recognised before it is read. If the captions move, the framing
changes or the pacing wanders between uploads, every video arrives looking like
someone else made it, and the viewer never builds the habit. So the look lives
here, in one committed file, and `render.py` takes every visual decision from
it. A render plan supplies clips, timings and words — never styling.

    python3 style.py init            # write style.json with the default look
    python3 style.py show
    python3 style.py check render.json
    python3 style.py set captions.uppercase true
    python3 style.py history
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["load", "current", "init", "set_field", "check_plan", "shorts_verdict",
           "ass_colour", "ass_override_colour", "StyleError", "DEFAULT_STYLE"]

HERE = Path(__file__).resolve().parent
STORE = HERE / "style.json"

DEFAULT_STYLE = {
    "name": "vertical-caption",
    # `fit` decides what happens to footage that is not already this shape.
    # "crop" fills the frame by cutting the sides off, which is right for
    # vertical and square sources and destroys a landscape composition --
    # centre-cropping 16:9 to 9:16 throws away two thirds of the width, which
    # is how you end up with half a title card on screen. "blur" keeps the
    # whole frame and fills above and below with a blurred copy of it.
    # "auto" crops when the shapes are close and blurs when they are not.
    # blur_zoom is how much wider than the frame the visible strip is scaled
    # when fitting by blur. 1.0 shows the whole source frame but leaves it small
    # on a phone; above 1.0 trades a little off the sides for a bigger picture.
    "format": {"width": 1080, "height": 1920, "fps": 30, "fit": "auto",
               "blur_zoom": 1.2,
               # How much of the vertical frame the real picture should fill
               # before blurred fill takes over. A whole 16:9 frame dropped
               # into 9:16 fills 32% of it, which reads as a small window
               # rather than a Short; cropping the source down to the band its
               # detail actually sits in gets the picture back up to this.
               "min_coverage": 0.64,
               # The share of a frame's detail that band has to contain. Higher
               # keeps more of the shot and crops less.
               "focus_keep": 0.72,
               # How far the source may be blown up to fill the frame. A 2.35:1
               # film would need 2.2x to fill a vertical one; soft is worse
               # than small, so the crop stops here and fill covers the rest.
               "max_upscale": 1.9,
               # A phone in daylight is not a grading suite. Footage cut from a
               # film swings from a 216-mean desert to a 15-mean cave, and the
               # dark end goes to a black rectangle on the device most people
               # watch on. Any beat measuring below this gets a gamma lift
               # towards it -- enough to read, not enough to stop being night.
               "min_luma": 52.0,
               # How far that lift may go. Past this the grain comes up with
               # the picture and it looks worse than it did dark.
               "max_lift": 2.2},
    "captions": {
        "font": "DejaVu Sans",
        "size_pct": 3.6,             # of frame height
        "bold": True,
        "uppercase": False,
        "colour": "FFFFFF",          # RGB hex
        "outline": "000000",
        "outline_pct": 0.28,         # of frame height
        "margin_bottom_pct": 14.0,
        "side_margin_pct": 8.0,
        # "karaoke" keeps the whole line on screen and lights up each word as
        # it is spoken: the sentence still reads as a sentence, and the eye is
        # told where the voice is. "word" shows one word at a time and nothing
        # else. "line" shows the line for the length of its beat and does not
        # track the voice at all.
        "mode": "karaoke",
        "highlight": "FFD23F",        # the word currently being said
        "highlight_outline": "1A1200",
        "pop_ms": 110,                # "word" mode only: how fast a word snaps in
        # A caption that wraps past this covers the picture it is captioning.
        # Two lines is the working limit for a Short.
        "max_lines": 2,
    },
    # a slow push on every clip: subtle, but it is what stops stock footage
    # reading as a slideshow. 0 turns it off.
    # `alternate` flips the push direction every other beat, so a run of cuts
    # does not read as one long creep in a single direction.
    "motion": {"push_in": 0.10, "alternate": True},
    "transition": {"kind": "cut", "seconds": 0.0},
    "pacing": {"min_beat_seconds": 1.5, "max_beat_seconds": 7.0},
    # What the Shorts feed rewards, as numbers rather than folklore. A viewer
    # re-asks "is this worth continuing?" every second or two, so a beat that
    # outlasts that is where they leave. hook_seconds is the swipe window:
    # roughly 1.5-2s to interrupt a thumb that is already moving.
    # A script can pass every timing check and still be a slog, because the
    # thing that makes narration tiring is not its pace. Seven of twelve beats
    # opening with the same word reads as one long sentence however well it is
    # cut.
    "narration": {
        "max_same_opening": 3,      # beats that may begin with the same word
        "min_word_variety": 0.55,   # distinct words over total words
        "repeat_lines_allowed": 1,  # the loop line, said twice, and nothing else
        # The share of beats that run on from, or into, the one beside them.
        # A script written one self-contained sentence per beat stops being
        # narration and becomes a caption track -- "Desert. Bamboo. Gone." is
        # a list of what is on screen, not somebody telling you a story. Prose
        # split at clause boundaries instead lets the voice carry across the
        # cuts, which is what every narrated Short actually does.
        "min_flow": 0.35,
    },
    # Silence under a narration is the cheapest thing that makes a video feel
    # thin. The bed is synthesised by music.py rather than licensed, because a
    # Content ID claim on the audio takes the revenue off a video whose
    # pictures were cleared specifically to avoid that.
    "music": {
        "enabled": True,
        "mood": "grief",            # music.py moods
        "gain_db": -21.0,           # how far under the narration it sits
        "duck_db": -7.0,            # how much further while a line is running
    },
    "retention": {
        "hook_seconds": 2.0,
        "beat_target_seconds": 2.0,
        "beat_ceiling_seconds": 2.6,
        "total_target_seconds": 30.0,
        "require_loop": True,
    },
    # The Shorts envelope. Vertical or square and 3 minutes or less is
    # automatically treated as a Short; one second over and YouTube files it as
    # an ordinary video instead, which is not what this channel publishes.
    "shorts": {"max_seconds": 180, "target_seconds": 45, "require_vertical": True},
    # 48kHz stereo AAC is what YouTube asks for, and low-rate mono is worse
    # than non-standard: plenty of players and inline previews simply play
    # nothing, which looks exactly like a video with no voice on it.
    "encode": {"crf": 20, "preset": "medium",
               "audio_rate": 48000, "audio_channels": 2, "audio_kbps": 160},
    # How the narration is spoken and treated. This lives in the style for the
    # same reason the captions do: a channel is recognised by its voice before
    # it is recognised by its edit, and a voice that changes level or timbre
    # between uploads never becomes recognisable. The mastering chain is what
    # separates narration that sounds produced from narration that sounds
    # pasted on, and it applies to a recorded voice as much as a synthesised
    # one.
    "voice": {
        "engine_voice": "en-gb-x-rp",   # espeak-ng voice; ignored by other engines
        "pico_language": "en-GB",       # pico2wave voice; ignored by other engines
        "words_per_minute": 160,        # honoured on every engine, by measurement
        "pitch": 45,                    # 0-99; lower reads as more assured
        "word_gap_ms": 8,               # a little air between words
        # Silence is cut off both ends of every line before it is used. Engines
        # leave a third of a second at the front; left in, a quarter of a Short
        # is nothing at all.
        "silence_floor_db": -45,
        "keep_head_ms": 25,             # left at the head so no consonant clips
        "gap_seconds": 0.09,            # breath between lines, not a pause
        # A full stop inside a line. Kokoro gives one 0.06s -- the same as a
        # comma -- so voice.py speaks the line in two takes and puts this
        # much silence between them itself.
        "sentence_pause_seconds": 0.30,
        "highpass_hz": 85,              # cut rumble below the voice
        "lowpass_hz": 8500,             # take the fizz off synthesised speech
        # Kokoro, when KOKORO_MODEL and KOKORO_VOICES point at local weights.
        # Neural, offline and free -- the best voice here that costs nothing.
        "kokoro_voice": "am_michael",
        "kokoro_language": "en-us",
        "kokoro_speed": 1.0,
        # ElevenLabs, when ELEVENLABS_API_KEY is set. Opt-in: it is the best
        # sounding option and the only one that needs the network and a card.
        "elevenlabs_voice_id": "",      # blank uses their default voice
        "elevenlabs_model": "eleven_multilingual_v2",
        "elevenlabs_stability": 0.45,
        "elevenlabs_similarity": 0.75,
        "compress": True,               # even out the level line to line
        "loudness_lufs": -16.0,         # consistent level against the footage
    },
}

_NUMERIC = {
    "format.width": (240, 4320), "format.height": (240, 4320), "format.fps": (12, 60),
    "captions.size_pct": (1.0, 12.0), "captions.outline_pct": (0.0, 2.0),
    "captions.margin_bottom_pct": (0.0, 60.0), "captions.side_margin_pct": (0.0, 30.0),
    "captions.pop_ms": (0, 600), "captions.max_lines": (0, 6),
    "format.blur_zoom": (1.0, 2.0),
    "format.min_coverage": (0.3, 1.0), "format.focus_keep": (0.3, 1.0),
    "format.max_upscale": (1.0, 4.0),
    "format.min_luma": (0.0, 160.0), "format.max_lift": (1.0, 4.0),
    "motion.push_in": (0.0, 0.6), "transition.seconds": (0.0, 2.0),
    "pacing.min_beat_seconds": (0.3, 30.0), "pacing.max_beat_seconds": (1.0, 120.0),
    "encode.crf": (14, 34), "encode.audio_rate": (8000, 48000),
    "encode.audio_channels": (1, 2), "encode.audio_kbps": (48, 320),
    "shorts.max_seconds": (1.0, 180.0), "shorts.target_seconds": (1.0, 180.0),
    "voice.words_per_minute": (80, 300), "voice.pitch": (0, 99),
    "voice.word_gap_ms": (0, 200), "voice.highpass_hz": (20, 300),
    "voice.elevenlabs_stability": (0.0, 1.0), "voice.elevenlabs_similarity": (0.0, 1.0),
    "music.gain_db": (-60.0, 0.0), "music.duck_db": (-30.0, 0.0),
    "narration.max_same_opening": (1, 20), "narration.min_word_variety": (0.1, 1.0),
    "narration.min_flow": (0.0, 1.0),
    "narration.repeat_lines_allowed": (0, 10),
    "retention.hook_seconds": (0.5, 6.0),
    "retention.beat_target_seconds": (0.5, 10.0),
    "retention.beat_ceiling_seconds": (0.5, 12.0),
    "retention.total_target_seconds": (5.0, 180.0),
    "voice.kokoro_speed": (0.5, 2.0), "voice.silence_floor_db": (-70, -20),
    "voice.keep_head_ms": (0, 200), "voice.gap_seconds": (0.0, 1.0),
    "voice.sentence_pause_seconds": (0.0, 1.5),
    "voice.lowpass_hz": (3000, 20000), "voice.loudness_lufs": (-30.0, -8.0),
}
# The same names render.TRANSITIONS knows. They live here too rather than being
# imported, because render.py imports this module and not the other way round;
# a test asserts the two lists have not drifted apart.
TRANSITION_KINDS = ("cut", "crossfade", "dissolve", "dip", "white", "wipe", "slide")
STYLED_KEYS = ("width", "height", "fps", "font", "caption_size", "colour", "color")


class StyleError(RuntimeError):
    """The style could not be read or changed."""


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fill_defaults(style):
    """Add sections and keys a newer version of this file introduced.

    A style.json written by an older version has no `voice` section. Refusing
    to load it would mean every style file has to be rewritten whenever the
    default gains a setting, so missing keys take the default and everything
    already committed is left exactly as it is.
    """
    if not isinstance(style, dict):
        return style
    for section, defaults in DEFAULT_STYLE.items():
        if not isinstance(defaults, dict):
            continue
        have = style.get(section)
        if not isinstance(have, dict):
            style[section] = json.loads(json.dumps(defaults))
            continue
        for key, value in defaults.items():
            have.setdefault(key, value)
    return style


def _validate(style):
    if not isinstance(style, dict):
        raise StyleError("the style must be a JSON object")
    _fill_defaults(style)
    for section in ("format", "captions", "motion", "transition", "pacing",
                    "encode", "shorts", "voice", "retention", "narration",
                    "music"):
        if not isinstance(style.get(section), dict):
            raise StyleError('style is missing the "%s" section' % section)
    for dotted, (low, high) in _NUMERIC.items():
        section, key = dotted.split(".")
        value = style[section].get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise StyleError("%s must be a number (got %r)" % (dotted, value))
        if not low <= value <= high:
            raise StyleError("%s is %g; it must be between %g and %g" % (dotted, value, low, high))
    mode = style["captions"].get("mode", "line")
    if mode not in ("line", "word", "karaoke"):
        raise StyleError('captions.mode must be "karaoke", "word" or "line" '
                         '(got %r)' % mode)
    for hex_key in ("highlight", "highlight_outline"):
        value = str(style["captions"].get(hex_key, "") or "")
        if value and (len(value) != 6 or
                      any(c not in "0123456789abcdefABCDEF" for c in value)):
            raise StyleError("captions.%s must be 6 hex digits like FFD23F (got %r)"
                             % (hex_key, value))
    fit = style["format"].get("fit", "auto")
    if fit not in ("auto", "crop", "blur"):
        raise StyleError('format.fit must be "auto", "crop" or "blur" (got %r)' % fit)
    kind = style["transition"].get("kind")
    if kind not in TRANSITION_KINDS:
        raise StyleError("transition.kind must be one of %s (got %r)"
                         % (", ".join(sorted(TRANSITION_KINDS)), kind))
    if kind != "cut" and style["transition"]["seconds"] <= 0:
        raise StyleError("a %s needs transition.seconds above 0" % kind)
    if style["pacing"]["min_beat_seconds"] >= style["pacing"]["max_beat_seconds"]:
        raise StyleError("pacing.min_beat_seconds must be below max_beat_seconds")
    if style["shorts"]["target_seconds"] > style["shorts"]["max_seconds"]:
        raise StyleError("shorts.target_seconds cannot exceed shorts.max_seconds")
    if style["shorts"].get("require_vertical") and \
            style["format"]["height"] < style["format"]["width"]:
        raise StyleError(
            "shorts.require_vertical is on but the format is %dx%d, which is "
            "landscape. YouTube files only vertical or square video as a Short."
            % (style["format"]["width"], style["format"]["height"]))
    for hex_key in ("colour", "outline"):
        value = str(style["captions"].get(hex_key, ""))
        if len(value) != 6 or any(c not in "0123456789abcdefABCDEF" for c in value):
            raise StyleError("captions.%s must be 6 hex digits like FFFFFF (got %r)"
                             % (hex_key, value))
    return style


def load(path=None):
    path = Path(path) if path else STORE
    if not path.exists():
        return {"style": None, "history": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StyleError("%s is not valid JSON (%s)" % (path, exc)) from exc
    if not isinstance(data, dict):
        raise StyleError("%s must contain a JSON object" % path)
    data.setdefault("history", [])
    if data.get("style") is not None:
        _validate(data["style"])
    return data


def current(path=None):
    """The committed style, or the default if none has been written yet."""
    got = load(path).get("style")
    if got:
        return got
    fallback = json.loads(json.dumps(DEFAULT_STYLE))
    fallback["version"] = 0
    fallback["_default"] = True
    return fallback


def _save(data, path=None):
    path = Path(path) if path else STORE
    tmp = path.with_name(path.name + ".tmp.%d" % os.getpid())
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def init(path=None, force=False):
    data = load(path)
    if data.get("style") and not force:
        raise StyleError("a style is already committed. Change one field with "
                         "style.py set, rather than starting again — the point of "
                         "this file is that the look stays put.")
    style = json.loads(json.dumps(DEFAULT_STYLE))
    style["version"] = 1
    style["committedAt"] = _now()
    _validate(style)
    data["style"] = style
    data["history"] = data.get("history", []) + [
        {"at": _now(), "action": "init", "version": 1}]
    _save(data, path)
    return style


def _coerce(raw):
    lowered = str(raw).strip()
    if lowered.lower() in ("true", "false"):
        return lowered.lower() == "true"
    try:
        return int(lowered)
    except ValueError:
        pass
    try:
        return float(lowered)
    except ValueError:
        pass
    return lowered


def set_field(dotted, raw_value, path=None):
    """Change one field. Records the change and bumps the version."""
    data = load(path)
    style = data.get("style")
    if not style:
        raise StyleError("no style committed yet — run: python3 style.py init")
    if dotted.count(".") != 1:
        raise StyleError("name the field as section.key, e.g. captions.uppercase")
    section, key = dotted.split(".")
    if section not in style or not isinstance(style[section], dict):
        raise StyleError("unknown section %r. Sections: %s"
                         % (section, ", ".join(k for k, v in style.items()
                                                if isinstance(v, dict))))
    if key not in style[section]:
        raise StyleError("unknown field %r in %s. Fields: %s"
                         % (key, section, ", ".join(style[section])))
    before = style[section][key]
    style[section][key] = _coerce(raw_value)
    try:
        _validate(style)
    except StyleError:
        style[section][key] = before
        raise
    style["version"] = int(style.get("version", 1)) + 1
    style["committedAt"] = _now()
    data["history"].append({"at": _now(), "action": "set", "field": dotted,
                            "from": before, "to": style[section][key],
                            "version": style["version"]})
    _save(data, path)
    return style


def upgrade(path=None):
    """Write out sections and fields a newer version introduced.

    `load` already fills these in memory, so a video renders correctly either
    way. This is about the record: a render stamps the styleVersion it used, and
    a file claiming to be that version should actually describe the look --
    including, now, how the channel sounds. Returns the list of fields added.
    """
    data = load(path)
    style = data.get("style")
    if not style:
        raise StyleError("no style committed yet — run: python3 style.py init")
    on_disk = json.loads((Path(path) if path else STORE).read_text(encoding="utf-8"))
    had = on_disk.get("style") or {}
    added = []
    for section, defaults in DEFAULT_STYLE.items():
        if not isinstance(defaults, dict):
            continue
        for key in defaults:
            if key not in (had.get(section) or {}):
                added.append("%s.%s" % (section, key))
    if not added:
        return []
    style["version"] = int(style.get("version", 1)) + 1
    style["committedAt"] = _now()
    data["history"].append({"at": _now(), "action": "upgrade", "added": added,
                            "version": style["version"]})
    _save(data, path)
    return added


def shorts_verdict(seconds, width, height, style=None):
    """Would YouTube treat this as a Short? Returns (ok, [reasons])."""
    style = style or current()
    limits = style["shorts"]
    reasons = []
    if limits.get("require_vertical") and height is not None and width is not None:
        if height < width:                       # square is fine; landscape is not
            reasons.append(
                "%dx%d is not vertical or square. YouTube files only vertical and "
                "square video as a Short; landscape becomes an ordinary video."
                % (width, height))
    if seconds is not None and seconds > limits["max_seconds"] + 0.05:
        reasons.append(
            "%.1fs is over the %.0fs Shorts limit. One second over and YouTube "
            "files it as an ordinary video, not a Short."
            % (seconds, limits["max_seconds"]))
    return (not reasons), reasons


def check_plan(plan, style=None):
    """Complain about anything in a render plan that tries to set the look."""
    style = style or current()
    problems, notes = [], []
    if not isinstance(plan, dict):
        raise StyleError("that does not look like a render plan")
    for key in STYLED_KEYS:
        if key in plan:
            problems.append('the plan sets "%s"; the style governs that. Remove it.' % key)
    for i, beat in enumerate(plan.get("beats") or []):
        if not isinstance(beat, dict):
            continue
        for key in STYLED_KEYS:
            if key in beat:
                problems.append('beats[%d] sets "%s"; the style governs that.' % (i, key))
        caption = (beat.get("caption") or "").strip()
        limit = int(style["captions"].get("max_lines", 2) or 0)
        if caption and limit > 0:
            try:
                sys.path.insert(0, str(HERE))
                import render as render_mod
                wrapped = render_mod.caption_line_count(
                    caption, style,
                    plan.get("width") or style["format"]["width"],
                    plan.get("height") or style["format"]["height"])
                if wrapped > limit:
                    notes.append("beats[%d]'s caption wraps to about %d lines, over the "
                                 "style's %d — it will cover the footage it is "
                                 "captioning. Shorten the line." % (i, wrapped, limit))
            except Exception:
                pass          # render.py absent or unreadable: not this tool's job
        seconds = beat.get("duration")
        if isinstance(seconds, (int, float)):
            low = style["pacing"]["min_beat_seconds"]
            high = style["pacing"]["max_beat_seconds"]
            if seconds < low:
                notes.append("beats[%d] is %gs, under the style's %gs minimum — it will "
                             "flash past before it is read" % (i, seconds, low))
            elif seconds > high:
                notes.append("beats[%d] is %gs, over the style's %gs maximum — long beats "
                             "are where retention goes" % (i, seconds, high))
    total = sum(b.get("duration", 0) for b in (plan.get("beats") or [])
                if isinstance(b, dict) and isinstance(b.get("duration"), (int, float)))
    if total:
        ok, reasons = shorts_verdict(total, style["format"]["width"],
                                     style["format"]["height"], style)
        problems.extend(reasons)
        target = style["shorts"]["target_seconds"]
        if ok and total > target:
            notes.append("%.1fs total is over the style's %.0fs target. Still a "
                         "Short, but short-form retention falls away the longer it "
                         "runs — trim unless the length is earning something."
                         % (total, target))
    return {"problems": problems, "notes": notes, "totalSeconds": round(total, 2),
            "styleVersion": style.get("version", 0)}


def ass_colour(rgb, alpha="00"):
    """RGB hex to the &HAABBGGRR that .ass Style lines use (byte order reversed)."""
    rgb = str(rgb).strip().lstrip("#")
    if len(rgb) != 6:
        raise StyleError("colour must be 6 hex digits (got %r)" % rgb)
    return "&H%s%s%s%s" % (alpha.upper(), rgb[4:6].upper(), rgb[2:4].upper(), rgb[0:2].upper())


def ass_override_colour(rgb):
    """RGB hex to the &HBBGGRR& that inline \\c and \\3c overrides take.

    Not the same spelling as a Style line's colour: overrides carry no alpha
    byte and are closed with a trailing &. Handing the 8-digit form to \\c
    makes renderers read the alpha as part of the blue channel.
    """
    rgb = str(rgb).strip().lstrip("#")
    if len(rgb) != 6:
        raise StyleError("colour must be 6 hex digits (got %r)" % rgb)
    return "&H%s%s%s&" % (rgb[4:6].upper(), rgb[2:4].upper(), rgb[0:2].upper())


def main(argv=None):
    parser = argparse.ArgumentParser(prog="style.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="write style.json with the default look")
    p_init.add_argument("--force", action="store_true")

    sub.add_parser("show", help="print the committed style")
    sub.add_parser("upgrade", help="write out settings a new version added")
    sub.add_parser("history", help="every change to the look")

    p_check = sub.add_parser("check", help="does a render plan respect the style?")
    p_check.add_argument("plan")

    p_set = sub.add_parser("set", help="change one field")
    p_set.add_argument("field", help="section.key, e.g. captions.uppercase")
    p_set.add_argument("value")

    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            got = init(force=args.force)
            print("wrote style.json (version %d) — every video now uses this look"
                  % got["version"])
        elif args.command == "show":
            got = current()
            if got.get("_default"):
                print("no style committed; showing the default. "
                      "Run: python3 style.py init", file=sys.stderr)
            print(json.dumps({k: v for k, v in got.items() if not k.startswith("_")}, indent=2))
        elif args.command == "upgrade":
            added = upgrade()
            if not added:
                print("style.json is already complete — nothing to add.")
            else:
                print("added %d setting%s, now version %d:"
                      % (len(added), "" if len(added) == 1 else "s",
                         current()["version"]))
                for field in added:
                    print("  %s" % field)
        elif args.command == "history":
            data = load()
            if not data["history"]:
                print("no style history yet")
                return 0
            for row in data["history"]:
                if row.get("action") == "set":
                    print("%s  v%s  %s: %r -> %r" % (row["at"], row["version"],
                                                     row["field"], row["from"], row["to"]))
                elif row.get("action") == "upgrade":
                    added = row.get("added") or []
                    print("%s  v%s  added %s" % (row["at"], row["version"],
                                                 ", ".join(added) or "nothing"))
                else:
                    print("%s  v%s  style committed" % (row["at"], row["version"]))
            changes = sum(1 for r in data["history"] if r.get("action") == "set")
            if changes >= 6:
                print("\n%d changes so far. Videos made before and after will not match; "
                      "a look only becomes recognisable if it stays put." % changes)
        elif args.command == "check":
            plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
            got = check_plan(plan)
            for line in got["problems"]:
                print("PROBLEM  " + line)
            for line in got["notes"]:
                print("note     " + line)
            if not got["problems"] and not got["notes"]:
                print("plan matches style v%d" % got["styleVersion"])
            return 1 if got["problems"] else 0
        else:
            got = set_field(args.field, args.value)
            section, key = args.field.split(".")
            print("%s is now %r (style v%d)" % (args.field, got[section][key], got["version"]))
        return 0
    except (StyleError, json.JSONDecodeError, OSError) as exc:
        print("style.py: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
