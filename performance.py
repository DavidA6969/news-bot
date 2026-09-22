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

__all__ = ["record", "refresh", "digest", "load", "PerformanceError", "SCOPES"]

SCOPES = ("youtube", "etsy")
DEFAULT_SCOPE = "youtube"
# what each scope calls the thing it publishes, so the digest reads naturally
NOUN = {"youtube": ("video", "videos"), "etsy": ("listing", "listings")}

HERE = Path(__file__).resolve().parent
STORE = HERE / "performance.json"
DIGEST = HERE / "performance.md"


def _digest_path(scope):
    return HERE / ("performance.md" if scope == DEFAULT_SCOPE
                   else "performance-%s.md" % scope)
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


def _scope(name):
    name = (name or DEFAULT_SCOPE).strip().lower()
    if name not in SCOPES:
        raise PerformanceError("scope must be one of %s (got %r)" % (", ".join(SCOPES), name))
    return name


def load(path=None):
    path = Path(path) if path else STORE
    if not path.exists():
        return {"updated": None, "scopes": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PerformanceError("%s is not valid JSON (%s)" % (path, exc)) from exc
    if not isinstance(data, dict):
        raise PerformanceError("%s must contain a JSON object" % path)
    # a file from before the Etsy shop existed held one flat list of videos
    if "videos" in data:
        legacy = data.pop("videos")
        data.setdefault("scopes", {})
        if isinstance(legacy, list) and legacy:
            data["scopes"].setdefault(DEFAULT_SCOPE, {"items": legacy})
    data.setdefault("scopes", {})
    if not isinstance(data["scopes"], dict):
        raise PerformanceError('%s has a "scopes" that is not an object' % path)
    for name, bucket in data["scopes"].items():
        if not isinstance(bucket, dict) or not isinstance(bucket.get("items"), list):
            raise PerformanceError('%s: scope %s must hold {"items": [...]}' % (path, name))
    return data


def items(data, scope):
    """The tracked records for one scope, creating the bucket if needed."""
    return data["scopes"].setdefault(_scope(scope), {"items": []})["items"]


def _save(data, path=None):
    path = Path(path) if path else STORE
    data["updated"] = _iso(_now())
    tmp = path.with_name(path.name + ".tmp.%d" % os.getpid())
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def record(video_id, title="", angle="", differentiator="", confidence="",
           published_at=None, path=None, scope=None):
    """Link a published item to the decision that produced it."""
    scope = _scope(scope)
    if not video_id or not str(video_id).strip():
        raise PerformanceError("an id is required")
    data = load(path)
    for entry in items(data, scope):
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
    items(data, scope).append(entry)
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


def _fetch_etsy(ids, shop_id, transport=None, token=None, token_path=None):
    """Listing views and favourites, through the Etsy client.

    Views are the closest thing Etsy gives you to the signal that matters, and
    unlike sales they need no extra OAuth scope.
    """
    sys.path.insert(0, str(HERE))
    import etsy as etsy_mod
    found = {}
    for listing_id in ids:
        try:
            got = etsy_mod._call("GET", "/shops/%s/listings/%s" % (shop_id, listing_id),
                                 None, token, transport, token_path)
        except Exception as exc:
            found[listing_id] = None
            if "404" in str(exc):
                continue
            raise PerformanceError("Etsy: %s" % exc) from exc
        found[listing_id] = {
            "views": int(got.get("views", 0) or 0),
            "likes": int(got.get("num_favorers", 0) or 0),
            "comments": 0,
            "privacy": "public" if got.get("state") == "active" else got.get("state"),
            "publishedAt": None,
        }
    return found


def refresh(api_key=None, path=None, opener=None, scope=None, shop_id=None,
            transport=None, token=None, token_path=None):
    """Fetch current statistics for everything tracked in a scope."""
    scope = _scope(scope)
    data = load(path)
    tracked = items(data, scope)
    ids = [v["videoId"] for v in tracked if v.get("videoId")]
    if not ids:
        return {"checked": 0, "public": 0, "pending": 0}

    seen, public, pending = {}, 0, 0
    if scope == "etsy":
        shop_id = shop_id or os.environ.get("ETSY_SHOP_ID")
        if not shop_id:
            raise PerformanceError(
                "no shop id. Find it with `python3 etsy.py whoami`, then pass "
                "--shop-id or set ETSY_SHOP_ID.")
        seen = _fetch_etsy(ids, shop_id, transport, token, token_path)
    else:
        api_key = api_key or os.environ.get("YOUTUBE_API_KEY")
        if not api_key:
            raise PerformanceError(
                "no API key. Create one in the Google Cloud console (Credentials → "
                "API key) and pass --api-key, or set YOUTUBE_API_KEY."
            )
        for i in range(0, len(ids), 50):              # the API takes 50 at a time
            seen.update(_fetch(ids[i:i + 50], api_key, opener))

    when = _iso(_now())
    for entry in tracked:
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


def digest(path=None, out=None, now=None, scope=None):
    """Write the digest for a scope. Returns the text."""
    scope = _scope(scope)
    one, many = NOUN[scope]
    data = load(path)
    now = now or _now()
    measured = []
    for entry in items(data, scope):
        rate = _views_per_day(entry, now)
        if rate is not None:
            measured.append((rate, entry))
    measured.sort(key=lambda pair: pair[0], reverse=True)

    reader = "ATLAS" if scope == "youtube" else "LOOM"
    lines = ["# What actually worked — %s" % scope, "",
             "Generated %s from performance.json. %s reads this before choosing."
             % (_iso(now), reader),
             ""]
    pending = [v for v in items(data, scope)
               if v.get("state") in ("not yet public", "unavailable")]
    if pending:
        lines += ["%d %s have no statistics yet (private, scheduled, or unavailable) "
                  "and are excluded — they are not counted as zero." % (len(pending), many), ""]

    if not measured:
        lines += ["## No measured %s yet" % many, "",
                  "Nothing to learn from. Publish, then run `performance.py refresh`.",
                  "Until then, choose on outside evidence, not on ours.", ""]
        text = "\n".join(lines)
        (Path(out) if out else _digest_path(scope)).write_text(text, encoding="utf-8")
        return text

    rates = [r for r, _ in measured]
    mid = _median(rates)
    lines += ["## The record", "",
              "| %s | views/day | views | engagement | age (d) | confidence |" % one,
              "| --- | ---: | ---: | ---: | ---: | --- |"]
    for rate, entry in measured:
        last = _latest(entry)
        published = _parse(entry.get("publishedAt"))
        age = (now - published).total_seconds() / 86400.0 if published else 0
        eng = (100.0 * last["likes"] / last["views"]) if last["views"] else 0.0
        lines.append("| %s | %.1f | %d | %.1f%% | %.1f | %s |" % (
            (entry.get("title") or entry["videoId"])[:46],
            rate, last["views"], eng, age, entry.get("confidence") or "—"))
    lines += ["", "Median: **%.1f views/day** across %d %s." % (mid, len(measured), many), ""]

    if len(measured) < MIN_FOR_PATTERNS:
        lines += ["## Not enough data to draw a pattern", "",
                  "%d measured %s; patterns drawn from fewer than %d are noise. "
                  "Do not change direction on this. Keep choosing on outside "
                  "evidence, and re-read once there are %d."
                  % (len(measured), many, MIN_FOR_PATTERNS, MIN_FOR_PATTERNS), ""]
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
        lines += ["", "The best %s is **%.1f×** the worst. %s" % (one,
            spread,
            "That gap is large enough that the choice is doing real work — study the "
            "best third's angles." if spread >= 3 else
            ("That gap is small; the choice is not yet the limiting factor. Look at "
             + ("titles, thumbnails and hooks" if scope == "youtube"
                else "the first photo, the title and the price")
             + " before blaming selection.")), ""]

        lines += _calibration(measured, reader)
        lines += _engagement(measured, scope)

    lines += ["## Instructions for the next run", "",
              "1. Propose at least one topic close to the best third's angles.",
              "2. Do not repeat an angle from the worst third without saying what changes.",
              "3. If a topic resembles one already here, say which and what will differ.", ""]
    text = "\n".join(lines)
    (Path(out) if out else _digest_path(scope)).write_text(text, encoding="utf-8")
    return text


def _engagement(measured, scope):
    """Likes per view: the only quality signal the public API actually gives.

    Retention and shares are what short-form is really judged on, and neither is
    in the Data API — so this is a proxy, and it is labelled as one. It is still
    worth reading: a modest view count with high engagement is a video that
    landed with the people it reached, which is the thing worth making more of.
    """
    rows = []
    for rate, entry in measured:
        last = _latest(entry)
        if last and last["views"] >= 100:
            rows.append((100.0 * last["likes"] / last["views"], rate, entry))
    if len(rows) < MIN_FOR_PATTERNS:
        return []
    rows.sort(key=lambda row: (row[0], row[1]), reverse=True)   # never compare the dicts
    out = ["## Which landed, rather than merely reached", ""]
    for eng, rate, entry in rows[:3]:
        out.append("- **%s** — %.1f%% engagement at %.1f views/day"
                   % ((entry.get("title") or entry["videoId"])[:52], eng, rate))
    best_eng = rows[0]
    best_reach = max(rows, key=lambda r: r[1])
    if best_eng[2] is not best_reach[2]:
        out += ["",
                "The most engaging one is not the most watched. That usually means "
                "the reach came from the hook and the engagement came from the "
                "substance — study the top of this list for what to say, and the "
                "top of the views list for how to open."]
    out += ["", "_Likes per view is a proxy. Retention and shares are what "
                "short-form is actually ranked on and neither is available here; "
                "read those in YouTube Studio._", ""]
    return out


def _calibration(measured, reader="ATLAS"):
    """Did high-confidence picks actually do better than low-confidence ones?"""
    buckets = {}
    for rate, entry in measured:
        conf = (entry.get("confidence") or "").lower()
        if conf in ("high", "medium", "low"):
            buckets.setdefault(conf, []).append(rate)
    if len(buckets) < 2 or sum(len(v) for v in buckets.values()) < MIN_FOR_PATTERNS:
        return []
    out = ["## Is %s's confidence worth anything?" % reader, ""]
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
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--scope", choices=list(SCOPES), default=DEFAULT_SCOPE,
                        help="which business (default: %s)" % DEFAULT_SCOPE)
    sub = parser.add_subparsers(dest="command", required=True)

    p_rec = sub.add_parser("record", parents=[common],
                           help="link a published item to the decision behind it")
    p_rec.add_argument("video_id")
    p_rec.add_argument("--title", default="")
    p_rec.add_argument("--angle", default="")
    p_rec.add_argument("--differentiator", default="")
    p_rec.add_argument("--confidence", default="", choices=["", "high", "medium", "low"])
    p_rec.add_argument("--published-at", default=None)

    p_ref = sub.add_parser("refresh", parents=[common], help="fetch current statistics")
    p_ref.add_argument("--api-key", default=None)
    p_ref.add_argument("--shop-id", default=None, help="Etsy shop id (etsy scope)")

    sub.add_parser("digest", parents=[common], help="write the digest")
    sub.add_parser("show", help="print the raw store")

    args = parser.parse_args(argv)
    try:
        if args.command == "record":
            entry = record(args.video_id, args.title, args.angle, args.differentiator,
                           args.confidence, args.published_at, scope=args.scope)
            print("recorded %s in %s" % (entry["videoId"], args.scope))
        elif args.command == "refresh":
            got = refresh(args.api_key, scope=args.scope, shop_id=args.shop_id)
            noun = NOUN[args.scope][1]
            print("checked %d %s: %d public, %d without statistics yet"
                  % (got["checked"], noun, got["public"], got["pending"]))
        elif args.command == "digest":
            digest(scope=args.scope)
            print("wrote %s" % _digest_path(args.scope))
        else:
            print(json.dumps(load(), indent=2))
        return 0
    except PerformanceError as exc:
        print("performance.py: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
