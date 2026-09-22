#!/usr/bin/env python3
"""Narrate a script, and cut the video to the voice.

The point of a voiceover here is not decoration: in a commentary video the
narration *is* the original contribution. The clips illustrate what is being
said; the saying is the work.

So this does the thing most pipelines get backwards. It synthesises each beat,
measures how long that beat actually takes to say, and rewrites the render
plan's beat durations to match. The video is cut to the voice rather than the
voice being squeezed into arbitrary durations — which also means the burned-in
captions land with the words instead of near them.

    python3 voice.py engines                        # what is available here
    python3 voice.py speak script.md                # one wav per beat -> voice/
    python3 voice.py fit render.json                # beats <- spoken lengths
    python3 voice.py track render.json -o voice.wav # one track, gapped to fit
    python3 voice.py narrate script.md render.json  # all three, in order

Engines, in order of preference. Any one is enough:

* **piper** — best quality, offline. ``pip install piper-tts`` then download a
  voice (``.onnx`` + ``.onnx.json``) and set ``PIPER_VOICE`` to it.
* **espeak-ng** — robotic but everywhere. ``apt install espeak-ng`` /
  ``brew install espeak-ng``.
* **say** — built into macOS, decent.
* **recorded** — a directory of ``beat01.wav`` … files you recorded yourself.
  Your own voice beats any of the above, and for commentary it is worth it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

__all__ = ["beats_from_script", "speak", "fit_plan", "build_track", "attach",
           "available_engines", "usable_engines", "VoiceError"]

HERE = Path(__file__).resolve().parent
PAD_SECONDS = 0.28          # breathing room after each line
SAMPLE_RATE = 24000


class VoiceError(RuntimeError):
    """The narration could not be produced."""


def _ffmpeg():
    sys.path.insert(0, str(HERE))
    import render as render_mod
    return render_mod.ffmpeg_bin()


def _duration(path):
    sys.path.insert(0, str(HERE))
    import render as render_mod
    return render_mod.probe(path)["duration"]


# --------------------------------------------------------------------------
# engines
# --------------------------------------------------------------------------
def available_engines():
    """Every engine this machine knows about, best first, as (name, note, usable).

    `usable` is carried explicitly rather than inferred from the name, because
    "installed" and "able to speak" are different states: piper with no voice
    model is present and useless, and a caller that cannot tell them apart
    reports a green check and then fails at the first line.
    """
    found = []
    voice = os.environ.get("PIPER_VOICE")
    try:
        import piper  # noqa: F401
        if voice and Path(voice).exists():
            found.append(("piper", "offline, best quality (%s)" % Path(voice).name, True))
        else:
            found.append(("piper", "installed, but PIPER_VOICE is not set to a "
                                   ".onnx voice model", False))
    except ImportError:
        pass
    for name, note in (("espeak-ng", "robotic but dependable"),
                       ("say", "built into macOS")):
        if shutil.which(name):
            found.append((name, note, True))
    return found


def usable_engines():
    """Just the engines that can actually produce audio right now."""
    return [(name, note) for name, note, ok in available_engines() if ok]


def _synthesise(engine, text, out_path, voice=None):
    """One line of text to one wav."""
    out_path = Path(out_path)
    if engine == "piper":
        model = voice or os.environ.get("PIPER_VOICE")
        if not model or not Path(model).exists():
            raise VoiceError(
                "piper needs a voice model. Download one (for example "
                "en_GB-alba-medium.onnx and its .onnx.json) and set PIPER_VOICE "
                "to the .onnx path.")
        proc = subprocess.run(
            [sys.executable, "-m", "piper", "-m", str(model), "-f", str(out_path)],
            input=text, capture_output=True, text=True)
        if proc.returncode != 0:
            raise VoiceError("piper failed: %s" % (proc.stderr or "").strip()[-300:])
    elif engine == "espeak-ng":
        proc = subprocess.run(["espeak-ng", "-s", "165", "-w", str(out_path), text],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise VoiceError("espeak-ng failed: %s" % (proc.stderr or "").strip()[-300:])
    elif engine == "say":
        aiff = out_path.with_suffix(".aiff")
        proc = subprocess.run(["say", "-o", str(aiff), text], capture_output=True, text=True)
        if proc.returncode != 0:
            raise VoiceError("say failed: %s" % (proc.stderr or "").strip()[-300:])
        subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(aiff), "-ar", str(SAMPLE_RATE), "-ac", "1",
                        str(out_path)], check=True)
        aiff.unlink(missing_ok=True)
    else:
        raise VoiceError("unknown engine %r. Available: %s"
                         % (engine, ", ".join(n for n, _ in usable_engines()) or "none"))
    if not out_path.exists() or out_path.stat().st_size < 256:
        raise VoiceError("%s produced no audio for %r" % (engine, text[:40]))
    return out_path


# --------------------------------------------------------------------------
# script
# --------------------------------------------------------------------------
def beats_from_script(script_path):
    """The numbered beats, in order — the same ones render.py plans from."""
    text = Path(script_path).read_text(encoding="utf-8")
    beats = []
    for line in text.splitlines():
        match = re.match(r"\s*(\d+)[.)]\s+(.*\S)", line)
        if match:
            beats.append(match.group(2).strip())
    if not beats:
        raise VoiceError("no numbered beats found in %s — write them as '1. ...'"
                         % script_path)
    return beats


def speak(script_path, out_dir, engine=None, voice=None, recorded=None):
    """One wav per beat. Returns [(path, seconds), ...] in beat order."""
    beats = beats_from_script(script_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if recorded:
        source = Path(recorded)
        clips = []
        for i in range(len(beats)):
            candidates = sorted(source.glob("beat%02d.*" % (i + 1))) or \
                sorted(source.glob("beat%d.*" % (i + 1)))
            if not candidates:
                raise VoiceError("no recording for beat %d in %s (expected beat%02d.wav)"
                                 % (i + 1, source, i + 1))
            target = out_dir / ("beat%02d.wav" % (i + 1))
            subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                            "-i", str(candidates[0]), "-ar", str(SAMPLE_RATE),
                            "-ac", "1", str(target)], check=True)
            clips.append((target, _duration(target)))
        return clips

    if engine is None:
        options = [n for n, _ in usable_engines()]
        if not options:
            raise VoiceError(
                "no speech engine available. Install one: pip install piper-tts "
                "(then set PIPER_VOICE), apt install espeak-ng, or record the "
                "lines yourself and pass --recorded <dir>. Your own voice is "
                "better than any of them for commentary.")
        engine = options[0]

    clips = []
    for i, line in enumerate(beats, 1):
        target = out_dir / ("beat%02d.wav" % i)
        _synthesise(engine, line, target, voice)
        clips.append((target, _duration(target)))
    return clips


# --------------------------------------------------------------------------
# cut the video to the voice
# --------------------------------------------------------------------------
def fit_plan(plan_path, clips, pad=PAD_SECONDS, style=None):
    """Rewrite each beat's duration to how long that line takes to say.

    This is the whole point: a beat that runs shorter than its line cuts the
    narration off mid-sentence, and one that runs longer leaves dead air. The
    spoken length is the truth, so the cut follows it.
    """
    path = Path(plan_path)
    plan = json.loads(path.read_text(encoding="utf-8"))
    beats = plan.get("beats")
    if not isinstance(beats, list) or not beats:
        raise VoiceError("%s has no beats" % path)
    if len(clips) != len(beats):
        raise VoiceError(
            "the script has %d beats but the plan has %d. They have to match — "
            "regenerate the plan from the same script." % (len(clips), len(beats)))

    sys.path.insert(0, str(HERE))
    import style as style_mod
    look = style or style_mod.current()
    low = look["pacing"]["min_beat_seconds"]
    high = look["pacing"]["max_beat_seconds"]

    notes, total = [], 0.0
    for i, (beat, (_, spoken)) in enumerate(zip(beats, clips)):
        wanted = spoken + pad
        clamped = max(low, min(high, wanted))
        if clamped < wanted - 0.01:
            notes.append(
                "beat %d needs %.1fs to say but the style caps a beat at %.1fs — "
                "the line is too long for this pacing, so shorten the line rather "
                "than stretching the beat." % (i + 1, wanted, high))
        beat["duration"] = round(clamped, 2)
        total += beat["duration"]

    ok, reasons = style_mod.shorts_verdict(total, look["format"]["width"],
                                           look["format"]["height"], look)
    if not ok:
        raise VoiceError("spoken at this length the video would not be a Short: "
                         + " ".join(reasons) + " Cut lines.")
    path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    return {"total": round(total, 2), "notes": notes,
            "durations": [b["duration"] for b in beats]}


def build_track(plan_path, clips, out_path, pad=PAD_SECONDS):
    """One audio track, each line padded out to its beat's length."""
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    beats = plan["beats"]
    if len(clips) != len(beats):
        raise VoiceError("clips and beats do not match")

    ff = _ffmpeg()
    out_path = Path(out_path)
    parts_dir = out_path.parent / (out_path.stem + "-parts")
    parts_dir.mkdir(parents=True, exist_ok=True)
    listing = []
    for i, (beat, (clip, spoken)) in enumerate(zip(beats, clips), 1):
        want = float(beat["duration"])
        padded = parts_dir / ("seg%02d.wav" % i)
        # apad then atrim: the line plays, then silence to the end of the beat
        subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(clip),
                        "-af", "apad", "-t", "%.3f" % want,
                        "-ar", str(SAMPLE_RATE), "-ac", "1", str(padded)], check=True)
        listing.append(padded)

    concat = parts_dir / "list.txt"
    concat.write_text("".join("file '%s'\n" % p.resolve() for p in listing), encoding="utf-8")
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
                    "-safe", "0", "-i", str(concat), "-c:a", "pcm_s16le",
                    "-ar", str(SAMPLE_RATE), "-ac", "1", str(out_path)], check=True)
    shutil.rmtree(parts_dir, ignore_errors=True)

    got = _duration(out_path)
    want_total = sum(float(b["duration"]) for b in beats)
    if abs(got - want_total) > 0.25:
        raise VoiceError("the track came out %.2fs but the cut is %.2fs — refusing "
                         "to hand over audio that would drift" % (got, want_total))
    return {"path": str(out_path), "duration": round(got, 2)}


