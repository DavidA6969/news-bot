#!/usr/bin/env python3
"""One command: a script and a folder of clips, out comes a finished Short.

    python3 short.py script.md --clips assets/ -o out/today.mp4

Everything between is the pipeline that already exists -- draft the plan, take
the clips in order, narrate, cut the video to the voice, time the captions to
the words, check the three gates, encode, verify. This exists so that the step
between "I have footage" and "I have a Short" is not eight commands in the
right order.

Clips are used in the order they sort, one per beat. A clip needs a licence:
put a licenses.json next to them ({"file.mp4": "CC BY 4.0 - source url"}), or
pass --license to apply one to all of them. Nothing renders without it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm", ".m4v")


class ShortError(RuntimeError):
    """The Short could not be made."""


def _clips_in(folder):
    found = sorted(p for p in Path(folder).iterdir()
                   if p.suffix.lower() in VIDEO_EXT)
    if not found:
        raise ShortError("no video files in %s (looked for %s)"
                         % (folder, ", ".join(VIDEO_EXT)))
    return found


def _licences_for(folder, clips, blanket=None):
    """A licence per clip, from licenses.json, --license, or an error."""
    ledger = Path(folder) / "licenses.json"
    known = {}
    if ledger.exists():
        try:
            raw = json.loads(ledger.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ShortError("%s is not valid JSON (%s)" % (ledger, exc))
        # fetch_clips writes {path: {...,"license":...}}; a hand-written file
        # is usually {name: "licence"}. Accept both.
        for key, value in (raw or {}).items():
            text = value.get("license") if isinstance(value, dict) else value
            if text:
                known[Path(key).name] = text
    out, missing = {}, []
    for clip in clips:
        text = known.get(clip.name) or blanket
        if text:
            out[clip.name] = text
        else:
            missing.append(clip.name)
    if missing:
        raise ShortError(
            "no licence recorded for %s. Put a licenses.json beside the clips "
            '({"%s": "CC BY 4.0 - https://..."}), or pass --license to apply '
            "one to all of them. Renders do not proceed without provenance."
            % (", ".join(missing[:3]), missing[0]))
    return out


def build(script, clips_dir, output=None, engine=None, emphasis=None,
          blanket_licence=None, attribution=None, progress=print, recorded=None,
          variant=None):
    import render as R, voice as V, style as S, review as RV

    look = S.current(variant=variant)
    if variant:
        progress("  look      style %s — %s captions%s, %s frame"
                 % (look["_variant"], look["captions"]["align"],
                    " in a box" if look["captions"]["box"] else "",
                    look["format"]["fit"]))
    lines = V.beats_from_script(script)
    clips = _clips_in(clips_dir)
    if len(clips) < len(lines):
        raise ShortError(
            "%d beats but only %d clip%s in %s. One clip per beat: add more, or "
            "cut the script." % (len(lines), len(clips),
                                 "" if len(clips) == 1 else "s", clips_dir))
    licences = _licences_for(clips_dir, clips, blanket_licence)

    out = Path(output or "out/short.mp4")
    plan = {"output": str(out), "styleVersion": look.get("version", 0), "beats": []}
    if variant:
        plan["styleVariant"] = look["_variant"]
    for line, clip in zip(lines, clips):
        beat = {"clip": str(clip), "license": licences[clip.name],
                "in": 0.0, "duration": 2.0, "caption": line[:120]}
        if attribution:
            beat["attribution"] = attribution
        plan["beats"].append(beat)
    plan_path = Path(clips_dir).parent / "render.json"
    plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    progress("  plan      %d beats from %d clips" % (len(lines), len(clips)))

    # Check the cheap, certain things BEFORE narrating. A licence that demands
    # credit and has none will fail the rights gate either way; finding that
    # out after synthesising every line wastes the one slow step in here.
    owed = [Path(c).name for c in licences
            if any(m in licences[c].lower() for m in R.ATTRIBUTION_REQUIRED)]
    if owed and not attribution:
        raise ShortError(
            "%s %s a licence that requires credit, and no --attribution was "
            "given. CC-BY without attribution is infringement, not a formality. "
            'Pass --attribution "Creator, CC BY 4.0 — source url".'
            % (", ".join(owed[:3]), "carries" if len(owed) == 1 else "carry"))

    long_caption = R.overlong_captions(json.loads(plan_path.read_text()), look)
    for index, count, text in long_caption:
        progress("  note      beat %d's caption wraps to %d lines: %s"
                 % (index, count, text[:44]))

    if recorded:
        # Your own voice needs no engine, and it is the one thing that takes
        # the synthetic-narration risk off the channel entirely.
        engine = "recorded"
    else:
        engine = engine or (V.usable_engines() or [(None, "")])[0][0]
        if not engine:
            raise ShortError("no speech engine available — run: python3 voice.py engines")
    voice_dir = Path(clips_dir).parent / "voice"
    delivery = V.delivery_from_script(script)
    if delivery:
        progress("  delivery  %s" % ", ".join(
            "%d:%s" % (i, "/".join("%s%+g" % (k[0], v) if k != "rate"
                                   else "x%.2f" % v for k, v in sorted(spec.items())))
            for i, spec in sorted(delivery.items())))
    # Measure the words BEFORE narrating: those durations are what decide
    # where inside a continuous sentence each picture cuts, and they are
    # needed by fit_plan rather than after it.
    # A recording cannot be asked to say one word at a time, so when the voice
    # is yours the word weights come from whichever synthesiser is installed.
    # They are only ever used as relative weights inside a beat whose real
    # length is measured from the recording, so the voice they came from does
    # not reach the video -- and with no synthesiser at all this is None and
    # the caller estimates.
    weigh_with = engine
    if recorded:
        weigh_with = (V.usable_engines() or [(None, "")])[0][0]
    measured = (V.measure_words(script, out_dir=voice_dir, engine=weigh_with)
                if weigh_with else None)
    spoken = V.speak(script, voice_dir, engine=engine, recorded=recorded,
                     emphasis=emphasis, delivery=delivery, weights=measured)
    fitted = V.fit_plan(plan_path, spoken, delivery=delivery)
    if measured:
        V.annotate_plan(plan_path, measured)
    track = Path(clips_dir).parent / "voice.wav"
    V.build_track(plan_path, spoken, track)
    V.attach(plan_path, str(track), licence="original narration (%s)" % engine)
    progress("  narrated  %s, %d lines as %d sentence%s"
             % (engine, len(lines), len(spoken), "" if len(spoken) == 1 else "s"))

    # Written BEFORE the gates, because one of them checks it exists. A CC-BY
    # credit that lives only in a JSON file beside the video has not been
    # given to anybody; the description is where the licence means.
    desc_path = out.with_suffix(".description.txt")
    rights_path = out.with_suffix(".rights.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    hook = R.hook_line(script) or (lines[0] if lines else "")
    desc_path.write_text(R.description(plan_path, hook=hook), encoding="utf-8")
    rights_path.write_text(
        json.dumps(R.rights_receipt(plan_path), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    progress("  credit    %s, and %s for a Content ID dispute"
             % (desc_path.name, rights_path.name))

    failed = []
    # `rights` asks whether a licence is recorded beside each clip; `sources`
    # asks whether anyone could verify that claim -- the URL it came from and
    # the day it was checked. `safe` is about the committed style rather than
    # this plan, but a caption sitting under the app's own buttons is wrong in
    # every video, so it is cheaper to refuse here than to find out one upload
    # at a time.
    for name, check in (("narration", R.narration_report),
                        ("retention", R.retention_report),
                        ("rights", R.rights_report),
                        ("sources", RV.sources_report),
                        ("safe", lambda _plan: RV.safe_zone_report(look)),
                        ("fresh", R.unused_report),
                        ("monetize", R.monetize_report)):
        ok, findings = check(plan_path)
        bad = [h for level, h, _ in findings if level == "fail"]
        progress("  %-9s %s%s" % (name, "pass" if ok else "FAIL",
                                  "" if ok else "  " + "; ".join(bad)))
        if not ok:
            failed.append(name)
    if failed:
        tool = "review.py" if failed[0] in ("sources", "safe") else "render.py"
        arg = "" if failed[0] == "safe" else " %s" % plan_path
        raise ShortError("%s did not pass. Run `python3 %s %s%s` for the detail."
                         % (" and ".join(failed), tool, failed[0], arg))

    result = R.build(plan_path, progress=lambda *a: None)
    # Recorded only once the render exists. A plan that failed a gate has not
    # used anything, and burning its footage would leave the channel unable to
    # make the video it was trying to make.
    kept = R.remember_used(plan_path, name=out.name)
    progress("  recorded  %d shots of %s — no later video may reuse them"
             % (kept["shots"], ", ".join(kept["sources"])))
    ok, why = S.shorts_verdict(result["duration"], look["format"]["width"],
                               look["format"]["height"])
    progress("  built     %s  %.1fs  %.1f MB%s"
             % (result["resolution"], result["duration"],
                result["size"] / 1048576, "" if ok else "  NOT a Short: %s" % why))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog="short.py", description=__doc__.splitlines()[0])
    parser.add_argument("script", help="script.md with numbered beats")
    parser.add_argument("--clips", required=True, help="folder of video files")
    parser.add_argument("-o", "--output", help="where to write the Short")
    parser.add_argument("--engine", help="speech engine (default: the best available)")
    parser.add_argument("--recorded", help="directory of beat01.wav … you recorded "
                                           "yourself — your own voice instead of TTS")
    parser.add_argument("--style", dest="variant", choices=["A", "B", "a", "b"],
                        help="which of the two looks: A curiosity, B story caption")
    parser.add_argument("--license", dest="licence",
                        help="apply one licence to every clip in the folder")
    parser.add_argument("--attribution", help="credit line for the description")
    parser.add_argument("--slow", default="",
                        help="beats to slow, e.g. 2,9,11 — the payoff and the number")
    args = parser.parse_args(argv)
    emphasis = {int(n): 0.85 for n in args.slow.replace(" ", "").split(",") if n}
    try:
        build(args.script, args.clips, args.output, args.engine, emphasis,
              args.licence, args.attribution, recorded=args.recorded,
              variant=args.variant)
    except Exception as exc:
        if type(exc).__name__ not in ("ShortError", "RenderError", "VoiceError",
                                      "StyleError", "FetchError"):
            raise
        print("short.py: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
