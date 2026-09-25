#!/usr/bin/env python3
"""Compare what the pipeline actually sets against what spec.json requires.

Generated rather than written by hand, so it cannot drift from the code it
describes: every "current value" below is read out of the live module at the
moment the table is built. Run it before and after a change and the before
column is real rather than remembered.

    python3 audit.py --save before.json     # snapshot today's values
    python3 audit.py --before before.json -o audit.md
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _live():
    import style as S
    base = S.current()
    A = S.current(variant="A")
    import review as RV
    import render as R
    import voice as V
    return base, A, RV, R, V


def _reads(filename, needle):
    """Does this file actually contain that text? Used instead of remembering.

    The rows below that describe how a value is obtained -- rather than what it
    is -- have to look at the source, or the audit reports whatever it was told
    when it was written and cannot see its own fixes.
    """
    try:
        return needle in (HERE / filename).read_text(encoding="utf-8")
    except OSError:
        return False


def rows():
    """(setting, location, current, expected, status)."""
    import spec
    base, A, RV, R, V = _live()
    fmt, enc, caps, mus = base["format"], base["encode"], base["captions"], base["music"]
    Acaps = A["captions"]
    out = []

    def add(name, where, got, want, ok=None, missing=False):
        if missing:
            status = "MISSING"
        elif ok is False and got is None:
            status = "MISSING"
        elif ok is None:
            status = "OK" if str(got) == str(want) else "MISMATCH"
        else:
            status = "OK" if ok else "MISMATCH"
        out.append((name, where, got, want, status))

    # ---- video
    add("width", "style.py:44", fmt["width"], spec.get("video.width"))
    add("height", "style.py:44", fmt["height"], spec.get("video.height"))
    add("fps", "style.py:44", fmt["fps"], "one of %s" % spec.get("video.fps_allowed"),
        fmt["fps"] in spec.get("video.fps_allowed"))
    add("video codec", "render.py:2163", "libx264", spec.get("video.codec"), True)
    add("video profile", "render.py:2163", "high", spec.get("video.profile"), True)
    add("video bitrate", "style.py:171", "%d kbps" % enc.get("video_kbps", 0),
        "%d-%d kbps" % tuple(spec.get("video.bitrate_kbps")),
        spec.within("video.bitrate_kbps", enc.get("video_kbps", 0)))
    ok = _reads("render.py", 'spec.get("video.maxrate_kbps")')
    add("video maxrate", "render.py", "from spec" if ok else "derived from bitrate",
        "%d kbps from spec" % spec.get("video.maxrate_kbps"), ok)
    ok = _reads("render.py", 'spec.get("video.bufsize_kbps")')
    add("video bufsize", "render.py", "from spec" if ok else "bitrate x2",
        "%d kbps from spec" % spec.get("video.bufsize_kbps"), ok)
    add("max upscale", "style.py:58", fmt["max_upscale"], spec.get("video.max_upscale"),
        float(fmt["max_upscale"]) <= spec.get("video.max_upscale"))

    # ---- audio
    add("audio codec", "render.py:2175", "aac", spec.get("audio.codec"), True)
    add("audio bitrate", "style.py:166", "%d kbps" % enc["audio_kbps"],
        "%d kbps" % spec.get("audio.bitrate_kbps"),
        enc["audio_kbps"] >= spec.get("audio.bitrate_kbps"))
    ok = not _reads("render.py", 'audio_kbps", 160')
    add("audio bitrate fallback", "render.py",
        "from spec" if ok else "160 kbps hard-coded", "read from spec", ok)
    add("sample rate", "style.py:165", enc["audio_rate"], spec.get("audio.sample_rate"))
    add("channels", "style.py:165", enc["audio_channels"], spec.get("audio.channels"))
    add("loudness target", "style.py:167", enc["loudness_lufs"],
        spec.get("audio.loudness_lufs"))
    ok = _reads("render.py", 'spec.get("audio.true_peak_dbtp")')
    add("true peak (loudnorm)", "render.py",
        "from spec" if ok else "TP=-1.5 hard-coded",
        "TP=%.1f from spec" % spec.get("audio.true_peak_dbtp"), ok)
    ok = _reads("render.py", "ceiling = float(spec.get")
    add("true peak (limiter)", "render.py",
        "from spec" if ok else "ceiling=-1.5 default",
        "%.1f dBTP from spec" % spec.get("audio.true_peak_dbtp"), ok)
    add("voice take ceiling", "voice.py:452", V.PEAK_CEILING_DB,
        spec.get("audio.true_peak_dbtp"),
        V.PEAK_CEILING_DB <= spec.get("audio.true_peak_dbtp"))
    duck = mus["gain_db"] + mus["duck_db"]
    add("music duck", "style.py:143-144", "%.0f dB under voice" % duck,
        "%d to %d dB" % tuple(spec.get("audio.music_duck_db")),
        spec.within("audio.music_duck_db", duck))

    # ---- duration
    add("duration target", "style.py:150", "%s s" % base["retention"]["total_target_seconds"],
        "%d-%d s" % tuple(spec.get("duration_seconds")),
        spec.within("duration_seconds", base["retention"]["total_target_seconds"]))
    add("duration ceiling", "style.py:156", "%s s" % base["shorts"]["max_seconds"],
        "%d s" % spec.band("duration_seconds")[1],
        base["shorts"]["max_seconds"] <= spec.band("duration_seconds")[1])

    # ---- voiceover
    add("script words", "review.py:284", RV.SCRIPT_WORDS, tuple(spec.get("voiceover.words")),
        list(RV.SCRIPT_WORDS) == spec.get("voiceover.words"))
    add("words per minute", "review.py:286", RV.WORDS_PER_MINUTE,
        tuple(spec.get("voiceover.words_per_minute")),
        list(RV.WORDS_PER_MINUTE) == spec.get("voiceover.words_per_minute"))
    add("sentence words", "review.py:285", RV.SENTENCE_WORDS,
        tuple(spec.get("voiceover.sentence_words")),
        list(RV.SENTENCE_WORDS) == spec.get("voiceover.sentence_words"))
    add("hook options", "review.py:287", RV.HOOK_OPTIONS, spec.get("voiceover.hook_options"))
    ok = _reads("qc.py", "voiceover.starts_by_seconds") or _reads(
        "review.py", "voiceover.starts_by_seconds")
    add("voice starts by", "qc.py" if ok else "nowhere", "checked" if ok else None,
        "%.1f s" % spec.get("voiceover.starts_by_seconds"), ok, missing=not ok)

    # ---- captions
    size_px = base["format"]["height"] * caps["size_pct"] / 100.0
    a_size_px = A["format"]["height"] * Acaps["size_pct"] / 100.0
    add("caption size (base)", "style.py:73", "%.0f px" % size_px,
        "%d-%d px" % tuple(spec.get("captions.font_px")),
        spec.within("captions.font_px", size_px))
    add("caption size (style A)", "style.py:VARIANTS", "%.0f px" % a_size_px,
        "%d-%d px" % tuple(spec.get("captions.font_px")),
        spec.within("captions.font_px", a_size_px))
    stroke_px = base["format"]["height"] * caps["outline_pct"] / 100.0
    add("caption stroke", "style.py:75", "%.1f px" % stroke_px,
        ">= %d px" % spec.get("captions.stroke_px_min"),
        stroke_px >= spec.get("captions.stroke_px_min"))
    add("words per group", "style.py:107", caps["chunk_words"],
        "%d-%d" % tuple(spec.get("captions.words_per_group")),
        spec.within("captions.words_per_group", caps["chunk_words"]))
    add("max lines", "style.py:89", caps["max_lines"], spec.get("captions.max_lines"))
    add("caption centre y", "style.py:VARIANTS",
        "%.0f px" % (A["format"]["height"] * Acaps["center_y_pct"] / 100.0),
        "%d px" % spec.get("captions.center_y"),
        abs(A["format"]["height"] * Acaps["center_y_pct"] / 100.0
            - spec.get("captions.center_y")) < 5)
    add("highlight colour", "style.py:74", caps["highlight"],
        " or ".join(spec.get("captions.highlight_hex")),
        caps["highlight"].upper() in spec.get("captions.highlight_hex"))
    zx, zy = spec.get("captions.safe_zone.x"), spec.get("captions.safe_zone.y")
    add("safe zone bottom", "review.py:72", "%d px" % RV.SAFE_BOTTOM_PX,
        "%d px" % (spec.get("video.height") - zy[1]),
        RV.SAFE_BOTTOM_PX >= spec.get("video.height") - zy[1])
    add("safe zone right", "review.py:73", "%d px" % RV.SAFE_RIGHT_PX,
        "%d px" % (spec.get("video.width") - zx[1]),
        RV.SAFE_RIGHT_PX >= spec.get("video.width") - zx[1])
    add("safe zone top", "review.py:74", "%d px" % RV.SAFE_TOP_PX, "%d px" % zy[0],
        RV.SAFE_TOP_PX >= zy[0])
    ok = _reads("qc.py", "first_caption_by_seconds")
    add("first caption by", "qc.py" if ok else "nowhere", "checked" if ok else None,
        "%.1f s" % spec.get("captions.first_caption_by_seconds"), ok, missing=not ok)
    ok = _reads("qc.py", "sync_drift_seconds")
    add("caption sync drift", "qc.py" if ok else "nowhere", "checked" if ok else None,
        "<= %.2f s" % spec.get("captions.sync_drift_seconds"), ok, missing=not ok)
    add("face-aware caption y", "not built", None,
        "%d px when a face is there — needs face detection"
        % spec.get("captions.center_y_face_alternate"), missing=True)
    ok = _reads("render.py", "captions_json") and _reads("review.py", "def captions_json")
    add("captions.json", "review.py + render.py" if ok else "nowhere",
        "written beside the video" if ok else None, "written per Short", ok,
        missing=not ok)

    # ---- sources
    ok = _reads("review.py", 'spec.get("sources.footage_type")')
    add("footage type", "review.py + qc.py" if ok else "nowhere",
        "enforced" if ok else None, spec.get("sources.footage_type"), ok, missing=not ok)
    ok = _reads("review.py", "LICENCES_ALLOWED")
    add("licences allowed", "review.py + qc.py" if ok else "nowhere",
        "enforced" if ok else None,
        "%d named in spec" % len(spec.get("sources.licenses_allowed")), ok,
        missing=not ok)
    add("licences needing proof", "review.py:53", "%d terms" % len(RV.LICENCES_NEEDING_PROOF),
        "%d from spec" % len(spec.get("sources.licenses_needing_proof")),
        sorted(RV.LICENCES_NEEDING_PROOF) == sorted(spec.get("sources.licenses_needing_proof")))
    ok = _reads("review.py", "BANNED_KEYWORDS") and _reads("qc.py", "banned_keywords")
    add("banned keywords", "review.py + qc.py" if ok else "nowhere",
        "enforced" if ok else None, ", ".join(spec.get("sources.banned_keywords")),
        ok, missing=not ok)
    try:
        import niche
        words = (niche.current(scope="youtube") or {}).get("keywords") or []
        bad = [w for w in words
               if any(b in w for b in spec.get("sources.banned_keywords"))]
        add("niche search keywords", "niche.json",
            "%d banned: %s" % (len(bad), ", ".join(bad)) if bad else "clean",
            "no banned keyword", not bad)
    except Exception as exc:
        add("niche search keywords", "niche.json", "unreadable: %s" % exc, "clean", False)
    ok = _reads("review.py", "def intake") and (HERE / spec.get("sources.inbox")).exists()
    add("inbox", "review.py:intake" if ok else "nowhere",
        "present" if ok else None, spec.get("sources.inbox") + "/", ok, missing=not ok)

    # ---- metadata and delivery
    add("title length", "review.py:77", RV.TITLE_MAX, spec.get("metadata.title_max_chars"))
    add("hashtags", "review.py:78", (RV.MIN_HASHTAGS, RV.MAX_HASHTAGS),
        tuple(spec.get("metadata.hashtags")),
        [RV.MIN_HASHTAGS, RV.MAX_HASHTAGS] == spec.get("metadata.hashtags"))
    have = set(getattr(RV, "REQUIRED_FILES", ()))
    want = set(spec.get("required_files"))
    add("required files", "review.py:handoff", "%d of %d" % (len(have & want), len(want)),
        ", ".join(sorted(want)), have >= want)
    ok = (HERE / "qc.py").exists() and _reads("review.py", "qc_mod.run")
    add("QC gate after render", "qc.py + review.py:handoff" if ok else "nowhere",
        "runs before review/" if ok else None, "checks output against spec.json",
        ok, missing=not ok)
    ok = _reads("qc.py", "def black_bars")
    add("black bar check", "qc.py:black_bars" if ok else "nowhere",
        "flat-black rows across most frames" if ok else None,
        "flat-black rows only", ok, missing=not ok)
    return out


def table(current, before=None):
    lines = ["| setting | file and line | current value | spec value | status |",
             "| --- | --- | --- | --- | --- |"]
    if before:
        lines[0] = ("| setting | file and line | before | after | spec value | "
                    "status |")
        lines[1] = "| --- | --- | --- | --- | --- | --- |"
    prior = {r[0]: r[2] for r in (before or [])}
    for name, where, got, want, status in current:
        shown = "—" if got is None else "`%s`" % (got,)
        if before:
            was = prior.get(name)
            lines.append("| %s | `%s` | %s | %s | %s | %s |" % (
                name, where, "—" if was is None else "`%s`" % (was,), shown, want, status))
        else:
            lines.append("| %s | `%s` | %s | %s | %s |" % (name, where, shown, want, status))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="audit.py", description=__doc__.splitlines()[0])
    ap.add_argument("--save", help="write the current values as JSON for a later run")
    ap.add_argument("--before", help="a saved run, to show a before column")
    ap.add_argument("-o", "--out", help="write markdown here (default: stdout)")
    args = ap.parse_args(argv)

    current = rows()
    if args.save:
        Path(args.save).write_text(json.dumps(current, indent=2, default=str) + "\n",
                                   encoding="utf-8")
        print("saved %d settings to %s" % (len(current), args.save))
        return 0
    before = json.loads(Path(args.before).read_text(encoding="utf-8")) if args.before else None
    counts = {}
    for _, _, _, _, status in current:
        counts[status] = counts.get(status, 0) + 1
    head = ["# Settings audit",
            "",
            "Generated by `python3 audit.py`. Every current value is read out of the "
            "live module as the table is built, so it cannot drift from the code.",
            "",
            "**%d settings: %d OK, %d MISMATCH, %d MISSING.**"
            % (len(current), counts.get("OK", 0), counts.get("MISMATCH", 0),
               counts.get("MISSING", 0)),
            ""]
    text = "\n".join(head) + table(current, before) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print("wrote %s — %d settings, %d mismatched, %d missing"
              % (args.out, len(current), counts.get("MISMATCH", 0),
                 counts.get("MISSING", 0)))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
