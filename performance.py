#!/usr/bin/env python3
"""Close the loop: record what each published video actually did.

Without this the pipeline is blind. COMPASS picks a niche, ATLAS picks topics,
HERALD publishes — and nothing ever finds out whether any of it worked, so the
next run repeats the same guesses with the same confidence. This records the
outcome of every video against the topic that produced it, and writes a digest
that ATLAS reads before choosing what to make next.

Reading public view counts needs only a **Data API key**, not OAuth — no extra
permission on your account. Create one in the same Google Cloud project
(Credentials → API key) and pass it with ``--api-key`` or ``YOUTUBE_API_KEY``.

    python3 performance.py record VIDEOID --title "..." --angle "..." \
        --differentiator "..." --confidence high
    python3 performance.py refresh
    python3 performance.py digest          # writes performance.md for ATLAS

Statistics are only visible once a video is public. Private or scheduled
videos are reported as "not yet public" rather than counted as zero views —
counting them as zero would poison every average here.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["record", "refresh", "digest", "load", "PerformanceError"]

HERE = Path(__file__).resolve().parent
STORE = HERE / "performance.json"
DIGEST = HERE / "performance.md"
API = "https://www.googleapis.com/youtube/v3/videos"

# Below this many measured videos, differences between them are noise. Saying
# so is more useful than a confident pattern drawn from four data points.
MIN_FOR_PATTERNS = 5


class PerformanceError(RuntimeError):
    """A performance record could not be read or written."""


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load(path=None):
    path = Path(path) if path else STORE
    if not path.exists():
        return {"updated": None, "videos": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PerformanceError("%s is not valid JSON (%s)" % (path, exc)) from exc
    if not isinstance(data, dict) or not isinstance(data.get("videos"), list):
        raise PerformanceError('%s must contain {"videos": [...]}' % path)
    return data


def _save(data, path=None):
    path = Path(path) if path else STORE
    data["updated"] = _iso(_now())
    tmp = path.with_name(path.name + ".tmp.%d" % os.getpid())
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def record(video_id, title="", angle="", differentiator="", confidence="",
           published_at=None, path=None):
    """Link a published video to the topic decision that produced it."""
    if not video_id or not str(video_id).strip():
        raise PerformanceError("a video id is required")
    data = load(path)
    for entry in data["videos"]:
        if entry.get("videoId") == video_id:
            entry.update({k: v for k, v in {
                "title": title, "angle": angle,
                "differentiator": differentiator, "confidence": confidence,
            }.items() if v})
            _save(data, path)
            return entry
    entry = {
        "videoId": str(video_id).strip(),
        "title": title,
        "angle": angle,
        "differentiator": differentiator,
        "confidence": (confidence or "").lower(),
        "publishedAt": published_at or _iso(_now()),
        "checks": [],
    }
    data["videos"].append(entry)
    _save(data, path)
    return entry


def _fetch(ids, api_key, opener=None):
    """Public statistics for up to 50 ids. Returns {id: stats-or-None}."""
    query = urllib.parse.urlencode({
        "part": "statistics,status,snippet", "id": ",".join(ids), "key": api_key,
    })
    url = "%s?%s" % (API, query)
    try:
        raw = (opener or urllib.request.urlopen)(url, timeout=30).read()
    except Exception as exc:
        raise PerformanceError("could not reach the YouTube API: %s" % exc) from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise PerformanceError("unreadable API response") from exc
    if "error" in payload:
        raise PerformanceError("YouTube API: %s" %
                               payload["error"].get("message", "unknown error"))
    found = {}
    for item in payload.get("items", []):
        stats = item.get("statistics") or {}
        found[item["id"]] = {
            "views": int(stats.get("viewCount", 0) or 0),
            "likes": int(stats.get("likeCount", 0) or 0),
            "comments": int(stats.get("commentCount", 0) or 0),
            "privacy": (item.get("status") or {}).get("privacyStatus"),
            "publishedAt": (item.get("snippet") or {}).get("publishedAt"),
        }
    return {vid: found.get(vid) for vid in ids}


def refresh(api_key=None, path=None, opener=None):
    """Fetch current statistics for every tracked video."""
    api_key = api_key or os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        raise PerformanceError(
            "no API key. Create one in the Google Cloud console (Credentials → "
            "API key) and pass --api-key, or set YOUTUBE_API_KEY."
        )
    data = load(path)
    ids = [v["videoId"] for v in data["videos"] if v.get("videoId")]
    if not ids:
        return {"checked": 0, "public": 0, "pending": 0}

    seen, public, pending = {}, 0, 0
    for i in range(0, len(ids), 50):                 # the API takes 50 at a time
        seen.update(_fetch(ids[i:i + 50], api_key, opener))

    when = _iso(_now())
    for entry in data["videos"]:
        stats = seen.get(entry["videoId"])
        if not stats:
            entry["state"] = "unavailable"           # deleted, or wrong id
            pending += 1
            continue
        if stats.get("privacy") and stats["privacy"] != "public":
            entry["state"] = "not yet public"
            pending += 1
            continue
        entry["state"] = "public"
        if stats.get("publishedAt"):
            entry["publishedAt"] = stats["publishedAt"]
        entry.setdefault("checks", []).append({
            "at": when, "views": stats["views"],
            "likes": stats["likes"], "comments": stats["comments"],
        })
        entry["checks"] = entry["checks"][-30:]
        public += 1
    _save(data, path)
    return {"checked": len(ids), "public": public, "pending": pending}


# --------------------------------------------------------------------------
# the digest — what ATLAS reads
# --------------------------------------------------------------------------
def _latest(entry):
    return entry["checks"][-1] if entry.get("checks") else None


def _views_per_day(entry, now=None):
    """Views normalised by age, so a week-old video is comparable to a new one."""
    last = _latest(entry)
    published = _parse(entry.get("publishedAt"))
    if not last or not published:
        return None
    days = max(0.25, ((now or _now()) - published).total_seconds() / 86400.0)
    return last["views"] / days


def _median(values):
    if not values:
        return None
    v = sorted(values)
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2


def digest(path=None, out=None, now=None):
    """Write performance.md. Returns the text."""
    data = load(path)
    now = now or _now()
    measured = []
    for entry in data["videos"]:
        rate = _views_per_day(entry, now)
        if rate is not None:
            measured.append((rate, entry))
    measured.sort(key=lambda pair: pair[0], reverse=True)

    lines = ["# What actually worked", "",
             "Generated %s from performance.json. ATLAS reads this before picking topics." % _iso(now),
             ""]
    pending = [v for v in data["videos"] if v.get("state") in ("not yet public", "unavailable")]
    if pending:
        lines += ["%d video(s) have no statistics yet (private, scheduled, or unavailable) "
                  "and are excluded — they are not counted as zero." % len(pending), ""]

    if not measured:
        lines += ["## No measured videos yet", "",
                  "Nothing to learn from. Publish, then run `performance.py refresh`.",
                  "Until then, choose topics on evidence from other channels, not from ours.", ""]
        text = "\n".join(lines)
        (Path(out) if out else DIGEST).write_text(text, encoding="utf-8")
        return text

    rates = [r for r, _ in measured]
    mid = _median(rates)
    lines += ["## The record", "",
              "| video | views/day | views | age (d) | confidence ATLAS gave it |",
              "| --- | ---: | ---: | ---: | --- |"]
    for rate, entry in measured:
        last = _latest(entry)
        published = _parse(entry.get("publishedAt"))
        age = (now - published).total_seconds() / 86400.0 if published else 0
        lines.append("| %s | %.1f | %d | %.1f | %s |" % (
            (entry.get("title") or entry["videoId"])[:52],
            rate, last["views"], age, entry.get("confidence") or "—"))
    lines += ["", "Median: **%.1f views/day** across %d video(s)." % (mid, len(measured)), ""]

    if len(measured) < MIN_FOR_PATTERNS:
        lines += ["## Not enough data to draw a pattern", "",
                  "%d measured video(s); patterns drawn from fewer than %d are noise. "
                  "Do not change direction on this. Keep choosing topics on outlier "
                  "evidence from other channels, and re-read this once there are %d."
                  % (len(measured), MIN_FOR_PATTERNS, MIN_FOR_PATTERNS), ""]
    else:
        third = max(1, len(measured) // 3)
        best, worst = measured[:third], measured[-third:]
        lines += ["## Best third", ""]
        for rate, entry in best:
            lines.append("- **%s** — %.1f/day. Angle: %s" % (
                (entry.get("title") or entry["videoId"])[:60], rate,
                entry.get("angle") or "not recorded"))
        lines += ["", "## Worst third", ""]
        for rate, entry in worst:
            lines.append("- **%s** — %.1f/day. Angle: %s" % (
                (entry.get("title") or entry["videoId"])[:60], rate,
                entry.get("angle") or "not recorded"))
        spread = (best[0][0] / worst[-1][0]) if worst[-1][0] > 0 else float("inf")
        lines += ["", "The best video is **%.1f×** the worst. %s" % (
            spread,
            "That gap is large enough that topic choice is doing real work — study the "
            "best third's angles." if spread >= 3 else
            "That gap is small; topic choice is not yet the limiting factor. Look at "
            "titles, thumbnails and hooks before blaming topic selection."), ""]

        # Is ATLAS's own confidence worth anything? If not, say so.
        lines += _calibration(measured)

    lines += ["## Instructions for the next run", "",
              "1. Propose at least one topic close to the best third's angles.",
              "2. Do not repeat an angle from the worst third without saying what changes.",
              "3. If a topic resembles one already here, say which and what will differ.", ""]
    text = "\n".join(lines)
    (Path(out) if out else DIGEST).write_text(text, encoding="utf-8")
    return text


def _calibration(measured):
    """Did high-confidence picks actually do better than low-confidence ones?"""
    buckets = {}
    for rate, entry in measured:
        conf = (entry.get("confidence") or "").lower()
        if conf in ("high", "medium", "low"):
            buckets.setdefault(conf, []).append(rate)
    if len(buckets) < 2 or sum(len(v) for v in buckets.values()) < MIN_FOR_PATTERNS:
        return []
    out = ["## Is ATLAS's confidence worth anything?", ""]
    for name in ("high", "medium", "low"):
        if name in buckets:
            out.append("- %s: median %.1f views/day across %d video(s)"
                       % (name, _median(buckets[name]), len(buckets[name])))
    hi, lo = buckets.get("high"), buckets.get("low")
    if hi and lo:
        h, l = _median(hi), _median(lo)
        if h > l * 1.3:
            out += ["", "High-confidence picks are outperforming. The judgement is adding "
                        "value — keep weighting it."]
        elif l > h * 1.3:
            out += ["", "**Low-confidence picks are beating high-confidence ones.** The "
                        "confidence rating is inverted or meaningless. Stop weighting it "
                        "and re-read what the best third actually have in common."]
        else:
            out += ["", "**Confidence is not predicting anything** — high and low perform "
                        "about the same. Treat the rating as noise until it earns its place, "
                        "and pick on evidence instead."]
    return out + [""]


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(prog="performance.py",
                                     description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_rec = sub.add_parser("record", help="link a published video to its topic")
    p_rec.add_argument("video_id")
    p_rec.add_argument("--title", default="")
    p_rec.add_argument("--angle", default="")
    p_rec.add_argument("--differentiator", default="")
    p_rec.add_argument("--confidence", default="", choices=["", "high", "medium", "low"])
    p_rec.add_argument("--published-at", default=None)

    p_ref = sub.add_parser("refresh", help="fetch current statistics")
    p_ref.add_argument("--api-key", default=None)

    sub.add_parser("digest", help="write performance.md")
    sub.add_parser("show", help="print the raw store")

    args = parser.parse_args(argv)
    try:
        if args.command == "record":
            entry = record(args.video_id, args.title, args.angle, args.differentiator,
                           args.confidence, args.published_at)
            print("recorded %s" % entry["videoId"])
        elif args.command == "refresh":
            got = refresh(args.api_key)
            print("checked %d video(s): %d public, %d without statistics yet"
                  % (got["checked"], got["public"], got["pending"]))
        elif args.command == "digest":
            digest()
            print("wrote %s" % DIGEST)
        else:
            print(json.dumps(load(), indent=2))
        return 0
    except PerformanceError as exc:
        print("performance.py: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
