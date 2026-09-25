#!/usr/bin/env python3
"""Fetch b-roll automatically, with its licence attached.

This closes the last manual step: instead of dropping files into ``assets/`` by
hand, the pipeline searches stock libraries whose licences permit commercial
reuse, downloads what it needs, and records provenance so ``render.py``'s
licence gate is satisfied with a real licence rather than a rubber stamp.

Sources
-------
Two kinds, and the difference matters for what you can make.

**Stock b-roll** — *pexels*, *pixabay*. Clean, generic, free for commercial use.
Good for illustrating a point. Nobody will recognise it.

**Archive footage** — *archive* (Internet Archive), *commons* (Wikimedia
Commons). Real film, newsreel, documentary and public-record material, mostly
public domain or Creative Commons. This is what you clip and talk over: footage
that is *about* something, which your narration can then be about in turn.
Neither needs an API key. Ask for it deliberately — ``--provider archive`` —
because mixing archival film with generic stock in one video looks like an
accident rather than an edit.

The two groups are ``stock`` (the default) and ``archive``. **Neither falls back
to the other**, on purpose: an empty archive search is a signal to rewrite the
search term, not licence to drop a stock shot of a laptop into a newsreel.
A single source is addressable by its own name — ``pexels``, ``pixabay``,
``internetarchive``, ``commons``.

Both archive providers check the licence on each item and **skip anything whose
rights are not clearly public domain or Creative Commons**. An archive that
happily handed you a copyrighted film would be worse than no archive.

What this cannot do is pull clips from YouTube, TikTok or Instagram, and the
reason is the download rather than the edit: those platforms' terms forbid
downloading their content, whatever you do with it afterwards. Note that a
YouTube video licensed **CC-BY** grants you reuse rights — but the sanctioned
way to use it is YouTube's own editor inside YouTube, not a scraper, because the
licence covers the copyright and the terms still cover the download.

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
import re
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


# --------------------------------------------------------------------------
# archive footage — real material, licence-checked
# --------------------------------------------------------------------------
PD_MARKERS = ("creativecommons.org", "publicdomain", "public domain", "cc0",
              "cc-by", "cc by", "no known copyright")
PD_COLLECTIONS = ("prelinger", "publicdomainmovies", "opensource_movies",
                  "nasa", "usgovernmentfilms", "feature_films",
                  "moviesandfilms", "newsandpublicaffairs")
VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm", ".ogv", ".m4v")


def _licence_ok(*blobs):
    """Only accept material whose rights are stated and permissive."""
    text = " ".join(str(b or "").lower() for b in blobs)
    return any(marker in text for marker in PD_MARKERS)


def _archive(query, count, orientation, opener=None):
    """Internet Archive. Public-domain film, newsreel and documentary."""
    search = "https://archive.org/advancedsearch.php?" + urllib.parse.urlencode({
        "q": '%s AND mediatype:(movies)' % query,
        "rows": max(5, min(50, count * 6)), "page": 1, "output": "json",
    }) + "&fl%5B%5D=identifier&fl%5B%5D=title&fl%5B%5D=licenseurl&fl%5B%5D=collection"
    payload = json.loads(_get(search, opener=opener).decode("utf-8"))
    docs = ((payload.get("response") or {}).get("docs") or [])
    out = []
    for doc in docs:
        ident = doc.get("identifier")
        if not ident:
            continue
        collections = doc.get("collection") or []
        if isinstance(collections, str):
            collections = [collections]
        permissive = _licence_ok(doc.get("licenseurl")) or \
            any(c in PD_COLLECTIONS for c in collections)
        if not permissive:
            continue                                   # rights unclear: skip it
        meta = json.loads(_get("https://archive.org/metadata/%s" % ident,
                               opener=opener).decode("utf-8"))
        info = meta.get("metadata") or {}
        if not (_licence_ok(info.get("licenseurl"), info.get("rights")) or
                any(c in PD_COLLECTIONS for c in collections)):
            continue
        files = [f for f in (meta.get("files") or [])
                 if str(f.get("name", "")).lower().endswith(VIDEO_EXT)]
        if not files:
            continue
        pick = min(files, key=lambda f: int(f.get("size") or 1 << 40))
        licence = info.get("licenseurl") or info.get("rights") or \
            "public domain (Internet Archive collection: %s)" % ", ".join(collections[:2])
        page = "https://archive.org/details/%s" % ident
        creator = info.get("creator") or "unknown"
        if isinstance(creator, list):
            creator = creator[0] if creator else "unknown"
        out.append({
            "provider": "internetarchive", "id": ident,
            "url": "https://archive.org/download/%s/%s"
                   % (ident, urllib.parse.quote(pick["name"])),
            "page": page, "width": None, "height": None, "duration": None,
            "author": creator, "author_url": page,
            "license": "%s — via Internet Archive. Source: %s" % (licence, page),
            "attribution": "Archive footage: %s (%s), via the Internet Archive"
                           % (doc.get("title") or ident, creator),
        })
        if len(out) >= count * 2:
            break
    return out


def _commons(query, count, orientation, opener=None):
    """Wikimedia Commons. Everything there is CC or public domain."""
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": "filetype:video %s" % query,
        "gsrnamespace": 6, "gsrlimit": max(5, min(50, count * 6)),
        "prop": "imageinfo", "iiprop": "url|size|extmetadata|user",
    })
    payload = json.loads(_get(url, opener=opener).decode("utf-8"))
    pages = ((payload.get("query") or {}).get("pages") or {})
    out = []
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        src = info.get("url")
        if not src or not str(src).lower().endswith(VIDEO_EXT):
            continue
        extra = info.get("extmetadata") or {}
        licence = (extra.get("LicenseShortName") or {}).get("value", "")
        if not _licence_ok(licence, (extra.get("UsageTerms") or {}).get("value")):
            continue
        author = re.sub(r"<[^>]+>", "", (extra.get("Artist") or {}).get("value", "")
                        or info.get("user") or "unknown").strip() or "unknown"
        desc = page.get("title", "").replace("File:", "")
        out.append({
            "provider": "commons", "id": str(page.get("pageid")),
            "url": src, "page": info.get("descriptionurl", ""),
            "width": info.get("width"), "height": info.get("height"),
            "duration": None, "author": author, "author_url": "",
            "license": "%s — via Wikimedia Commons. Source: %s"
                       % (licence, info.get("descriptionurl", "")),
            "attribution": "%s by %s, via Wikimedia Commons (%s)"
                           % (desc, author, licence),
        })
        if len(out) >= count * 2:
            break
    return out


PROVIDERS = {"pexels": _pexels, "pixabay": _pixabay,
             "internetarchive": _archive, "commons": _commons}
# Two groups rather than a flat list, because the choice between them is a
# choice about what kind of video you are making, not a source preference.
STOCK = ("pexels", "pixabay")
ARCHIVE = ("internetarchive", "commons")
GROUPS = {"stock": STOCK, "archive": ARCHIVE}

# The host each provider actually has to reach, and the key it needs. A
# missing key and a blocked host fail at the same moment and read almost the
# same, so `reachable` separates them: one is something you set, the other is
# something only the environment's network policy can allow.
PROVIDER_HOSTS = {
    "pexels": ("api.pexels.com", "PEXELS_API_KEY"),
    "pixabay": ("pixabay.com", "PIXABAY_API_KEY"),
    "internetarchive": ("archive.org", None),
    "commons": ("commons.wikimedia.org", None),
}


def reachable(timeout=12):
    """Can each provider be reached, and is its key set?

    Returns [(provider, host, ok, note)]. Nothing here downloads a video; it
    asks the host for its head and reports what came back, so the answer to
    "why did sourcing fail" is one command rather than a guess.
    """
    import http.client
    out = []
    for name in sorted(PROVIDER_HOSTS):
        host, key_name = PROVIDER_HOSTS[name]
        has_key = bool(os.environ.get(key_name)) if key_name else True
        ok, note = False, ""
        try:
            conn = http.client.HTTPSConnection(host, timeout=timeout)
            conn.request("HEAD", "/", headers={"User-Agent": UA})
            status = conn.getresponse().status
            conn.close()
            # A connection that completes is not the same as a host that
            # answered. Every one of these serves its root publicly, so a 4xx
            # here is the gateway refusing the host rather than the host
            # refusing us -- and reporting that as "reachable" sends someone
            # off to find an API key for a host they cannot open.
            ok = status < 400
            note = ("HTTP %d" % status if ok
                    else "HTTP %d — refused before reaching the host" % status)
        except Exception as exc:
            note = "unreachable: %s" % str(exc)[:70]
        if ok and key_name and not has_key:
            note += ", but %s is not set" % key_name
        out.append((name, host, ok and has_key, note))
    return out


def search(query, count=3, provider=None, orientation="portrait", opener=None):
    """Find candidate clips. Tries each provider that has a key configured."""
    if not (query or "").strip():
        raise FetchError("a search term is required")
    if provider in GROUPS:
        names = list(GROUPS[provider])
    elif provider:
        names = [provider]
    else:
        # stock by default: b-roll is the common case, and mixing archival film
        # with generic stock in one result set gives an incoherent video. Ask for
        # archive footage deliberately, with --provider archive.
        names = list(STOCK)
    results, problems = [], []
    for name in names:
        if name not in PROVIDERS:
            raise FetchError("unknown provider %r — choose a group ('stock' or "
                             "'archive') or one source (%s). No group falls back "
                             "to the other: archival film cut against generic "
                             "stock looks like an accident."
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

    # Ask for more candidates than will be taken. Choosing needs room: a pool
    # the size of the request forces the first duplicate, whatever the logic
    # below decides.
    candidates = search(query, max(count * 5, 8), provider, orientation, opener)

    def key(clip):
        return "%s:%s" % (clip["provider"], clip["id"])

    blocked = set(exclude or ())                  # never twice inside one video
    usable = [c for c in candidates if key(c) not in blocked]
    if not usable:
        # A duplicate inside one video is visible to the viewer, so this is a
        # hard stop rather than a degradation. Falling back to the full list
        # here is what used to make every beat the same clip.
        raise FetchError(
            "every clip found for %r is already used elsewhere in this video "
            "(%d candidate%s, all taken). Two beats of the same footage is "
            "visible, so vary this beat's search term instead."
            % (query, len(candidates), "" if len(candidates) == 1 else "s"))

    history = _used_history(used_path)
    fresh = [c for c in usable if key(c) not in history]
    if fresh:
        chosen = fresh[:count]
    else:
        # Everything here has been used in an EARLIER video. That is a
        # degradation rather than a defect, so take the least recently used
        # rather than the first — always taking the first is how a channel
        # ends up with the same b-roll in every upload.
        chosen = sorted(usable, key=lambda c: history.get(key(c), -1))[:count]
        print("fetch_clips.py: every match for %r has been used in a previous "
              "video; reusing the least recent. Vary the search terms to keep "
              "the channel from looking templated." % query, file=sys.stderr)

    ledger = _load(ledger_file, {})
    got = []
    for clip in chosen:
        name = "%s-%s.mp4" % (clip["provider"], clip["id"])
        target = out_dir / name
        if not target.exists():
            data = (downloader or _get)(clip["url"])
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
    _remember([key(c) for c in chosen], used_path)
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
    p_search.add_argument("--provider", choices=list(GROUPS) + list(PROVIDERS))
    p_search.add_argument("--count", type=int, default=5)

    p_fetch = sub.add_parser("fetch", help="download clips and record their licences")
    p_fetch.add_argument("query")
    p_fetch.add_argument("--provider", choices=list(GROUPS) + list(PROVIDERS))
    p_fetch.add_argument("--count", type=int, default=1)
    p_fetch.add_argument("--out", default=None)

    p_auto = sub.add_parser("autofill", help="fill a render plan's empty beats")
    p_auto.add_argument("plan")
    p_auto.add_argument("--provider", choices=list(GROUPS) + list(PROVIDERS))
    p_auto.add_argument("--out", default=None)

    sub.add_parser("reachable", help="can the footage sources be reached at all?")

    p_in = sub.add_parser("into-inbox",
                          help="download one clip you have the rights to into inbox/")
    p_in.add_argument("url")
    p_in.add_argument("--creator", required=True)
    p_in.add_argument("--license", dest="licence", required=True,
                      help="must be one the spec allows")
    p_in.add_argument("--attribution", required=True)
    p_in.add_argument("--proof", default="",
                      help="the receipt, DM screenshot or licence page, saved locally")
    p_in.add_argument("--name", default="", help="filename to save as")
    p_in.add_argument("--notes", default="")

    p_att = sub.add_parser("attribution", help="print the credit block for the description")
    p_att.add_argument("plan")

    args = parser.parse_args(argv)
    try:
        if args.command == "into-inbox":
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import review as review_mod, spec as spec_mod
            box = Path(__file__).resolve().parent / spec_mod.get("sources.inbox")
            box.mkdir(parents=True, exist_ok=True)
            name = args.name or Path(urllib.parse.urlsplit(args.url).path).name
            if not name:
                raise FetchError("no filename in %s — pass --name" % args.url)
            target = box / name
            blob = _get(args.url)
            if len(blob) < 100000:
                raise FetchError("%s returned only %d bytes — not a usable clip"
                                 % (args.url, len(blob)))
            target.write_bytes(blob)
            # Logged as what it is, at the moment it arrives. A clip that sits
            # in the inbox without a row is a clip nobody can vouch for later.
            review_mod.log_source(
                target.name, url=args.url, creator=args.creator,
                licence=args.licence, attribution=args.attribution,
                proof=args.proof, footage_type=spec_mod.get("sources.footage_type"),
                notes=args.notes)
            print("saved %s (%.1f MB) and logged it" % (target, len(blob) / 1048576.0))
            report = review_mod.intake(box)
            for row in report["unusable"]:
                if Path(row["clip"]).name == target.name:
                    print("  not usable yet: %s" % "; ".join(row["why"]))
            return 0
        if args.command == "reachable":
            rows = reachable()
            for name, host, ok, note in rows:
                print("%s %-16s %-26s %s" % ("  ok  " if ok else " FAIL ",
                                             name, host, note))
            if not any(ok for _, _, ok, _ in rows):
                print("\nNo footage source can be reached. Where every host "
                      "fails to connect it is the environment's network "
                      "policy, not the sources: allow these hosts, or widen "
                      "the access level, in the environment's settings.",
                      file=sys.stderr)
            return 0 if any(ok for _, _, ok, _ in rows) else 1
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