def attach(plan_path, track_path, licence="original narration"):
    """Point the render plan at the finished voice track."""
    path = Path(plan_path)
    plan = json.loads(path.read_text(encoding="utf-8"))
    plan["audio"] = {"path": str(track_path), "license": licence}
    path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    return plan["audio"]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="voice.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("engines", help="what speech engines are available here")

    def add_common(p):
        p.add_argument("--engine")
        p.add_argument("--voice", help="piper .onnx path (or set PIPER_VOICE)")
        p.add_argument("--recorded", help="directory of beat01.wav … you recorded")
        p.add_argument("--out-dir", default="voice")
        return p

    p_speak = add_common(sub.add_parser("speak", help="synthesise one wav per beat"))
    p_speak.add_argument("script")

    p_fit = sub.add_parser("fit", help="rewrite beat durations to the spoken lengths")
    p_fit.add_argument("plan")
    p_fit.add_argument("--voice-dir", default="voice")

    p_track = sub.add_parser("track", help="assemble the single voice track")
    p_track.add_argument("plan")
    p_track.add_argument("--voice-dir", default="voice")
    p_track.add_argument("-o", "--out", default="voice.wav")

    p_nar = add_common(sub.add_parser("narrate", help="speak, fit and attach in one go"))
    p_nar.add_argument("script")
    p_nar.add_argument("plan")
    p_nar.add_argument("-o", "--out", default="voice.wav")

    args = parser.parse_args(argv)
    try:
        if args.command == "engines":
            found = available_engines()
            for name, note, ok in found:
                print("  %-12s %-4s %s" % (name, "ok" if ok else "--", note))
            # Exit non-zero unless something can actually speak: an engine that
            # is installed but unusable must not read as a passing check.
            if not any(ok for _, _, ok in found):
                print("\nno engine can speak yet. Any one of these is enough:\n"
                      "  pip install piper-tts   (then set PIPER_VOICE to a .onnx voice)\n"
                      "  apt install espeak-ng   /  brew install espeak-ng\n"
                      "  record the lines yourself and pass --recorded <dir>  <- best")
                return 1
            return 0

        if args.command == "speak":
            clips = speak(args.script, args.out_dir, args.engine, args.voice, args.recorded)
            for path, seconds in clips:
                print("  %-22s %.2fs" % (Path(path).name, seconds))
            print("%d line(s), %.1fs spoken" % (len(clips), sum(c[1] for c in clips)))
            return 0

        if args.command in ("fit", "track"):
            voice_dir = Path(args.voice_dir)
            wavs = sorted(voice_dir.glob("beat*.wav"))
            if not wavs:
                raise VoiceError("no beat*.wav in %s — run voice.py speak first" % voice_dir)
            clips = [(w, _duration(w)) for w in wavs]
            if args.command == "fit":
                got = fit_plan(args.plan, clips)
                print("beats fitted to the voice: %s" %
                      ", ".join("%.2fs" % d for d in got["durations"]))
                for note in got["notes"]:
                    print("  note: %s" % note)
                print("total %.2fs" % got["total"])
            else:
                got = build_track(args.plan, clips, args.out)
                print("%s  %.2fs" % (got["path"], got["duration"]))
            return 0

        clips = speak(args.script, args.out_dir, args.engine, args.voice, args.recorded)
        fitted = fit_plan(args.plan, clips)
        for note in fitted["notes"]:
            print("  note: %s" % note)
        track = build_track(args.plan, clips, args.out)
        attach(args.plan, args.out)
        print("narrated: %d lines, %.2fs, cut fitted to the voice" %
              (len(clips), track["duration"]))
        print("plan now points at %s — render it with: python3 render.py build %s"
              % (args.out, args.plan))
        return 0
    except VoiceError as exc:
        print("voice.py: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
