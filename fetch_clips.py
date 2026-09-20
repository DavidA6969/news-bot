#!/usr/bin/env python3
"""Fetch b-roll automatically, with its licence attached.

This closes the last manual step: instead of dropping files into ``assets/`` by
hand, the pipeline searches stock libraries whose licences permit commercial
reuse, downloads what it needs, and records provenance so ``render.py``'s
licence gate is satisfied with a real licence rather than a rubber stamp.

Sources
-------
**Pexels** and **Pixabay**. Both are free, both allow commercial use, and both
issue free API keys. Pexels requires crediting the creator when you access it
through the API — so ``attribution`` generates the credit block for your video
description, and you should paste it in.

What this deliberately cannot do is pull clips from YouTube, TikTok or
Instagram. Those are other people's copyrighted uploads; re-cutting them is
infringement, breaches those platforms' terms, and is the exact pattern
YouTube's Inauthentic Content policy demonetizes. Stock libraries exist because
this problem is common, and they solve it legally.

    export PEXELS_API_KEY=...        # https://www.pexels.com/api/
    export PIXABAY_API_KEY=...       # https://pixabay.com/api/docs/

    python3 fetch_clips.py search "focused work desk"
    python3 fetch_clips.py fetch "focused work desk" --count 3
    python3 fetch_clips.py autofill render.json
    python3 fetch_clips.py attribution render.json
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

__all__ = ["search", "fetch", "autofill", "attribution_for", "FetchError", "PROVIDERS"]

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
LEDGER = ASSETS / "licenses.json"
USED = HERE / "clips_used.json"
UA = "news-bot/1.0 (automated b-roll fetcher)"


class FetchError(RuntimeError):
    """A clip could not be found or downloaded."""


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _get(url, headers=None, timeout=30, opener=None):
    if opener:
        return opener(url, headers or {}, timeout)
    request = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except Exception as exc:
        raise FetchError("request to %s failed: %s" % (urllib.parse.urlsplit(url).netloc, exc)) from exc


# --------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------
def _pexels(query, count, orientation, opener=None):
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        raise FetchError("PEXELS_API_KEY is not set — get a free key at "
                         "https://www.pexels.com/api/")
    url = "https://api.pexels.com/videos/search?" + urllib.parse.urlencode({
        "query": query, "per_page": max(1, min(80, count * 4)),
        "orientation": orientation, "size": "medium",
    })
    payload = json.loads(_get(url, {"Authorization": key}, opener=opener).decode("utf-8"))
    out = []
    for item in payload.get("videos", []):
        files = [f for f in item.get("video_files", []) if f.get("link")]
        if not files:
            continue
        # the smallest file at least 1080 wide, else the largest available
        big = [f for f in files if (f.get("width") or 0) >= 1080]
        pick = min(big, key=lambda f: f.get("width", 0)) if big else \
            max(files, key=lambda f: f.get("width", 0))
        author = item.get("user", {}).get("name", "unknown")
        out.append({
            "provider": "pexels", "id": str(item.get("id")),
            "url": pick["link"], "page": item.get("url", ""),
            "width": pick.get("width"), "height": pick.get("height"),
            "duration": item.get("duration"), "author": author,
            "author_url": item.get("user", {}).get("url", ""),
            "license": "Pexels License — free for commercial use; credit required via API. "
                       "Source: %s" % item.get("url", ""),
            "attribution": "Video by %s on Pexels (%s)" % (author, item.get("url", "")),
        })
    return out


def _pixabay(query, count, orientation, opener=None):
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        raise FetchError("PIXABAY_API_KEY is not set — get a free key at "
                         "https://pixabay.com/api/docs/")
    url = "https://pixabay.com/api/videos/?" + urllib.parse.urlencode({
        "key": key, "q": query, "per_page": max(3, min(200, count * 4)),
        "safesearch": "true",
    })
    payload = json.loads(_get(url, opener=opener).decode("utf-8"))
    out = []
    for item in payload.get("hits", []):
        streams = item.get("videos", {})
        pick = streams.get("large") or streams.get("medium") or streams.get("small")
        if not pick or not pick.get("url"):
            continue
        author = item.get("user", "unknown")
        page = item.get("pageURL", "")
        out.append({
            "provider": "pixabay", "id": str(item.get("id")),
            "url": pick["url"], "page": page,
            "width": pick.get("width"), "height": pick.get("height"),
            "duration": item.get("duration"), "author": author, "author_url": "",
            "license": "Pixabay Content License — free for commercial use. Source: %s" % page,
            "attribution": "Video by %s on Pixabay (%s)" % (author, page),
        })
    return out


PROVIDERS = {"pexels": _pexels, "pixabay": _pixabay}


def search(query, count=3, provider=None, orientation="portrait", opener=None):
    """Find candidate clips. Tries each provider that has a key configured."""
    if not (query or "").strip():
        raise FetchError("a search term is required")
    names = [provider] if provider else list(PROVIDERS)
    results, problems = [], []
    for name in names:
        if name not in PROVIDERS:
            raise FetchError("unknown provider %r — choose from %s"
                             % (name, ", ".join(PROVIDERS)))
        try:
            results.extend(PROVIDERS[name](query, count, orientation, opener))
        except FetchError as exc:
            problems.append("%s: %s" % (name, exc))
    if not results:
        raise FetchError("no clips found for %r. %s" % (query, " ".join(problems)))
    return results


# --------------------------------------------------------------------------
# ledger + variety
# --------------------------------------------------------------------------
def _load(path, default):
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FetchError("%s is not valid JSON (%s)" % (path, exc)) from exc


def _save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp.%d" % os.getpid())
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _used_history(used_path=None):
    """key -> position in the used list, so "least recently used" is orderable."""
    clips = _load(used_path or USED, {"clips": []}).get("clips", [])
    return {key: i for i, key in enumerate(clips)}


def _remember(keys, used_path=None):
    path = Path(used_path or USED)
    data = _load(path, {"clips": []})
    data["clips"] = (data.get("clips", []) + list(keys))[-400:]
    _save(path, data)


def fetch(query, count=1, provider=None, out_dir=None, orientation="portrait",
          opener=None, downloader=None, ledger_path=None, used_path=None,
          exclude=None):
    """Download clips for a search term and record where each came from.

    Clips already used are skipped where possible: reusing the same footage
    across videos is what makes a channel look templated, which is the thing
    the Inauthentic Content policy is actually looking for.
    """
    out_dir = Path(out_dir or ASSETS)
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger_file = Path(ledger_path) if ledger_path else out_dir / "licenses.json"

    candidates = search(query, count, provider, orientation, opener)
    blocked = set(exclude or ())                  # never twice inside one video
    candidates = [c for c in candidates
                  if "%s:%s" % (c["provider"], c["id"]) not in blocked] or candidates
    history = _used_history(used_path)
    fresh = [c for c in candidates
             if "%s:%s" % (c["provider"], c["id"]) not in history]
    if fresh:
        chosen = fresh[:count]
    else:
        # everything here has been used before, so take the least recent rather
        # than the first — always taking the first is how every beat ends up
        # being the same clip
        chosen = sorted(candidates,
                        key=lambda c: history.get("%s:%s" % (c["provider"], c["id"]), -1)
                        )[:count]
        print("fetch_clips.py: every match for %r has been used before; reusing the "
              "least recent. Vary the search terms to keep the channel from looking "
              "templated." % query, file=sys.stderr)

    ledger = _load(ledger_file, {})
    got = []
    for clip in chosen:
        name = "%s-%s.mp4" % (clip["provider"], clip["id"])
        target = out_dir / name
        if not target.exists():
            data = (downloader or _get)(clip["url"]) if downloader else _get(clip["url"])
            if len(data) < 2048:
                raise FetchError("%s returned only %d bytes — not a video"
                                 % (clip["url"], len(data)))
            target.write_bytes(data)
        try:
            rel = str(target.resolve().relative_to(HERE))
        except ValueError:
            rel = str(target.resolve())           # outside the repo: keep it absolute
        ledger[rel] = {k: clip[k] for k in
                       ("provider", "id", "page", "author", "author_url",
                        "license", "attribution")}
        ledger[rel]["query"] = query
        ledger[rel]["fetched"] = _now()
        got.append({"path": rel, **ledger[rel]})
    _save(ledger_file, ledger)
    _remember(["%s:%s" % (c["provider"], c["id"]) for c in chosen], used_path)
    return got


# --------------------------------------------------------------------------
# render-plan integration
# --------------------------------------------------------------------------
def autofill(plan_path, out_dir=None, provider=None, opener=None, downloader=None,
             ledger_path=None, used_path=None):
    """Fill in every beat that has a `search` term but no clip yet.

    The licence written here is the real one from the provider, so render.py's
    gate is satisfied by provenance rather than by a rubber stamp.
    """
    path = Path(plan_path)
    plan = _load(path, None)
    if not isinstance(plan, dict) or not isinstance(plan.get("beats"), list):
        raise FetchError("%s does not look like a render plan" % path)

    filled = 0
    taken = set()                                 # no clip twice in one video
    for i, beat in enumerate(plan["beats"]):
        if beat.get("clip") and beat.get("license"):
            continue
        term = (beat.get("search") or beat.get("caption") or "").strip()
        if not term:
            raise FetchError('beats[%d] has no clip and no "search" term to find one with' % i)
        got = fetch(term, 1, provider, out_dir, "portrait", opener, downloader,
                    ledger_path, used_path, exclude=taken)[0]
        taken.add("%s:%s" % (got["provider"], got["id"]))
        beat["clip"] = got["path"]
        beat["license"] = got["license"]
        beat["attribution"] = got["attribution"]
        filled += 1
    _save(path, plan)
    return {"filled": filled, "beats": len(plan["beats"])}


def attribution_for(plan_path):
    """The credit block to paste into the video description."""
    plan = _load(Path(plan_path), None)
    if not isinstance(plan, dict):
        raise FetchError("%s does not look like a render plan" % plan_path)
    credits, seen = [], set()
    for beat in plan.get("beats", []):
        line = (beat.get("attribution") or "").strip()
        if line and line not in seen:
            seen.add(line)
            credits.append(line)
    audio = plan.get("audio") or {}
    if audio.get("attribution"):
        credits.append(audio["attribution"].strip())
    if not credits:
        return ""
    return "Footage:\n" + "\n".join("- " + c for c in credits)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="fetch_clips.py",
                                     description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser("search", help="list candidates without downloading")
    p_search.add_argument("query")
    p_search.add_argument("--provider", choices=list(PROVIDERS))
    p_search.add_argument("--count", type=int, default=5)

    p_fetch = sub.add_parser("fetch", help="download clips and record their licences")
    p_fetch.add_argument("query")
    p_fetch.add_argument("--provider", choices=list(PROVIDERS))
    p_fetch.add_argument("--count", type=int, default=1)
    p_fetch.add_argument("--out", default=None)

    p_auto = sub.add_parser("autofill", help="fill a render plan's empty beats")
    p_auto.add_argument("plan")
    p_auto.add_argument("--provider", choices=list(PROVIDERS))
    p_auto.add_argument("--out", default=None)

    p_att = sub.add_parser("attribution", help="print the credit block for the description")
    p_att.add_argument("plan")

    args = parser.parse_args(argv)
    try:
        if args.command == "search":
            for clip in search(args.query, args.count, args.provider)[:args.count]:
                print("%-8s %-10s %sx%s %ss  %s" % (
                    clip["provider"], clip["id"], clip.get("width"), clip.get("height"),
                    clip.get("duration"), clip["page"]))
        elif args.command == "fetch":
            for got in fetch(args.query, args.count, args.provider, args.out):
                print("%s   %s" % (got["path"], got["attribution"]))
        elif args.command == "autofill":
            result = autofill(args.plan, args.out, args.provider)
            print("filled %d of %d beats in %s" %
                  (result["filled"], result["beats"], args.plan))
            block = attribution_for(args.plan)
            if block:
                print("\nAdd this to the video description:\n\n" + block)
        else:
            block = attribution_for(args.plan)
            print(block or "no attributable footage in that plan")
        return 0
    except FetchError as exc:
        print("fetch_clips.py: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
