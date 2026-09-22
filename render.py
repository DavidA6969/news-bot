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
def _beat_filter(w, h, fps, push, duration):
    """Reframe to the style's format, and apply its push-in.

    The slow push is what stops a run of stock clips reading as a slideshow;
    because it comes from the style it is identical in every video.
    """
    chain = ("scale=%d:%d:force_original_aspect_ratio=increase,"
             "crop=%d:%d,fps=%d,setsar=1" % (w, h, w, h, fps))
    if push > 0:
        frames = max(1, int(round(duration * fps)))
        step = push / frames
        # Driven off `on` (the output frame counter), not the accumulating `zoom`
        # variable: with d=1 zoom resets on every input frame, so the usual
        # zoom+step recipe silently produces no motion at all.
        chain += (",zoompan=z='min(1+%.8f*on,%.4f)':d=1"
                  ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                  ":s=%dx%d:fps=%d" % (step, 1.0 + push, w, h, fps))
    return chain


def _ass_time(seconds):
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return "%d:%02d:%05.2f" % (h, m, s)


def _ass_escape(text):
    return (str(text).replace("\\", "\\\\").replace("{", "(").replace("}", ")")
            .replace("\n", "\\N"))


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
    at = 0.0
    any_caption = False
    for beat in plan["beats"]:
        caption = (beat.get("caption") or "").strip()
        if caps.get("uppercase"):
            caption = caption.upper()
        if caption:
            any_caption = True
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
    crf = str(look["encode"]["crf"])
    preset = str(look["encode"]["preset"])
    reporter = _Reporter(agent, out.name)
    reporter.start(len(plan["beats"]))

    tmp = Path(tempfile.mkdtemp(prefix="render-"))
    try:
        # 1. normalise every beat to identical codec/size/fps so concat is safe
        parts = []
        for i, beat in enumerate(plan["beats"]):
            part = tmp / ("part%03d.mp4" % i)
            progress("  beat %d/%d  %.1fs  %s" % (
                i + 1, len(plan["beats"]), beat["duration"], Path(beat["_path"]).name))
            _run([
                ff, "-hide_banner", "-loglevel", "error", "-y",
                "-ss", "%.3f" % beat["in"], "-t", "%.3f" % beat["duration"],
                "-i", beat["_path"],
                "-vf", _beat_filter(w, h, fps, push, beat["duration"]),
                "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", crf,
                "-pix_fmt", "yuv420p", str(part),
            ], "trimming beat %d" % (i + 1))
            got = probe(part)
            if not got["video"]:
                raise RenderError("beat %d produced no video — is %s really a video file?"
                                  % (i + 1, beat["_path"]))
            parts.append(part)

        # 2. concatenate
        listing = tmp / "parts.txt"
        listing.write_text("".join("file '%s'\n" % p for p in parts), encoding="utf-8")
        silent = tmp / "silent.mp4"
        progress("  joining %d beats" % len(parts))
        _run([ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
              "-safe", "0", "-i", str(listing), "-c", "copy", str(silent)],
             "joining the beats")

        # 3. captions + audio in one finishing pass
        subs = tmp / "captions.ass"
        has_captions = build_subtitles(plan, subs)
        args = [ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(silent)]
        audio = plan.get("audio")
        if audio:
            args += ["-i", audio["_path"]]
        if has_captions:
            escaped = str(subs).replace("\\", "/").replace(":", r"\:")
            args += ["-vf", "subtitles='%s'" % escaped]
            progress("  burning in captions")
        args += ["-c:v", "libx264", "-preset", preset, "-crf", crf,
                 "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        if audio:
            # the video is the reference length; trim or pad the track to match
            args += ["-c:a", "aac", "-b:a", "160k", "-map", "0:v:0", "-map", "1:a:0",
                     "-af", "apad", "-shortest"]
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
