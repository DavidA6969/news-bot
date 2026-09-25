#!/usr/bin/env python3
"""Measure a finished Short against spec.json. Nothing ships without passing.

Every limit here is read from `spec.json`; this file states none of its own.
The point is that "does this meet the spec" is answered by measuring the file
that would actually be uploaded, not by trusting the settings that produced
it -- settings drift, encoders undershoot, and a pass that was never measured
is a guess.

    python3 qc.py out/video.mp4                 # measure one file
    python3 qc.py review/some-short/            # measure a whole folder
    python3 qc.py out/video.mp4 --report qc_report.json

Exit code 0 means every check passed. Anything else means it did not ship.
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
import review as RV                            # noqa: E402


class QCError(Exception):
    pass


def _ffprobe(path, args):
    out = subprocess.run(["ffprobe", "-v", "error", *args, str(path)],
                         capture_output=True, text=True)
    return out.stdout.strip()


def probe(path):
    raw = _ffprobe(path, ["-show_entries",
                          "format=duration,bit_rate:stream=codec_type,codec_name,"
                          "profile,width,height,r_frame_rate,bit_rate,sample_rate,"
                          "channels", "-of", "json"])
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        raise QCError("ffprobe could not read %s" % path)
    got = {"duration": float(data.get("format", {}).get("duration") or 0),
           "video": None, "audio": None}
    for stream in data.get("streams") or []:
        if stream.get("codec_type") == "video" and got["video"] is None:
            num, _, den = (stream.get("r_frame_rate") or "0/1").partition("/")
            got["video"] = {
                "codec": stream.get("codec_name"),
                "profile": (stream.get("profile") or "").lower(),
                "width": stream.get("width"), "height": stream.get("height"),
                "fps": round(float(num) / float(den or 1), 3),
                "kbps": round(float(stream.get("bit_rate") or 0) / 1000.0, 1)}
        elif stream.get("codec_type") == "audio" and got["audio"] is None:
            got["audio"] = {
                "codec": stream.get("codec_name"),
                "kbps": round(float(stream.get("bit_rate") or 0) / 1000.0, 1),
                "sample_rate": int(stream.get("sample_rate") or 0),
                "channels": int(stream.get("channels") or 0)}
    # A container without a per-stream video bitrate still has an overall one,
    # and for a Short the picture is nearly all of it.
    if got["video"] and not got["video"]["kbps"]:
        overall = float(data.get("format", {}).get("bit_rate") or 0) / 1000.0
        audio_kbps = (got["audio"] or {}).get("kbps") or 0
        got["video"]["kbps"] = round(max(0.0, overall - audio_kbps), 1)
        got["video"]["kbps_estimated"] = True
    return got


def loudness(path):
    """Integrated LUFS and true peak, from ebur128 rather than from hope."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"],
        capture_output=True, text=True)
    text = proc.stderr or ""
    lufs = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", text)
    peak = re.findall(r"Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS", text)
    return (float(lufs[-1]) if lufs else None,
            float(peak[-1]) if peak else None)


