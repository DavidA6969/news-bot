#!/usr/bin/env python3
"""Upload a finished video to your own YouTube channel via the Data API v3.

This uses OAuth 2.0 and the official API — the path Google provides for
programmatic uploads to a channel you own. It does not drive youtube.com in a
browser, does not touch your Google password, and does not try to disguise
itself as a person. Automating the website would breach YouTube's Terms of
Service; the API does not.

Read this before your first run
-------------------------------
**An API project that has not passed YouTube's compliance audit can only
create PRIVATE videos.** Ask for `public` and the upload still lands private,
the API reports success, and the video cannot be appealed — the only fix is to
re-upload through an audited client or the YouTube app. So until you have
passed the audit, treat this as "upload and stage"; a human publishes. Once
audited, `--publish-at` hands scheduling to YouTube itself, which is more
reliable than keeping a machine awake to press the button.

Setup
-----
1. Google Cloud console → new project → enable **YouTube Data API v3**.
2. OAuth consent screen → External → add yourself as a test user.
3. Credentials → OAuth client ID → **Desktop app** → download the JSON and
   save it next to this file as ``client_secret.json``.
4. ``python3 youtube.py login`` — opens a browser once, stores a refresh token
   in ``youtube_token.json`` (mode 600). Both files are git-ignored. Never
   commit either.

Usage
-----
    python3 youtube.py login
    python3 youtube.py upload out/video.mp4 --title "..." --description-file d.txt
    python3 youtube.py upload out/video.mp4 --title "..." --publish-at 2026-09-20T17:00:00Z
    python3 youtube.py upload out/video.mp4 --title "..." --dry-run

Only the ``youtube.upload`` scope is requested — enough to insert a video and
nothing else. It cannot read your analytics, edit other videos, or delete
anything.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
import secrets
import socket
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

__all__ = ["upload", "load_credentials", "YouTubeError", "AuditRestriction", "SCOPE"]

SCOPE = "https://www.googleapis.com/auth/youtube.upload"
AUTH_HOST, AUTH_PATH = "accounts.google.com", "/o/oauth2/v2/auth"
TOKEN_HOST, TOKEN_PATH = "oauth2.googleapis.com", "/token"
API_HOST = "www.googleapis.com"
UPLOAD_PATH = "/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"

CHUNK = 8 * 1024 * 1024                 # must be a multiple of 256 KiB
HERE = Path(__file__).resolve().parent
CLIENT_SECRET = HERE / "client_secret.json"
TOKEN_FILE = HERE / "youtube_token.json"

# https://developers.google.com/youtube/v3/docs/videoCategories/list for the full set
CATEGORY_DEFAULT = "22"                 # People & Blogs
MAX_TITLE = 100
MAX_DESCRIPTION = 5000
MAX_TAGS_CHARS = 500


class YouTubeError(RuntimeError):
    """An upload could not be completed."""


class AuditRestriction(YouTubeError):
    """The API project is unaudited, so the upload could not go public."""


# --------------------------------------------------------------------------
# plumbing
# --------------------------------------------------------------------------
def _https(host, method, path, body=None, headers=None, timeout=120):
    """One HTTPS round trip. Returns (status, headers, body-bytes).

    http.client rather than urllib because a resumable upload answers 308 for
    "keep going", which urllib would try to follow as a redirect.
    """
    conn = http.client.HTTPSConnection(host, timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        conn.close()


def _json_or_raise(status, payload, what):
    try:
        data = json.loads(payload.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        raise YouTubeError("%s: unreadable response (HTTP %d)" % (what, status))
    if status >= 400:
        err = (data.get("error") or {})
        msg = err.get("message") or err.get("error_description") or payload[:200].decode("utf-8", "replace")
        reason = ""
        details = err.get("errors") or []
        if details and isinstance(details, list):
            reason = " [%s]" % details[0].get("reason", "")
        raise YouTubeError("%s failed (HTTP %d)%s: %s" % (what, status, reason, msg))
    return data


def _read_client_secret(path=None):
    path = Path(path) if path else CLIENT_SECRET
    if not path.exists():
        raise YouTubeError(
            "%s not found. Create an OAuth client ID of type 'Desktop app' in the "
            "Google Cloud console and save the downloaded JSON there." % path
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise YouTubeError("%s is not valid JSON (%s)" % (path, exc)) from exc
    blob = raw.get("installed") or raw.get("web")
    if not blob or not blob.get("client_id") or not blob.get("client_secret"):
        raise YouTubeError(
            "%s does not look like a Desktop-app OAuth client (no installed.client_id)." % path
        )
    if raw.get("web"):
        print("youtube.py: warning — that is a 'Web application' client; a 'Desktop app' "
              "client is what this flow expects.", file=sys.stderr)
    return blob["client_id"], blob["client_secret"]


def _save_token(data, path=None):
    path = Path(path) if path else TOKEN_FILE
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)           # it is a credential, not a config file
    except OSError:                     # pragma: no cover
        pass


# --------------------------------------------------------------------------
# OAuth
# --------------------------------------------------------------------------
class _CallbackHandler(BaseHTTPRequestHandler):
    result = None

    def do_GET(self):                                     # noqa: N802
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _CallbackHandler.result = query
        ok = "code" in query
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<body style='font:14px system-ui;padding:40px'>"
            + (b"<h3>Authorised.</h3><p>You can close this tab and go back to the terminal.</p>"
               if ok else b"<h3>Authorisation failed.</h3><p>Check the terminal.</p>")
            + b"</body>")

    def log_message(self, *args):                         # keep the console clean
        pass


def login(client_secret_path=None, token_path=None, open_browser=True):
    """Run the installed-app OAuth flow once and store a refresh token."""
    client_id, client_secret = _read_client_secret(client_secret_path)

    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(24)

    with socket.socket() as probe:                        # a free loopback port
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    redirect_uri = "http://127.0.0.1:%d/" % port

    params = {
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent",
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
    }
    url = "https://%s%s?%s" % (AUTH_HOST, AUTH_PATH, urllib.parse.urlencode(params))
    print("Open this URL to authorise (it asks only for permission to upload):\n\n%s\n" % url)
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:                                 # pragma: no cover
            pass

    server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    server.timeout = 300
    _CallbackHandler.result = None
    server.handle_request()
    server.server_close()
    got = _CallbackHandler.result
    if not got:
        raise YouTubeError("timed out waiting for the browser to come back")
    if got.get("state", [None])[0] != state:
        raise YouTubeError("state mismatch on the OAuth callback — aborting")
    if "code" not in got:
        raise YouTubeError("authorisation denied: %s" % got.get("error", ["unknown"])[0])

    body = urllib.parse.urlencode({
        "code": got["code"][0], "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
        "code_verifier": verifier,
    }).encode()
    status, _, payload = _https(TOKEN_HOST, "POST", TOKEN_PATH, body,
                                {"Content-Type": "application/x-www-form-urlencoded"})
    token = _json_or_raise(status, payload, "token exchange")
    if not token.get("refresh_token"):
        raise YouTubeError(
            "Google returned no refresh token. Revoke this app at "
            "https://myaccount.google.com/permissions and run login again."
        )
    token["obtained_at"] = int(time.time())
    _save_token(token, token_path)
    print("Authorised. Refresh token stored in %s (mode 600) — do not commit it."
          % (token_path or TOKEN_FILE))
    return token


def load_credentials(client_secret_path=None, token_path=None):
    """Return a valid access token, refreshing it if necessary."""
    path = Path(token_path) if token_path else TOKEN_FILE
    if not path.exists():
        raise YouTubeError("not authorised yet — run: python3 youtube.py login")
    try:
        token = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise YouTubeError("%s is not valid JSON (%s); run login again" % (path, exc)) from exc

    age = time.time() - token.get("obtained_at", 0)
    if token.get("access_token") and age < token.get("expires_in", 3600) - 120:
        return token["access_token"]

    client_id, client_secret = _read_client_secret(client_secret_path)
    body = urllib.parse.urlencode({
        "client_id": client_id, "client_secret": client_secret,
        "refresh_token": token["refresh_token"], "grant_type": "refresh_token",
    }).encode()
    status, _, payload = _https(TOKEN_HOST, "POST", TOKEN_PATH, body,
                                {"Content-Type": "application/x-www-form-urlencoded"})
    fresh = _json_or_raise(status, payload, "token refresh")
    token.update(fresh)
    token["obtained_at"] = int(time.time())
    _save_token(token, path)
    return token["access_token"]


# --------------------------------------------------------------------------
# upload
# --------------------------------------------------------------------------
def build_body(title, description="", tags=None, category_id=CATEGORY_DEFAULT,
               privacy="private", publish_at=None, made_for_kids=False,
               language=None):
    """Validate the metadata and build the videos.insert request body."""
    title = (title or "").strip()
    if not title:
        raise YouTubeError("a title is required")
    if len(title) > MAX_TITLE:
        raise YouTubeError("title is %d characters; YouTube allows %d" % (len(title), MAX_TITLE))
    if "<" in title or ">" in title:
        raise YouTubeError("YouTube rejects < and > in titles")
    description = description or ""
    if len(description) > MAX_DESCRIPTION:
        raise YouTubeError("description is %d characters; YouTube allows %d"
                           % (len(description), MAX_DESCRIPTION))
    tags = [t.strip() for t in (tags or []) if t and t.strip()]
    if sum(len(t) + 1 for t in tags) > MAX_TAGS_CHARS:
        raise YouTubeError("tags total more than %d characters" % MAX_TAGS_CHARS)

    if privacy not in ("private", "unlisted", "public"):
        raise YouTubeError('privacy must be private, unlisted or public (got %r)' % privacy)

    status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": bool(made_for_kids)}
    if publish_at:
        when = publish_at if isinstance(publish_at, datetime) else _parse_iso(publish_at)
        if when <= datetime.now(timezone.utc):
            raise YouTubeError("--publish-at must be in the future (got %s)" % when.isoformat())
        if privacy != "private":
            # YouTube only honours publishAt on a private video; it goes public at that time.
            raise YouTubeError("a scheduled publishAt requires privacy 'private' — "
                               "YouTube flips it to public at that moment")
        status["publishAt"] = (when.astimezone(timezone.utc)
                               .replace(microsecond=0).isoformat().replace("+00:00", "Z"))

    snippet = {"title": title, "description": description,
               "tags": tags, "categoryId": str(category_id)}
    if language:
        snippet["defaultLanguage"] = language
        snippet["defaultAudioLanguage"] = language
    return {"snippet": snippet, "status": status}


def _parse_iso(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise YouTubeError("could not read %r as an ISO-8601 timestamp "
                           "(try 2026-09-20T17:00:00Z)" % value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def upload(video_path, title, description="", tags=None, category_id=CATEGORY_DEFAULT,
           privacy="private", publish_at=None, made_for_kids=False, language=None,
           token=None, dry_run=False, progress=None, transport=None,
           client_secret_path=None, token_path=None):
    """Upload one video. Returns the created video resource.

    ``transport`` exists so the resumable protocol can be tested without a
    network; leave it None in real use.
    """
    path = Path(video_path)
    if not path.exists():
        raise YouTubeError("no such file: %s" % path)
    size = path.stat().st_size
    if size == 0:
        raise YouTubeError("%s is empty" % path)

    body = build_body(title, description, tags, category_id, privacy,
                      publish_at, made_for_kids, language)

    if dry_run:
        return {"dryRun": True, "bytes": size, "request": body}

    send = transport or _https
    access = token or load_credentials(client_secret_path, token_path)
    meta = json.dumps(body).encode("utf-8")

    status, headers, payload = send(API_HOST, "POST", UPLOAD_PATH, meta, {
        "Authorization": "Bearer " + access,
        "Content-Type": "application/json; charset=UTF-8",
        "Content-Length": str(len(meta)),
        "X-Upload-Content-Length": str(size),
        "X-Upload-Content-Type": "video/*",
    })
    if status >= 400:
        _json_or_raise(status, payload, "starting the upload")
    location = headers.get("Location") or headers.get("location")
    if not location:
        raise YouTubeError("the API accepted the metadata but returned no upload URL")

    parsed = urllib.parse.urlparse(location)
    up_host = parsed.netloc or API_HOST
    up_path = parsed.path + (("?" + parsed.query) if parsed.query else "")

    sent, attempts, result = 0, 0, None
    with path.open("rb") as fh:
        while sent < size:
            fh.seek(sent)
            chunk = fh.read(CHUNK)
            last = sent + len(chunk) - 1
            try:
                status, headers, payload = send(up_host, "PUT", up_path, chunk, {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": "bytes %d-%d/%d" % (sent, last, size),
                }, 600)
            except (OSError, http.client.HTTPException) as exc:
                attempts += 1
                if attempts > 5:
                    raise YouTubeError("upload failed after 5 retries: %s" % exc) from exc
                time.sleep(min(2 ** attempts, 30))
                sent = _resume_offset(send, up_host, up_path, size, sent)
                continue

            if status in (200, 201):
                result = _json_or_raise(status, payload, "finishing the upload")
                sent = size
            elif status == 308:
                rng = headers.get("Range") or headers.get("range")
                sent = int(rng.split("-")[1]) + 1 if rng else sent + len(chunk)
                attempts = 0
            elif status in (500, 502, 503, 504):
                attempts += 1
                if attempts > 5:
                    raise YouTubeError("upload failed after 5 retries (HTTP %d)" % status)
                time.sleep(min(2 ** attempts, 30))
                sent = _resume_offset(send, up_host, up_path, size, sent)
            else:
                _json_or_raise(status, payload, "uploading")
                raise YouTubeError("unexpected HTTP %d during upload" % status)

            if progress:
                progress(min(sent, size), size)

    if result is None:
        raise YouTubeError("upload finished but YouTube returned no video resource")

    got = (result.get("status") or {}).get("privacyStatus")
    if privacy != "private" and got == "private":
        raise AuditRestriction(
            "Uploaded, but YouTube forced it to PRIVATE. That is what an API project "
            "which has not passed the compliance audit always does, whatever privacy "
            "you ask for, and it cannot be appealed. Video id %s. Either publish it by "
            "hand in YouTube Studio, or apply for the API compliance audit."
            % result.get("id", "?")
        )
    return result


def _resume_offset(send, host, path, size, fallback):
    """Ask the server how much of the file it already has."""
    try:
        status, headers, _ = send(host, "PUT", path, b"", {
            "Content-Length": "0", "Content-Range": "bytes */%d" % size,
        })
    except Exception:                                     # pragma: no cover
        return fallback
    if status in (200, 201):
        return size
    rng = headers.get("Range") or headers.get("range")
    return int(rng.split("-")[1]) + 1 if rng else 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(prog="youtube.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="authorise once and store a refresh token")
    p_login.add_argument("--no-browser", action="store_true")

    p_up = sub.add_parser("upload", help="upload a video file")
    p_up.add_argument("video")
    p_up.add_argument("--title", required=True)
    p_up.add_argument("--description", default="")
    p_up.add_argument("--description-file")
    p_up.add_argument("--tags", nargs="*", default=[])
    p_up.add_argument("--category", default=CATEGORY_DEFAULT)
    p_up.add_argument("--privacy", choices=["private", "unlisted", "public"], default="private")
    p_up.add_argument("--publish-at", help="ISO-8601; schedules the public go-live (needs an audited project)")
    p_up.add_argument("--made-for-kids", action="store_true")
    p_up.add_argument("--language", help="e.g. en")
    p_up.add_argument("--dry-run", action="store_true", help="validate and print, send nothing")
    p_up.add_argument("--agent", help="report progress to this dashboard agent id via status.py")

    args = parser.parse_args(argv)
    try:
        if args.command == "login":
            login(open_browser=not args.no_browser)
            return 0

        description = args.description
        if args.description_file:
            description = Path(args.description_file).read_text(encoding="utf-8")

        reporter = _Reporter(args.agent, Path(args.video).name, dry=args.dry_run)
        try:
            reporter.start()
            result = upload(
                args.video, args.title, description, args.tags, args.category,
                args.privacy, args.publish_at, args.made_for_kids, args.language,
                dry_run=args.dry_run, progress=_print_progress if not args.dry_run else None,
            )
        except YouTubeError as exc:
            reporter.fail(str(exc))
            raise

        if args.dry_run:
            print("DRY RUN — nothing was sent. %.1f MB would upload with:"
                  % (result["bytes"] / 1048576))
            print(json.dumps(result["request"], indent=2))
            reporter.finish("dry run ok")
            return 0

        vid = result.get("id", "?")
        state = (result.get("status") or {}).get("privacyStatus", "?")
        print("\nUploaded: https://youtu.be/%s  (%s)" % (vid, state))
        if (result.get("status") or {}).get("publishAt"):
            print("Scheduled to go public at %s" % result["status"]["publishAt"])
        reporter.finish("uploaded %s (%s)" % (vid, state))
        return 0
    except AuditRestriction as exc:
        print("youtube.py: %s" % exc, file=sys.stderr)
        return 2
    except YouTubeError as exc:
        print("youtube.py: %s" % exc, file=sys.stderr)
        return 1


def _print_progress(done, total):
    pct = 100.0 * done / total if total else 100.0
    sys.stdout.write("\r  uploading %5.1f%%  (%.1f / %.1f MB)" %
                     (pct, done / 1048576, total / 1048576))
    sys.stdout.flush()


class _Reporter:
    """Mirror the upload onto the operations dashboard, if status.py is around."""

    def __init__(self, agent_id, label, dry=False):
        self.agent, self.label, self.mod, self.dry = agent_id, label, None, dry
        if not agent_id:
            return
        try:
            sys.path.insert(0, str(HERE))
            import status
            self.mod = status
        except Exception:                                 # pragma: no cover
            print("youtube.py: status.py not usable — continuing without dashboard reporting",
                  file=sys.stderr)

    def _safe(self, fn, *a):
        if not self.mod:
            return
        try:
            fn(*a)
        except Exception as exc:                          # never let reporting kill an upload
            print("youtube.py: dashboard update failed: %s" % exc, file=sys.stderr)

    def start(self):
        if self.dry:
            return                                        # nothing is really running
        self._safe(lambda: self.mod.start(self.agent, "Uploading %s" % self.label))

    def finish(self, msg):
        if self.dry:
            # visible in the feed, but not counted as a run
            self._safe(lambda: self.mod.log(self.agent, "Dry run: %s" % msg, "info"))
            return
        self._safe(lambda: self.mod.finish(self.agent, msg))

    def fail(self, msg):
        if self.dry:
            self._safe(lambda: self.mod.log(self.agent, "Dry run failed: " + msg[:280], "warn"))
            return
        self._safe(lambda: self.mod.fail(self.agent, msg[:300]))


if __name__ == "__main__":
    sys.exit(main())
