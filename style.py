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
           "ass_colour", "StyleError", "DEFAULT_STYLE"]

HERE = Path(__file__).resolve().parent
STORE = HERE / "style.json"

DEFAULT_STYLE = {
    "name": "vertical-caption",
    "format": {"width": 1080, "height": 1920, "fps": 30},
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
    },
    # a slow push on every clip: subtle, but it is what stops stock footage
    # reading as a slideshow. 0 turns it off.
    "motion": {"push_in": 0.10},
    "transition": {"kind": "cut", "seconds": 0.0},   # cut | crossfade
    "pacing": {"min_beat_seconds": 1.5, "max_beat_seconds": 7.0},
    # The Shorts envelope. Vertical or square and 3 minutes or less is
    # automatically treated as a Short; one second over and YouTube files it as
    # an ordinary video instead, which is not what this channel publishes.
    "shorts": {"max_seconds": 180, "target_seconds": 45, "require_vertical": True},
    "encode": {"crf": 20, "preset": "medium"},
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
        "highpass_hz": 85,              # cut rumble below the voice
        "lowpass_hz": 8500,             # take the fizz off synthesised speech
        "compress": True,               # even out the level line to line
        "loudness_lufs": -16.0,         # consistent level against the footage
    },
}

_NUMERIC = {
    "format.width": (240, 4320), "format.height": (240, 4320), "format.fps": (12, 60),
    "captions.size_pct": (1.0, 12.0), "captions.outline_pct": (0.0, 2.0),
    "captions.margin_bottom_pct": (0.0, 60.0), "captions.side_margin_pct": (0.0, 30.0),
    "motion.push_in": (0.0, 0.6), "transition.seconds": (0.0, 2.0),
    "pacing.min_beat_seconds": (0.3, 30.0), "pacing.max_beat_seconds": (1.0, 120.0),
    "encode.crf": (14, 34),
    "shorts.max_seconds": (1.0, 180.0), "shorts.target_seconds": (1.0, 180.0),
    "voice.words_per_minute": (80, 300), "voice.pitch": (0, 99),
    "voice.word_gap_ms": (0, 200), "voice.highpass_hz": (20, 300),
    "voice.lowpass_hz": (3000, 20000), "voice.loudness_lufs": (-30.0, -8.0),
}
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
                    "encode", "shorts", "voice"):
        if not isinstance(style.get(section), dict):
            raise StyleError('style is missing the "%s" section' % section)
    for dotted, (low, high) in _NUMERIC.items():
        section, key = dotted.split(".")
        value = style[section].get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise StyleError("%s must be a number (got %r)" % (dotted, value))
        if not low <= value <= high:
            raise StyleError("%s is %g; it must be between %g and %g" % (dotted, value, low, high))
    kind = style["transition"].get("kind")
    if kind not in ("cut", "crossfade"):
        raise StyleError('transition.kind must be "cut" or "crossfade" (got %r)' % kind)
    if kind == "crossfade" and style["transition"]["seconds"] <= 0:
        raise StyleError("a crossfade needs transition.seconds above 0")
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
    """RGB hex to the &HAABBGGRR that .ass files use (byte order is reversed)."""
    rgb = str(rgb).strip().lstrip("#")
    if len(rgb) != 6:
        raise StyleError("colour must be 6 hex digits (got %r)" % rgb)
    return "&H%s%s%s%s" % (alpha.upper(), rgb[4:6].upper(), rgb[2:4].upper(), rgb[0:2].upper())


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