def voice_starts_at(path, floor_db=-40.0):
    """When sound actually begins, in seconds, or None if it never does.

    Measured off the waveform rather than off the plan: a track can be built
    with the voice at zero and still come out with a moment of silence in
    front of it, and the spec's limit is about what a viewer hears.
    """
    try:
        import numpy as np
    except ImportError:
        return None
    rate = 8000
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn",
         "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    raw = proc.stdout or b""
    if len(raw) < rate // 10:
        return None
    wave = np.frombuffer(raw[:len(raw) // 2 * 2], dtype="<i2").astype("float32") / 32768.0
    # A short window, so one stray sample of noise is not "the voice started".
    window = max(1, rate // 100)
    trimmed = wave[:len(wave) // window * window].reshape(-1, window)
    level = np.sqrt((trimmed ** 2).mean(axis=1) + 1e-12)
    loud = np.nonzero(20 * np.log10(level) > floor_db)[0]
    return round(float(loud[0]) * window / rate, 3) if len(loud) else None


def black_bars(path, samples=24):
    """Rows that are flat black across most frames — letterboxing, not night.

    A dark scene has dark rows; a letterbox has rows that are black in EVERY
    frame and flat across their whole width. Both conditions are required, so
    a night shot with a black sky is not reported as a bar.
    """
    try:
        import numpy as np
    except ImportError:
        return {"checked": False, "reason": "numpy is not installed"}
    height = int(spec.get("video.height"))
    width = 64
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path),
         "-vf", "fps=%d/%d,format=gray,scale=%d:%d" % (samples, 1, width, height),
         "-frames:v", str(samples), "-f", "rawvideo", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    raw = proc.stdout or b""
    frames = len(raw) // (width * height)
    if frames < 2:
        return {"checked": False, "reason": "could not decode enough frames"}
    grid = np.frombuffer(raw[:frames * width * height], dtype=np.uint8)
    grid = grid.reshape(frames, height, width).astype("float32")
    # Three conditions, because darkness alone is not a bar. A letterbox is
    # (1) almost black, (2) flat across its width, and (3) IDENTICAL from frame
    # to frame -- it is padding, so nothing in it moves. A night shot satisfies
    # the first and fails the third, which is the whole reason for measuring
    # across frames rather than judging one.
    # Tight, because a letterbox is padding rather than a dark picture: it
    # encodes to within a couple of levels of zero, it does not vary across
    # its width, and it is the same in every frame. Generous thresholds called
    # a night scene at luma 12-14 a bar, which is the exact mistake the rule
    # exists to avoid.
    dark = grid.max(axis=2) <= 6                             # (frames, height)
    flat = grid.std(axis=2) <= 1.0                           # (frames, height)
    still = grid.std(axis=0).max(axis=1) <= 0.5              # (height,)
    share = (dark & flat).mean(axis=0)
    always = (share >= 0.9) & still
    top = int(np.argmax(~always)) if not always.all() else height
    bottom = int(np.argmax(~always[::-1])) if not always.all() else height
    tolerance = height * float(spec.get("video.black_bar_row_tolerance_pct")) / 100.0
    return {"checked": True, "top_rows": top, "bottom_rows": bottom,
            "tolerance_rows": round(tolerance, 1),
            "bars": bool(top > tolerance or bottom > tolerance)}


def _check(rows, name, got, want, ok):
    rows.append({"check": name, "measured": got, "expected": want,
                 "status": "pass" if ok else "FAIL"})
    return ok


def video_checks(rows, info):
    v = info.get("video")
    if not _check(rows, "video stream", "present" if v else "missing", "present", bool(v)):
        return
    _check(rows, "width", v["width"], spec.get("video.width"),
           v["width"] == spec.get("video.width"))
    _check(rows, "height", v["height"], spec.get("video.height"),
           v["height"] == spec.get("video.height"))
    _check(rows, "fps", v["fps"], "one of %s" % spec.get("video.fps_allowed"),
           any(abs(v["fps"] - f) < 0.5 for f in spec.get("video.fps_allowed")))
    _check(rows, "video codec", v["codec"], spec.get("video.codec"),
           v["codec"] == spec.get("video.codec"))
    _check(rows, "video profile", v["profile"] or "unknown", spec.get("video.profile"),
           v["profile"] == spec.get("video.profile"))
    lo, hi = spec.band("video.bitrate_kbps")
    label = "%.1f kbps%s" % (v["kbps"], " (estimated)" if v.get("kbps_estimated") else "")
    _check(rows, "video bitrate", label, "%d-%d kbps" % (lo, hi),
           lo <= v["kbps"] <= hi)


def audio_checks(rows, info, path):
    a = info.get("audio")
    if not _check(rows, "audio stream", "present" if a else "missing", "present", bool(a)):
        return
    _check(rows, "audio codec", a["codec"], spec.get("audio.codec"),
           a["codec"] == spec.get("audio.codec"))
    floor = spec.get("audio.bitrate_kbps_min")
    _check(rows, "audio bitrate", "%.1f kbps" % a["kbps"],
           ">= %d kbps (target %d)" % (floor, spec.get("audio.bitrate_kbps")),
           a["kbps"] >= floor)
    _check(rows, "sample rate", a["sample_rate"], spec.get("audio.sample_rate"),
           a["sample_rate"] == spec.get("audio.sample_rate"))
    _check(rows, "channels", a["channels"], spec.get("audio.channels"),
           a["channels"] == spec.get("audio.channels"))
    lufs, peak = loudness(path)
    target = spec.get("audio.loudness_lufs")
    slack = spec.get("audio.loudness_tolerance_db")
    _check(rows, "loudness", "%s LUFS" % lufs, "%.1f +/- %.1f LUFS" % (target, slack),
           lufs is not None and abs(lufs - target) <= slack)
    ceiling = spec.get("audio.true_peak_dbtp")
    _check(rows, "true peak", "%s dBTP" % peak, "<= %.1f dBTP" % ceiling,
           peak is not None and peak <= ceiling)
    if spec.get("voiceover.required"):
        starts = voice_starts_at(path)
        by = spec.get("voiceover.starts_by_seconds")
        _check(rows, "voice starts",
               "never" if starts is None else "%.2f s" % starts,
               "<= %.1f s" % by, starts is not None and starts <= by)


def duration_check(rows, info):
    lo, hi = spec.band("duration_seconds")
    _check(rows, "duration", "%.1f s" % info["duration"], "%d-%d s" % (lo, hi),
           lo <= info["duration"] <= hi)


def bars_check(rows, path):
    got = black_bars(path)
    if not got.get("checked"):
        _check(rows, "no black bars", got.get("reason", "not checked"),
               "measurable", False)
        return
    _check(rows, "no black bars",
           "%d top / %d bottom flat-black rows" % (got["top_rows"], got["bottom_rows"]),
           "<= %.1f rows either edge" % got["tolerance_rows"], not got["bars"])


def files_check(rows, folder):
    if folder is None:
        _check(rows, "required files", "no folder given",
               ", ".join(spec.get("required_files")), False)
        return None
    missing = [n for n in spec.get("required_files") if not (folder / n).exists()]
    _check(rows, "required files",
           "missing %s" % ", ".join(missing) if missing else "all present",
           ", ".join(spec.get("required_files")), not missing)
    return missing


def caption_checks(rows, folder):
    book = (folder / "captions.json") if folder else None
    if not book or not book.exists():
        _check(rows, "captions.json", "missing", "present and readable", False)
        return
    try:
        data = json.loads(book.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _check(rows, "captions.json", "unreadable: %s" % exc, "valid JSON", False)
        return
    groups = data.get("groups") or []
    _check(rows, "captions present", len(groups), "at least one group", bool(groups))
    if not groups:
        return
    first = data.get("first_at")
    by = spec.get("captions.first_caption_by_seconds")
    _check(rows, "first caption", "%.2f s" % (first or 0), "<= %.1f s" % by,
           first is not None and first <= by)
    lo, hi = spec.band("captions.words_per_group")
    over = [g for g in groups if not lo <= g.get("words", 0) <= hi]
    _check(rows, "words per group",
           "%d of %d outside" % (len(over), len(groups)), "%d-%d words" % (lo, hi),
           not over)
    # A group that ends after the next one starts, or starts before the last
    # ended, is out of sync with whatever it was timed against.
    drift = spec.get("captions.sync_drift_seconds")
    bad = []
    for a, b in zip(groups, groups[1:]):
        gap = b["start"] - a["end"]
        if gap < -drift:
            bad.append(round(gap, 3))
    _check(rows, "caption sync", "%d overlaps" % len(bad),
           "no group overlaps the next by more than %.2f s" % drift, not bad)


def script_checks(rows, folder, info):
    page = (folder / "script.txt") if folder else None
    if not page or not page.exists():
        _check(rows, "script.txt", "missing", "present", False)
        return
    text = page.read_text(encoding="utf-8")
    body = text.split("Hook options considered:")[0]
    words = len(body.split())
    lo, hi = spec.band("voiceover.words")
    _check(rows, "script words", words, "%d-%d" % (lo, hi), lo <= words <= hi)
    if info["duration"] > 0:
        wpm = words / (info["duration"] / 60.0)
        plo, phi = spec.band("voiceover.words_per_minute")
        _check(rows, "speaking pace", "%.0f wpm" % wpm, "%d-%d wpm" % (plo, phi),
               plo <= wpm <= phi)


def source_checks(rows, folder):
    book = (folder / "sources.csv") if folder else None
    if not book or not book.exists():
        _check(rows, "sources.csv", "missing", "present", False)
        return
    entries = RV.sources(book)
    _check(rows, "sources logged", len(entries), "at least one", bool(entries))
    banned, wrong, unlicensed, unproven = [], [], [], []
    for row in entries:
        blob = " ".join(str(v) for v in row.values()).lower()
        hits = [b for b in spec.get("sources.banned_keywords") if b in blob]
        if hits:
            banned.append("%s (%s)" % (row.get("clip"), ", ".join(hits)))
        if str(row.get("footage_type") or "").lower() != spec.get("sources.footage_type"):
            wrong.append(row.get("clip"))
        flat = RV._flatten(row.get("license"))
        if not any(RV._flatten(w) in flat for w in spec.get("sources.licenses_allowed")):
            unlicensed.append("%s (%s)" % (row.get("clip"), row.get("license")))
        elif any(RV._flatten(w) in flat
                 for w in spec.get("sources.licenses_needing_proof")):
            kept = str(row.get("proof") or "").strip()
            if not kept or not (Path(kept).exists() or (HERE / kept).exists()):
                unproven.append("%s (%s)" % (row.get("clip"), kept or "none"))
    _check(rows, "no banned footage", "; ".join(banned) or "none",
           "none of %s" % ", ".join(spec.get("sources.banned_keywords")), not banned)
    _check(rows, "footage type", "; ".join(wrong) or "all %s" % spec.get("sources.footage_type"),
           spec.get("sources.footage_type"), not wrong)
    _check(rows, "licences allowed", "; ".join(unlicensed) or "all allowed",
           "one of %d named" % len(spec.get("sources.licenses_allowed")), not unlicensed)
    _check(rows, "licence proof on disk", "; ".join(unproven) or "all present",
           "a file for every licence that needs one", not unproven)


def run(target, report=None):
    target = Path(target)
    folder = target if target.is_dir() else None
    video = (folder / "final.mp4") if folder else target
    rows = []
    if not video.exists():
        rows.append({"check": "video file", "measured": "missing at %s" % video,
                     "expected": "present", "status": "FAIL"})
        info = {"duration": 0, "video": None, "audio": None}
    else:
        info = probe(video)
        video_checks(rows, info)
        audio_checks(rows, info, video)
        duration_check(rows, info)
        bars_check(rows, video)
    files_check(rows, folder)
    caption_checks(rows, folder)
    script_checks(rows, folder, info)
    source_checks(rows, folder)

    failed = [r for r in rows if r["status"] != "pass"]
    payload = {"target": str(target), "video": str(video),
               "spec_version": spec.get("version"),
               "passed": len(rows) - len(failed), "failed": len(failed),
               "ship": not failed, "checks": rows}
    if report:
        Path(report).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8")
    return payload


def main(argv=None):
    ap = argparse.ArgumentParser(prog="qc.py", description=__doc__.splitlines()[0])
    ap.add_argument("target", help="a final.mp4, or the folder holding one")
    ap.add_argument("--report", default="qc_report.json",
                    help="where to write the report (default: qc_report.json)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    try:
        got = run(args.target, args.report)
    except QCError as exc:
        print("qc.py: %s" % exc, file=sys.stderr)
        return 2
    if not args.quiet:
        for row in got["checks"]:
            print("%s %-24s %-34s %s" % (
                "  ok  " if row["status"] == "pass" else " FAIL ",
                row["check"], str(row["measured"])[:34], row["expected"]))
        print("\n%d passed, %d failed — %s" % (
            got["passed"], got["failed"],
            "ready for review" if got["ship"] else
            "NOT moved to review/; see " + args.report))
    return 0 if got["ship"] else 1


if __name__ == "__main__":
    sys.exit(main())
